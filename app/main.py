import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.db.database import init_db
from app.routers import account, admin, lookup, pages
from app.services.checker import scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(lookup.router)
app.include_router(account.router)
app.include_router(admin.router)
app.include_router(pages.router)  # last: has the /{slug} catch-all
