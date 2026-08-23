"""Lease reaper.

Phones drop off constantly -- the owner picks the phone up, Wi-Fi flaps, the
thermal governor pulls the plug. Leases, not trust: this loop is the only thing
between a dropped phone and a shard stuck forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from sqlalchemy import select

from common.schemas import ShardState
from config import get_settings
from scheduler import aggregator, queue
from store.db import session_factory
from store.models import Shard

log = logging.getLogger("neuromesh.reaper")


async def sweep() -> list[dict]:
    """One pass. Returns what was reaped, so tests can drive it directly."""
    reaped = await queue.reap()
    if not reaped:
        return []

    async with session_factory()() as session:
        rows = (
            await session.execute(
                select(Shard).where(
                    Shard.id.in_([uuid.UUID(r["shard_id"]) for r in reaped])
                )
            )
        ).scalars().all()
        by_id = {str(s.id): s for s in rows}

        finished_jobs: set[uuid.UUID] = set()
        for r in reaped:
            shard = by_id.get(r["shard_id"])
            if shard is None:
                continue
            if r["outcome"] == 1:
                await aggregator.mark_shard_requeued(session, shard)
            else:
                await aggregator.mark_shard_failed(session, shard)
                shard.attempts += 1
            if r["done"] + r["failed"] >= r["shard_count"]:
                finished_jobs.add(shard.job_id)

        for job_id in finished_jobs:
            await aggregator.finalize(session, job_id)
        await session.commit()

    for r in reaped:
        await queue.publish(
            r["job_id"],
            "shard.requeued" if r["outcome"] == 1 else "shard.failed",
            queue.Progress(r["shard_count"], r["done"], r["failed"], 0),
            index=r["index"],
        )
        log.info(
            "reaped shard %s of job %s (%s)",
            r["index"], r["job_id"], "requeued" if r["outcome"] == 1 else "failed",
        )
    return reaped


async def run_forever() -> None:
    interval = get_settings().reaper_interval_s
    while True:
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception:  # a reaper that dies silently is worse than a slow one
            log.exception("reaper sweep failed")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def running():
    task = asyncio.create_task(run_forever(), name="neuromesh-reaper")
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
