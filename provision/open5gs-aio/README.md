# Open5GS 5G core (all-in-one)

Self-contained Open5GS 5G core where a single `5gc` test binary runs every
NF in-process (NRF, SCP, AUSF, UDM, UDR, AMF, SMF, UPF, PCF, BSF, NSSF).
SBI routing between NFs is all over loopback (`127.0.0.x:7777`); only the
AMF NGAP listener (`10.53.1.2:38412`) and UPF GTPU (`10.53.1.2:2152`) bind
to the docker network so a gNB can reach them.

## When to use this vs the split-NF stack

| Stack | Use when | Trade-off |
| --- | --- | --- |
| **AIO** (this dir) | Running the OCUDU gNB + srsUE attach smoke ([benchmark/provision/ocudu-gnb-ue/](../ocudu-gnb-ue/)). The in-process SCP routing is the validated path for end-to-end NAS auth with srsUE 23.11. | No per-NF restart granularity. Mongo lives inside the 5gc container, not host-exposed. `live_core` adapter does NOT target this stack. |
| **Split-NF** ([open5gs-core/](../open5gs-core/)) | Exercising the `live_core` adapter (`RESTART_CORE_NF`, `UPDATE_CORE_UE_REGISTRATION`, per-NF metrics). Validated by the 19/19 smoke. | UE attach NAS auth is not currently working — multi-NF SBI routing in `gradiant/open5gs:2.7.7` rejects with "Invalid API name". |

The two stacks **cannot run simultaneously** — both bind AMF SCTP `38412`
on the host and use compose project name `open5gs`. Bring down one before
the other goes up.

## Bring up

```bash
docker compose -f compose/docker-compose.open5gs-aio.yml up -d
bash tests/check_aio_ready.sh
# expected: "open5gs aio core ready"
```

First-time bring-up: ~30 s (internal mongod start, 11 NF spawn, NRF
heartbeat round-trip).

## Tear down

```bash
docker compose -f compose/docker-compose.open5gs-aio.yml down -v
```

## Image dependency

`skillful-ran/open5gs:v2.7.0` (~3.25 GB, locally built on 5090pc). Built
from upstream Open5GS sources via the `ocudu-gpu-channel-workspace`
reference Dockerfile. Includes:

* All Open5GS NF binaries at `/open5gs/install/bin/`
* The `5gc` monolithic test binary at `/open5gs/build/tests/app/5gc`
* The `open5gs_entrypoint.sh` that creates `127.0.0.x` dummy interfaces,
  spawns internal mongod, runs `envsubst` on the YAML template, seeds the
  default subscriber from `SUBSCRIBER_DB`, then exec's the 5gc binary

A portable Docker Hub rebuild is a follow-up.

## Config template

`compose/configs/open5gs-5gc.yml.in` is the YAML template the entrypoint
runs through `envsubst`. Recognized variables (all set in the compose):

| var | value here | what |
| --- | --- | --- |
| `OPEN5GS_IP` | `10.53.1.2` | AMF NGAP + UPF GTPU bind |
| `UPF_ADVERTISE_IP` | `10.53.1.2` | UPF GTPU advertise to SMF |
| `MONGODB_IP` | `127.0.0.1` | internal mongod |
| `UE_IP_BASE` | `10.45.0` | becomes `UE_IP_RANGE=10.45.0.0/24`, `UE_GATEWAY_IP=10.45.0.1` |
| `NETWORK_NAME_FULL/SHORT` | `Open5GS` | NAS info to UE |
| `SUBSCRIBER_DB` | `001010000000001,...` | seeds the test UE matching ocudu-gnb-ue/configs/ue_zmq.conf |

## Seeded subscriber

`SUBSCRIBER_DB` env var format (per the image's `add_users.py`):
`IMSI,K,OPc_marker,OPc_value,AMF,QCI,UE_IP`. The current value matches
the UE in [`ocudu-gnb-ue/compose/configs/ue_zmq.conf`](../ocudu-gnb-ue/compose/configs/ue_zmq.conf):

```
IMSI:  001010000000001
K:     00112233445566778899aabbccddeeff
OPc:   63bfa50ee6523365ff14c1f45f88737d
AMF:   8000
QCI:   9
UE IP: 10.45.0.2
DNN:   srsapn  (script default; aligns with UE [nas].apn)
```

To add more subscribers, either extend the `add_users.py` invocation or
switch to mounting a CSV (the split-NF stack's seeder pattern).

## Not in this slice

* Per-NF restart granularity (use the split-NF stack for that)
* Host-exposed mongo (`127.0.0.1:27017` is not published; mongo runs inside
  the 5gc container)
* `live_core` adapter integration (the split-NF stack is the live_core target)
