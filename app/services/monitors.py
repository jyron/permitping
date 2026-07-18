from datetime import datetime

from sqlalchemy.orm import Session

from app import config
from app.db import repo
from app.db.models import Account, Monitor
from app.services import accounts, emailer, lookup


class PlanLimitReached(Exception):
    pass


class AlreadyMonitored(Exception):
    pass


def _create_monitor(db: Session, account: Account, jurisdiction: str, permit_number: str) -> Monitor:
    record = lookup.lookup_permit(db, jurisdiction, permit_number)

    if repo.find_monitor(db, account.id, jurisdiction, record.permit_number):
        raise AlreadyMonitored(f"{record.permit_number} is already being monitored")

    limit = config.PLAN_LIMITS.get(account.plan)
    if limit is not None and repo.count_active_monitors(db, account.id) >= limit:
        raise PlanLimitReached(
            f"The {account.plan} plan allows {limit} active permits. Upgrade to add more."
        )

    return repo.create_monitor(
        db,
        account.id,
        jurisdiction=jurisdiction,
        permit_number=record.permit_number,
        address=record.address,
        description=record.description,
        portal_url=record.portal_url,
        current_status=record.status,
        last_checked_at=datetime.utcnow(),
    )


def start_monitoring(db: Session, email: str, jurisdiction: str, permit_number: str) -> Monitor:
    account = accounts.get_or_create_account(db, email)
    monitor = _create_monitor(db, account, jurisdiction, permit_number)
    emailer.send_monitor_started(account.email, monitor, accounts.issue_token(db, account))
    return monitor


def add_monitor_for_account(db: Session, account: Account, jurisdiction: str, permit_number: str) -> Monitor:
    return _create_monitor(db, account, jurisdiction, permit_number)


def set_paused(db: Session, account: Account, monitor_id: int, paused: bool) -> Monitor:
    monitor = repo.get_monitor(db, account.id, monitor_id)
    if not monitor:
        raise LookupError("Monitor not found")
    if not paused:
        limit = config.PLAN_LIMITS.get(account.plan)
        if limit is not None and repo.count_active_monitors(db, account.id) >= limit:
            raise PlanLimitReached(
                f"The {account.plan} plan allows {limit} active permits."
            )
    return repo.set_monitor_paused(db, monitor, paused)


def remove(db: Session, account: Account, monitor_id: int) -> None:
    monitor = repo.get_monitor(db, account.id, monitor_id)
    if not monitor:
        raise LookupError("Monitor not found")
    repo.delete_monitor(db, monitor)
