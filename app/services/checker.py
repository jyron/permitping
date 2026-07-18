"""Daily cycle: sync feed datasets, then check every active monitor.

Feed-city monitors read the freshly synced store (zero per-permit HTTP).
Portal-city monitors make one live lookup each.
"""

import asyncio
from datetime import datetime

from app import config
from app.db import repo
from app.db.database import SessionLocal
from app.registry import get_jurisdiction
from app.services import emailer, lookup
from app.services.adapters.base import AdapterUnavailable, PermitNotFound
from app.sync import feeds


def run_all_checks() -> dict:
    db = SessionLocal()
    checked = changed = errors = 0
    try:
        for monitor in repo.list_active_monitors(db):
            jurisdiction = get_jurisdiction(monitor.jurisdiction)
            if not jurisdiction:
                continue
            now = datetime.utcnow()
            try:
                record = lookup.lookup_permit(
                    db,
                    monitor.jurisdiction,
                    monitor.permit_number,
                    force_live=jurisdiction["source_class"] == "portal",
                )
            except (PermitNotFound, AdapterUnavailable):
                errors += 1
                continue
            checked += 1
            if record.status != monitor.current_status:
                previous = monitor.current_status
                repo.record_status_change(db, monitor, record.status, now)
                emailer.send_status_change(
                    monitor.account.email, monitor, previous, record.status, now
                )
                changed += 1
            else:
                repo.touch_monitor(db, monitor, now)
    finally:
        db.close()
    return {"checked": checked, "changed": changed, "errors": errors}


def run_sync() -> dict:
    db = SessionLocal()
    try:
        return feeds.sync_all(db)
    finally:
        db.close()


def run_cycle() -> dict:
    """One scheduled cycle: sync feeds first so checks read fresh data."""
    return {"sync": run_sync(), "checks": run_all_checks()}


async def scheduler_loop():
    # initial sync shortly after boot so feed lookups hit the store
    try:
        result = await asyncio.to_thread(run_sync)
        print(f"[sync] startup {result}")
    except Exception as exc:
        print(f"[sync] startup failed: {exc}")

    while True:
        await asyncio.sleep(config.CHECK_INTERVAL_SECONDS)
        try:
            result = await asyncio.to_thread(run_cycle)
            print(f"[cycle] {result}")
        except Exception as exc:  # keep the loop alive across bad runs
            print(f"[cycle] failed: {exc}")
