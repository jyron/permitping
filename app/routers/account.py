from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app import config
from app.db import repo
from app.db.database import get_db
from app.db.models import Account
from app.services import accounts, monitors
from app.services.adapters.base import AdapterUnavailable, PermitNotFound

router = APIRouter(prefix="/api")


def get_account(t: str = Query(...), db: Session = Depends(get_db)) -> Account:
    account = accounts.resolve_token(db, t)
    if not account:
        raise HTTPException(401, "Invalid or expired account link. Request a new one.")
    return account


class EmailBody(BaseModel):
    email: EmailStr


class StartMonitorBody(BaseModel):
    jurisdiction: str
    permit_number: str
    email: EmailStr


class AddMonitorBody(BaseModel):
    jurisdiction: str
    permit_number: str


def _monitor_json(m):
    return {
        "id": m.id,
        "jurisdiction": m.jurisdiction,
        "permit_number": m.permit_number,
        "address": m.address,
        "description": m.description,
        "portal_url": m.portal_url,
        "current_status": m.current_status,
        "paused": m.paused,
        "last_checked_at": m.last_checked_at.isoformat() + "Z" if m.last_checked_at else None,
    }


@router.post("/monitors", status_code=201)
def start_monitoring(body: StartMonitorBody, db: Session = Depends(get_db)):
    try:
        monitor = monitors.start_monitoring(
            db, body.email, body.jurisdiction, body.permit_number
        )
    except PermitNotFound:
        raise HTTPException(404, "No permit found with that number in this jurisdiction")
    except AdapterUnavailable as exc:
        raise HTTPException(400, str(exc))
    except monitors.AlreadyMonitored as exc:
        raise HTTPException(409, str(exc))
    except monitors.PlanLimitReached as exc:
        raise HTTPException(402, str(exc))
    return _monitor_json(monitor)


@router.post("/account/link", status_code=202)
def request_login_link(body: EmailBody, db: Session = Depends(get_db)):
    accounts.send_login_link(db, body.email)
    return {"message": "If that email has an account, a link is on its way."}


@router.get("/account")
def account_overview(account: Account = Depends(get_account), db: Session = Depends(get_db)):
    limit = config.PLAN_LIMITS.get(account.plan)
    return {
        "email": account.email,
        "plan": account.plan,
        "monitor_limit": limit,
        "active_monitors": repo.count_active_monitors(db, account.id),
        "monitors": [_monitor_json(m) for m in repo.list_monitors(db, account.id)],
    }


@router.post("/account/email")
def update_email(body: EmailBody, account: Account = Depends(get_account),
                 db: Session = Depends(get_db)):
    accounts.change_email(db, account, body.email)
    return {"email": account.email}


@router.post("/account/monitors", status_code=201)
def add_monitor(body: AddMonitorBody, account: Account = Depends(get_account),
                db: Session = Depends(get_db)):
    try:
        monitor = monitors.add_monitor_for_account(
            db, account, body.jurisdiction, body.permit_number
        )
    except PermitNotFound:
        raise HTTPException(404, "No permit found with that number in this jurisdiction")
    except AdapterUnavailable as exc:
        raise HTTPException(400, str(exc))
    except monitors.AlreadyMonitored as exc:
        raise HTTPException(409, str(exc))
    except monitors.PlanLimitReached as exc:
        raise HTTPException(402, str(exc))
    return _monitor_json(monitor)


@router.post("/monitors/{monitor_id}/pause")
def pause_monitor(monitor_id: int, account: Account = Depends(get_account),
                  db: Session = Depends(get_db)):
    try:
        return _monitor_json(monitors.set_paused(db, account, monitor_id, True))
    except LookupError:
        raise HTTPException(404, "Monitor not found")


@router.post("/monitors/{monitor_id}/resume")
def resume_monitor(monitor_id: int, account: Account = Depends(get_account),
                   db: Session = Depends(get_db)):
    try:
        return _monitor_json(monitors.set_paused(db, account, monitor_id, False))
    except LookupError:
        raise HTTPException(404, "Monitor not found")
    except monitors.PlanLimitReached as exc:
        raise HTTPException(402, str(exc))


@router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int, account: Account = Depends(get_account),
                   db: Session = Depends(get_db)):
    try:
        monitors.remove(db, account, monitor_id)
    except LookupError:
        raise HTTPException(404, "Monitor not found")


@router.get("/monitors/{monitor_id}/events")
def monitor_events(monitor_id: int, account: Account = Depends(get_account),
                   db: Session = Depends(get_db)):
    if not repo.get_monitor(db, account.id, monitor_id):
        raise HTTPException(404, "Monitor not found")
    return [
        {
            "previous_status": e.previous_status,
            "new_status": e.new_status,
            "changed_at": e.changed_at.isoformat() + "Z",
        }
        for e in repo.list_events(db, monitor_id)
    ]
