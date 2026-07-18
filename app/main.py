import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.db.database import init_db
from app.routers import account, addresses, admin, lookup, pages
from app.services.checker import scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)

_CANONICAL_HOST = urlsplit(config.BASE_URL).hostname


@app.middleware("http")
async def canonicalize_host(request: Request, call_next):
    """301 stray hosts (the *.up.railway.app URL) to the canonical domain so
    search engines index exactly one host. Cloudflare handles hndshake.com
    and www at the edge; unknown hosts (localhost, healthchecks) pass through."""
    host = (request.headers.get("host") or "").split(":")[0]
    if (
        request.method in ("GET", "HEAD")
        and host != _CANONICAL_HOST
        and (host.endswith(".up.railway.app") or host.endswith("hndshake.com"))
    ):
        url = request.url.replace(scheme="https", netloc=_CANONICAL_HOST)
        return RedirectResponse(str(url), status_code=301)
    return await call_next(request)


app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(lookup.router)
app.include_router(addresses.router)
app.include_router(account.router)
app.include_router(admin.router)
app.include_router(pages.router)  # last: has the /{slug} catch-all
