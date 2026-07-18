from fastapi import APIRouter, Header, HTTPException

from app import config
from app.services import checker

router = APIRouter(prefix="/api/admin")


def _require_admin(token: str) -> None:
    if not config.ADMIN_TOKEN or token != config.ADMIN_TOKEN:
        raise HTTPException(403, "Bad admin token")


@router.post("/run-sync")
def run_sync(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
    return checker.run_sync()


@router.post("/run-checks")
def run_checks(x_admin_token: str = Header(...)):
    _require_admin(x_admin_token)
    return checker.run_all_checks()
