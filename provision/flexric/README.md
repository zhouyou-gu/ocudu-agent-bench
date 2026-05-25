# FlexRIC (nearRT-RIC) — E2 KPM v05 observation

Self-contained FlexRIC deployment that joins the existing `open5gs_ran`
docker network, accepts E2 connections from the OCUDU gNB on SCTP 36421,
and continuously writes decoded **OCUDU E2SM-KPM v05** indication records
as JSONL to a volume the host can read via `docker exec`.

The [`live_e2`](../../benchmark_api/live_e2.py) adapter tails this JSONL
to refresh `E2_KPM_V05` evidence on every observation when a task sets
`E.runtime_adapter = "live_e2"`.

## When to use vs the other live adapters

| Stack | Adapter | Live surface |
|---|---|---|
| open5gs-core (split-NF) | `live_core` | `RESTART_CORE_NF`, `UPDATE_CORE_UE_REGISTRATION`, `core_runtime` evidence |
| ocudu-gnb-ue + open5gs-aio | `live_ocudu` | `SET_PRB_POLICY_RATIO_WS`, `SET_SSB_BLOCK_POWER_WS` (WS), `TRIGGER_HANDOVER_CLI`, `TRIGGER_CONDITIONAL_HANDOVER_CLI`, `SET_CFO_CLI`, `SET_TX_TIME_OFFSET_CLI` (stdin) |
| flexric + ocudu-gnb-ue + open5gs-aio | `live_e2` | `E2_KPM_V05` evidence (`RRU.PrbAvail/Used/TotDl`). E2 control (CCC/RC) deferred to Phase 2b. |

The three adapters are mutually exclusive at a task level — `runtime_adapter`
is single-valued. Choose based on which actions/observations the task needs.

## Bring up

The flexric compose depends on `open5gs_ran` (the network created by the AIO
core stack). Order:

```bash
# 1. AIO open5gs core
docker compose -f benchmark/provision/open5gs-aio/compose/docker-compose.open5gs-aio.yml up -d
bash benchmark/provision/open5gs-aio/tests/check_aio_ready.sh

# 2. FlexRIC (starts RIC + monitor xApp loop)
docker compose -f benchmark/provision/flexric/compose/docker-compose.flexric.yml up -d

# 3. OCUDU gNB + srsUE (will E2-connect to flexric)
docker compose -f benchmark/provision/ocudu-gnb-ue/compose/docker-compose.gnb-ue.yml up -d
bash benchmark/provision/ocudu-gnb-ue/tests/check_attach_ping.sh
```

Within ~30 s after the gNB attaches, `docker exec flexric-ric wc -l /var/log/flexric/kpm.jsonl`
should show records growing at ~1/s (one per E2 node per second).

## Tear down

```bash
docker compose -f benchmark/provision/ocudu-gnb-ue/compose/docker-compose.gnb-ue.yml down
docker compose -f benchmark/provision/flexric/compose/docker-compose.flexric.yml down -v
docker compose -f benchmark/provision/open5gs-aio/compose/docker-compose.open5gs-aio.yml down -v
```

The `-v` on flexric removes the `flexric-kpm` volume that holds the JSONL.

## Configs

* `compose/configs/ric.conf` — nearRT-RIC bind on `0.0.0.0` so the gNB
  outside the container can reach E2 SCTP 36421 / E42 36422.
* `compose/configs/xapp_oran_sm.conf` — KPM subscription request. The
  comment block in that file documents why only 3 DL measurements
  (`RRU.PrbAvail/Used/TotDl`) and only `ngran_gNB_DU` scope: adding any
  UL measurement or `DRB.*` measurement causes OCUDU's KPM v05 DU
  provider to reject the subscription, which crashes this FlexRIC build
  via an unimplemented failure-handler assertion.

## OCUDU gNB e2: config alignment

The OCUDU gNB at [`benchmark/provision/ocudu-gnb-ue/compose/configs/gnb_zmq.yaml`](../ocudu-gnb-ue/compose/configs/gnb_zmq.yaml)
has an `e2:` block configured for this stack:

```yaml
e2:
  enable_cu_cp_e2: true
  enable_cu_up_e2: false   # this FlexRIC build asserts on E1 comp type
  enable_du_e2: true
  addrs: 10.53.1.30
  port: 36421
  bind_addrs: 10.53.1.20
  e2sm_kpm_enabled: true
  e2sm_rc_enabled: true
```

`enable_cu_up_e2: false` is essential — the CU-UP E2 agent's E2 SETUP
carries an E1 component type that this FlexRIC build does not implement;
it crashes the RIC.

## Sharp edges

* The FlexRIC build is `skillful-ran/flexric-bench:br-flexric-1a3903a7-kpm-v5-ocudu-26_04`,
  a locally-built image on 5090pc. Not on Docker Hub. Rebuilding from
  upstream FlexRIC requires the OCUDU ASN.1 sources — see
  `OCUDU_KPM_V05.md` inside the image.
* nearRT-RIC's `e2ap_handle_subscription_failure_ric` is unimplemented:
  ANY subscription rejection crashes the RIC. The xApp restart loop will
  keep starting xApps but a failed subscription will repeatedly take
  down the container.
* The wrapper waits 30 s after RIC start before launching the xApp to
  give the gNB time to establish E2 SETUP. Adjust if the stack startup
  ordering changes.
* All output goes to `docker logs flexric-ric` (no file redirection)
  so crashes leave a usable diagnostic.

## Image dependency

| Image | Size | Source |
|---|---|---|
| `skillful-ran/flexric-bench:br-flexric-1a3903a7-kpm-v5-ocudu-26_04` | 1.37 GB | Locally built on 5090pc from FlexRIC commit `1a3903a7` patched for OCUDU KPM v05; portable rebuild is a follow-up. |

## Phase 2b — E2 control (not in this slice)

The compose ships the monitor xApp binary; the control xApps for CCC
(`SET_PRB_POLICY_RATIO_CCC`) and RC (`SET_PRB_POLICY_RATIO_RC_DU`) have
C source at `/opt/flexric/examples/xApp/c/{control,kpm_rc}/` inside the
image but no prebuilt binaries. Wiring those needs:

1. Build the control xApp binaries (extend the image's Dockerfile or
   build on first compose-up via a multi-stage build).
2. Sidecar daemon that exposes a TCP RPC to receive
   `SET_PRB_POLICY_RATIO_CCC` / `SET_PRB_POLICY_RATIO_RC_DU` payloads
   and forwards via the xApp's control_*_sm API.
3. Wire into `live_e2.dispatch_action(...)` + `ran_api` dispatch branch.
