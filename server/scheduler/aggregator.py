"""Shards -> result, and the settlement that goes with it.

A job completes when every shard is done. The aggregator then assembles the
outputs in shard-index order -- the order matters, phones finish out of order --
persists them to Postgres, refunds whatever escrow was never spent, and lets
Redis drop everything it was holding.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common import pricing
from common.schemas import JobStatus, ShardState
from scheduler import queue
from store.models import Device, Job, Shard


async def settle_shard(
    session: AsyncSession,
    shard: Shard,
    device: Device,
    *,
    duration_ms: int,
    delegate: str,
) -> int:
    """Pay the provider for one completed shard. Returns the payout in mC."""
    payout = pricing.provider_share_mc(shard.price_mc)

    shard.state = ShardState.DONE.value
    shard.device_id = device.id
    shard.duration_ms = duration_ms
    shard.delegate = delegate
    shard.completed_at = datetime.now(timezone.utc)
    device.shards_completed += 1

    job = await session.get(Job, shard.job_id)
    job.spent_mc += shard.price_mc
    if job.status == JobStatus.QUEUED.value:
        job.status = JobStatus.RUNNING.value

    await pricing.post(
        session,
        user_id=device.owner_id,
        delta_mc=payout,
        kind=pricing.EntryKind.SHARD_PAYOUT,
        ref_type="shard",
        ref_id=str(shard.id),
    )
    return payout


async def mark_shard_failed(session: AsyncSession, shard: Shard) -> None:
    shard.state = ShardState.FAILED.value
    shard.device_id = None


async def mark_shard_requeued(session: AsyncSession, shard: Shard) -> None:
    shard.state = ShardState.QUEUED.value
    shard.device_id = None
    shard.attempts += 1


async def finalize(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Assemble and close a job, if and only if every shard has settled.

    Idempotent: called from whichever request happens to land the last shard,
    and from the reaper when the last shard dies instead.
    """
    job = await session.get(Job, job_id, with_for_update=True)
    if job is None or job.status in (
        JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value
    ):
        return job

    shards = (
        await session.execute(
            select(Shard).where(Shard.job_id == job_id).order_by(Shard.index)
        )
    ).scalars().all()

    if any(s.state in (ShardState.QUEUED.value, ShardState.CLAIMED.value) for s in shards):
        return job  # still running

    done = [s for s in shards if s.state == ShardState.DONE.value]
    payloads = await queue.results([str(s.id) for s in done])

    outputs: list = [None] * job.item_count
    for shard, out in zip(done, payloads):
        if out is None:
            continue
        start = shard.index * job.shard_size
        outputs[start : start + len(out)] = out

    failed = [s for s in shards if s.state != ShardState.DONE.value]
    job.result = {
        "model_ref": job.model_ref,
        "item_count": job.item_count,
        "outputs": outputs,
        "failed_shards": [s.index for s in failed],
    }
    job.status = JobStatus.COMPLETED.value if not failed else JobStatus.FAILED.value
    job.completed_at = datetime.now(timezone.utc)

    # Escrow covered every shard; refund the ones that never ran.
    unspent = job.cost_mc - job.spent_mc
    if unspent > 0:
        await pricing.post(
            session,
            user_id=job.owner_id,
            delta_mc=unspent,
            kind=pricing.EntryKind.JOB_REFUND,
            ref_type="job",
            ref_id=str(job.id),
        )

    prog = await queue.progress(str(job_id))
    await queue.publish(
        str(job_id),
        "job.completed" if not failed else "job.failed",
        prog,
    )
    await queue.drop_job(str(job_id), [str(s.id) for s in shards])
    return job
