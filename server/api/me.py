"""Account, balance, devices, earnings -- everything the app's home screen needs."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from api.deps import RateLimitedUser, Session
from api.devices import _device_view
from common import pricing
from common.schemas import DeviceView, JobStatus
from store.models import Device, Job, LedgerEntry, Shard

router = APIRouter(prefix="/me", tags=["me"])


@router.get("")
async def account(user: RateLimitedUser, session: Session) -> dict:
    earned = await _sum_kind(session, user.id, pricing.EntryKind.SHARD_PAYOUT)
    spent = await _sum_kind(session, user.id, pricing.EntryKind.JOB_ESCROW)
    refunded = await _sum_kind(session, user.id, pricing.EntryKind.JOB_REFUND)

    jobs = (
        await session.execute(
            select(Job.status, func.count())
            .where(Job.owner_id == user.id)
            .group_by(Job.status)
        )
    ).all()

    return {
        "user_id": str(user.id),
        "email": user.email,
        "balance_mc": user.balance_credits,
        "balance_display": pricing.format_credits(user.balance_credits),
        "earned_mc": earned,
        "spent_mc": -spent - refunded,
        "jobs": {status: count for status, count in jobs},
        "member_since": user.created_at,
    }


@router.get("/devices", response_model=list[DeviceView])
async def devices(user: RateLimitedUser, session: Session) -> list[DeviceView]:
    """Owned devices with what each one has actually earned."""
    rows = (
        await session.execute(
            select(Device).where(Device.owner_id == user.id).order_by(Device.created_at)
        )
    ).scalars().all()

    per_device = dict(
        (
            await session.execute(
                select(Shard.device_id, func.coalesce(func.sum(Shard.price_mc), 0))
                .where(Shard.device_id.in_([d.id for d in rows]))
                .group_by(Shard.device_id)
            )
        ).all()
    ) if rows else {}

    return [
        _device_view(d, pricing.provider_share_mc(int(per_device.get(d.id, 0))))
        for d in rows
    ]


@router.get("/jobs")
async def jobs(user: RateLimitedUser, session: Session, limit: int = 50) -> list[dict]:
    rows = (
        await session.execute(
            select(Job)
            .where(Job.owner_id == user.id)
            .order_by(Job.created_at.desc())
            .limit(min(limit, 200))
        )
    ).scalars().all()
    return [
        {
            "id": str(j.id),
            "model_ref": j.model_ref,
            "status": JobStatus(j.status),
            "shard_count": j.shard_count,
            "item_count": j.item_count,
            "cost_mc": j.cost_mc,
            "spent_mc": j.spent_mc,
            "created_at": j.created_at,
            "completed_at": j.completed_at,
        }
        for j in rows
    ]


@router.get("/ledger")
async def ledger(user: RateLimitedUser, session: Session, limit: int = 100) -> list[dict]:
    rows = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.user_id == user.id)
            .order_by(LedgerEntry.created_at.desc())
            .limit(min(limit, 500))
        )
    ).scalars().all()
    return [
        {
            "id": str(e.id),
            "delta_mc": e.delta_mc,
            "balance_after_mc": e.balance_after_mc,
            "kind": e.kind,
            "ref_type": e.ref_type,
            "ref_id": e.ref_id,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.get("/reconcile")
async def reconcile(user: RateLimitedUser, session: Session) -> dict:
    """Prove this account's balance equals the sum of its ledger."""
    rows = await pricing.reconcile(session, user.id)
    return rows[0] if rows else {"ok": True, "drift_mc": 0}


async def _sum_kind(session, user_id, kind: pricing.EntryKind) -> int:
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(LedgerEntry.delta_mc), 0)).where(
                    LedgerEntry.user_id == user_id, LedgerEntry.kind == kind.value
                )
            )
        ).scalar_one()
    )
