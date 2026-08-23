# NeuroMesh

A marketplace for idle phone compute. Android phones sell their NPU time
overnight while charging; developers submit batch AI workloads that get sharded
across the fleet. Phone owners earn credits, developers get compute without
buying a GPU.

Built for a 30-hour hackathon, demoed on four iQOO 15s and no laptop — one
submits the job, three execute the shards.

**Why it is novel:** every decentralised compute network — Vast.ai, Akash,
io.net — pools desktop or datacenter GPUs. None treats the phone NPU as the
primary substrate, because the assumption is that phones are too weak, and that
assumption is a generation out of date. Flagship NPUs now ship 40–50 TOPS, and
the ideal conditions are already universal: plugged in, on Wi-Fi, screen off,
cool.

---

## What is here

| Layer | Path | Stack |
|---|---|---|
| Orchestrator API | `server/` | FastAPI on port 8000 |
| Android app | `android/` | Kotlin, Jetpack Compose, foreground service, LiteRT |
| Shared rules | `server/common/` | pricing, tiers, shard/log schemas, model allow-list |
| Postgres | container | users, devices, jobs, shards, ledger |
| Redis | container | shard queue, atomic claim, leases, payloads, live progress |
| Model builder | `tools/make_models.py` | generates the three registered `.tflite` graphs |
| Fleet simulator | `tools/simulate_fleet.py` | fake phones, for rehearsal and failure drills |
| Phone-hosted server | `tools/termux_bootstrap.sh` | the "no laptop" path |

**One APK, two modes.** Provider mode donates compute; Consumer mode submits
jobs and watches progress. Same binary, mode picked at login — this is what
lets the whole demo run on phones.

---

## Run it

### Server

```bash
docker compose up -d          # postgres + redis + api, migrations run on boot
curl localhost:8000/health
```

`/health` touches both stores for real. A green check that never opened a
connection is how a demo dies on stage.

### Tests

```bash
cd server
uv venv --python 3.12 && uv pip install -r pyproject.toml --extra dev
.venv/Scripts/python -m pytest -q      # 30 tests
```

They run against the real Postgres and the real Redis, not sqlite and not
fakeredis: the two things most worth testing here are a Lua script and a row
lock, and neither exists in a fake.

### Rehearsal without phones

```bash
python tools/simulate_fleet.py --devices 3 --items 120 --shard-size 8
python tools/simulate_fleet.py --devices 3 --items 64 --drop 1   # failure drill
```

The drill yanks a phone while it holds a shard. Expected: the lease expires,
the reaper requeues it, another phone runs it, the job still completes with
every output in order, and the phone that walked away earns nothing.

### APK

```bash
cd android
./gradlew assembleDebug -Pneuromesh.orchestrator=http://192.168.1.7:8000
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The orchestrator address is a build property so the four phones can be flashed
once and not typed into. It is editable on the Setup screen either way.

---

## Demo run book

1. **Orchestrator.** `docker compose up -d`, or `bash tools/termux_bootstrap.sh`
   on the phone hosting it. Note the LAN IP.
2. **Consumer phone.** Install, open, enter the orchestrator address, create an
   account. Choose *Submit a job*.
3. **Three provider phones.** Install, enter the same address, sign in to the
   same account, choose *Provide compute*. On the consumer phone tap **Get a
   code**, read the eight characters off the screen, type them into each
   provider phone, tap **Pair this phone**.
4. **Plug the providers in, put them on Wi-Fi, screen off.** Each shows its
   measured tier. Tap **Start providing**.
5. **Submit.** Pick a model, 120 items, shard size 8 → 15 shards. Watch the
   shard grid fill in live over SSE.
6. **The drill.** Mid-job, unplug one provider. Its cell goes back to queued
   within the 30-second lease and another phone picks it up. The run does not
   lose a single output.

---

## Design decisions

**Storage split by lifetime, not convenience.** Redis owns transient state: the
shard queue, the atomic claim, leases, live progress, encrypted payloads.
Postgres owns what must still be true in a year: users, devices, jobs, shards,
the ledger. A running job's hot state is Redis overlaid on the SQL row at read
time (`api/jobs.py::_view`).

**Claiming is one atomic Redis Lua script** (`scheduler/lua/claim.lua`), never
read-then-write. It pops the strongest shard a device is capable of, walking
tiers strongest-first, and does the pop, the state transition, the lease and the
payload read inside one script. Two devices claiming one shard is a
double-credit bug, so it is made impossible rather than unlikely.

**Leases, not trust.** Every claim carries a 30-second lease. A background
reaper requeues anything whose lease expired. Phones drop off constantly — the
owner picks the phone up, Wi-Fi flaps, the thermal governor pulls the plug —
and this loop is the only thing between a dropped phone and a shard stuck
forever. The TTL is short because mobile is far less reliable than a rack.

**Tiers are derived from measured capability**, never a marketing name:
available RAM, which LiteRT delegate actually initialised, which quantizations
it accepts (`common/tiers.py`). A job declares the tier it needs; a device
advertises what it has and serves any shard at or below its own tier.

**Credits are integers in the smallest unit** (milli-credits) everywhere. Never
floats. Display conversion happens once, at the UI edge. `common/pricing.py::post`
is the sole writer to the ledger and the sole mutator of a balance, and
`reconcile()` ships beside it — a money system without a reconciliation routine
is one nobody can audit.

**Payloads are encrypted at rest.** Shard inputs sit sealed in Redis and are
opened only inside a successful claim, for the device that holds the lease. A
status poll never decrypts anything, and the SSE stream carries no inputs at
all.

**Ownership leaks nothing.** Someone else's job or device is a 404, never a 403
— a 403 confirms the resource exists, which is an enumeration oracle for job
IDs. A malformed ID gets the same 404.

**One frozen settings dataclass**, read through `config.get_settings()`. Nothing
else in the codebase touches `os.environ`, and `migrations/env.py` reads the DSN
from that same accessor, so the API and the migrations cannot end up pointed at
different databases.

**Rate limits are per-account for authenticated actions**, per-IP only for
anonymous ones — a shared office NAT must not rate-limit its own users into
each other.

---

## Constraints this was designed *from*

- **Inference only, never training.** Mobile NPUs are fixed-function int8/fp16
  inference silicon; they cannot backprop. Nothing in the system implies
  on-device training.
- **Embarrassingly-parallel workloads only.** A shard is a contiguous slice of
  the input manifest and nothing else. No shard ever talks to another. Anything
  needing gradient sync is bandwidth-bound and out of scope.
- **QNN delegate, not NNAPI.** NNAPI is deprecated as of Android 15. The ladder
  is QNN (AI Engine Direct) → LiteRT GPU delegate → CPU, tried in that order,
  and a rung that fails to initialise is logged and stepped over rather than
  thrown (`infer/InferenceEngine.kt`). The GPU fallback is the one that must
  always work: a slow demo beats a dead demo.
- **The phone never runs arbitrary code.** Only the three pre-registered graphs
  in `infer/ModelRegistry.kt`, in a fixed interpreter, each verified against a
  pinned sha256 before it loads. This is a security property, and it is also why
  user-uploaded models and user-supplied Python cannot be supported.
- **Compute only while charging + unmetered Wi-Fi + screen off + under the
  thermal ceiling.** All four, re-checked before every claim, both on the phone
  (`provider/Eligibility.kt`) and server-side on the claim endpoint. The ceiling
  is `THERMAL_LIGHT`: at `MODERATE` the phone is already throttling and the
  owner would feel it. The owner's device comes first.

---

## The models

`tools/make_models.py` builds three real quantized graphs into the APK's assets:

| ref | what | quantization |
|---|---|---|
| `textembed-mlp-int8` | 384-d sentence embeddings over hashed tokens | full int8, int8 in and out |
| `mobilenet-v2-cls-int8` | ImageNet-1k, 224×224 RGB, real pretrained weights | full int8 |
| `sweep-mlp-fp16` | hyperparameter sweep evaluator | float16 weights |

Full-integer quantization for the two int8 graphs because that is what QNN
wants; float16 for the sweep model because that is what the GPU delegate wants.
MobileNet **V2** rather than V3 on purpose — V3's hard-swish does not survive
full-int8 quantization cleanly and XNNPACK refuses to prepare the result, and
the CPU path is the one that may never fail.

The embedding model is a small MLP over a hashed bag of tokens, not MobileBERT,
and it does not pretend otherwise. The contribution here is the marketplace and
the scheduler, not the model.

Assets are committed (~4 MB) so the APK builds without a TensorFlow install.
Regenerate with `python tools/make_models.py`, which prints the digests to paste
into `ModelRegistry.kt`; the build is seeded, so a rebuild is byte-identical.

---

## Endpoints

```
POST   /auth/register  /auth/login  /auth/refresh

GET    /jobs/models                 the allow-list
POST   /jobs                        model ref + inputs + shard size
GET    /jobs/{id}                   SQL row overlaid with Redis hot state
GET    /jobs/{id}/events            SSE live shard progress
GET    /jobs/{id}/result            outputs assembled in shard-index order
POST   /jobs/{id}/cancel            refunds unspent escrow

POST   /devices/pairing-code        short-lived, owner-authenticated
POST   /devices/register            redeem a code for a device key
POST   /devices/heartbeat           capability + eligibility
POST   /devices/claim               the atomic claim; 204 means empty queue
POST   /devices/shards/{id}/renew   extend the lease
POST   /devices/shards/{id}/complete
POST   /devices/shards/{id}/fail
POST   /devices/logs
GET    /devices                     owned devices
DELETE /devices/{id}                revoke a lost phone

GET    /me  /me/devices  /me/jobs  /me/ledger  /me/reconcile
GET    /health
```

Device API keys are stored as a sha256 digest. The plaintext is returned once,
at pairing, and lives only on the phone.

---

## Deliberately not built

Fraud verification (redundant execution on sampled shards + result hashing +
reputation — designed, not built), payout rails, user-uploaded models, training
of any kind, and iOS (no equivalent open compute surface exists there).

The two honest open problems: verifying that a device actually did the work,
and unit economics against datacenter GPUs. The pitch is not "cheaper than an
H100" — it is a closed loop: earn credits overnight, spend them by day running
models the phone could not run alone.
