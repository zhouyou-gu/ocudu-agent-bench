# OCUDU gNB + srsUE (software-only ZMQ)

Real, deployable OCUDU gNB + srsUE pair that attaches to an Open5GS 5G core
on the same docker network and proves end-to-end NAS + PDU session + ping.

Joins the `open5gs_ran` network created by a core stack:

* gNB at `10.53.1.20` → AMF NGAP at `10.53.1.2:38412`
* UE at `10.53.1.21`, ZMQ tx/rx ports `2000`/`2001` paired with the gNB
* UE seeded with IMSI `001010000000001` (standard srsRAN ZMQ test triplet)

## Bring up

A core stack must be running first. Two choices, pick one per session:

* **All-in-one core** ([`benchmark/provision/open5gs-aio/`](../open5gs-aio/))
  — the validated path for this attach test. NAS auth + PDU session + ping
  all work end-to-end.
* **Split-NF core** ([`benchmark/provision/open5gs-core/`](../open5gs-core/))
  — gNB reaches AMF (NGAP) fine, but the multi-NF SBI routing in the
  `gradiant/open5gs:2.7.7` split image does not currently complete NAS auth
  for srsUE 23.11. Use this stack for the `live_core` adapter slice
  ([live_core.py](../../benchmark_api/live_core.py)) which exercises core
  control surfaces, NOT for UE attach.

```bash
# Bring up the AIO core
docker compose -f ../open5gs-aio/compose/docker-compose.open5gs-aio.yml up -d
bash ../open5gs-aio/tests/check_aio_ready.sh   # "open5gs aio core ready"

# Bring up gNB + UE against it
docker compose -f compose/docker-compose.gnb-ue.yml up -d
```

## Verify

```bash
bash tests/check_attach_ping.sh
# expected: "ocudu gnb + srsue attached and ping ok"
```

Five checks:

1. gNB container running
2. gNB log shows `Connected to AMF.` (NGAP setup)
3. UE container running
4. UE log shows `PDU Session Establishment successful.`
5. UE pings `10.45.0.1` (SMF gateway) from inside netns `ue1`

## Tear down

```bash
docker compose -f compose/docker-compose.gnb-ue.yml down
docker compose -f ../open5gs-aio/compose/docker-compose.open5gs-aio.yml down -v
```

## Image dependencies (locally built on 5090pc)

| Image | Size | Purpose |
| --- | --- | --- |
| `ocudu/gnb:latest` | 3.34 GB | OCUDU gNB binary (release `2563975`) |
| `gnb-srsue-direct/srsue:release_23_11` | (varies) | srsUE 23.11 runtime with `srsue` at `/usr/local/bin/srsue` |

Neither image is currently on Docker Hub. A portable rebuild from OCUDU /
srsRAN_4G upstream is a follow-up. Until then, this slice runs only on
hosts where both images are cached.

## Config alignment

| Property | Value | Set by |
| --- | --- | --- |
| PLMN | `00101` | gNB cell_cfg + UE usim + AMF amf.yaml + subscriber |
| TAC | `1` | gNB cell_cfg.tac + AMF tai.tac |
| SST | `1` | gNB tai_slice_support_list + AMF plmn_support + subscriber slice |
| DNN / APN | `srsapn` | UE [nas].apn + SMF info.dnn + subscriber slice (default from image's `add_users.py`) |
| IMSI | `001010000000001` | UE usim.imsi + subscriber doc |
| K / OPc | standard srsRAN ZMQ triplet | UE usim + subscriber doc |
| UE IP / gateway | `10.45.0.2` / `10.45.0.1` | SMF session subnet + UE allocation |

Any drift in any of these breaks attach.

## Known sharp edges

* The gNB requires `privileged: true` (not just `cap_add: NET_ADMIN`) — its
  initialization path touches `epoll_ctl` on stdin and tries operations
  that need broader capabilities.
* The srsUE image has `Entrypoint:[srsue]` so the compose `command:` would
  become `srsue /bin/bash -c ...` without an `entrypoint:` override. The
  compose overrides to `[/bin/bash, -c]`.
* srsUE rejects `;`-prefixed INI comments — `#` only. All comments in
  `ue_zmq.conf` use `#`.
* OCUDU writes logs to a file (`/tmp/gnb.log` per `log.filename`), NOT
  stdout. The acceptance script reads the file via `docker compose exec`.
* If the gNB+UE compose fails to start because of a missing source for a
  bind-mounted file, Docker creates an empty root-owned directory at the
  mount target path. `git reset --hard` then fails with `cannot rmdir`.
  Fix by running `docker run --rm -v $(pwd):/work alpine rm -rf /work/<path>`
  before resyncing.

## OCUDU remote control WebSocket (port 8001)

The gNB compose enables `remote_control` and publishes port 8001 to host
loopback. The [`live_ocudu`](../../benchmark_api/live_ocudu.py) adapter
connects to `ws://127.0.0.1:8001/` to dispatch actions and read metrics.

Set `E.runtime_adapter = "live_ocudu"` in a task manifest to route
actions through this transport:

| Action type | Routed live? | Transport |
|---|---|---|
| `SET_PRB_POLICY_RATIO_WS` | ✅ via `rrm_policy_ratio_set` | WebSocket port 8001 |
| `SET_SSB_BLOCK_POWER_WS` | ✅ via `ssb_set` | WebSocket port 8001 |
| `TRIGGER_HANDOVER_CLI` | ✅ via `ho <svp> <rnti> <tp> <pl> <tac>` | stdin FIFO (`/tmp/gnb_stdin`) |
| `TRIGGER_CONDITIONAL_HANDOVER_CLI` | ✅ via `cho <svp> <rnti> <tp1> [...] [timeout <s>]` | stdin FIFO |
| `SET_CFO_CLI` | ✅ via `cfo <sector> <hz>` | stdin FIFO |
| `SET_TX_TIME_OFFSET_CLI` | ✅ via `tx_time_offset <sector> <us>` | stdin FIFO |

OCUDU's remote_control WebSocket only registers `rrm_policy_ratio_set`,
`ssb_set`, `metrics_subscribe`, `metrics_unsubscribe`, `quit`. The four
cmdline_command actions (`ho`, `cho`, `cfo`, `tx_time_offset`) live in
`*_cmdline_commands.h` and are stdin-only.

The compose wrapper enables a stdin path:
`touch /tmp/gnb_stdin && stdbuf -oL tail -F /tmp/gnb_stdin | gnb -c <cfg>`.
The adapter writes commands via `docker exec ocudu-gnb sh -c "echo '<line>' >> /tmp/gnb_stdin"`,
then scans `docker logs` for `Invalid `/`Usage:` lines (success-silent
commands are reported as accepted by default).

Caveat: gnb's argument parsers are lenient on type errors (e.g.,
`cfo abc xyz` coerces to `cfo 0 0` silently). The adapter can only
detect failures gnb actually emits — well-formed but semantically
wrong inputs may show as accepted.

Metrics evidence reads use `live_ocudu.read_metrics(cfg, count=N)`.

## Not in this slice

* FlexRIC / E2 control (live_e2 adapter for `SET_PRB_POLICY_RATIO_CCC`,
  `SET_PRB_POLICY_RATIO_RC_DU`, `E2_KPM` observation)
* Multiple UEs, mobility, handover scenarios
* Portable (Docker Hub) rebuilds of `ocudu/gnb` and `srsue`
