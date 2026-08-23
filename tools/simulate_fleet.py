"""A fleet of fake phones, for rehearsal and failure drills.

Runs the full provider protocol -- pair, heartbeat, claim, renew, complete --
against a live orchestrator, so the server half of the demo can be exercised
without four charged phones on a desk. It does not run LiteRT; it returns
shaped outputs of the right length. Everything else is the real wire protocol.

    python tools/simulate_fleet.py --devices 3 --items 120

Failure drill: --drop 1 makes one device claim a shard and then vanish, which
is what pulling a phone off the desk mid-job looks like to the server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://localhost:8000"

PHONE_PROFILES = [
    ("iQOO-15-A", {"available_ram_mb": 4096, "qnn_delegate": True, "gpu_delegate": True,
                   "quantizations": ["int8", "fp16"], "soc": "Qualcomm SM8850", "npu_tops": 45.0}),
    ("iQOO-15-B", {"available_ram_mb": 3800, "qnn_delegate": True, "gpu_delegate": True,
                   "quantizations": ["int8", "fp16"], "soc": "Qualcomm SM8850", "npu_tops": 45.0}),
    ("iQOO-15-C", {"available_ram_mb": 2048, "qnn_delegate": False, "gpu_delegate": True,
                   "quantizations": ["fp16"], "soc": "Qualcomm SM8750"}),
    ("Old-Pixel", {"available_ram_mb": 900, "qnn_delegate": False, "gpu_delegate": False,
                   "quantizations": ["int8"]}),
]


class Client:
    """Blocking urllib, run in a thread. Adding httpx to a demo tool that makes
    a hundred requests would be a dependency for nothing."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body=None, headers=None) -> tuple[int, dict | None]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None)


async def call(client: Client, *args, **kwargs):
    return await asyncio.to_thread(client.call, *args, **kwargs)


def outputs_for(model_ref: str, items: list) -> list:
    """Shaped like the real thing, without running the graph."""
    if model_ref.startswith("mobilenet"):
        return [
            {"top": [{"class": random.randrange(1000), "score": round(random.random(), 4)}
                     for _ in range(5)]}
            for _ in items
        ]
    if model_ref.startswith("sweep"):
        return [round(random.random(), 4) for _ in items]
    return [[round(random.uniform(-1, 1), 4) for _ in range(384)] for _ in items]


async def provider(
    client: Client, device: dict, stop: asyncio.Event, drop_after: int | None, log,
) -> dict:
    """One fake phone's claim loop."""
    headers = {"X-Device-Key": device["device_key"]}
    stats = {"name": device["name"], "shards": 0, "items": 0, "earned_mc": 0}
    claims = 0

    while not stop.is_set():
        status, _ = await call(
            client, "POST", "/devices/heartbeat",
            {
                "capability": device["capability"],
                "charging": True, "wifi": True, "screen_off": True,
                "thermal_status": 0, "battery_pct": 90,
            },
            headers,
        )
        if status != 200:
            log(f"{device['name']}: heartbeat rejected ({status})")
            return stats

        status, assignment = await call(client, "POST", "/devices/claim", {}, headers)
        if status == 204 or assignment is None:
            await asyncio.sleep(0.4)
            continue
        if status != 200:
            log(f"{device['name']}: claim failed ({status})")
            await asyncio.sleep(1)
            continue

        claims += 1
        if drop_after is not None and claims > drop_after:
            log(f"{device['name']}: YANKED holding shard #{assignment['index']} "
                f"-- the lease reaper now owns this")
            return stats

        # Pretend the NPU took a moment.
        await asyncio.sleep(random.uniform(0.05, 0.25))

        status, ack = await call(
            client, "POST", f"/devices/shards/{assignment['shard_id']}/complete",
            {
                "outputs": outputs_for(assignment["model_ref"], assignment["items"]),
                "duration_ms": random.randrange(40, 400),
                "delegate": "qnn" if device["capability"]["qnn_delegate"] else "gpu",
            },
            headers,
        )
        if status == 200 and ack and ack["accepted"]:
            stats["shards"] += 1
            stats["items"] += len(assignment["items"])
            stats["earned_mc"] += ack["payout_mc"]
            log(f"{device['name']}: shard #{assignment['index']} done  +{ack['payout_mc']} mC")
        else:
            log(f"{device['name']}: shard #{assignment['index']} not credited ({status})")

    return stats


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--devices", type=int, default=3)
    parser.add_argument("--items", type=int, default=120)
    parser.add_argument("--shard-size", type=int, default=8)
    parser.add_argument("--model", default="textembed-mlp-int8")
    parser.add_argument("--email", default=f"demo{int(time.time())}@example.com")
    parser.add_argument("--password", default="hunter2hunter2")
    parser.add_argument(
        "--drop", type=int, default=0,
        help="how many devices vanish after their first claim (the failure drill)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    client = Client(args.base)
    log = (lambda *_: None) if args.quiet else (lambda m: print(f"  {m}", flush=True))

    status, health = await call(client, "GET", "/health")
    if status != 200 or not health.get("ok"):
        print(f"orchestrator not healthy at {args.base}: {status} {health}")
        return 1
    print(f"orchestrator up: postgres={health['postgres']} redis={health['redis']}")

    status, tokens = await call(
        client, "POST", "/auth/register", {"email": args.email, "password": args.password}
    )
    if status != 201:
        return _fail(f"register failed: {status} {tokens}")
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    print(f"account {args.email}")

    devices = []
    for i in range(args.devices):
        name, capability = PHONE_PROFILES[i % len(PHONE_PROFILES)]
        _, code = await call(client, "POST", "/devices/pairing-code", {}, auth)
        status, credentials = await call(
            client, "POST", "/devices/register",
            {"pairing_code": code["code"], "name": f"{name}-{i}", "capability": capability},
        )
        if status != 201:
            return _fail(f"pairing failed: {status} {credentials}")
        devices.append({**credentials, "name": f"{name}-{i}", "capability": capability})
        print(f"paired {name}-{i} as {credentials['tier_label']}")

    inputs = [f"sentence number {i}" for i in range(args.items)]
    status, job = await call(
        client, "POST", "/jobs",
        {"model_ref": args.model, "inputs": inputs, "shard_size": args.shard_size},
        auth,
    )
    if status != 201:
        return _fail(f"submit failed: {status} {job}")
    print(f"job {job['id'][:8]} -- {job['shard_count']} shards, {job['cost_mc']} mC escrowed\n")

    stop = asyncio.Event()
    started = time.time()
    workers = [
        asyncio.create_task(
            provider(client, device, stop, (0 if i < args.drop else None), log)
        )
        for i, device in enumerate(devices)
    ]

    # Watch until the job lands, or until it is clear it never will.
    final = None
    while time.time() - started < 120:
        await asyncio.sleep(0.5)
        _, view = await call(client, "GET", f"/jobs/{job['id']}", None, auth)
        if view and view["status"] in ("completed", "failed", "cancelled"):
            final = view
            break
    stop.set()
    stats = await asyncio.gather(*workers)

    elapsed = time.time() - started
    print()
    if final is None:
        print("job did not finish inside 120s")
        return 1

    _, result = await call(client, "GET", f"/jobs/{job['id']}/result", None, auth)
    _, account = await call(client, "GET", "/me", None, auth)
    _, recon = await call(client, "GET", "/me/reconcile", None, auth)

    print(f"job {final['status']} in {elapsed:.1f}s")
    print(f"  shards   {final['shards_done']}/{final['shard_count']} done, "
          f"{final['shards_failed']} failed")
    print(f"  outputs  {len(result['outputs'])} assembled in order, "
          f"{sum(o is None for o in result['outputs'])} missing")
    for s in stats:
        print(f"  {s['name']:14} {s['shards']:3} shards  {s['items']:5} items  "
              f"{s['earned_mc']:6} mC")
    print(f"  balance  {account['balance_mc']} mC "
          f"(earned {account['earned_mc']}, spent {account['spent_mc']})")
    print(f"  ledger   {'reconciles' if recon['ok'] else 'DRIFTS by ' + str(recon['drift_mc'])}")

    ok = (
        final["status"] == "completed"
        and len(result["outputs"]) == args.items
        and not any(o is None for o in result["outputs"])
        and recon["ok"]
    )
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _fail(message: str) -> int:
    print(message)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
