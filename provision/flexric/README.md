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
| flexric + ocudu-gnb-ue + open5gs-aio | `live_e2` | `E2_KPM_V05` evidence (`RRU.PrbAvail/Used/TotDl`) + `SET_PRB_POLICY_RATIO_RC_DU` (RAN func 3) + `SET_PRB_POLICY_RATIO_CCC` (RAN func 4). |

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
| `skillful-ran/flexric-bench:br-flexric-1a3903a7-kpm-v5-ocudu-26_04` | 1.37 GB | Original locally-built image (Phase 2a baseline). Built on 5090pc from the FlexRIC fork at [github.com/zhouyou-gu/flexric-ocudu-kpm-v05](https://github.com/zhouyou-gu/flexric-ocudu-kpm-v05) (pinned in `.config` under `sources.flexric-ocudu-repo`). Carries OCUDU KPM v05 + custom OCUDU-specific control xApps (`examples/xApp/c/control/ocudu_ccc_prb_control.c` + `ocudu_rc_du_prb_control.cpp`) and patches FlexRIC core (`src/xApp/{e42_xapp.c, msg_handler_xapp.c, sync_ui.c}`) to return structured E2 control failures instead of crashing. |
| **`skillful-ran/flexric-bench:patch-control-failure`** | 1.37 GB | **Used by current compose (Phase 2b).** Same image + two more patches on a local branch `patch-control-failure-decoder`: removes the `"Untested code"` assert in `e2ap_msg_dec_asn.c:1284` so RIC Control Failures decode, and implements `e2ap_handle_control_failure_ric` at `msg_handler_ric.c:370`. Branch not yet pushed to the GitHub fork. |

## Phase 2b — E2 control (both live)

### SET_PRB_POLICY_RATIO_RC_DU ✅ live

Dispatched via `live_e2.dispatch_rc_du_prb_policy`, which runs the
fork's `ocudu_rc_du_prb_control` xApp inside this container:

```python
from benchmark.benchmark_api import live_e2
cfg = live_e2.LiveE2Config()
result = live_e2.dispatch_rc_du_prb_policy(
    cfg, du_ue_id=0, plmn="00101", sst=1, sd=0xFFFFFF,
    min_prb_policy_ratio=30, max_prb_policy_ratio=70,
)
# {"accepted": true, "action_type": "SET_PRB_POLICY_RATIO_RC_DU",
#  "ran_function_id": 3, "control_style": 2, "control_action": 6,
#  "outcome": {"acknowledged": true,
#              "evidence": "OCUDU E2SM-RC control acknowledged"}}
```

`du_ue_id` **must be the gNB-assigned DU UE ID** (typically `0` for
the first attached UE), NOT the RNTI (`0x4601`). Wrong `du_ue_id`
triggers the gNB''s `RICcontrolFailure` path which, even with the two
FlexRIC patches in the rebuilt image, still asserts in a deeper layer
(the iApp→xApp forwarding code). The happy path is fully robust; the
failure path crashes the RIC and needs another patch. For benchmark
dispatch with well-formed actions this is a non-issue.

### SET_PRB_POLICY_RATIO_CCC ✅ live

Dispatched via `live_e2.dispatch_ccc_prb_policy`, which runs the
fork's `ocudu_ccc_prb_control` xApp inside this container. CCC is
cell-level (no `du_ue_id`); it carries an optional `dedicated_ratio`
that RC-DU does not:

```python
from benchmark.benchmark_api import live_e2
cfg = live_e2.LiveE2Config()
result = live_e2.dispatch_ccc_prb_policy(
    cfg, plmn="00101", sst=1, sd=0xFFFFFF,
    min_prb_policy_ratio=30, max_prb_policy_ratio=70,
    dedicated_ratio=50,
)
# {"accepted": true, "action_type": "SET_PRB_POLICY_RATIO_CCC",
#  "ran_function_id": 4, "control_name": "O-RRMPolicyRatio",
#  "control_style": 2, "control_action": 6,
#  "outcome": {"acknowledged": true,
#              "evidence": "FlexRIC E2SM-CCC control acknowledged"}}
```

The earlier "OCUDU gNB SIGSEGV on `e2sm_ccc_enabled: true`" claim was
wrong. `ocudu/gnb:latest` (commit `2563975`) carries a working CCC DU
implementation (`e2sm_ccc_impl` + `e2sm_ccc_control_service_style_2` +
`e2sm_ccc_control_o_rrm_policy_ratio_executor`, wired in
`lib/e2/common/e2_du_factory.cpp`); the gnb stays stable, E2 SETUP
accepts RAN function ID 4 (`ORAN-E2SM-CCC`), and the gnb logs
`E2SM-CCC: O-RRMPolicyRatio Control Request` when the xApp dispatches.

## Rebuilding the patched FlexRIC image

```bash
export OCUDU_ASN1_ROOT=~/ocudu-gpu-channel-workspace/ocudu
cd ~/skillful-ran-workspace/.benchmark-workspace/external/flexric-ocudu-kpm-v05
git checkout patch-control-failure-decoder
BUILD_CONTEXT=$(./tools/prepare_ocudu_kpm_v05_context.sh)
docker build -f $BUILD_CONTEXT/flexric/docker/ocudu-kpm-v05/Dockerfile \
    --build-arg FLEXRIC_REF=patch-control-failure-decoder \
    -t skillful-ran/flexric-bench:patch-control-failure \
    $BUILD_CONTEXT
```

Build takes ~3 min with warm ccache, ~10 min cold.
