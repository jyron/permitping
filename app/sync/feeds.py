"""Dataset pulls for feed-class cities.

ArcGIS cities download the city's full published dataset (paged) and swap it
into the canonical permit store — full snapshot per run: simple, idempotent,
self-healing. Socrata cities publish millions of historical rows, so each of
their datasets contributes a capped newest-first window instead, upserted so
permits written through by live lookups survive outside the window. Runs on
the scheduler in app.main and via the admin endpoint.
"""

import re
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db import repo
from app.registry import feed_jurisdictions
from app.services.adapters.socrata import socrata_headers

PAGE_SIZE = 2000
SOCRATA_PAGE_SIZE = 5000
SOCRATA_SYNC_CAP = 20000


_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")


def _format_date(value, epoch_ms: bool) -> str:
    if not value:
        return ""
    if epoch_ms:
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            return str(value)
    text = str(value)
    # some datasets publish dates as MM/DD/YYYY text (NYC BIS); store ISO so
    # dates sort and the year stats count
    if match := _US_DATE.match(text):
        month, day, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return text[:10]


def _fetch_arcgis_pages(query_url: str) -> list[dict]:
    """Page through an ArcGIS layer and return all feature attribute dicts."""
    attributes, offset = [], 0
    with httpx.Client(timeout=120) as client:
        while True:
            resp = client.get(
                query_url,
                params={
                    "where": "1=1",
                    "outFields": "*",
                    "returnGeometry": "false",
                    "f": "json",
                    "resultOffset": offset,
                    "resultRecordCount": PAGE_SIZE,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"ArcGIS error: {data['error']}")
            features = data.get("features") or []
            attributes.extend(f["attributes"] for f in features)
            if len(features) < PAGE_SIZE:
                return attributes
            offset += PAGE_SIZE


def _fetch_socrata_pages(query_url: str, order_field: str | None, cap: int) -> list[dict]:
    """Newest-first window of a Socrata dataset. Plain $order/$offset paging
    only — $where-style queries trip the portals' WAF on datacenter IPs."""
    rows: list[dict] = []
    with httpx.Client(timeout=120, headers=socrata_headers()) as client:
        while len(rows) < cap:
            params = {
                "$limit": str(min(SOCRATA_PAGE_SIZE, cap - len(rows))),
                "$offset": str(len(rows)),
            }
            if order_field:
                # NULL LAST matters: plain DESC serves the null-date rows first
                params["$order"] = f"{order_field} DESC NULL LAST"
            resp = client.get(query_url, params=params)
            resp.raise_for_status()
            page = resp.json()
            rows.extend(page)
            if len(page) < SOCRATA_PAGE_SIZE:
                break
    return rows


def _permit_row(
    attrs: dict, fields: dict, jurisdiction: dict, now: datetime, epoch_ms: bool = False
) -> dict | None:
    permit_number = str(attrs.get(fields["permit_number"]) or "").strip()
    if not permit_number:
        return None
    address_cols = fields.get("address") or []
    if isinstance(address_cols, str):
        address_cols = [address_cols]
    address = " ".join(
        part for c in address_cols if (part := str(attrs.get(c) or "").strip())
    )
    return {
        "permit_number": permit_number[:100],
        "status": str(attrs.get(fields["status"]) or "Unknown")[:100],
        "address": address[:255],
        "description": str(attrs.get(fields.get("description", "")) or "")[:500],
        "status_date": _format_date(attrs.get(fields.get("date", "")), epoch_ms)[:50],
        "portal_url": jurisdiction["portal_url"],
        "fetched_at": now,
    }


def _sync_arcgis(db: Session, jurisdiction: dict, now: datetime) -> int:
    source = jurisdiction["source"]
    epoch_ms = bool(source.get("date_is_epoch_ms"))
    raw = _fetch_arcgis_pages(source["query_url"])
    # feeds can repeat a permit number (one row per address); keep the last
    rows: dict[str, dict] = {}
    for attrs in raw:
        if row := _permit_row(attrs, source["fields"], jurisdiction, now, epoch_ms):
            rows[row["permit_number"]] = row
    return repo.replace_city_permits(db, jurisdiction["slug"], list(rows.values()))


def _sync_socrata(db: Session, jurisdiction: dict, now: datetime) -> int:
    # datasets are ordered issued-first and rows arrive newest-first, so the
    # first row seen for a permit number is the authoritative one
    rows: dict[str, dict] = {}
    for dataset in jurisdiction["source"]["datasets"]:
        if not dataset.get("sync", True):
            continue
        fields = dataset["fields"]
        raw = _fetch_socrata_pages(
            dataset["query_url"],
            dataset.get("sync_order") or fields.get("date"),
            dataset.get("sync_limit", SOCRATA_SYNC_CAP),
        )
        for attrs in raw:
            if row := _permit_row(attrs, fields, jurisdiction, now):
                rows.setdefault(row["permit_number"], row)
    return repo.upsert_city_permits(db, jurisdiction["slug"], list(rows.values()))


def sync_city(db: Session, jurisdiction: dict) -> int:
    """Pull one feed city's dataset(s) into the permit store. Returns row count."""
    now = datetime.utcnow()
    if jurisdiction["adapter"] == "socrata":
        return _sync_socrata(db, jurisdiction, now)
    return _sync_arcgis(db, jurisdiction, now)


def sync_all(db: Session) -> dict[str, int | str]:
    """Sync every feed city. A city that fails keeps its previous snapshot."""
    results: dict[str, int | str] = {}
    for jurisdiction in feed_jurisdictions():
        try:
            results[jurisdiction["slug"]] = sync_city(db, jurisdiction)
        except Exception as exc:
            results[jurisdiction["slug"]] = f"failed: {exc}"
    return results
