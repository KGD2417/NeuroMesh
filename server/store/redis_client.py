"""Redis owns transient state: the queue, the atomic claim, leases, live
progress and encrypted payloads. Nothing here outlives a job."""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as aioredis

from config import get_settings


@lru_cache(maxsize=1)
def redis() -> aioredis.Redis:
    # decode_responses stays off: shard payloads are ciphertext, not text.
    return aioredis.from_url(get_settings().redis_url, decode_responses=False)


def text(value: bytes | str | None) -> str | None:
    return value.decode() if isinstance(value, bytes) else value
