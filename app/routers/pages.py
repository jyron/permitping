import re
import time
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app import config
from app.db import repo
from app.db.database import get_db
from app.registry import JURISDICTIONS, get_jurisdiction
from app.services import lookup
from app.services.addresses import (
    permits_at,
    resolve_address_slug,
    slugify_address,
)
from app.services.adapters.base import AdapterUnavailable, PermitNotFound

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent.parent / "static"

SITEMAP_SECTION_CAP = 20000


def _render(template: str, replacements: dict[str, str]) -> HTMLResponse:
    html = (STATIC / template).read_text()
    for key, value in replacements.items():
        html = html.replace(key, value)
    return HTMLResponse(html)


def _status_class(status: str) -> str:
    """Mirror of statusClass() in app.js — keep the two in sync."""
    s = (status or "").lower()
    if re.search(r"approved|finaled|final|issued|passed|complete|done|active", s):
        return "status-green"
    if re.search(r"correction|denied|hold|expired|action|failed|revoked", s):
        return "status-red"
    if re.search(r"review|received|pending|inspection|submitted|applied", s):
        return "status-amber"
    return ""


def _not_found_page(message: str, back_href: str, back_label: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found | Permit Ping</title><meta name="robots" content="noindex">
<link rel="stylesheet" href="/static/css/style.css"></head><body>
<header class="site"><div class="wrap"><a class="logo" href="/">Permit<span>Ping</span></a></div></header>
<main class="wrap"><div class="hero"><h1>Nothing here.</h1>
<p class="sub">{escape(message)}</p></div>
<a class="btn" href="{escape(back_href)}">{escape(back_label)}</a></main></body></html>""",
        status_code=404,
    )


@router.get("/", include_in_schema=False)
def landing():
    return FileResponse(STATIC / "index.html")


@router.get("/account", include_in_schema=False)
def account_page():
    return FileResponse(STATIC / "account.html")


@router.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nDisallow: /account\nDisallow: /api/\n\n"
        f"Sitemap: {config.BASE_URL}/sitemap.xml\n"
    )


_sitemap_cache: dict[str, tuple[float, str]] = {}
_SITEMAP_TTL = 1800


def _urlset(urls: list[str]) -> str:
    body = "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )


def _cached_sitemap(key: str, build) -> Response:
    cached = _sitemap_cache.get(key)
    if not cached or time.monotonic() - cached[0] > _SITEMAP_TTL:
        _sitemap_cache[key] = (time.monotonic(), build())
    return Response(_sitemap_cache[key][1], media_type="application/xml")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_index():
    body = "".join(
        f"<sitemap><loc>{config.BASE_URL}/sitemap-{name}.xml</loc></sitemap>"
        for name in ("core", "permits", "addresses")
    )
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</sitemapindex>",
        media_type="application/xml",
    )


@router.get("/sitemap-core.xml", include_in_schema=False)
def sitemap_core():
    urls = [f"{config.BASE_URL}/"] + [
        f"{config.BASE_URL}/{j['slug']}" for j in JURISDICTIONS
    ]
    return Response(_urlset(urls), media_type="application/xml")


@router.get("/sitemap-permits.xml", include_in_schema=False)
def sitemap_permits(db: Session = Depends(get_db)):
    def build():
        return _urlset([
            f"{config.BASE_URL}/{slug}/permit/{quote(permit_number)}"
            for slug, permit_number in repo.recent_permit_pages(db, SITEMAP_SECTION_CAP)
            if get_jurisdiction(slug)
        ])
    return _cached_sitemap("permits", build)


@router.get("/sitemap-addresses.xml", include_in_schema=False)
def sitemap_addresses(db: Session = Depends(get_db)):
    def build():
        urls = []
        for slug, address in repo.known_addresses(db, SITEMAP_SECTION_CAP):
            jurisdiction = get_jurisdiction(slug)
            # only cities with address search get address pages
            if jurisdiction and jurisdiction.get("address_search"):
                urls.append(f"{config.BASE_URL}/{slug}/address/{slugify_address(address)}")
        return _urlset(urls)
    return _cached_sitemap("addresses", build)


@router.get("/{slug}/permit/{permit_number}", include_in_schema=False)
def permit_page(slug: str, permit_number: str, db: Session = Depends(get_db)):
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction:
        return _not_found_page("That city isn't supported yet.", "/", "See supported cities")
    try:
        record = lookup.lookup_permit(db, slug, permit_number)
    except PermitNotFound:
        return _not_found_page(
            f"No permit numbered {permit_number} is on file in {jurisdiction['name']}. "
            f"Numbers there usually look like {jurisdiction['permit_example']}.",
            f"/{slug}", f"Search {jurisdiction['city']} permits",
        )
    except AdapterUnavailable as exc:
        raise HTTPException(502, str(exc))

    canonical_path = f"/{slug}/permit/{quote(record.permit_number)}"
    if record.address and jurisdiction.get("address_search"):
        aslug = slugify_address(record.address)
        address_block = (
            f'<p class="result-meta"><a href="/{slug}/address/{aslug}">'
            f"{escape(record.address)}</a> — all permits at this address</p>"
        )
    elif record.address:
        address_block = f'<p class="result-meta">{escape(record.address)}</p>'
    else:
        address_block = ""
    return _render("permit.html", {
        "__CANONICAL__": f"{config.BASE_URL}{canonical_path}",
        "__PERMIT_NUMBER__": escape(record.permit_number),
        "__STATUS__": escape(record.status),
        "__STATUS_CLASS__": _status_class(record.status),
        "__ADDRESS_BLOCK__": address_block,
        "__DESCRIPTION__": escape(record.description),
        "__STATUS_DATE__": escape(record.status_date or "n/a"),
        "__CITY_NAME__": escape(jurisdiction["name"]),
        "__CITY__": escape(jurisdiction["city"]),
        "__SLUG__": jurisdiction["slug"],
        "__PORTAL_URL__": escape(record.portal_url or jurisdiction["portal_url"]),
        "__PORTAL_NAME__": escape(jurisdiction["portal_name"]),
        "__SOURCE__": escape(record.details.get("source", "official municipal record")),
        "__FRESHNESS__": escape(record.details.get("freshness") or "n/a"),
    })


@router.get("/{slug}/address/{addr_slug}", include_in_schema=False)
async def address_page(slug: str, addr_slug: str, db: Session = Depends(get_db)):
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction or not jurisdiction.get("address_search"):
        return _not_found_page(
            "Address pages aren't available for that city yet.", "/", "See supported cities"
        )
    suggestion = await resolve_address_slug(slug, addr_slug)
    data = await permits_at(slug, suggestion["filters"]) if suggestion else None
    permits = data["permits"] if data else []

    # feed what we just rendered into the permit store: it seeds the sitemap
    # (permit + address pages) and makes the linked permit pages instant
    for p in permits:
        try:
            repo.upsert_permit(
                db, slug, p["permit_number"],
                status=p["status"][:100],
                address=(p["address"] or suggestion["address"])[:255],
                description=p["description"][:500],
                status_date=p["date"][:50],
                portal_url=jurisdiction["portal_url"],
                fetched_at=datetime.utcnow(),
            )
        except Exception:  # a bad row must never break the page
            db.rollback()

    if permits:
        robots = ""
        count_text = f"{len(permits)} permit{'s' if len(permits) != 1 else ''} on file"
        rows = "".join(
            f"""<div class="permit-row">
  <div class="permit-row-main">
    <a class="permit-link" href="/{slug}/permit/{quote(p["permit_number"])}">{escape(p["permit_number"])}</a>
    <span class="status-badge {_status_class(p["status"])}">{escape(p["status"])}</span>
  </div>
  <p class="result-meta">{escape(p["description"] or "")}{f" · {escape(p['date'])}" if p["date"] else ""}</p>
</div>"""
            for p in permits
        )
    else:
        robots = '\n<meta name="robots" content="noindex">'
        count_text = "No permits on file"
        rows = (
            '<p class="muted" style="margin:0">No permits found at this address. '
            "Try the search below — a nearby address or different unit may match.</p>"
        )

    address_text = (suggestion["address"] if suggestion
                    else " ".join(addr_slug.split("-")).upper())
    return _render("address.html", {
        "__CANONICAL__": f"{config.BASE_URL}/{slug}/address/{addr_slug}",
        "__ROBOTS__": robots,
        "__ADDRESS__": escape(address_text),
        "__CITY_NAME__": escape(jurisdiction["name"]),
        "__CITY__": escape(jurisdiction["city"]),
        "__STATE__": jurisdiction["state"],
        "__SLUG__": jurisdiction["slug"],
        "__COUNT_TEXT__": count_text,
        "__ROWS__": rows,
    })


@router.get("/{slug}", include_in_schema=False)
def city_page(slug: str):
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction:
        return _not_found_page("That page doesn't exist.", "/", "Back to Permit Ping")
    return _render("city.html", {
        "__CANONICAL__": f"{config.BASE_URL}/{jurisdiction['slug']}",
        "__CITY_NAME__": jurisdiction["name"],
        "__CITY__": jurisdiction["city"],
        "__SLUG__": jurisdiction["slug"],
        "__PORTAL_NAME__": jurisdiction["portal_name"],
        "__PORTAL_URL__": jurisdiction["portal_url"],
        "__PERMIT_EXAMPLE__": jurisdiction["permit_example"],
    })
