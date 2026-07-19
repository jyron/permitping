"""Permit lookup: canonical store first, live source only when needed.

- feed cities: the store is the synced dataset — read it; a miss or a forced
  check falls back to one live query (covers the window before the first
  sync lands, and permits outside a windowed Socrata sync), with the stored
  row as the answer of last resort.
- portal cities: the store acts as a short-lived cache (LOOKUP_CACHE_SECONDS);
  stale or missing -> live portal lookup, written back to the store.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import config
from app.db import repo
from app.db.models import Permit
from app.registry import get_adapter, get_jurisdiction
from app.services.adapters.base import (
    AdapterUnavailable,
    PermitNotFound,
    PermitRecord,
)


def _record_from_row(row: Permit, jurisdiction: dict) -> PermitRecord:
    age = int((datetime.utcnow() - row.fetched_at).total_seconds())
    return PermitRecord(
        permit_number=row.permit_number,
        jurisdiction=jurisdiction["slug"],
        jurisdiction_name=jurisdiction["name"],
        status=row.status,
        address=row.address,
        description=row.description,
        status_date=row.status_date,
        portal_url=row.portal_url or jurisdiction["portal_url"],
        details={
            "source": jurisdiction["source"].get("attribution", "official municipal record"),
            "freshness": jurisdiction["freshness"],
            "retrieved_seconds_ago": max(age, 0),
        },
    )


def _fetch_live(db: Session, jurisdiction: dict, permit_number: str) -> PermitRecord:
    record = get_adapter(jurisdiction["slug"]).lookup(permit_number)
    repo.upsert_permit(
        db,
        jurisdiction["slug"],
        record.permit_number,
        status=record.status,
        address=record.address,
        description=record.description[:500],
        status_date=record.status_date,
        portal_url=record.portal_url,
        fetched_at=datetime.utcnow(),
    )
    record.details.setdefault("source", "official municipal record")
    # adapters that can read the source's real refresh time set freshness
    # themselves; the registry string is only the static fallback
    record.details.setdefault("freshness", jurisdiction["freshness"])
    record.details["retrieved_seconds_ago"] = 0
    return record


def lookup_permit(
    db: Session, slug: str, permit_number: str, force_live: bool = False
) -> PermitRecord:
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction:
        raise PermitNotFound(permit_number)
    permit_number = permit_number.strip()
    if not permit_number:
        raise PermitNotFound(permit_number)

    row = repo.get_permit(db, slug, permit_number)

    if jurisdiction["source_class"] == "feed":
        if row and not force_live:
            return _record_from_row(row, jurisdiction)
        try:
            return _fetch_live(db, jurisdiction, permit_number)
        except AdapterUnavailable:
            if row:
                return _record_from_row(row, jurisdiction)
            raise

    # portal city: store row is a cache with a TTL
    if row and not force_live:
        ttl = timedelta(seconds=config.LOOKUP_CACHE_SECONDS)
        if datetime.utcnow() - row.fetched_at < ttl:
            return _record_from_row(row, jurisdiction)
    try:
        return _fetch_live(db, jurisdiction, permit_number)
    except AdapterUnavailable:
        # a stale answer beats an error page when the source is throttling us
        if row:
            return _record_from_row(row, jurisdiction)
        raise
