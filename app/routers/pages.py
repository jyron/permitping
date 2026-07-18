from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.registry import get_jurisdiction

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent.parent / "static"


@router.get("/", include_in_schema=False)
def landing():
    return FileResponse(STATIC / "index.html")


@router.get("/account", include_in_schema=False)
def account_page():
    return FileResponse(STATIC / "account.html")


@router.get("/{slug}", include_in_schema=False)
def city_page(slug: str):
    jurisdiction = get_jurisdiction(slug)
    if not jurisdiction:
        raise HTTPException(404, "Page not found")
    # ponytail: stdlib str.replace, not a template engine — enough for SEO
    # titles/headings on programmatic city pages.
    html = (STATIC / "city.html").read_text()
    for key, value in {
        "__CITY_NAME__": jurisdiction["name"],
        "__CITY__": jurisdiction["city"],
        "__SLUG__": jurisdiction["slug"],
        "__PORTAL_NAME__": jurisdiction["portal_name"],
        "__PORTAL_URL__": jurisdiction["portal_url"],
        "__PERMIT_EXAMPLE__": jurisdiction["permit_example"],
    }.items():
        html = html.replace(key, value)
    return HTMLResponse(html)
