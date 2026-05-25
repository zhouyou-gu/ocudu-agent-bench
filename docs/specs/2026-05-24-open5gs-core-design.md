# Open5GS 5G core — first vertical slice for live OCUDU runtime

Status: **approved design, ready for implementation plan**
Author: brainstormed in pair-session 2026-05-24
Scope owner: `benchmark/provision/open5gs-core/`

## 1. Context

The benchmark currently runs every task against the `simulated_ocudu` adapter
(closed-loop deterministic state machine, no live transport). All
provisioning under `benchmark/provision/` is intentional placeholder per its
own `README.md`. This slice is the **first concrete step toward live OCUDU**:
a deployable Open5GS 5G core that a future OCUDU gNB can attach to over NGAP.

Out of scope for this slice (covered by later slices):

- OCUDU gNB itself
- srsUE / ZMQ test UE
- FlexRIC (E2 KPM/CCC/RC)
- `benchmark/benchmark_api/live_ocudu.py` (the live runtime adapter code)
- Task manifests that declare `E.runtime_adapter = ocudu_live`
- Live-runtime conformance checks beyond the smoke script in §8

What this slice **must** deliver:

> `docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml up -d`
> brings up a functioning 5G core where AMF is reachable on the docker
> network at a stable IP/port, with at least one provisioned subscriber,
> ready for an OCUDU gNB to later attach via NGAP.

## 2. Decisions

Each decision was made in the brainstorming pair-session; trade-offs were
weighed at the time and the rejected alternatives are kept here for
posterity.

| # | Decision | Rejected alternatives | Why |
|---|---|---|---|
| D1 | Split-NF deployment (one container per Open5GS NF + a MongoDB container) | All-in-one Open5GS container; split-mongo-only | Closer to a production 5G core; enables future `RESTART_CORE_NF` action to target individual NFs; mongo always isolated for clean DB persistence. Cost: bigger compose, longer cold-start, ~10 NF containers. |
| D2 | Upstream community images (`gradiant/open5gs-<nf>:2.7.0` per NF) | Build-from-source via shared Dockerfile w/ multi-stage targets; per-NF Dockerfile | No build infra to maintain inside this repo; pinned tags are stable; depending on external registry is acceptable given we already depend on `mongo:7` and `python:3.12-slim`. The previously-cached `skillful-ran/open5gs:v2.7.0` is out of scope per the repo-only directive. |
| D3 | Custom docker bridge network `ran` (`10.53.1.0/24`) with static per-NF IPs; AMF NGAP also published to host | Static IPs only with no host publication; default `bridge` with DNS-only | Both internal docker traffic AND external host-reach work. Predictable IPs make gNB config drift-free. Mirrors the `10.53.1.0/24` scheme used elsewhere in the project. |
| D4 | CSV + Python seeder one-shot container (idempotent pymongo writes) | Open5GS WebUI (manual); Open5GS `subscriber-import` CLI inside entrypoint | Reproducible from clean state; version-controlled subscriber set; matches the existing stub structure (`add_users.py`, `subscriber_db.csv.example`); easy to add more UEs by editing one CSV. |
| D5 | Done = compose up + healthcheck green + AMF SCTP 38412 bound + AMF log shows NF registration + seeded subscriber queryable via mongosh | Add NGAP-handshake smoke (writes a minimal SCTP/ASN.1 client); defer all verification | Strong enough to guarantee "gNB will be able to attach" without writing an NGAP client from scratch. The 4-check script doubles as the live-runtime readiness check in the future adapter. |

## 3. Architecture

```text
              ┌─────────────────── docker network: ran (10.53.1.0/24) ───────────────────┐
              │                                                                          │
              │   mongo (10.53.1.10:27017)                                               │
              │      ▲                                                                   │
              │      │                                                                   │
              │   nrf (10.53.1.4) ◄─SBI── ausf udm udr pcf bsf scp smf upf amf           │
              │      ▲    (registrations)                                                │
              │      │                                                                   │
              │   subscriber-seeder (one-shot: reads CSV, writes mongo, exits)           │
              │                                                                          │
              │                                       amf (10.53.1.2)                    │
              │                                          │ NGAP/SCTP 38412               │
              └──────────────────────────────────────────│───────────────────────────────┘
                                                         │
                                                  host 0.0.0.0:38412
                                                  (future gNB attaches here)

                                                  host 127.0.0.1:27017
                                                  (mongo, for future control APIs)
```

11 long-running containers + 1 one-shot seeder, all on the `ran` network.

**Compose project name** is pinned to `open5gs` (`name: open5gs` at the top
of the compose file). The future live runtime adapter knows the compose
project name when it issues `docker compose -p open5gs ps` / `restart` /
`exec` calls, so container names are stable across machines.

| Service | Image | Static IP | Role |
|---|---|---|---|
| `mongo` | `mongo:7` | 10.53.1.10 | subscriber DB backend |
| `nrf` | `gradiant/open5gs-nrf:2.7.0` | 10.53.1.4 | NF discovery |
| `scp` | `gradiant/open5gs-scp:2.7.0` | 10.53.1.5 | service comms proxy |
| `ausf` | `gradiant/open5gs-ausf:2.7.0` | 10.53.1.6 | auth |
| `udm` | `gradiant/open5gs-udm:2.7.0` | 10.53.1.7 | unified data |
| `udr` | `gradiant/open5gs-udr:2.7.0` | 10.53.1.8 | unified data repo |
| `pcf` | `gradiant/open5gs-pcf:2.7.0` | 10.53.1.9 | policy |
| `bsf` | `gradiant/open5gs-bsf:2.7.0` | 10.53.1.11 | binding support |
| `smf` | `gradiant/open5gs-smf:2.7.0` | 10.53.1.3 | session mgmt |
| `upf` | `gradiant/open5gs-upf:2.7.0` | 10.53.1.12 | user plane |
| `amf` | `gradiant/open5gs-amf:2.7.0` | 10.53.1.2 | access + mobility (NGAP) |
| `subscriber-seeder` | local build: `python:3.12-slim` + `pymongo` | n/a | one-shot, exits after seeding |

Two host port publications make the stack reachable from outside the `ran`
network without losing internal isolation:

- `38412:38412/sctp` on AMF — a future gNB can reach AMF either from inside the `ran` network or via the host IP.
- `127.0.0.1:27017:27017/tcp` on mongo — bound to loopback only. Lets the benchmark / agents process on the same host run `pymongo` upserts post-startup, which is required by the `UPDATE_CORE_UE_REGISTRATION` action and the `core_ue_registration_misconfig` stimulus. Loopback-only means the LAN never sees the DB.

NF containers that may be targeted by the `core_latency_profile` stimulus
(AMF, SMF, UPF) get `cap_add: [NET_ADMIN]` so a future stimulus can run
`docker compose exec <nf> tc qdisc add dev eth0 root netem delay <ms> loss <%>`.
Other NFs do not get the capability; principle-of-least-privilege.

## 4. Components in detail

### 4.1 Per-NF config files

Mounted at `/opt/open5gs/etc/open5gs/<nf>.yaml` in each NF container. The
gradiant images consume Open5GS's standard YAML schema.

Common fragment across all SBI-only NFs (varies only in `sbi.server.address`,
which is always `0.0.0.0` inside the container):

```yaml
logger:
  level: info
sbi:
  server:
    address: 0.0.0.0
    port: 7777
  client:
    nrf:
      uri: http://10.53.1.4:7777
    scp:
      uri: http://10.53.1.5:7777
```

NF-specific deltas:

| NF | Delta |
|---|---|
| `nrf` | PLMN list `[{mcc: '001', mnc: '01'}]`; no `client.nrf` (it IS the NRF) |
| `amf` | `plmn_support` (PLMN + s-NSSAI `sst=1`), `tai` (TAC=1), `guami` (region/set/pointer), `ngap.server.address: 0.0.0.0` SCTP port 38412, `access_control` allow-list |
| `smf` | `pdn` block (DNN `internet`, IPv4 pool `10.45.0.0/16`), `gtpu.server.address: 0.0.0.0`, `pfcp` peers |
| `upf` | `gtpu` (TEID pool), `pfcp.server.address` matching SMF expectation, subnet `10.45.0.0/16` |
| `udm` / `udr` / `ausf` / `pcf` / `bsf` / `scp` | minimal; SBI client to NRF only |

All NFs share PLMN MCC=001 MNC=01, TAC=1, slice SST=1 (no SD) — the standard
srsRAN/Open5GS ZMQ test triplet.

### 4.2 Subscriber DB + seeder

Tree:

```text
seed/
├── Dockerfile          # FROM python:3.12-slim; pip install -r requirements.txt; COPY add_users.py + csv
├── requirements.txt    # pymongo==4.*
├── add_users.py        # idempotent: connect to mongo, upsert each CSV row
└── subscriber_db.csv   # one row to start: the standard test UE
```

Default subscriber row (matches the simulated_ocudu defaults already in
`CLAUDE.md`):

```csv
imsi,k,opc,amf,sqn,plmn,dnn,sst,sd,auth_profile_id,ue_id
001010000000001,00112233445566778899aabbccddeeff,63bfa50ee6523365ff14c1f45f88737d,8000,000000000000,00101,internet,1,,ue1_test_profile,ue1
```

`add_users.py` writes to the `subscribers` collection in the `open5gs`
database using Open5GS's standard document schema (IMSI key, security with
K+OP/OPc, AMBR, slice list, session list). Script is **idempotent** — re-runs
upsert; the seeder can re-run safely.

Compose snippet for the seeder:

```yaml
subscriber-seeder:
  build: ./seed
  depends_on:
    mongo: { condition: service_healthy }
  environment:
    MONGO_URI: mongodb://mongo:27017/
  restart: 'no'
```

### 4.3 Healthchecks + startup ordering

| NF | Healthcheck | Depends on |
|---|---|---|
| `mongo` | `mongosh --quiet --eval "db.adminCommand('ping')"` | — |
| `subscriber-seeder` | n/a (one-shot) | `mongo: service_healthy` |
| `nrf` | TCP `:7777` open | `mongo: service_healthy` |
| `scp`/`ausf`/`udm`/`udr`/`pcf`/`bsf` | TCP `:7777` open | `nrf: service_healthy` |
| `upf` | TCP `:8805` (PFCP) | `nrf: service_healthy` |
| `smf` | TCP `:7777` open | `nrf: service_healthy`, `upf: service_healthy` |
| `amf` | TCP `:7777` open AND SCTP `:38412` reachable | `nrf: service_healthy` |

Compose's `depends_on` with `condition: service_healthy` serializes the
dependency chain: mongo → NRF → all other NFs → AMF last.

### 4.4 File layout in the repo

```text
benchmark/provision/open5gs-core/
├── README.md                                   # rewritten: bring-up + tear-down + acceptance
├── compose/
│   ├── docker-compose.open5gs.yml              # NEW, ~150 lines (split-NF compose)
│   ├── configs/
│   │   ├── nrf.yaml
│   │   ├── scp.yaml
│   │   ├── ausf.yaml
│   │   ├── udm.yaml
│   │   ├── udr.yaml
│   │   ├── pcf.yaml
│   │   ├── bsf.yaml
│   │   ├── smf.yaml
│   │   ├── upf.yaml
│   │   └── amf.yaml                            # 10 NF YAMLs total
│   └── seed/
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── add_users.py
│       └── subscriber_db.csv
└── tests/
    └── check_core_ready.sh                     # 4-check acceptance verifier (see §8)

Old stubs to delete:
    compose/open5gs/Dockerfile
    compose/open5gs/open5gs.env
    compose/open5gs/open5gs-5gc.yml
    compose/open5gs/open5gs_entrypoint.sh
    compose/open5gs/setup_tun.py
    compose/open5gs/add_users.py
    compose/open5gs/subscriber_db.csv.example
```

The old stubs were placeholders for a build-from-source path that D2 rejects;
they're useless under the upstream-image approach.

## 5. Data flow

```text
1. operator: docker compose up -d
2. mongo starts                                 healthcheck: db.adminCommand('ping') → ok
3. subscriber-seeder runs (one-shot)            depends on mongo healthy
     ↳ reads subscriber_db.csv
     ↳ pymongo upserts each row into open5gs.subscribers
     ↳ exits 0
4. nrf starts                                   depends on mongo healthy
5. scp/ausf/udm/udr/pcf/bsf/upf/smf start       depends on nrf healthy
     ↳ each NF registers profile with NRF via NFRegister
     ↳ NRF log: "NF Profile Updated" per NF
6. amf starts last                              depends on nrf healthy
     ↳ SBI register with NRF
     ↳ opens NGAP SCTP listener on 0.0.0.0:38412
     ↳ healthcheck passes
7. host port 38412/sctp now bound               gNB on host or in `ran` net can SCTP-connect
```

Future gNB attach (out of scope for this slice, documented for context):
gNB → NGAP Setup Request → AMF → Setup Response with `served_guami_list`,
PLMN/TAC. UE attach later: AMF → AUSF → UDM → UDR → mongo subscriber lookup.

## 6. Error handling

| Failure | What happens | How to detect |
|---|---|---|
| Image pull fails | compose fails fast with "dependency failed to start" | `docker compose up` exit ≠ 0 |
| Mongo unhealthy after 60 s | seeder + dependent NFs don't start; compose hangs in "waiting" | `docker compose ps` shows `unhealthy` |
| Seeder fails (bad CSV, schema mismatch) | seeder exits ≠ 0; NFs still start; subscriber lookup later fails | `docker compose logs subscriber-seeder` |
| NRF down | other NFs retry SBI registration, eventually unhealthy | NRF logs: no `NFRegister`; other NFs: SBI connect retries |
| AMF can't bind SCTP 38412 (host port in use) | compose errors at AMF startup | error visible in `docker compose up` output |
| One NF restarts mid-run | others reconnect via NRF heartbeat; recovers in ~10 s | logs; AMF healthcheck transiently red |

**Restart policies:**
- Long-running containers: `restart: unless-stopped`
- Seeder: `restart: 'no'`
- `pull_policy: missing` so repeat invocations don't re-pull.

**Logs**: stdout/stderr only, captured by docker; nothing written outside
mongo's volume. Inspect via `docker compose logs <svc>`.

**Volume**: one named volume `mongo-data` for subscriber DB persistence
across compose restarts. Declared in compose `volumes:` block; not a host
bind-mount.

## 7. Testing strategy

### 7.1 No unit tests for the compose itself

It's declarative YAML, not Python. The integration test IS the
compose-up + acceptance script (§8).

### 7.2 Acceptance script (also reused by future live-runtime conformance)

`benchmark/provision/open5gs-core/tests/check_core_ready.sh`:

```bash
#!/usr/bin/env bash
# All-in-one health verification for the Open5GS core compose stack.
set -euo pipefail

COMPOSE_FILE="${1:-benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml}"

# 1. compose services all running + healthy
docker compose -f "$COMPOSE_FILE" ps --format json | \
  jq -e 'all(.State == "running") and all(.Health == "healthy" or .Health == "")'

# 2. SCTP port 38412 bound inside AMF container
docker compose -f "$COMPOSE_FILE" exec -T amf ss -tnl4 | grep -q '38412'

# 3. AMF log contains successful NF registration with NRF
docker compose -f "$COMPOSE_FILE" logs --no-color amf | grep -q 'NF registered'

# 4. seeded subscriber queryable
docker compose -f "$COMPOSE_FILE" exec -T mongo mongosh --quiet open5gs \
  --eval 'db.subscribers.findOne({imsi: "001010000000001"})' | grep -q 'imsi'

# 5. mongo is reachable on host loopback (required by future control APIs)
python3 -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", 27017)); s.close()'

echo "open5gs core ready"
```

Returns 0 on all pass, non-zero otherwise. Suitable for CI and for any
`verification-before-completion` checkpoint when touching this area.

### 7.3 Manual smoke

```bash
cd ~/skillful-ran-workspace
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml up -d
bash benchmark/provision/open5gs-core/tests/check_core_ready.sh
# expect: "open5gs core ready"
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml down -v
```

## 8. Definition of done

This slice is complete when:

1. All files in §4.4 exist in the repo, the stubs in §4.4 are deleted, and `git diff --check` is clean.
2. On 5090pc: `docker compose ... up -d` finishes with exit 0 and all 12 containers report `running` (seeder reports `exited (0)`).
3. `bash benchmark/provision/open5gs-core/tests/check_core_ready.sh` prints `open5gs core ready` and exits 0.
4. `docker compose ... down -v` cleanly removes containers + the `mongo-data` volume.
5. README at `benchmark/provision/open5gs-core/README.md` documents the bring-up, tear-down, acceptance command, and the static-IP map (so a future gNB writer knows AMF is at 10.53.1.2).
6. CLAUDE.md at the workspace root is updated to reflect that Open5GS core is now deployable from the repo (replaces "live OCUDU/FlexRIC/UE/core adapters are not wired" with the more precise "Open5GS core is deployable; gNB / UE / FlexRIC / live transport remain unwired").
7. Mongo is reachable on `127.0.0.1:27017` from the host (acceptance script check 5), so the future live transport can perform `pymongo` subscriber updates without re-architecting the compose.

## 9. Integration with later control APIs

The whole purpose of this slice is to land a core that the **future**
live-runtime adapter can drive via the existing benchmark control surface.
This section is the explicit contract: each control surface the simulated
adapter already implements has a corresponding live-implementation path
**enabled by this core slice** (the code goes in a later slice, but no part
of this slice may block any row below).

### 9.1 Live adapter mechanism per benchmark control surface

| Benchmark control surface | What the live adapter will do (later slice) | What this core slice provides | Verified by |
|---|---|---|---|
| Action `RESTART_CORE_NF` with `nf ∈ {amf, smf, upf, open5gs}` | `docker compose -p open5gs restart <nf>` (or all NFs for `open5gs`); poll until healthy; update `restart_counts` in evidence | Stable compose project name `open5gs`; per-NF split containers; healthchecks that turn green after restart; NRF auto re-registration when an NF returns | acceptance script check 1 (`ps` per service); manual `docker compose -p open5gs restart amf` round-trip |
| Action `UPDATE_CORE_UE_REGISTRATION` (change ue_id/supi/plmn/dnn/sst/sd/auth_profile_id) | `pymongo.MongoClient("mongodb://127.0.0.1:27017/")` connect; upsert `open5gs.subscribers` doc matching the requested ue_id; verify via re-read | Mongo published to `127.0.0.1:27017` (loopback-only); idempotent subscriber schema already known and used by the seeder | acceptance script check 5 (mongo loopback reachable); seeder's own `add_users.py` is the reference impl pattern |
| Stimulus `core_latency_profile` (latency_ms / jitter_ms / loss_rate / degraded_nf) | `docker compose -p open5gs exec <degraded_nf> tc qdisc replace dev eth0 root netem delay <ms> loss <%>`; record evidence including current `tc qdisc show` | `cap_add: [NET_ADMIN]` on AMF/SMF/UPF containers; image alpine-base supports `tc` (gradiant images include iproute2) | manual exec `docker compose -p open5gs exec amf tc qdisc show dev eth0` returns the default `noqueue` qdisc |
| Stimulus `core_ue_registration_misconfig` (mismatch_field, mismatch_value) | same pymongo channel as `UPDATE_CORE_UE_REGISTRATION` but writes a "current" subscriber doc that diverges from the "desired" profile; AMF/AUSF reject UE attach with the stale profile | mongo loopback channel; subscriber doc shape known | covered by the loopback-reachability check |
| Observation source `core_runtime` (running, available_nfs, nf_status, degraded_nf, restart_counts) | `docker compose -p open5gs ps --format json` for running/health; pymongo read for subscribers; internal counters for restart_counts | Same compose-project + mongo-loopback channels above | implied by the other rows; no extra check |

### 9.2 Compose-level constraints derived from §9.1

- **`name: open5gs`** at the top of the compose file (compose project name).
  Without this, `docker compose -p open5gs ...` is ambiguous when multiple
  compose stacks coexist on the same host.
- **Mongo port published**: `127.0.0.1:27017:27017/tcp`. Loopback bind keeps
  the DB off the LAN; the host benchmark process gets a direct channel.
- **`cap_add: [NET_ADMIN]`** on AMF, SMF, UPF — NOT on the other NFs (least
  privilege). If a future stimulus needs to shape NRF or AUSF traffic, add
  the cap there too.
- **No `tty: true`** or **`stdin_open: true`** on any container — exec
  patterns we'll use (`docker compose exec -T ...`) work without a TTY and
  are scriptable.

### 9.3 What's deliberately NOT enabled here

- No write access to compose definitions at runtime. Tasks cannot mutate
  the compose file or the per-NF YAML configs — those are repo-managed
  ground truth.
- No subscriber DELETION via the future control API. The simulated adapter
  only updates / repairs registrations; live should match that constraint
  to avoid drift between simulated and live scoring.
- No NF SCALING (replicas). Open5GS NFs are single-instance by design in
  this stack; the future control API doesn't support scaling and this slice
  doesn't enable it.

## 10. Not in this slice (next-slice notes)

1. **OCUDU gNB Dockerfile + config** — replaces stubs at `docker/ocudu-build.Dockerfile` + `ocudu-zmq-open5gs-e2e/config/gnb_zmq.yaml`. Adds a `gnb` service to a sibling compose; needs PLMN/TAC/slice matching the AMF config.
2. **srsUE Dockerfile + config** — same shape, adds `ue` service.
3. **Compose composition** — either include gNB+UE in the same compose with profiles (`docker compose --profile radio up`), or a sibling compose at `benchmark/provision/ocudu-zmq-open5gs-e2e/compose/docker-compose.e2e.yml` that extends networks + targets the same `ran` network.
4. **Live transport** — `benchmark/benchmark_api/live_ocudu.py` (real WebSocket + CLI + JSON metrics).
5. **Dispatcher fork** — branch in `ran_api.dispatch_runtime_action` on `runtime.runtime_adapter == "ocudu_live"`.
6. **Task manifest variants** — add `<task>_v1_ocudu_live` variants for at least one task per family to prove the live path.
7. **FlexRIC** — separate, larger slice; not blocked by this one.

## 10. Risks

- Upstream `gradiant/open5gs:2.7.0` per-NF tag availability — if any NF's tag is missing, fall back to building from source for that NF only (single-Dockerfile multi-stage from D2's rejected option B).
- Open5GS schema drift — the YAML schema is version-pinned to 2.7.0; bumping the image tag may require config updates. Pin the image tags exactly; don't use `:latest`.
- `nvidia-container-toolkit` is installed on the target but Open5GS does not use GPUs; no GPU runtime needed for this slice.
- The standard test triplet (`001010000000001` + K=`0011..eeff` + OPc=`63bf..737d`) is a public well-known value; safe to commit; explicitly **not** a real subscriber.
