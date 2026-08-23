"""Provider side: pair a phone, heartbeat, claim, renew, settle.

Everything here is authenticated with the device key, not the owner's JWT, and
every claim carries a short lease. The phone is assumed to vanish at any moment.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from api import security
from api.deps import (
    NOT_FOUND, RateLimitedDevice, RateLimitedUser, Session, parse_uuid_or_404,
)
from common import registry
from common.schemas import (
    DeviceHeartbeat, DeviceLog, DeviceRegister, DeviceView, ShardAssignment,
    ShardFailure, ShardResult,
)
from common.thermal import THERMAL_CEILING
from common.tiers import Tier, derive_tier
from config import get_settings
from scheduler import aggregator, queue
from store.models import Device, DeviceLogRow, Shard, User
from store.redis_client import redis, text

router = APIRouter(prefix="/devices", tags=["devices"])

_PAIR_KEY = "nm:pair:{}"


class PairingCode(BaseModel):
    code: str
    expires_in_s: int


class DeviceCredentials(BaseModel):
    device_id: str
    device_key: str
    tier: Tier
    tier_label: str
    lease_ttl_s: int
    models: list[str]


class ClaimAck(BaseModel):
    accepted: bool
    payout_mc: int = 0
    balance_mc: int = 0
    detail: str = ""


# --- pairing ----------------------------------------------------------------

@router.post("/pairing-code", response_model=PairingCode, status_code=status.HTTP_201_CREATED)
async def create_pairing_code(user: RateLimitedUser) -> PairingCode:
    """Short-lived code, read off one phone screen and typed into another.
    This is the only way a device gets attached to an account."""
    ttl = get_settings().pairing_code_ttl_s
    code = security.new_pairing_code()
    await redis().set(_PAIR_KEY.format(code), str(user.id), ex=ttl)
    return PairingCode(code=code, expires_in_s=ttl)


@router.post("/register", response_model=DeviceCredentials, status_code=status.HTTP_201_CREATED)
async def register(body: DeviceRegister, session: Session) -> DeviceCredentials:
    """Redeem a pairing code for a device key. The key is returned exactly once."""
    key = _PAIR_KEY.format(body.pairing_code.strip().upper())
    owner_id = text(await redis().getdel(key))
    if owner_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pairing code invalid or expired")

    owner = await session.get(User, uuid.UUID(owner_id))
    if owner is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pairing code invalid or expired")

    plaintext, digest = security.new_device_key()
    tier = derive_tier(body.capability)
    device = Device(
        owner_id=owner.id,
        name=body.name,
        tier=int(tier),
        capability=body.capability.model_dump(),
        api_key_hash=digest,
    )
    session.add(device)
    await session.flush()

    return DeviceCredentials(
        device_id=str(device.id),
        device_key=plaintext,
        tier=tier,
        tier_label=tier.label,
        lease_ttl_s=get_settings().lease_ttl_s,
        models=registry.refs(),
    )


# --- heartbeat --------------------------------------------------------------

@router.post("/heartbeat")
async def heartbeat(body: DeviceHeartbeat, device: RateLimitedDevice, session: Session) -> dict:
    """Re-derives the tier every beat: available RAM moves, and the QNN delegate
    can fail to initialise on a phone that had it yesterday."""
    tier = derive_tier(body.capability)
    device.tier = int(tier)
    device.capability = body.capability.model_dump()
    device.last_heartbeat_at = datetime.now(timezone.utc)
    device.eligible = body.eligible

    return {
        "tier": int(tier),
        "tier_label": tier.label,
        "eligible": body.eligible,
        "may_claim": body.eligible,
        "thermal_ceiling": THERMAL_CEILING,
        "lease_ttl_s": get_settings().lease_ttl_s,
        "reason": _ineligible_reason(body),
    }


def _ineligible_reason(hb: DeviceHeartbeat) -> str | None:
    """The owner's device comes first, and the app says so out loud."""
    if not hb.charging:
        return "not charging"
    if not hb.wifi:
        return "not on Wi-Fi"
    if not hb.screen_off:
        return "screen is on"
    if hb.thermal_status > THERMAL_CEILING:
        return "too warm"
    return None


# --- claim / lease / settle -------------------------------------------------

@router.post("/claim", response_model=ShardAssignment | None)
async def claim(device: RateLimitedDevice, response: Response) -> ShardAssignment | None:
    """Atomically claim the strongest shard this phone can run.

    204 means the queue had nothing for this tier -- the normal case, and the
    phone should back off rather than spin.
    """
    if not device.eligible:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "device is not currently eligible to compute (charge + Wi-Fi + screen off + cool)",
        )
    assignment = await queue.claim(str(device.id), Tier(device.tier))
    if assignment is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    await queue.publish(
        assignment.job_id,
        "shard.claimed",
        await queue.progress(assignment.job_id),
        index=assignment.index,
        device_id=str(device.id),
    )
    return assignment


@router.post("/shards/{shard_id}/renew")
async def renew(shard_id: str, device: RateLimitedDevice) -> dict:
    """A long shard renews mid-flight. Losing the lease is not an error the
    phone can argue with -- it just stops and throws the work away."""
    deadline = await queue.renew(shard_id, str(device.id))
    if deadline is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "lease lost; abandon this shard")
    return {"lease_deadline_ms": deadline, "lease_ttl_s": get_settings().lease_ttl_s}


@router.post("/shards/{shard_id}/complete", response_model=ClaimAck)
async def complete(
    shard_id: str, body: ShardResult, device: RateLimitedDevice, session: Session
) -> ClaimAck:
    shard = await session.get(Shard, parse_uuid_or_404(shard_id))
    if shard is None:
        raise NOT_FOUND
    if len(body.outputs) != shard.item_count:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"expected {shard.item_count} outputs, got {len(body.outputs)}",
        )

    accepted, prog, job_id = await queue.complete(shard_id, str(device.id), body.outputs)
    if not accepted:
        # The lease expired and the shard was reassigned. Not an error on the
        # phone's part, and explicitly not paid: another device owns it now.
        return ClaimAck(accepted=False, detail="lease lost; result discarded")

    payout = await aggregator.settle_shard(
        session, shard, device, duration_ms=body.duration_ms, delegate=body.delegate
    )
    await _store_logs(session, device, shard, body.device_logs)
    await session.flush()

    await queue.publish(
        job_id, "shard.done", prog, index=shard.index, device_id=str(device.id)
    )
    if prog.finished:
        await aggregator.finalize(session, shard.job_id)

    owner = await session.get(User, device.owner_id)
    return ClaimAck(accepted=True, payout_mc=payout, balance_mc=owner.balance_credits)


@router.post("/shards/{shard_id}/fail", response_model=ClaimAck)
async def fail(
    shard_id: str, body: ShardFailure, device: RateLimitedDevice, session: Session
) -> ClaimAck:
    shard = await session.get(Shard, parse_uuid_or_404(shard_id))
    if shard is None:
        raise NOT_FOUND

    outcome, prog, job_id = await queue.fail(shard_id, str(device.id), body.retryable)
    if outcome == 0:
        return ClaimAck(accepted=False, detail="lease lost; nothing to report")

    if outcome == 1:
        await aggregator.mark_shard_requeued(session, shard)
    else:
        await aggregator.mark_shard_failed(session, shard)
    await _store_logs(
        session, device, shard,
        [DeviceLog(ts=datetime.now(timezone.utc), level="error",
                   event="shard.failed", detail=body.reason)],
    )
    await session.flush()

    await queue.publish(
        job_id,
        "shard.requeued" if outcome == 1 else "shard.failed",
        prog,
        index=shard.index,
        device_id=str(device.id),
    )
    if prog.finished:
        await aggregator.finalize(session, shard.job_id)
    return ClaimAck(accepted=True, detail="requeued" if outcome == 1 else "failed")


@router.post("/logs", status_code=status.HTTP_202_ACCEPTED)
async def post_logs(
    body: list[DeviceLog], device: RateLimitedDevice, session: Session
) -> dict:
    await _store_logs(session, device, None, body[:200])
    return {"stored": min(len(body), 200)}


async def _store_logs(
    session, device: Device, shard: Shard | None, logs: list[DeviceLog]
) -> None:
    for entry in logs:
        session.add(
            DeviceLogRow(
                device_id=device.id,
                shard_id=shard.id if shard else None,
                level=entry.level,
                event=entry.event,
                detail=entry.detail,
                thermal_status=entry.thermal_status,
                battery_pct=entry.battery_pct,
            )
        )


# --- owner-facing -----------------------------------------------------------

@router.get("", response_model=list[DeviceView])
async def my_devices(user: RateLimitedUser, session: Session) -> list[DeviceView]:
    rows = (
        await session.execute(
            select(Device).where(Device.owner_id == user.id).order_by(Device.created_at)
        )
    ).scalars().all()
    return [_device_view(d) for d in rows]


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(device_id: str, user: RateLimitedUser, session: Session) -> Response:
    """Revoking is how a lost phone stops earning. Someone else's device 404s."""
    device = await session.get(Device, parse_uuid_or_404(device_id))
    if device is None or device.owner_id != user.id:
        raise NOT_FOUND
    await session.delete(device)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _device_view(d: Device, earned_mc: int = 0) -> DeviceView:
    stale_after = get_settings().heartbeat_stale_s
    online = (
        d.last_heartbeat_at is not None
        and (datetime.now(timezone.utc) - d.last_heartbeat_at).total_seconds() < stale_after
    )
    return DeviceView(
        id=str(d.id),
        name=d.name,
        tier=Tier(d.tier),
        online=online,
        eligible=bool(d.eligible) and online,
        last_heartbeat_at=d.last_heartbeat_at,
        shards_completed=d.shards_completed,
        earned_mc=earned_mc,
    )
