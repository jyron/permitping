from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import addresses

router = APIRouter(prefix="/api/addresses")


class PermitsAtRequest(BaseModel):
    slug: str
    filters: dict[str, str]


@router.get("/suggest")
async def suggest(q: str = Query("", max_length=120)):
    return await addresses.suggest(q)


@router.post("/permits")
async def permits(body: PermitsAtRequest):
    result = await addresses.permits_at(body.slug, body.filters)
    if result is None:
        raise HTTPException(404, "Address search is not available for that city")
    return result
