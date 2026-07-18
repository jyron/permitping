"""Dataset pulls for feed-class cities.

Each sync downloads the city's full published dataset (paged) and swaps it
into the canonical permit store. Full snapshot per run: simple, idempotent,
self-healing. Runs on the scheduler in app.main and via the admin endpoint.
"""

from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db import repo
from app.registry import feed_jurisdictions

PAGE_SIZE = 2000


def _format_date(value, epoch_ms: bool) -> str:
    if not value:
        return ""
    if epoch_ms:
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            return str(value)
    return str(value)[:10]


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


def sync_city(db: Session, jurisdiction: dict) -> int:
    """Pull one feed city's dataset into the permit store. Returns row count."""
    source = jurisdiction["source"]
    fields = source["fields"]
    epoch_ms = bool(source.get("date_is_epoch_ms"))
    now = datetime.utcnow()

    raw = _fetch_arcgis_pages(source["query_url"])
    # feeds can repeat a permit number (one row per address); keep the last
    rows: dict[str, dict] = {}
    for attrs in raw:
        permit_number = str(attrs.get(fields["permit_number"]) or "")
        if not permit_number:
            continue
        rows[permit_number] = {
            "permit_number": permit_number,
            "status": str(attrs.get(fields["status"]) or "Unknown"),
            "address": str(attrs.get(fields.get("address", "")) or ""),
            "description": str(attrs.get(fields.get("description", "")) or "")[:500],
            "status_date": _format_date(attrs.get(fields.get("date", "")), epoch_ms),
            "portal_url": jurisdiction["portal_url"],
            "fetched_at": now,
        }
    return repo.replace_city_permits(db, jurisdiction["slug"], list(rows.values()))


def sync_all(db: Session) -> dict[str, int | str]:
    """Sync every feed city. A city that fails keeps its previous snapshot."""
    results: dict[str, int | str] = {}
    for jurisdiction in feed_jurisdictions():
        try:
            results[jurisdiction["slug"]] = sync_city(db, jurisdiction)
        except Exception as exc:
            results[jurisdiction["slug"]] = f"failed: {exc}"
    return results
