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
| Browser console | `server/console.html` | consumer side in a browser, served at `/console` |
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

Four things to start, in this order: Docker Desktop, the stack, the APK, the
phone. The commands below are the Windows path, verified end to end against a
Samsung S20 FE; every error each step can throw is in **Troubleshooting**.

### 0. Prerequisites

| Need | Why | Check |
|---|---|---|
| Docker Desktop | postgres + redis + api | `docker --version` |
| JDK 17 or newer | Gradle build | `java -version` |
| Android SDK platform-tools | `adb` | see step 4 |
| uv + Python 3.12 | tests and model regeneration only | `uv --version` |

You do **not** need Android Studio, and you do **not** need TensorFlow — the
`.tflite` assets are committed.

### 1. Start the orchestrator

Docker Desktop must actually be *running*, not just installed. A stopped daemon
fails with `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the
file specified`, which reads like a missing file and is really a missing daemon.

```bash
docker compose up -d
```

First run pulls two images and builds the API; expect a couple of minutes.
Migrations run on API boot, so there is no separate migrate step.

```bash
curl.exe -s http://localhost:8000/health
```

Expect `{"ok":true,"postgres":true,"redis":true,...}`. `ok:true` means both
stores were opened for real — a green check that never touched the database is
how a demo dies on stage.

> **PowerShell:** use `curl.exe`, not `curl`. In Windows PowerShell `curl` is an
> alias for `Invoke-WebRequest`, which takes different flags and errors on `-s`.
> Or use `irm http://localhost:8000/health`.

### 2. The browser console

```
http://localhost:8000/console
```

Served by the API itself, so there is no second process to run and no CORS
origin to get wrong. Sign in or register, and you get the device table (tier,
online, eligible, heartbeat age, shards, earnings — refreshing continuously), a
pairing-code button, job submission, a live shard grid, and the result.

It exists because of a real constraint: a provider phone only computes with its
**screen off**, so you cannot watch the phone work on the phone. The console is
the window into a fleet that is by definition not looking back at you.

### 3. Tests

The stack from step 1 must be up — the suite runs against the real Postgres and
the real Redis, in a separate `neuromesh_test` database it creates itself.

```bash
cd server
uv venv --python 3.12
uv pip install -r pyproject.toml --extra dev
./.venv/Scripts/python.exe -m pytest -q      # 30 passed
```

Not sqlite and not fakeredis: the two things most worth testing here are a Lua
script and a row lock, and neither exists in a fake.

### 4. Build and install the APK

**`adb` is not on PATH by default on Windows.** It lives in the SDK:

```bash
export PATH="$PATH:/c/Users/YOU/AppData/Local/Android/Sdk/platform-tools"   # Git Bash
$env:PATH += ";$env:LOCALAPPDATA\Android\Sdk\platform-tools"                # PowerShell
adb devices          # your phone should be listed as "device"
```

If the list is empty: Settings → About phone → Software information → tap
*Build number* seven times → Developer options → **USB debugging** on, and set
the USB mode to file transfer rather than charging-only. If it says
`unauthorized`, unlock the phone and accept the RSA prompt.

**`android/local.properties` is gitignored**, so a fresh clone has no `sdk.dir`
and Gradle fails with `SDK location not found`. Create it:

```
sdk.dir=C:/Users/YOU/AppData/Local/Android/Sdk
```

Forward slashes even on Windows — a backslash is an escape character in a Java
properties file.

Now pick how the phone reaches the orchestrator. **Two options, and choosing
wrong is the most common silent failure**: the app builds and installs fine, and
then simply cannot connect.

**Option A — USB tunnel. Recommended for one phone.** No firewall rule, no IP to
look up, works on any network:

```bash
cd android
./gradlew assembleDebug -Pneuromesh.orchestrator=http://127.0.0.1:8000
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb reverse tcp:8000 tcp:8000
```

`adb reverse` forwards port 8000 on the phone back to your PC over the cable, so
`127.0.0.1:8000` on the phone *is* your machine. Verify from the phone itself:

```bash
adb shell curl -s http://127.0.0.1:8000/health
```

It does **not** survive a reboot or an unplug. Re-run that one line after either.

**Option B — LAN address. Required for more than one phone.** Find your IPv4 and
open the port. The firewall rule needs an admin PowerShell, and without it
Windows drops the phone's connection with no message anywhere:

```powershell
Get-NetRoute -DestinationPrefix '0.0.0.0/0' | ForEach-Object { (Get-NetIPAddress -ifIndex $_.ifIndex -AddressFamily IPv4).IPAddress }
New-NetFirewallRule -DisplayName "NeuroMesh 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

```bash
./gradlew assembleDebug -Pneuromesh.orchestrator=http://192.168.1.7:8000
```

Phone and PC must be on the same Wi-Fi, and it must not be a guest network with
client isolation. The address is a build property so a fleet can be flashed once
and never typed into; it is editable on the Setup screen either way.

> From PowerShell the wrapper is `.\gradlew.bat`, not `./gradlew`.

### 5. On the phone

1. **Setup screen.** The Orchestrator field is prefilled with whatever you baked
   in at build time. Enter an email and a password (8 characters minimum), flip
   the switch to **Create a new account** the first time, tap **Create account**.
   New accounts get a signup grant, so you can submit a job before earning
   anything.
2. **"One app, two sides".** Tap **Choose** under **Provide compute**.
3. **Join the fleet.** A phone is attached to no account until it redeems a
   pairing code. Tap **Get a code**, or mint one in the browser console, then
   type the eight characters into **Pairing code** and tap **Pair this phone**.
   The device key it returns is stored only on this phone.
4. **Tap "Start providing".** A foreground-service notification appears.
5. **Check the "Your phone comes first" card.** Four dots, and *all four* must be
   lit before a single shard is claimed:

   ```
   ● Charging              plug it in
   ● Unmetered Wi-Fi       Wi-Fi on, and not a metered hotspot
   ○ Screen off            you are looking at the screen
   ● Cool (0 ≤ 1)          at or below THERMAL_LIGHT
   ```

   Three of four while you hold the phone is correct and expected.
6. **Submit a job** — from the browser console, from a second phone, or from this
   phone via **Switch mode** → **Submit a job**. 120 items at shard size 8 is 15
   shards.
7. **Turn the phone's screen off.** Within about five seconds the console row
   flips to *computing* and the shard grid starts filling in.

### Rehearsal without phones

```bash
python tools/simulate_fleet.py --devices 3 --items 120 --shard-size 8
python tools/simulate_fleet.py --devices 3 --items 64 --drop 1   # failure drill
```

The drill yanks a phone while it holds a shard. Expected: the lease expires, the
reaper requeues it, another phone runs it, the job still completes with every
output in order, and the phone that walked away earns nothing.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `open //./pipe/dockerDesktopLinuxEngine` | Docker Desktop is not running | Start it and wait for the engine |
| `curl: A parameter cannot be found that matches '-s'` | PowerShell aliases `curl` to `Invoke-WebRequest` | Use `curl.exe`, or `irm` |
| `SDK location not found` | `local.properties` is gitignored | Create it with `sdk.dir=`, forward slashes |
| `adb: command not found` | platform-tools not on PATH | Add `%LOCALAPPDATA%\Android\Sdk\platform-tools` |
| `adb devices` is empty | USB debugging off, or a charge-only cable/mode | Developer options → USB debugging; USB mode → file transfer |
| `adb devices` says `unauthorized` | RSA prompt not accepted | Unlock the phone, tap Allow |
| App shows a connection error | Wrong orchestrator address for your setup | Option A: re-run `adb reverse tcp:8000 tcp:8000`. Option B: add the firewall rule, confirm same Wi-Fi |
| Worked, then broke after a reboot or unplug | `adb reverse` is not persistent | Re-run `adb reverse tcp:8000 tcp:8000` |
| **Started providing and nothing happens** | One of the four conditions is false — nearly always *screen off* | Plug in, Wi-Fi on, screen off. Read the conditions card |
| Console shows *online, not eligible* | Same thing: it is heartbeating but will not claim | Same fix |
| `402 Payment Required` on submit | Out of credits | Register a fresh account for the grant, or provide compute to earn |
| Job stays `queued` forever | No eligible device at or above the job's tier | Check the console device table; `sweep-mlp-fp16` needs tier 1 (GPU fp16) |
| Tests fail to connect | Stack not up | `docker compose up -d` first — the suite uses the real stores |

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

On a single phone wired over USB, do not use the unplug drill — the cable is
both the charger and the network link, so unplugging tests two things at once.
Wake the screen instead: `screen_off` goes false, the phone abandons its shard,
the lease expires, the reaper requeues it. Screen off again and it re-claims.

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
GET    /console                      the browser consumer console
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
