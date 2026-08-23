"""The demo, as a test.

A job submitted from one phone splits into shards, executes across three other
phones, assembles a correct result in order, and credits each provider. Then
someone yanks a phone mid-job and it still lands.
"""

from __future__ import annotations

from common import pricing
from common.tiers import Tier
from scheduler import queue, reaper
from store.redis_client import redis
from tests.conftest import GPU_PHONE, NPU_PHONE, WEAK_PHONE, go_online, pair_device

MODEL = "textembed-mlp-int8"
INPUTS = [f"sentence {i}" for i in range(10)]


def compute(items: list[str]) -> list[str]:
    """What the phone would produce. Deterministic so we can assert on order."""
    return [f"emb({s})" for s in items]


async def _run_one_shard(client, device) -> dict | None:
    r = await client.post("/devices/claim", headers=device["headers"])
    if r.status_code == 204:
        return None
    assert r.status_code == 200, r.text
    a = r.json()

    ack = await client.post(
        f"/devices/shards/{a['shard_id']}/complete",
        headers=device["headers"],
        json={
            "outputs": compute(a["items"]),
            "duration_ms": 120,
            "delegate": "qnn",
            "device_logs": [
                {"ts": "2026-08-23T00:00:00Z", "level": "info", "event": "shard.done",
                 "thermal_status": 1, "battery_pct": 88}
            ],
        },
    )
    assert ack.status_code == 200, ack.text
    return {**a, "ack": ack.json()}


async def test_job_shards_across_the_fleet_and_pays_everyone(client, account, session):
    phones = []
    for name, cap in (("iQOO-A", NPU_PHONE), ("iQOO-B", NPU_PHONE), ("iQOO-C", GPU_PHONE)):
        d = await pair_device(client, account, name, cap)
        await go_online(client, d, cap)
        phones.append(d)

    assert phones[0]["tier"] == int(Tier.NPU_FP16)
    assert phones[2]["tier"] == int(Tier.GPU_FP16)

    submitted = await client.post(
        "/jobs",
        headers=account["headers"],
        json={"model_ref": MODEL, "inputs": INPUTS, "shard_size": 4},
    )
    assert submitted.status_code == 201, submitted.text
    job = submitted.json()
    assert job["shard_count"] == 3  # 4 + 4 + 2
    job_id = job["id"]

    # Every phone pulls work until the queue is dry.
    ran = []
    for _ in range(6):
        for phone in phones:
            got = await _run_one_shard(client, phone)
            if got:
                ran.append((phone["id"], got))

    assert len(ran) == 3
    assert {r[1]["index"] for r in ran} == {0, 1, 2}
    assert all(r[1]["ack"]["accepted"] for r in ran)
    assert all(r[1]["ack"]["payout_mc"] > 0 for r in ran)

    view = (await client.get(f"/jobs/{job_id}", headers=account["headers"])).json()
    assert view["status"] == "completed"
    assert view["shards_done"] == 3

    result = (await client.get(f"/jobs/{job_id}/result", headers=account["headers"])).json()
    assert result["outputs"] == compute(INPUTS), "outputs were not assembled in order"
    assert result["failed_shards"] == []

    me = (await client.get("/me", headers=account["headers"])).json()
    expected_cost = pricing.job_cost_mc(Tier.CPU_INT8, [4, 4, 2])
    payouts = sum(pricing.provider_share_mc(pricing.shard_price_mc(Tier.CPU_INT8, n))
                  for n in (4, 4, 2))
    assert me["balance_mc"] == pricing.SIGNUP_GRANT_MC - expected_cost + payouts

    recon = (await client.get("/me/reconcile", headers=account["headers"])).json()
    assert recon["ok"], recon

    devices = (await client.get("/me/devices", headers=account["headers"])).json()
    assert sum(d["shards_completed"] for d in devices) == 3
    assert all(d["online"] for d in devices)


async def test_a_phone_yanked_mid_job_does_not_lose_the_run(client, account):
    a = await pair_device(client, account, "iQOO-A", NPU_PHONE)
    b = await pair_device(client, account, "iQOO-B", NPU_PHONE)
    for d in (a, b):
        await go_online(client, d)

    job_id = (
        await client.post(
            "/jobs",
            headers=account["headers"],
            json={"model_ref": MODEL, "inputs": INPUTS[:4], "shard_size": 4},
        )
    ).json()["id"]

    stolen = (await client.post("/devices/claim", headers=a["headers"])).json()
    assert (await client.post("/devices/claim", headers=b["headers"])).status_code == 204

    # Owner picks the phone up and walks off: the lease is all that is left.
    await redis().zadd(queue.LEASES_KEY, {stolen["shard_id"]: 0})
    reaped = await reaper.sweep()
    assert [r["outcome"] for r in reaped] == [1]

    rerun = await _run_one_shard(client, b)
    assert rerun["shard_id"] == stolen["shard_id"]
    assert rerun["items"] == stolen["items"]

    result = (await client.get(f"/jobs/{job_id}/result", headers=account["headers"])).json()
    assert result["outputs"] == compute(INPUTS[:4])

    # The phone that walked away earned nothing for the shard it dropped.
    devices = {d["name"]: d for d in
               (await client.get("/me/devices", headers=account["headers"])).json()}
    assert devices["iQOO-A"]["shards_completed"] == 0
    assert devices["iQOO-B"]["shards_completed"] == 1


async def test_late_result_from_a_reassigned_shard_is_refused(client, account):
    a = await pair_device(client, account, "slow", NPU_PHONE)
    b = await pair_device(client, account, "fast", NPU_PHONE)
    for d in (a, b):
        await go_online(client, d)

    await client.post(
        "/jobs", headers=account["headers"],
        json={"model_ref": MODEL, "inputs": INPUTS[:2], "shard_size": 2},
    )
    stolen = (await client.post("/devices/claim", headers=a["headers"])).json()
    await redis().zadd(queue.LEASES_KEY, {stolen["shard_id"]: 0})
    await reaper.sweep()
    await _run_one_shard(client, b)

    late = await client.post(
        f"/devices/shards/{stolen['shard_id']}/complete",
        headers=a["headers"],
        json={"outputs": compute(stolen["items"]), "duration_ms": 9000, "delegate": "cpu"},
    )
    assert late.status_code == 200
    assert late.json()["accepted"] is False


async def test_status_never_returns_payload_data(client, account):
    job_id = (
        await client.post(
            "/jobs", headers=account["headers"],
            json={"model_ref": MODEL, "inputs": INPUTS, "shard_size": 4},
        )
    ).json()["id"]

    body = (await client.get(f"/jobs/{job_id}", headers=account["headers"])).text
    assert "sentence" not in body, "a status poll leaked shard inputs"


async def test_someone_elses_job_and_device_are_404_not_403(client, account):
    job_id = (
        await client.post(
            "/jobs", headers=account["headers"],
            json={"model_ref": MODEL, "inputs": INPUTS[:2], "shard_size": 2},
        )
    ).json()["id"]
    device = await pair_device(client, account, "mine", NPU_PHONE)

    other = (
        await client.post(
            "/auth/register",
            json={"email": "stranger@example.com", "password": "hunter2hunter2"},
        )
    ).json()
    h = {"Authorization": f"Bearer {other['access_token']}"}

    assert (await client.get(f"/jobs/{job_id}", headers=h)).status_code == 404
    assert (await client.get(f"/jobs/{job_id}/result", headers=h)).status_code == 404
    assert (await client.get(f"/jobs/{job_id}/events", headers=h)).status_code == 404
    assert (await client.delete(f"/devices/{device['id']}", headers=h)).status_code == 404
    # A malformed id is indistinguishable from someone else's id.
    assert (await client.get("/jobs/not-a-uuid", headers=h)).status_code == 404


async def test_an_ineligible_phone_cannot_claim(client, account):
    d = await pair_device(client, account, "phone-in-pocket", NPU_PHONE)
    hb = await client.post(
        "/devices/heartbeat",
        headers=d["headers"],
        json={"capability": NPU_PHONE, "charging": False, "wifi": True,
              "screen_off": True, "thermal_status": 0, "battery_pct": 40},
    )
    assert hb.json()["eligible"] is False
    assert hb.json()["reason"] == "not charging"

    await client.post(
        "/jobs", headers=account["headers"],
        json={"model_ref": MODEL, "inputs": INPUTS[:2], "shard_size": 2},
    )
    assert (await client.post("/devices/claim", headers=d["headers"])).status_code == 409


async def test_a_hot_phone_stops_computing(client, account):
    d = await pair_device(client, account, "toasty", NPU_PHONE)
    hb = await client.post(
        "/devices/heartbeat",
        headers=d["headers"],
        json={"capability": NPU_PHONE, "charging": True, "wifi": True,
              "screen_off": True, "thermal_status": 3, "battery_pct": 100},
    )
    assert hb.json()["eligible"] is False and hb.json()["reason"] == "too warm"
    assert (await client.post("/devices/claim", headers=d["headers"])).status_code == 409


async def test_only_registered_models_may_run(client, account):
    r = await client.post(
        "/jobs", headers=account["headers"],
        json={"model_ref": "../../etc/passwd", "inputs": ["x"], "shard_size": 1},
    )
    assert r.status_code == 422
    assert MODEL in (await client.get("/jobs/models")).text


async def test_a_job_you_cannot_afford_is_refused(client, account):
    r = await client.post(
        "/jobs", headers=account["headers"],
        json={"model_ref": "sweep-mlp-fp16", "inputs": list(range(60_000)),
              "shard_size": 512},
    )
    assert r.status_code == 402


async def test_weak_phone_is_not_offered_npu_work(client, account):
    weak = await pair_device(client, account, "old-phone", WEAK_PHONE)
    await go_online(client, weak, WEAK_PHONE)
    assert weak["tier"] == int(Tier.CPU_INT8)

    await client.post(
        "/jobs", headers=account["headers"],
        json={"model_ref": "sweep-mlp-fp16", "inputs": [[0.0] * 64], "shard_size": 1},
    )
    assert (await client.post("/devices/claim", headers=weak["headers"])).status_code == 204


async def test_cancel_refunds_the_unspent_escrow(client, account):
    before = (await client.get("/me", headers=account["headers"])).json()["balance_mc"]
    job_id = (
        await client.post(
            "/jobs", headers=account["headers"],
            json={"model_ref": MODEL, "inputs": INPUTS, "shard_size": 2},
        )
    ).json()["id"]

    cancelled = await client.post(f"/jobs/{job_id}/cancel", headers=account["headers"])
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"

    after = (await client.get("/me", headers=account["headers"])).json()["balance_mc"]
    assert after == before
    assert (await client.get("/me/reconcile", headers=account["headers"])).json()["ok"]


async def test_revoked_device_stops_earning(client, account):
    d = await pair_device(client, account, "lost-phone", NPU_PHONE)
    await go_online(client, d)
    assert (await client.delete(f"/devices/{d['id']}", headers=account["headers"])).status_code == 204
    assert (await client.post("/devices/heartbeat", headers=d["headers"],
                              json={"capability": NPU_PHONE, "charging": True, "wifi": True,
                                    "screen_off": True, "thermal_status": 0,
                                    "battery_pct": 90})).status_code == 401


async def test_live_progress_streams_over_sse(client, account):
    """The consumer phone watches its shards land, in real time, over SSE."""
    import asyncio
    import json

    device = await pair_device(client, account, "iQOO-A", NPU_PHONE)
    await go_online(client, device)

    job_id = (
        await client.post(
            "/jobs", headers=account["headers"],
            json={"model_ref": MODEL, "inputs": INPUTS[:4], "shard_size": 2},
        )
    ).json()["id"]

    seen: list[dict] = []

    async def watch():
        async with client.stream(
            "GET", f"/jobs/{job_id}/events", headers=account["headers"]
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    seen.append(json.loads(line[6:]))
                    if seen[-1]["type"] in ("job.completed", "job.failed"):
                        return

    async def work():
        await asyncio.sleep(0.2)  # let the subscription attach first
        while await _run_one_shard(client, device):
            pass

    await asyncio.wait_for(asyncio.gather(watch(), work()), timeout=20)

    types = [e["type"] for e in seen]
    assert "shard.claimed" in types and "shard.done" in types
    assert types[-1] == "job.completed"
    assert seen[-1]["shards_done"] == 2
    # A progress stream is the widest surface in the API: it carries no inputs.
    assert all("items" not in e for e in seen)
