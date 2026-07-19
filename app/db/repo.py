import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Account, LoginToken, Monitor, Permit, StatusEvent


def get_permit(db: Session, jurisdiction: str, permit_number: str) -> Permit | None:
    return db.scalar(
        select(Permit).where(
            Permit.jurisdiction == jurisdiction,
            func.upper(Permit.permit_number) == permit_number.strip().upper(),
        )
    )


def upsert_permit(db: Session, jurisdiction: str, permit_number: str, **fields) -> Permit:
    permit = get_permit(db, jurisdiction, permit_number)
    if permit:
        for key, value in fields.items():
            setattr(permit, key, value)
    else:
        permit = Permit(jurisdiction=jurisdiction, permit_number=permit_number, **fields)
        db.add(permit)
    db.commit()
    return permit


def replace_city_permits(db: Session, jurisdiction: str, rows: list[dict]) -> int:
    """Full-snapshot sync: atomically swap a feed city's rows."""
    db.query(Permit).filter(Permit.jurisdiction == jurisdiction).delete()
    db.bulk_insert_mappings(Permit, [{"jurisdiction": jurisdiction, **r} for r in rows])
    db.commit()
    return len(rows)


def count_city_permits(db: Session, jurisdiction: str) -> int:
    return db.scalar(
        select(func.count(Permit.id)).where(Permit.jurisdiction == jurisdiction)
    )


def recent_permit_pages(db: Session, limit: int) -> list[tuple[str, str]]:
    """(jurisdiction, permit_number) of the most recently seen permits —
    the permit detail pages worth listing in the sitemap."""
    return list(
        db.execute(
            select(Permit.jurisdiction, Permit.permit_number)
            .order_by(Permit.fetched_at.desc())
            .limit(limit)
        )
    )


def known_addresses(db: Session, limit: int) -> list[tuple[str, str]]:
    """Distinct (jurisdiction, address) pairs that have at least one permit —
    the only address pages that belong in the sitemap."""
    return list(
        db.execute(
            select(Permit.jurisdiction, Permit.address)
            .where(Permit.address != "")
            .group_by(Permit.jurisdiction, Permit.address)
            .order_by(func.max(Permit.fetched_at).desc())
            .limit(limit)
        )
    )


def get_account_by_email(db: Session, email: str) -> Account | None:
    return db.scalar(select(Account).where(Account.email == email))


def create_account(db: Session, email: str) -> Account:
    account = Account(email=email)
    db.add(account)
    db.commit()
    return account


def update_account_email(db: Session, account: Account, email: str) -> Account:
    account.email = email
    db.commit()
    return account


def count_active_monitors(db: Session, account_id: int) -> int:
    return db.scalar(
        select(func.count(Monitor.id)).where(
            Monitor.account_id == account_id, Monitor.paused.is_(False)
        )
    )


def get_monitor(db: Session, account_id: int, monitor_id: int) -> Monitor | None:
    return db.scalar(
        select(Monitor).where(Monitor.id == monitor_id, Monitor.account_id == account_id)
    )


def find_monitor(
    db: Session, account_id: int, jurisdiction: str, permit_number: str
) -> Monitor | None:
    return db.scalar(
        select(Monitor).where(
            Monitor.account_id == account_id,
            Monitor.jurisdiction == jurisdiction,
            Monitor.permit_number == permit_number,
        )
    )


def create_monitor(db: Session, account_id: int, **fields) -> Monitor:
    monitor = Monitor(account_id=account_id, **fields)
    db.add(monitor)
    db.commit()
    return monitor


def list_monitors(db: Session, account_id: int) -> list[Monitor]:
    return list(
        db.scalars(
            select(Monitor)
            .where(Monitor.account_id == account_id)
            .order_by(Monitor.created_at.desc())
        )
    )


def set_monitor_paused(db: Session, monitor: Monitor, paused: bool) -> Monitor:
    monitor.paused = paused
    db.commit()
    return monitor


def delete_monitor(db: Session, monitor: Monitor) -> None:
    db.delete(monitor)
    db.commit()


def record_status_change(
    db: Session, monitor: Monitor, new_status: str, checked_at: datetime
) -> StatusEvent:
    event = StatusEvent(
        monitor_id=monitor.id,
        previous_status=monitor.current_status,
        new_status=new_status,
        changed_at=checked_at,
    )
    monitor.current_status = new_status
    monitor.last_checked_at = checked_at
    db.add(event)
    db.commit()
    return event


def touch_monitor(db: Session, monitor: Monitor, checked_at: datetime) -> None:
    monitor.last_checked_at = checked_at
    db.commit()


def list_events(db: Session, monitor_id: int) -> list[StatusEvent]:
    return list(
        db.scalars(
            select(StatusEvent)
            .where(StatusEvent.monitor_id == monitor_id)
            .order_by(StatusEvent.changed_at.desc())
        )
    )


def list_active_monitors(db: Session) -> list[Monitor]:
    return list(db.scalars(select(Monitor).where(Monitor.paused.is_(False))))


def create_login_token(db: Session, account_id: int, days: int) -> LoginToken:
    token = LoginToken(
        account_id=account_id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.utcnow() + timedelta(days=days),
    )
    db.add(token)
    db.commit()
    return token


def get_account_by_token(db: Session, token: str) -> Account | None:
    row = db.scalar(
        select(LoginToken).where(
            LoginToken.token == token, LoginToken.expires_at > datetime.utcnow()
        )
    )
    return db.get(Account, row.account_id) if row else None
