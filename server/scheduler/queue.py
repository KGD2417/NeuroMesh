"""The shard queue: Redis-side scheduling, leases and live progress.

Every state transition a shard can make goes through one of the Lua scripts in
lua/. None of them is a read-then-write, because the fleet is a few hundred
phones racing each other for the same list and a lost race here pays two
devices for one shard.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from redis.asyncio.client import Redis

from common.schemas import JobEvent, ShardAssignment
from common.tiers import Tier, tiers_servable_by
from config import get_settings
from store.crypto import open_, seal
from store.redis_client import redis, text

_LUA_DIR = Path(__file__).parent / "lua"
_scripts: dict[str, object] = {}

LEASES_KEY = "nm:leases"
_JOB_KEY_TTL_S = 24 * 3600


def _script(name: str, r: Redis):
    if name not in _scripts:
        _scripts[name] = r.register_script((_LUA_DIR / f"{name}.lua").read_text())
    return _scripts[name]


def _now_ms() -> int:
    return int(time.time() * 1000)


def job_key(job_id: str) -> str:
    return f"nm:job:{job_id}"


def shard_key(shard_id: str) -> str:
    return f"nm:shard:{shard_id}"


@dataclass(slots=True)
class Progress:
    shard_count: int
    done: int
    failed: int
    claimed: int

    @property
    def queued(self) -> int:
        return self.shard_count - self.done - self.failed - self.claimed

    @property
    def finished(self) -> bool:
        return self.done + self.failed >= self.shard_count and self.shard_count > 0


# --- enqueue ----------------------------------------------------------------

async def enqueue(
    job_id: str,
    model_ref: str,
    tier: Tier,
    shards: list[tuple[str, int, list]],
) -> None:
    """Load a freshly split job into Redis. `shards` is (shard_id, index, items).

    Payloads are sealed before they touch Redis and are only ever opened inside
    a successful claim.
    """
    r = redis()
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(
            job_key(job_id),
            mapping={"shard_count": len(shards), "done": 0, "failed": 0, "claimed": 0},
        )
        pipe.expire(job_key(job_id), _JOB_KEY_TTL_S)
        for shard_id, index, items in shards:
            pipe.hset(
                shard_key(shard_id),
                mapping={
                    "job_id": job_id,
                    "idx": index,
                    "tier": int(tier),
                    "model_ref": model_ref,
                    "state": "queued",
                    "attempts": 0,
                },
            )
            pipe.expire(shard_key(shard_id), _JOB_KEY_TTL_S)
            pipe.set(f"nm:payload:{shard_id}", seal(items), ex=_JOB_KEY_TTL_S)
            pipe.rpush(f"nm:q:{int(tier)}", shard_id)
        await pipe.execute()


# --- claim / renew / settle -------------------------------------------------

async def claim(device_id: str, tier: Tier) -> ShardAssignment | None:
    """Pop the strongest shard this device can run. One atomic script."""
    s = get_settings()
    r = redis()
    res = await _script("claim", r)(
        keys=[LEASES_KEY],
        args=[
            device_id,
            _now_ms(),
            s.lease_ttl_s * 1000,
            s.max_shard_attempts,
            *[str(int(t)) for t in tiers_servable_by(tier)],
        ],
    )
    if not res:
        return None

    shard_id, job_id, idx, model_ref, shard_tier, deadline, payload = res
    return ShardAssignment(
        shard_id=text(shard_id),
        job_id=text(job_id),
        index=int(idx),
        model_ref=text(model_ref),
        tier=Tier(int(shard_tier)),
        items=open_(payload) if payload else [],
        lease_deadline_ms=int(deadline),
        lease_ttl_s=s.lease_ttl_s,
    )


async def renew(shard_id: str, device_id: str) -> int | None:
    """Extend the lease. None means the shard is no longer this device's."""
    s = get_settings()
    deadline = await _script("renew", redis())(
        keys=[LEASES_KEY],
        args=[shard_id, device_id, _now_ms(), s.lease_ttl_s * 1000],
    )
    return int(deadline) or None


async def complete(shard_id: str, device_id: str, outputs: list) -> tuple[bool, Progress, str]:
    """Accept a shard result. False means the device lost the lease and the
    work has already been (or is being) redone elsewhere."""
    status, done, failed, count, job_id = await _script("complete", redis())(
        keys=[LEASES_KEY],
        args=[shard_id, device_id, seal(outputs)],
    )
    prog = await progress(text(job_id)) if job_id else Progress(count, done, failed, 0)
    return bool(status), prog, text(job_id) or ""


async def fail(shard_id: str, device_id: str, retryable: bool) -> tuple[int, Progress, str]:
    """Returns (outcome, progress, job_id); outcome 1=requeued 2=dead 0=not ours."""
    outcome, done, failed, count, job_id = await _script("fail", redis())(
        keys=[LEASES_KEY],
        args=[shard_id, device_id, 1 if retryable else 0, get_settings().max_shard_attempts],
    )
    prog = await progress(text(job_id)) if job_id else Progress(count, done, failed, 0)
    return int(outcome), prog, text(job_id) or ""


async def reap(limit: int = 200) -> list[dict]:
    """Requeue expired leases. Returns one dict per reaped shard."""
    raw = await _script("reap", redis())(
        keys=[LEASES_KEY],
        args=[_now_ms(), get_settings().max_shard_attempts, limit],
    )
    out = []
    for i in range(0, len(raw), 7):
        sid, job_id, idx, outcome, done, failed, count = raw[i : i + 7]
        out.append(
            {
                "shard_id": text(sid),
                "job_id": text(job_id),
                "index": int(idx),
                "outcome": int(outcome),
                "done": int(done),
                "failed": int(failed),
                "shard_count": int(count),
            }
        )
    return out


# --- read-side --------------------------------------------------------------

async def progress(job_id: str) -> Progress:
    h = await redis().hgetall(job_key(job_id))
    g = lambda k: int(h.get(k.encode(), 0) or 0)  # noqa: E731
    return Progress(g("shard_count"), g("done"), g("failed"), g("claimed"))


async def shard_states(shard_ids: list[str]) -> dict[str, dict]:
    """Hot per-shard state, for overlaying on the SQL rows at read time."""
    if not shard_ids:
        return {}
    r = redis()
    async with r.pipeline(transaction=False) as pipe:
        for sid in shard_ids:
            pipe.hgetall(shard_key(sid))
        rows = await pipe.execute()
    return {
        sid: {text(k): text(v) for k, v in row.items()}
        for sid, row in zip(shard_ids, rows)
        if row
    }


async def results(shard_ids: list[str]) -> list[list | None]:
    """Decrypted outputs in the order given. None for a shard with no result."""
    if not shard_ids:
        return []
    r = redis()
    async with r.pipeline(transaction=False) as pipe:
        for sid in shard_ids:
            pipe.get(f"nm:result:{sid}")
        blobs = await pipe.execute()
    return [open_(b) if b else None for b in blobs]


async def drop_job(job_id: str, shard_ids: list[str]) -> None:
    """Once a job is aggregated into Postgres, Redis has nothing left to own."""
    r = redis()
    async with r.pipeline(transaction=False) as pipe:
        pipe.delete(job_key(job_id))
        for sid in shard_ids:
            pipe.delete(shard_key(sid), f"nm:payload:{sid}", f"nm:result:{sid}")
            pipe.zrem(LEASES_KEY, sid)
        await pipe.execute()


async def purge_queued(job_id: str, shard_ids: list[str]) -> None:
    """Cancel: mark queued shards dead so a claim skips them. We do not try to
    LREM them out of the tier lists -- claim.lua drops stale entries anyway."""
    r = redis()
    async with r.pipeline(transaction=False) as pipe:
        for sid in shard_ids:
            pipe.hset(shard_key(sid), "state", "cancelled")
            pipe.delete(f"nm:payload:{sid}")
        await pipe.execute()


# --- events -----------------------------------------------------------------

def events_channel(job_id: str) -> str:
    return f"nm:events:{job_id}"


async def publish(
    job_id: str,
    type_: str,
    prog: Progress | None = None,
    *,
    index: int | None = None,
    device_id: str | None = None,
) -> None:
    """Live progress for the consumer phone. Never carries payload data."""
    event = JobEvent(
        job_id=job_id,
        type=type_,
        index=index,
        device_id=device_id,
        shards_done=prog.done if prog else 0,
        shards_failed=prog.failed if prog else 0,
        shard_count=prog.shard_count if prog else 0,
        ts=datetime.now(timezone.utc),
    )
    await redis().publish(events_channel(job_id), event.model_dump_json())


async def subscribe(job_id: str):
    """Async iterator of raw JSON event strings for one job."""
    pubsub = redis().pubsub()
    await pubsub.subscribe(events_channel(job_id))
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                yield text(message["data"])
    finally:
        await pubsub.unsubscribe(events_channel(job_id))
        await pubsub.aclose()


def parse_event(raw: str) -> dict:
    return json.loads(raw)
