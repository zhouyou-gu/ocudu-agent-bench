# Open5GS 5G core (split-NF)

Real, deployable Open5GS 5G core for the OCUDUAgentBench live-runtime path.
Brings up 11 containers (`mongo` + 10 Open5GS NF instances of the upstream
monolithic `gradiant/open5gs:2.7.7` image, each invoked with its own NF
binary via `command:`) + a one-shot subscriber seeder.

For the why behind the choices (split-NF via per-service `command:`, mongo
on loopback, NET_ADMIN cap on AMF/SMF, `privileged: true` on UPF for the
upstream entrypoint's sysctl call, etc.), see
[`benchmark/docs/specs/2026-05-24-open5gs-core-design.md`](../../docs/specs/2026-05-24-open5gs-core-design.md).

## Bring up

```bash
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml up -d
```

`docker compose ps` should show all 11 services `running` (10 NFs + mongo)
plus `open5gs-subscriber-seeder` as `exited (0)` within ~30 s.

## Verify

```bash
bash benchmark/provision/open5gs-core/tests/check_core_ready.sh
# expected: "open5gs core ready"
```

The script runs five checks: all services healthy, AMF SCTP 38412 bound,
AMF registered with NRF, seeded subscriber queryable, mongo reachable on
`127.0.0.1:27017`.

## Tear down

```bash
docker compose -f benchmark/provision/open5gs-core/compose/docker-compose.open5gs.yml down -v
```

The `-v` removes the `mongo-data` volume; subscribers re-seeded on next bring-up.

## Static IP map

A future OCUDU gNB attaches to AMF NGAP at `10.53.1.2:38412` (or via host
`38412/sctp`).

| NF | Static IP | Listens on |
| --- | --- | --- |
| `mongo` | `10.53.1.10` | `27017/tcp` (also published to host `127.0.0.1:27017`) |
| `amf` | `10.53.1.2` | `7777/tcp` (SBI), `38412/sctp` (NGAP; published to host `38412/sctp`) |
| `smf` | `10.53.1.3` | `7777/tcp` (SBI), `8805/udp` (PFCP), `2152/udp` (GTP-U) |
| `nrf` | `10.53.1.4` | `7777/tcp` (SBI) |
| `scp` | `10.53.1.5` | `7777/tcp` (SBI) |
| `ausf` | `10.53.1.6` | `7777/tcp` (SBI) |
| `udm` | `10.53.1.7` | `7777/tcp` (SBI) |
| `udr` | `10.53.1.8` | `7777/tcp` (SBI) |
| `pcf` | `10.53.1.9` | `7777/tcp` (SBI), `9090/tcp` (metrics) |
| `bsf` | `10.53.1.11` | `7777/tcp` (SBI) |
| `upf` | `10.53.1.12` | `8805/udp` (PFCP), `2152/udp` (GTP-U), `9090/tcp` (metrics) |

## Seeded UE

The seeder loads `compose/seed/subscriber_db.csv`. Default content is one
test UE matching the standard srsRAN/Open5GS ZMQ triplet (IMSI
`001010000000001`, K `0011..eeff`, OPc `63bf..737d`, PLMN `00101`, DNN
`internet`, SST 1, no SD). Add more rows to the CSV and re-run
`docker compose up -d --build subscriber-seeder` to upsert them.

## Future control APIs enabled by this slice

Per spec §9, this slice enables (but does not implement) the following
benchmark control surfaces in the future live transport:

* `RESTART_CORE_NF` — `docker compose -p open5gs restart <nf>`
* `UPDATE_CORE_UE_REGISTRATION` — pymongo upsert via `127.0.0.1:27017`
* `core_latency_profile` stimulus — `tc netem` inside AMF/SMF/UPF via NET_ADMIN
* `core_ue_registration_misconfig` stimulus — pymongo write of mismatched subscriber
* `core_runtime` observation — `docker compose -p open5gs ps --format json` + pymongo read

## Not in this slice

OCUDU gNB, srsUE, FlexRIC, the live transport code in
`benchmark/benchmark_api/`, and any task manifests declaring
`E.runtime_adapter = ocudu_live` are explicitly out of scope. See spec §10.
