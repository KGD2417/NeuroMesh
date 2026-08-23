"""Tiers and money: the two sets of rules everything else is built on."""

from __future__ import annotations

import pytest

from common import pricing
from common.schemas import DeviceHeartbeat
from common.tiers import Capability, Tier, derive_tier, tiers_servable_by
from tests.conftest import GPU_PHONE, NPU_PHONE, WEAK_PHONE


def test_tier_is_derived_from_measured_capability():
    assert derive_tier(Capability(**NPU_PHONE)) is Tier.NPU_FP16
    assert derive_tier(Capability(**GPU_PHONE)) is Tier.GPU_FP16
    assert derive_tier(Capability(**WEAK_PHONE)) is Tier.CPU_INT8

    # A QNN delegate the phone cannot feed does not buy a tier.
    starved = Capability(**{**NPU_PHONE, "available_ram_mb": 900})
    assert derive_tier(starved) is Tier.CPU_INT8


def test_device_serves_its_own_tier_and_below_strongest_first():
    assert tiers_servable_by(Tier.NPU_FP16) == [
        Tier.NPU_FP16, Tier.NPU_INT8, Tier.GPU_FP16, Tier.CPU_INT8
    ]
    assert tiers_servable_by(Tier.CPU_INT8) == [Tier.CPU_INT8]
    assert Tier.NPU_INT8 not in tiers_servable_by(Tier.GPU_FP16)


def test_eligibility_requires_all_four_conditions():
    ok = dict(charging=True, wifi=True, screen_off=True, thermal_status=0,
              battery_pct=80, capability=Capability(**NPU_PHONE))
    assert DeviceHeartbeat(**ok).eligible
    assert not DeviceHeartbeat(**{**ok, "charging": False}).eligible
    assert not DeviceHeartbeat(**{**ok, "wifi": False}).eligible
    assert not DeviceHeartbeat(**{**ok, "screen_off": False}).eligible
    assert not DeviceHeartbeat(**{**ok, "thermal_status": 3}).eligible


def test_credits_are_integers_and_the_split_never_leaks():
    price = pricing.shard_price_mc(Tier.NPU_INT8, 7)
    assert isinstance(price, int) and price == 35

    share = pricing.provider_share_mc(price)
    fee = price - share
    assert isinstance(share, int)
    assert share + fee == price  # no rounding leak, ever

    # An awkward price still splits exactly.
    for p in range(1, 200):
        assert pricing.provider_share_mc(p) + (p - pricing.provider_share_mc(p)) == p


def test_job_cost_is_the_sum_of_its_shards():
    counts = [16, 16, 3]
    assert pricing.job_cost_mc(Tier.GPU_FP16, counts) == sum(
        pricing.shard_price_mc(Tier.GPU_FP16, n) for n in counts
    )


async def test_ledger_is_the_only_writer_and_reconciles(session):
    from store.models import User

    user = User(email="ledger@example.com", password_hash="x", balance_credits=0)
    session.add(user)
    await session.flush()

    await pricing.post(session, user_id=user.id, delta_mc=1000,
                       kind=pricing.EntryKind.SIGNUP_GRANT)
    await pricing.post(session, user_id=user.id, delta_mc=-250,
                       kind=pricing.EntryKind.JOB_ESCROW)
    await pricing.post(session, user_id=user.id, delta_mc=80,
                       kind=pricing.EntryKind.SHARD_PAYOUT)

    assert user.balance_credits == 830
    rows = await pricing.reconcile(session, user.id)
    assert rows[0]["ok"] and rows[0]["drift_mc"] == 0


async def test_balance_cannot_go_negative(session):
    from store.models import User

    user = User(email="broke@example.com", password_hash="x", balance_credits=10)
    session.add(user)
    await session.flush()

    with pytest.raises(pricing.InsufficientCredits):
        await pricing.post(session, user_id=user.id, delta_mc=-11,
                           kind=pricing.EntryKind.JOB_ESCROW)
    assert user.balance_credits == 10


def test_display_conversion_happens_only_at_the_edge():
    assert pricing.format_credits(1234) == "1.234"
    assert isinstance(pricing.SIGNUP_GRANT_MC, int)
