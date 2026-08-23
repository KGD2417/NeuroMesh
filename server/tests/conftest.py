"""Tests run against a real Postgres and a real Redis.

Not sqlite, not fakeredis: the two things most worth testing here are a Lua
script and a row lock, and neither of them exists in a fake. `docker compose up
-d postgres redis` is the only setup.
"""

from __future__ import annotations

import os

# Must happen before anything imports config: get_settings() is cached for the
# life of the process, and these tests must never touch the demo database.
os.environ.setdefault(
    "NEUROMESH_DATABASE_DSN",
    "postgresql+asyncpg://neuromesh:neuromesh@localhost:5432/neuromesh_test",
)
os.environ.setdefault("NEUROMESH_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NEUROMESH_LEASE_TTL_S", "2")
os.environ.setdefault("NEUROMESH_MAX_SHARD_ATTEMPTS", "3")
os.environ.setdefault("NEUROMESH_RL_ACCOUNT_N", "100000")
os.environ.setdefault("NEUROMESH_RL_ANON_N", "100000")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from config import get_settings  # noqa: E402
from store.db import engine, session_factory  # noqa: E402
from store.models import Base  # noqa: E402
from store.redis_client import redis  # noqa: E402


async def _ensure_test_database() -> None:
    """Create neuromesh_test if it is not there yet."""
    import asyncpg

    dsn = get_settings().database_dsn
    name = dsn.rsplit("/", 1)[1]
    admin = dsn.replace(f"/{name}", "/postgres").replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(admin)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    await _ensure_test_database()
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine().dispose()
    await redis().aclose()


@pytest.fixture(autouse=True)
async def _clean(_schema):
    """Every test starts from an empty fleet and an empty queue."""
    async with engine().begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE device_logs, ledger_entries, shards, jobs, devices, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    await redis().flushdb()
    yield


@pytest.fixture
async def session():
    async with session_factory()() as s:
        yield s
        await s.commit()


@pytest.fixture
async def client():
    from main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def account(client):
    """A registered consumer, with the signup grant already in the ledger."""
    r = await client.post(
        "/auth/register", json={"email": "dev@example.com", "password": "hunter2hunter2"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {
        "user_id": body["user_id"],
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
        "refresh_token": body["refresh_token"],
    }


async def pair_device(client, account, name: str, capability: dict) -> dict:
    """Owner mints a pairing code; the phone redeems it for a device key."""
    code = (
        await client.post("/devices/pairing-code", headers=account["headers"])
    ).json()["code"]
    r = await client.post(
        "/devices/register",
        json={"pairing_code": code, "name": name, "capability": capability},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {
        "id": body["device_id"],
        "tier": body["tier"],
        "headers": {"X-Device-Key": body["device_key"]},
    }


NPU_PHONE = {
    "available_ram_mb": 4096,
    "qnn_delegate": True,
    "gpu_delegate": True,
    "quantizations": ["int8", "fp16"],
    "soc": "Snapdragon 8 Elite",
    "npu_tops": 45.0,
}
GPU_PHONE = {
    "available_ram_mb": 2048,
    "qnn_delegate": False,
    "gpu_delegate": True,
    "quantizations": ["fp16"],
    "soc": "Snapdragon 7s",
}
WEAK_PHONE = {
    "available_ram_mb": 700,
    "qnn_delegate": False,
    "gpu_delegate": False,
    "quantizations": ["int8"],
}

ONLINE = {"charging": True, "wifi": True, "screen_off": True, "thermal_status": 0, "battery_pct": 90}


async def go_online(client, device, capability=None) -> dict:
    r = await client.post(
        "/devices/heartbeat",
        headers=device["headers"],
        json={"capability": capability or NPU_PHONE, **ONLINE},
    )
    assert r.status_code == 200, r.text
    return r.json()
