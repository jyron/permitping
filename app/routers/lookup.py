from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.registry import JURISDICTIONS
from app.services import locations, lookup
from app.services.adapters.base import AdapterUnavailable, PermitNotFound
from app.services.locations import UnsupportedLocation

router = APIRouter(prefix="/api")


class LookupRequest(BaseModel):
    location: str
    permit_number: str


@router.get("/jurisdictions")
def jurisdictions():
    return [
        {k: j[k] for k in ("slug", "name", "city", "state", "portal_name",
                           "portal_url", "permit_example")}
        for j in JURISDICTIONS
    ]


@router.post("/lookup")
def lookup_permit(body: LookupRequest, db: Session = Depends(get_db)):
    if not body.permit_number.strip():
        raise HTTPException(422, "Permit number is required")
    try:
        jurisdiction = locations.resolve(body.location)
    except UnsupportedLocation as exc:
        raise HTTPException(404, str(exc))
    try:
        record = lookup.lookup_permit(db, jurisdiction["slug"], body.permit_number)
    except PermitNotFound:
        raise HTTPException(
            404, f"No permit found with that number in {jurisdiction['name']}"
        )
    except AdapterUnavailable as exc:
        raise HTTPException(502, str(exc))
    return asdict(record)
