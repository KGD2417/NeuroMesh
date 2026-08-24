"""NeuroMesh orchestrator.

    uvicorn main:app --host 0.0.0.0 --port 8000

Binds 0.0.0.0 on purpose: the whole demo runs on phones on the same Wi-Fi, and
there is no laptop browser in the loop.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text as sql_text

from api import auth, devices, jobs, me
from common import registry
from common.thermal import THERMAL_CEILING
from config import get_settings
from scheduler import reaper
from store.db import engine
from store.redis_client import redis

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with reaper.running():
        yield
    await engine().dispose()
    await redis().aclose()


app = FastAPI(
    title="NeuroMesh",
    version="0.1.0",
    summary="A marketplace for idle phone NPU compute",
    lifespan=lifespan,
)

# The phones talk to this directly; a browser dashboard is not part of the demo,
# but leaving CORS off entirely makes local debugging needlessly painful.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(devices.router)
app.include_router(me.router)


@app.get("/console", include_in_schema=False)
async def console() -> FileResponse:
    """A browser consumer console, served by the API that it talks to, so there
    is no second process to remember and no CORS origin to get wrong."""
    return FileResponse(pathlib.Path(__file__).parent / "console.html")


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Both stores, checked for real. A green health check that never touched
    the database is how a demo dies on stage."""
    db_ok = redis_ok = True
    try:
        async with engine().connect() as conn:
            await conn.execute(sql_text("SELECT 1"))
    except Exception:
        db_ok = False
    try:
        await redis().ping()
    except Exception:
        redis_ok = False

    s = get_settings()
    return {
        "ok": db_ok and redis_ok,
        "postgres": db_ok,
        "redis": redis_ok,
        "env": s.env,
        "lease_ttl_s": s.lease_ttl_s,
        "thermal_ceiling": THERMAL_CEILING,
        "models": registry.refs(),
    }
