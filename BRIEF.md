**NeuroMesh** is a marketplace for idle phone compute. Android phones sell their
NPU time overnight while charging; developers submit batch AI workloads that get
sharded across the fleet. Phone owners earn credits, developers get compute
without buying a GPU. Built for a 30-hour hackathon, demoed on four iQOO 15s
with no laptop — one submits, three compute.

**Why it's novel:** decentralised compute networks (Vast.ai, Akash, io.net) all
pool desktop or datacenter GPUs. No mainstream network treats the phone NPU as
the primary substrate — the assumption was phones are too weak, and that's a
generation out of date. Flagship NPUs now ship 40–50 TOPS, and the ideal
conditions are already universal: plugged in, Wi-Fi, screen off, cool.

**Architecture:** FastAPI orchestrator + Redis (shard queue, atomic claim,
leases, encrypted payloads) + Postgres (users, devices, jobs, shards, ledger).
One Android app in Kotlin with two modes — Provider donates compute, Consumer
submits jobs.

**Constraints that shape every decision:**

- Inference only. Mobile NPUs are fixed-function inference sil
  backprop. No on-device training.
- Embarrassingly-parallel workloads only — shards are fully in
  inter-shard communication. Anything needing gradient sync is bandwidth-bound.
- LiteRT + Qualcomm QNN delegate. **Not NNAPI** — deprecated in Android 15.
  LiteRT GPU delegate is the fallback path.
- The phone runs only pre-registered model graphs in a fixed interpreter, never
  arbitrary user code.
- Compute runs only while charging + Wi-Fi + screen off + belo
  ceiling.
- Shard claiming is a single atomic Redis Lua script; every cl
  short lease that a reaper requeues when a phone drops off.
- Credits are integers in the smallest unit. One function owns all money rules
  and is the sole writer to the ledger.
  short lease that a reaper requeues when a phone drops off.
- Credits are integers in the smallest unit. One function owns
  and is the sole writer to the ledger.

**Known open problems:** verifying a device actually did the work (planned:
redundant execution on sampled shards + result hashing + reputation), and unit
economics versus datacenter GPUs — the honest pitch is not "ch
but a closed loop, earn credits overnight and spend them by day running models
the phone couldn't run alone.
