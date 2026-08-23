"""The scheduler: atomic claim, leases, the reaper.

Two devices claiming one shard is a double-credit bug, and a phone that walks
away must not strand a shard. Those are the two things this file exists for.
"""

from __future__ import annotations

import asyncio
import uuid

from common.tiers import Tier
from scheduler import queue
from store.redis_client import redis


def _shards(n: int, items_each: int = 2) -> list[tuple[str, int, list]]:
    return [
        (str(uuid.uuid4()), i, [f"item-{i}-{k}" for k in range(items_each)])
        for i in range(n)
    ]


async def test_one_shard_can_only_be_claimed_once():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "textembed-mlp-int8", Tier.CPU_INT8, _shards(1))

    winners = await asyncio.gather(
        *[queue.claim(f"device-{i}", Tier.NPU_FP16) for i in range(16)]
    )
    claimed = [w for w in winners if w is not None]
    assert len(claimed) == 1, "the same shard was handed to more than one device"


async def test_claim_walks_tiers_strongest_first():
    weak_job, strong_job = str(uuid.uuid4()), str(uuid.uuid4())
    await queue.enqueue(weak_job, "m", Tier.CPU_INT8, _shards(1))
    await queue.enqueue(strong_job, "m", Tier.NPU_FP16, _shards(1))

    first = await queue.claim("npu-phone", Tier.NPU_FP16)
    assert first.job_id == strong_job, "capable phone should drain NPU work first"

    second = await queue.claim("npu-phone", Tier.NPU_FP16)
    assert second.job_id == weak_job


async def test_a_weak_phone_never_sees_work_above_its_tier():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "m", Tier.NPU_FP16, _shards(3))
    assert await queue.claim("weak-phone", Tier.CPU_INT8) is None
    assert await queue.claim("gpu-phone", Tier.GPU_FP16) is None
    assert await queue.claim("npu-phone", Tier.NPU_FP16) is not None


async def test_payload_is_encrypted_at_rest_and_opened_only_on_claim():
    job_id = str(uuid.uuid4())
    shards = _shards(1)
    await queue.enqueue(job_id, "m", Tier.CPU_INT8, shards)

    raw = await redis().get(f"nm:payload:{shards[0][0]}")
    assert b"item-0-0" not in raw, "shard inputs sat in Redis in the clear"

    assignment = await queue.claim("device-a", Tier.CPU_INT8)
    assert assignment.items == shards[0][2]


async def test_expired_lease_is_requeued_by_the_reaper():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "m", Tier.CPU_INT8, _shards(1))

    first = await queue.claim("phone-that-walks-away", Tier.CPU_INT8)
    assert await queue.claim("phone-b", Tier.CPU_INT8) is None  # held

    await _expire_lease(first.shard_id)
    reaped = await queue.reap()
    assert [r["outcome"] for r in reaped] == [1]  # requeued

    second = await queue.claim("phone-b", Tier.CPU_INT8)
    assert second.shard_id == first.shard_id
    assert second.items == first.items  # the work survived the phone


async def test_a_phone_that_lost_its_lease_is_not_paid():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "m", Tier.CPU_INT8, _shards(1))

    lost = await queue.claim("phone-a", Tier.CPU_INT8)
    await _expire_lease(lost.shard_id)
    await queue.reap()
    winner = await queue.claim("phone-b", Tier.CPU_INT8)

    accepted, _, _ = await queue.complete(lost.shard_id, "phone-a", ["late"])
    assert not accepted, "a device that lost its lease was still credited"

    accepted, prog, _ = await queue.complete(winner.shard_id, "phone-b", ["on time"])
    assert accepted and prog.done == 1


async def test_renew_only_works_for_the_holder():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "m", Tier.CPU_INT8, _shards(1))
    a = await queue.claim("phone-a", Tier.CPU_INT8)

    assert await queue.renew(a.shard_id, "phone-b") is None
    extended = await queue.renew(a.shard_id, "phone-a")
    assert extended > a.lease_deadline_ms - 1

    # A renewed lease survives a reaper sweep that would have taken it.
    assert await queue.reap() == []


async def test_shard_dies_after_max_attempts():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "m", Tier.CPU_INT8, _shards(1))

    outcomes = []
    for _ in range(5):
        c = await queue.claim("flaky-phone", Tier.CPU_INT8)
        if c is None:
            break
        outcome, prog, _ = await queue.fail(c.shard_id, "flaky-phone", retryable=True)
        outcomes.append(outcome)

    assert 2 in outcomes, "a shard that keeps failing must eventually be burned"
    prog = await queue.progress(job_id)
    assert prog.failed == 1 and prog.finished


async def test_progress_counts_track_the_fleet():
    job_id = str(uuid.uuid4())
    await queue.enqueue(job_id, "m", Tier.CPU_INT8, _shards(3))

    assert (await queue.progress(job_id)).queued == 3
    a = await queue.claim("p1", Tier.CPU_INT8)
    assert (await queue.progress(job_id)).claimed == 1

    await queue.complete(a.shard_id, "p1", ["out"])
    prog = await queue.progress(job_id)
    assert (prog.done, prog.claimed, prog.queued) == (1, 0, 2)


async def _expire_lease(shard_id: str) -> None:
    """Yank the phone: rewind the lease deadline into the past."""
    await redis().zadd(queue.LEASES_KEY, {shard_id: 0})
