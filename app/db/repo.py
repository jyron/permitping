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


def upsert_city_permits(db: Session, jurisdiction: str, rows: list[dict]) -> int:
    """Windowed sync: replace the fetched rows in place and leave everything
    outside the window (live write-through permits) untouched."""
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        db.query(Permit).filter(
            Permit.jurisdiction == jurisdiction,
            Permit.permit_number.in_([r["permit_number"] for r in chunk]),
        ).delete(synchronize_session=False)
        db.bulk_insert_mappings(
            Permit, [{"jurisdiction": jurisdiction, **r} for r in chunk]
        )
    db.commit()
    return len(rows)


def count_city_permits(db: Session, jurisdiction: str) -> int:
    return db.scalar(
        select(func.count(Permit.id)).where(Permit.jurisdiction == jurisdiction)
    )


# status_date is free text from portals; only ISO dates sort and count sanely
_ISO_DATE = "____-__-__"


def city_permit_pages(db: Session, jurisdiction: str, limit: int) -> list[tuple[str, datetime]]:
    """(permit_number, fetched_at) for the city's permit sitemap, newest first."""
    return list(
        db.execute(
            select(Permit.permit_number, Permit.fetched_at)
            .where(Permit.jurisdiction == jurisdiction)
            .order_by(Permit.fetched_at.desc(), Permit.status_date.desc())
            .limit(limit)
        )
    )


def city_address_pages(db: Session, jurisdiction: str, limit: int) -> list[tuple[str, datetime]]:
    """(address, last fetched_at) of every address with a permit on file —
    the only address pages that belong in the city's sitemap."""
    return list(
        db.execute(
            select(Permit.address, func.max(Permit.fetched_at))
            .where(Permit.jurisdiction == jurisdiction, Permit.address != "")
            .group_by(Permit.address)
            .order_by(func.max(Permit.fetched_at).desc())
            .limit(limit)
        )
    )


def city_stats(db: Session, jurisdiction: str, year: int) -> dict:
    total = db.scalar(
        select(func.count(Permit.id)).where(Permit.jurisdiction == jurisdiction)
    )
    year_count = db.scalar(
        select(func.count(Permit.id)).where(
            Permit.jurisdiction == jurisdiction,
            Permit.status_date.like(f"{year}-%"),
        )
    )
    latest = db.scalar(
        select(func.max(Permit.status_date)).where(
            Permit.jurisdiction == jurisdiction,
            Permit.status_date.like(_ISO_DATE),
        )
    )
    return {"total": total or 0, "year": year_count or 0, "latest": latest or ""}


def recent_city_permits(db: Session, jurisdiction: str, limit: int) -> list[Permit]:
    return list(
        db.scalars(
            select(Permit)
            .where(
                Permit.jurisdiction == jurisdiction,
                Permit.status_date.like(_ISO_DATE),
            )
            .order_by(Permit.status_date.desc())
            .limit(limit)
        )
    )


def active_city_addresses(db: Session, jurisdiction: str, limit: int) -> list[tuple[str, int, str]]:
    """(address, permit count, latest ISO status date), most recently active first."""
    return list(
        db.execute(
            select(Permit.address, func.count(Permit.id), func.max(Permit.status_date))
            .where(
                Permit.jurisdiction == jurisdiction,
                Permit.address != "",
                Permit.status_date.like(_ISO_DATE),
            )
            .group_by(Permit.address)
            .order_by(func.max(Permit.status_date).desc())
            .limit(limit)
        )
    )


def _address_match_query(base, house: str | None, street_tokens: list[str]):
    if house:
        base = base.where(func.upper(Permit.address).like(f"{house} %"))
    for token in street_tokens:
        base = base.where(func.upper(Permit.address).like(f"%{token.upper()}%"))
    return base


def address_suggestions_from_store(
    db: Session, slugs: list[str], house: str | None,
    street_tokens: list[str], limit: int,
) -> list[tuple[str, str]]:
    """Distinct (jurisdiction, address) pairs already in the permit store that
    match the typed pieces, most recently active first — without the ORDER BY
    the DB returns an arbitrary (and unstable) subset."""
    if not house and not street_tokens:
        return []
    query = (
        select(Permit.jurisdiction, Permit.address)
        .where(Permit.jurisdiction.in_(slugs), Permit.address != "")
        .group_by(Permit.jurisdiction, Permit.address)
        .order_by(func.max(Permit.status_date).desc())
        .limit(limit)
    )
    return list(db.execute(_address_match_query(query, house, street_tokens)))


def permits_matching_address(
    db: Session, jurisdiction: str, house: str | None,
    street_tokens: list[str], limit: int,
) -> list[Permit]:
    query = (
        select(Permit)
        .where(Permit.jurisdiction == jurisdiction, Permit.address != "")
        .order_by(Permit.status_date.desc())
        .limit(limit)
    )
    return list(db.scalars(_address_match_query(query, house, street_tokens)))


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
