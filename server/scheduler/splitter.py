"""Job -> shards.

Shards are fully independent by construction: each one is a contiguous slice of
the input manifest and nothing else. No shard ever needs to talk to another,
which is the only reason a fleet of phones on home Wi-Fi can run this at all.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from common import pricing
from common.schemas import JobStatus, JobSubmit, ShardState
from common.tiers import Tier
from scheduler import queue
from store.models import Job, Shard, User


def slice_indices(item_count: int, shard_size: int) -> list[tuple[int, int]]:
    """(start, end) per shard. The tail shard is short, never padded."""
    return [(s, min(s + shard_size, item_count)) for s in range(0, item_count, shard_size)]


async def create_job(session: AsyncSession, owner: User, submit: JobSubmit) -> Job:
    """Split, price, escrow and enqueue -- in that order.

    The consumer is debited the full cost up front. A shard that never runs is
    refunded at aggregation; a provider is paid the instant its shard lands.
    Nobody can spend credits they do not have, because post() refuses.
    """
    tier = Tier(submit.min_tier)
    bounds = slice_indices(len(submit.inputs), submit.shard_size)
    counts = [end - start for start, end in bounds]
    cost = pricing.job_cost_mc(tier, counts)

    job = Job(
        id=uuid.uuid4(),
        owner_id=owner.id,
        model_ref=submit.model_ref,
        min_tier=int(tier),
        shard_count=len(bounds),
        shard_size=submit.shard_size,
        item_count=len(submit.inputs),
        status=JobStatus.QUEUED.value,
        cost_mc=cost,
    )
    session.add(job)

    shards = []
    for index, (start, end) in enumerate(bounds):
        shard = Shard(
            id=uuid.uuid4(),
            job_id=job.id,
            index=index,
            state=ShardState.QUEUED.value,
            item_count=end - start,
            price_mc=pricing.shard_price_mc(tier, end - start),
        )
        session.add(shard)
        shards.append((str(shard.id), index, submit.inputs[start:end]))

    # Raises InsufficientCredits before anything reaches Redis.
    await pricing.post(
        session,
        user_id=owner.id,
        delta_mc=-cost,
        kind=pricing.EntryKind.JOB_ESCROW,
        ref_type="job",
        ref_id=str(job.id),
    )
    await session.flush()

    await queue.enqueue(str(job.id), job.model_ref, tier, shards)
    await queue.publish(str(job.id), "job.queued", await queue.progress(str(job.id)))
    return job
