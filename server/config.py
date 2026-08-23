"""The one place environment variables are read.

Nothing else in the codebase touches os.environ. Everything -- API, workers,
alembic migrations -- reads configuration through get_settings(), so the API
and the migrations can never disagree about which database they are pointed at.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    env: str

    # storage
    database_dsn: str
    redis_url: str

    # auth
    jwt_secret: str
    jwt_algorithm: str
    access_token_ttl_s: int
    refresh_token_ttl_s: int
    pairing_code_ttl_s: int

    # payload encryption at rest (Fernet key, urlsafe base64 of 32 bytes)
    payload_key: str

    # scheduling
    lease_ttl_s: int
    max_shard_attempts: int
    reaper_interval_s: int
    heartbeat_stale_s: int

    # rate limiting: (requests, window seconds)
    rate_limit_account: tuple[int, int]
    rate_limit_anonymous: tuple[int, int]

    # money
    provider_share_bps: int


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.environ.get("NEUROMESH_ENV", "dev")
    dev = env != "prod"

    secret = os.environ.get("NEUROMESH_JWT_SECRET")
    if not secret:
        if not dev:
            raise RuntimeError("NEUROMESH_JWT_SECRET is required outside dev")
        secret = "dev-insecure-jwt-secret"

    key = os.environ.get("NEUROMESH_PAYLOAD_KEY")
    if not key:
        if not dev:
            raise RuntimeError("NEUROMESH_PAYLOAD_KEY is required outside dev")
        # Deterministic in dev so a restart can still decrypt queued payloads.
        key = base64.urlsafe_b64encode(b"neuromesh-dev-payload-key-32byte").decode()

    return Settings(
        env=env,
        database_dsn=os.environ.get(
            "NEUROMESH_DATABASE_DSN",
            "postgresql+asyncpg://neuromesh:neuromesh@localhost:5432/neuromesh",
        ),
        redis_url=os.environ.get("NEUROMESH_REDIS_URL", "redis://localhost:6379/0"),
        jwt_secret=secret,
        jwt_algorithm="HS256",
        access_token_ttl_s=_int("NEUROMESH_ACCESS_TTL_S", 3600),
        refresh_token_ttl_s=_int("NEUROMESH_REFRESH_TTL_S", 30 * 86400),
        pairing_code_ttl_s=_int("NEUROMESH_PAIRING_TTL_S", 300),
        payload_key=key,
        # Mobile is far less reliable than a datacenter: keep the lease short so
        # a phone that drops off costs us seconds, not minutes.
        lease_ttl_s=_int("NEUROMESH_LEASE_TTL_S", 30),
        max_shard_attempts=_int("NEUROMESH_MAX_SHARD_ATTEMPTS", 3),
        reaper_interval_s=_int("NEUROMESH_REAPER_INTERVAL_S", 5),
        heartbeat_stale_s=_int("NEUROMESH_HEARTBEAT_STALE_S", 120),
        rate_limit_account=(_int("NEUROMESH_RL_ACCOUNT_N", 300), 60),
        rate_limit_anonymous=(_int("NEUROMESH_RL_ANON_N", 20), 60),
        provider_share_bps=_int("NEUROMESH_PROVIDER_SHARE_BPS", 8000),
    )
