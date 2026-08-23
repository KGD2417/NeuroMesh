"""Credits and the ledger.

Two rules, both absolute:

1. Credits are integers in the smallest unit (a "milli-credit", mC) everywhere
   in the system. Never floats. Conversion to a display string happens exactly
   once, at the UI edge, via format_credits().
2. post() is the sole writer to the ledger table and the sole mutator of
   User.balance_credits. Every money rule in the system routes through it, so
   the ledger and the balance cannot drift. reconcile() proves it.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.tiers import Tier
from config import get_settings

# Milli-credits earned per input item, by the tier the work ran at. Stronger
# silicon does more work per second, so it is worth more per item.
PRICE_PER_ITEM_MC: dict[Tier, int] = {
    Tier.CPU_INT8: 1,
    Tier.GPU_FP16: 2,
    Tier.NPU_INT8: 5,
    Tier.NPU_FP16: 8,
}

MIN_SHARD_PRICE_MC = 1


class EntryKind(str, enum.Enum):
    SIGNUP_GRANT = "signup_grant"
    JOB_ESCROW = "job_escrow"        # consumer debit at submit
    JOB_REFUND = "job_refund"        # unspent escrow returned
    SHARD_PAYOUT = "shard_payout"    # provider credit on a completed shard
    PLATFORM_FEE = "platform_fee"    # the cut NeuroMesh keeps


SIGNUP_GRANT_MC = 100_000


def shard_price_mc(tier: Tier, item_count: int) -> int:
    """What a shard of `item_count` inputs costs the consumer, in mC."""
    return max(MIN_SHARD_PRICE_MC, PRICE_PER_ITEM_MC[tier] * item_count)


def job_cost_mc(tier: Tier, shard_item_counts: list[int]) -> int:
    return sum(shard_price_mc(tier, n) for n in shard_item_counts)


def provider_share_mc(shard_price: int) -> int:
    """Provider's cut. Integer floor -- the remainder is the platform fee, so
    share + fee == price exactly, with no rounding leak."""
    return shard_price * get_settings().provider_share_bps // 10_000


def format_credits(mc: int) -> str:
    """The only float in the money path, and it never travels."""
    return f"{mc / 1000:.3f}"


async def post(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    delta_mc: int,
    kind: EntryKind,
    ref_type: str | None = None,
    ref_id: str | None = None,
    allow_negative: bool = False,
):
    """The sole writer to the ledger. Returns the created LedgerEntry.

    Locks the user row so concurrent shard payouts cannot interleave into a
    lost update. Raises InsufficientCredits rather than letting a balance go
    negative, unless the caller is a system entry that is allowed to.
    """
    from store.models import LedgerEntry, User  # local import: avoids cycle

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise LookupError(f"no such user {user_id}")

    new_balance = user.balance_credits + delta_mc
    if new_balance < 0 and not allow_negative:
        raise InsufficientCredits(
            f"balance {user.balance_credits} mC cannot absorb {delta_mc} mC"
        )

    user.balance_credits = new_balance
    entry = LedgerEntry(
        user_id=user_id,
        delta_mc=delta_mc,
        balance_after_mc=new_balance,
        kind=kind.value,
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id is not None else None,
    )
    session.add(entry)
    await session.flush()
    return entry


class InsufficientCredits(Exception):
    """Raised instead of writing a balance that would go negative."""


async def reconcile(session: AsyncSession, user_id: uuid.UUID | None = None) -> list[dict]:
    """Prove the invariant: for every user, sum(ledger deltas) == balance.

    Ships alongside post() because a money system without a reconciliation
    routine is a money system nobody can audit. Returns one row per user with
    ok=False if they disagree.
    """
    from store.models import LedgerEntry, User

    q = select(
        User.id,
        User.email,
        User.balance_credits,
        func.coalesce(func.sum(LedgerEntry.delta_mc), 0).label("ledger_sum"),
    ).outerjoin(LedgerEntry, LedgerEntry.user_id == User.id).group_by(User.id)
    if user_id is not None:
        q = q.where(User.id == user_id)

    out = []
    for uid, email, balance, ledger_sum in (await session.execute(q)).all():
        out.append(
            {
                "user_id": str(uid),
                "email": email,
                "balance_mc": int(balance),
                "ledger_sum_mc": int(ledger_sum),
                "drift_mc": int(balance) - int(ledger_sum),
                "ok": int(balance) == int(ledger_sum),
            }
        )
    return out
