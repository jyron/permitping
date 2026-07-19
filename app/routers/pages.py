import json
import re
import time
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app import config
from app.db import repo
from app.db.database import get_db
from app.registry import JURISDICTIONS, get_jurisdiction
from app.services import analytics, lookup
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
    # every page gets the analytics config; an empty key makes ph.js a no-op
    replacements = {
        "__POSTHOG_KEY__": config.POSTHOG_API_KEY,
        "__POSTHOG_HOST__": config.POSTHOG_HOST,
        **replacements,
    }
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


def _jsonld(data: dict) -> str:
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def _breadcrumbs(*crumbs: tuple[str, str]) -> str:
    """BreadcrumbList JSON-LD from (name, url) pairs, root first."""
    return _jsonld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(crumbs, 1)
        ],
    })


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
    return _render("index.html", {})


@router.get("/account", include_in_schema=False)
def account_page():
    return _render("account.html", {})


@router.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nDisallow: /account\nDisallow: /api/\n\n"
        f"Sitemap: {config.BASE_URL}/sitemap.xml\n"
    )


_sitemap_cache: dict[str, tuple[float, str]] = {}
_SITEMAP_TTL = 1800


def _urlset(urls: list[tuple[str, str | None]]) -> str:
    """urls are (loc, lastmod-or-None) pairs."""
    body = "".join(
        f"<url><loc>{escape(loc)}</loc>"
        + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
        + "</url>"
        for loc, lastmod in urls
    )
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
def sitemap_index(db: Session = Depends(get_db)):
    def build():
        names = ["core"]
        for j in JURISDICTIONS:
            # a city only gets section sitemaps once its store has rows,
            # so the index never points at empty files
            if not repo.count_city_permits(db, j["slug"]):
                continue
            names.append(f"permits-{j['slug']}")
            if j.get("address_search"):
                names.append(f"addresses-{j['slug']}")
        body = "".join(
            f"<sitemap><loc>{config.BASE_URL}/sitemap-{name}.xml</loc></sitemap>"
            for name in names
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{body}</sitemapindex>"
        )
    return _cached_sitemap("index", build)


@router.get("/sitemap-core.xml", include_in_schema=False)
def sitemap_core():
    urls = [(f"{config.BASE_URL}/", None)] + [
        (f"{config.BASE_URL}/{j['slug']}", None) for j in JURISDICTIONS
    ]
    return Response(_urlset(urls), media_type="application/xml")


@router.get("/sitemap-permits-{slug}.xml", include_in_schema=False)
def sitemap_city_permits(slug: str, db: Session = Depends(get_db)):
    if not get_jurisdiction(slug):
        raise HTTPException(404)

    def build():
        return _urlset([
            (f"{config.BASE_URL}/{slug}/permit/{quote(permit_number)}",
             fetched_at.strftime("%Y-%m-%d") if fetched_at else None)
            for permit_number, fetched_at
            in repo.city_permit_pages(db, slug, SITEMAP_SECTION_CAP)
        ])
    return _cached_sitemap(f"permits-{slug}", build)


@router.get("/sitemap-addresses-{slug}.xml", include_in_schema=False)
def sitemap_city_addresses(slug: str, db: Session = Depends(get_db)):
    jurisdiction = get_jurisdiction(slug)
    # only cities with address search get address pages
    if not jurisdiction or not jurisdiction.get("address_search"):
        raise HTTPException(404)

    def build():
        return _urlset([
            (f"{config.BASE_URL}/{slug}/address/{slugify_address(address)}",
             fetched_at.strftime("%Y-%m-%d") if fetched_at else None)
            for address, fetched_at
            in repo.city_address_pages(db, slug, SITEMAP_SECTION_CAP)
        ])
    return _cached_sitemap(f"addresses-{slug}", build)


@router.get("/{slug}/permit/{permit_number}", include_in_schema=False)
def permit_page(slug: str, permit_number: str, db: Session = Depends(get_db)):
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction:
        return _not_found_page("That city isn't supported yet.", "/", "See supported cities")
    try:
        record = lookup.lookup_permit(db, slug, permit_number)
    except PermitNotFound:
        analytics.capture("permit_lookup_failed", {"city": slug, "reason": "not_found"})
        return _not_found_page(
            f"No permit numbered {permit_number} is on file in {jurisdiction['name']}. "
            f"Numbers there usually look like {jurisdiction['permit_example']}.",
            f"/{slug}", f"Search {jurisdiction['city']} permits",
        )
    except AdapterUnavailable as exc:
        analytics.capture("permit_lookup_failed", {"city": slug, "reason": "adapter_unavailable"})
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
        "__JSONLD__": _breadcrumbs(
            ("All cities", f"{config.BASE_URL}/"),
            (jurisdiction["name"], f"{config.BASE_URL}/{slug}"),
            (record.permit_number, f"{config.BASE_URL}{canonical_path}"),
        ),
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
    permits = []
    if suggestion and suggestion["filters"]:
        data = await permits_at(slug, suggestion["filters"])
        permits = data["permits"] if data else []
    if not permits:
        # live sources unavailable (or a store-derived suggestion): serve the
        # page from our own permit store
        tokens = [t for t in addr_slug.split("-") if t]
        house = tokens[0] if tokens and tokens[0].isdigit() else None
        street = [t for t in (tokens[1:] if house else tokens) if len(t) >= 2]
        target = "".join(re.findall(r"[a-z0-9]+", addr_slug))
        rows_from_store = repo.permits_matching_address(db, slug, house, street, 100)
        permits = [
            {
                "permit_number": r.permit_number,
                "status": r.status,
                "description": r.description,
                "date": r.status_date,
                "address": r.address.upper(),
            }
            for r in rows_from_store
            if "".join(re.findall(r"[a-z0-9]+", r.address.lower())) == target
        ][:25]
        if permits and not suggestion:
            suggestion = {"address": permits[0]["address"]}

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
        "__JSONLD__": _breadcrumbs(
            ("All cities", f"{config.BASE_URL}/"),
            (jurisdiction["name"], f"{config.BASE_URL}/{slug}"),
            (address_text, f"{config.BASE_URL}/{slug}/address/{addr_slug}"),
        ),
        "__ROBOTS__": robots,
        "__ADDRESS__": escape(address_text),
        "__CITY_NAME__": escape(jurisdiction["name"]),
        "__CITY__": escape(jurisdiction["city"]),
        "__STATE__": jurisdiction["state"],
        "__SLUG__": jurisdiction["slug"],
        "__COUNT_TEXT__": count_text,
        "__ROWS__": rows,
    })


_CITY_STATS_MIN = 25  # below this the store is too sparse to brag about


def _city_stats_block(stats: dict, city: str, year: int) -> str:
    if stats["total"] < _CITY_STATS_MIN:
        return ""
    parts = [f"<b>{stats['total']:,}</b> {escape(city)} permits on file"]
    if stats["year"]:
        parts.append(f"<b>{stats['year']:,}</b> with activity in {year}")
    if stats["latest"]:
        parts.append(f"latest update {escape(stats['latest'])}")
    return f'<p class="city-stats">{" · ".join(parts)}</p>'


def _city_recent_block(recent: list, slug: str, city: str) -> str:
    if not recent:
        return ""
    rows = "".join(
        f"""<div class="permit-row">
  <div class="permit-row-main">
    <a class="permit-link" href="/{slug}/permit/{quote(p.permit_number)}">{escape(p.permit_number)}</a>
    <span class="status-badge {_status_class(p.status)}">{escape(p.status)}</span>
  </div>
  <p class="result-meta">{escape(" · ".join(x for x in (p.description[:120], p.address, p.status_date) if x))}</p>
</div>"""
        for p in recent
    )
    return (
        f"<section><h2>Recent {escape(city)} permits</h2>"
        f'<div class="card">{rows}</div></section>'
    )


def _city_addresses_block(addresses: list, slug: str, city: str) -> str:
    if not addresses:
        return ""
    rows = "".join(
        f'<div class="permit-row"><div class="permit-row-main">'
        f'<a class="permit-link" href="/{slug}/address/{slugify_address(address)}">{escape(address)}</a>'
        f'<span class="muted">{count} permit{"s" if count != 1 else ""}</span>'
        f"</div></div>"
        for address, count, _latest in addresses
    )
    return (
        f"<section><h2>Recently active addresses in {escape(city)}</h2>"
        f'<div class="card">{rows}</div></section>'
    )


def _city_faq(jurisdiction: dict, stats: dict, year: int) -> list[tuple[str, str]]:
    city = jurisdiction["city"]
    faqs = [
        (
            f"How do I check the status of a building permit in {city}?",
            f"Type the permit number (for example {jurisdiction['permit_example']}) "
            f"or a property address into the search box on this page. Permit Ping "
            f"shows the current status straight from the official record — free, "
            f"no account required.",
        ),
        (
            f"Where does this {city} permit data come from?",
            f"Records come from {jurisdiction['source'].get('attribution', 'the official municipal record')}. "
            f"{jurisdiction['freshness']}. Every permit links back to the official "
            f"source, {jurisdiction['portal_name']}, so you can verify it yourself.",
        ),
        (
            f"What does a {city} permit number look like?",
            f"{city} permit numbers typically look like {jurisdiction['permit_example']}. "
            f"You can find yours on the permit card posted at the job site, on city "
            f"correspondence, or by searching this page by address.",
        ),
    ]
    if jurisdiction.get("address_search"):
        faqs.append((
            f"Can I see every permit at a specific {city} address?",
            f"Yes — type the address into the search box and pick it from the "
            f"suggestions. You'll get a page listing every {city} permit on file "
            f"at that address with its current status.",
        ))
    if stats["total"] >= _CITY_STATS_MIN:
        answer = f"Permit Ping currently has {stats['total']:,} {city} permits on file"
        if stats["year"]:
            answer += f", {stats['year']:,} of them with activity in {year}"
        faqs.append((f"How many {city} permits does Permit Ping track?",
                     answer + ", refreshed from the official dataset."))
    faqs.append((
        "How can I find out when a permit's status changes?",
        "Open the permit's page and enter your email — Permit Ping checks the "
        "official record daily and emails you the moment the status changes. "
        "Monitoring is free for up to 3 permits.",
    ))
    return faqs


@router.get("/{slug}", include_in_schema=False)
def city_page(slug: str, db: Session = Depends(get_db)):
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction:
        return _not_found_page("That page doesn't exist.", "/", "Back to Permit Ping")
    city = jurisdiction["city"]
    year = datetime.utcnow().year
    stats = repo.city_stats(db, slug, year)
    recent = repo.recent_city_permits(db, slug, 12)
    addresses = (repo.active_city_addresses(db, slug, 12)
                 if jurisdiction.get("address_search") else [])
    faqs = _city_faq(jurisdiction, stats, year)
    faq_jsonld = _jsonld({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faqs
        ],
    })
    faq_section = (
        f"<section><h2>{escape(city)} permit lookup FAQ</h2>"
        + "".join(f'<h3 class="faq-q">{escape(q)}</h3><p class="faq-a">{escape(a)}</p>'
                  for q, a in faqs)
        + "</section>"
    )
    return _render("city.html", {
        "__CANONICAL__": f"{config.BASE_URL}/{jurisdiction['slug']}",
        "__JSONLD__": faq_jsonld,
        "__CITY_NAME__": jurisdiction["name"],
        "__CITY__": city,
        "__SLUG__": jurisdiction["slug"],
        "__PORTAL_NAME__": jurisdiction["portal_name"],
        "__PORTAL_URL__": jurisdiction["portal_url"],
        "__PERMIT_EXAMPLE__": jurisdiction["permit_example"],
        "__STATS_BLOCK__": _city_stats_block(stats, city, year),
        "__RECENT_BLOCK__": _city_recent_block(recent, slug, city),
        "__ADDRESSES_BLOCK__": _city_addresses_block(addresses, slug, city),
        "__FAQ_SECTION__": faq_section,
    })
