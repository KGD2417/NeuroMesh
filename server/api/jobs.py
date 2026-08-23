"""Consumer side: submit a job, watch it, collect the result."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from api.deps import NOT_FOUND, RateLimitedUser, Session, parse_uuid_or_404
from common import pricing, registry
from common.schemas import (
    JobStatus, JobSubmit, JobView, ShardState, ShardView,
)
from common.tiers import Tier
from scheduler import aggregator, queue, splitter
from store.models import Job, Shard

router = APIRouter(prefix="/jobs", tags=["jobs"])

_SSE_HEARTBEAT_S = 15


async def _owned_job(session: Session, job_id: str, user_id: uuid.UUID) -> Job:
    """Someone else's job is a 404, never a 403."""
    job = await session.get(Job, parse_uuid_or_404(job_id))
    if job is None or job.owner_id != user_id:
        raise NOT_FOUND
    return job


async def _view(session, job: Job, *, with_shards: bool = True) -> JobView:
    """SQL row overlaid with Redis hot state -- Redis wins while a job runs."""
    prog = await queue.progress(str(job.id))
    shards: list[ShardView] = []

    if with_shards:
        rows = (
            await session.execute(
                select(Shard).where(Shard.job_id == job.id).order_by(Shard.index)
            )
        ).scalars().all()
        hot = await queue.shard_states([str(s.id) for s in rows])
        for s in rows:
            h = hot.get(str(s.id), {})
            shards.append(
                ShardView(
                    index=s.index,
                    state=ShardState(h.get("state", s.state)),
                    device_id=h.get("device_id") or (str(s.device_id) if s.device_id else None),
                    attempts=int(h.get("attempts", s.attempts)),
                )
            )

    if prog.shard_count == 0:  # job already aggregated; Redis has dropped it
        done = sum(1 for s in shards if s.state == ShardState.DONE)
        failed = sum(1 for s in shards if s.state == ShardState.FAILED)
        claimed = 0
    else:
        done, failed, claimed = prog.done, prog.failed, prog.claimed

    return JobView(
        id=str(job.id),
        model_ref=job.model_ref,
        status=JobStatus(job.status),
        min_tier=Tier(job.min_tier),
        shard_count=job.shard_count,
        shards_done=done,
        shards_failed=failed,
        shards_claimed=claimed,
        cost_mc=job.cost_mc,
        created_at=job.created_at,
        completed_at=job.completed_at,
        shards=shards,
    )


@router.get("/models", tags=["jobs"])
async def list_models() -> list[dict]:
    """The allow-list. A job may only name one of these."""
    return [
        {
            "ref": m.ref,
            "description": m.description,
            "min_tier": int(m.min_tier),
            "min_tier_label": m.min_tier.label,
            "input_kind": m.input_kind,
            "output_dim": m.output_dim,
            "price_per_item_mc": pricing.PRICE_PER_ITEM_MC[m.min_tier],
        }
        for m in registry.REGISTRY.values()
    ]


@router.post("", response_model=JobView, status_code=status.HTTP_201_CREATED)
async def submit(body: JobSubmit, user: RateLimitedUser, session: Session) -> JobView:
    model = registry.get(body.model_ref)
    if model is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown model_ref; registered models are {registry.refs()}",
        )
    tier = Tier(max(int(body.min_tier), int(model.min_tier)))
    body = body.model_copy(update={"min_tier": tier})

    try:
        job = await splitter.create_job(session, user, body)
    except pricing.InsufficientCredits as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from None
    await session.flush()
    return await _view(session, job)


@router.get("/{job_id}", response_model=JobView)
async def get_job(job_id: str, user: RateLimitedUser, session: Session) -> JobView:
    return await _view(session, await _owned_job(session, job_id, user.id))


@router.get("/{job_id}/result")
async def get_result(job_id: str, user: RateLimitedUser, session: Session) -> dict:
    job = await _owned_job(session, job_id, user.id)
    if job.result is None:
        # Last shard may have landed a moment ago; try to close it out now.
        await aggregator.finalize(session, job.id)
        await session.flush()
        await session.refresh(job)
    if job.result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "job has not finished yet")
    return {"job_id": str(job.id), "status": job.status, **job.result}


@router.post("/{job_id}/cancel", response_model=JobView)
async def cancel(job_id: str, user: RateLimitedUser, session: Session) -> JobView:
    job = await _owned_job(session, job_id, user.id)
    if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
        raise HTTPException(status.HTTP_409_CONFLICT, "job already finished")

    rows = (
        await session.execute(select(Shard).where(Shard.job_id == job.id))
    ).scalars().all()
    await queue.purge_queued(str(job.id), [str(s.id) for s in rows])
    for shard in rows:
        if shard.state in (ShardState.QUEUED.value, ShardState.CLAIMED.value):
            shard.state = ShardState.CANCELLED.value
    job.status = JobStatus.CANCELLED.value

    refund = job.cost_mc - job.spent_mc
    if refund > 0:
        await pricing.post(
            session,
            user_id=job.owner_id,
            delta_mc=refund,
            kind=pricing.EntryKind.JOB_REFUND,
            ref_type="job",
            ref_id=str(job.id),
        )
    await session.flush()
    return await _view(session, job)


@router.get("/{job_id}/events")
async def events(job_id: str, request: Request, user: RateLimitedUser, session: Session):
    """Server-sent events: live shard progress for the consumer phone.

    Deliberately carries no payload data -- a progress stream is the widest
    surface in the API and the least authenticated in practice.
    """
    job = await _owned_job(session, job_id, user.id)
    prog = await queue.progress(str(job.id))

    async def stream():
        # Open with the current state so a late subscriber is never blank.
        yield _frame(
            {
                "job_id": str(job.id),
                "type": "job.queued" if job.status == JobStatus.QUEUED.value else "heartbeat",
                "shards_done": prog.done,
                "shards_failed": prog.failed,
                "shard_count": job.shard_count,
            }
        )
        events_iter = queue.subscribe(str(job.id)).__aiter__()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    raw = await asyncio.wait_for(
                        events_iter.__anext__(), timeout=_SSE_HEARTBEAT_S
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # keeps mobile NAT paths open
                    continue
                except StopAsyncIteration:
                    break
                yield f"data: {raw}\n\n"
                if json.loads(raw).get("type") in ("job.completed", "job.failed"):
                    break
        finally:
            with contextlib.suppress(Exception):
                await events_iter.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
