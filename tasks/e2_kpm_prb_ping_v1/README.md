# `e2_kpm_prb_ping_v1`

## Purpose

This task extends `ws_prb_ping_v1` with a standards-facing E2 observation path. The agent still controls OCUDU through WebSocket PRB policy actions, but the episode is scored only when Dockerized FlexRIC and the KPM xApp produce decoded E2SM-KPM v05 records.

## Runtime Stack

- Open5GS core from the configured compose file.
- OCUDU gNB with E2 and JSON metrics overlay enabled.
- srsUE with ZMQ RF emulation.
- Dockerized FlexRIC Near-RT RIC.
- KPM monitor xApp built for OCUDU E2SM-KPM v05.
- UE ping traffic to `10.45.1.1`.
- OCUDU WebSocket remote control on port `8001`.
- E2 connection on port `36421`.

## Required Conformance

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`

## Action Contract

Allowed action type:

- `SET_PRB_POLICY_RATIO_WS`

The action path is intentionally the same as `ws_prb_ping_v1`. E2 RC and CCC control actions are not part of this task.

## Observations

Observation frames include all `ws_prb_ping_v1` fields plus:

- RIC connection status.
- KPM indication count.
- Last decoded KPM record.
- PRB measurement evidence.
- xApp status.
- E2 PCAP/log oracle status.

Agents should treat E2 fields as optional unless the backend status says E2 KPM is available.

## Scoring

Scored dimensions:

- Accepted valid action rate.
- Invalid local rejection correctness.
- Ping success ratio.
- JSON metrics continuity.
- E2 KPM continuity.
- E2 oracle availability.
- Clean teardown success.

The run is unscored if decoded E2SM-KPM v05 records are unavailable or if oracle artifacts are missing.

## CLI Example

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_kpm_prb_ping_v1 \
  --agent fixed_prb \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-kpm-smoke \
  --json
```

## Artifacts

Expected remote artifacts under `<remote.workspace>/runs/<run_id>/episode/`:

- `actions.jsonl`
- `observations.jsonl`
- `metrics_raw.jsonl`
- `e2_kpm_raw.jsonl`
- `e2_oracle.json`
- `summary.json`
- `logs/gnb.log`
- `logs/ue.log`
- `logs/ping.log`
- `logs/core.log`
- `logs/ric.log`
- `logs/kpm_xapp.log`
- E2 PCAP/log oracle files when capture is enabled.

## Limitations

This task uses E2 KPM for observation only. PRB control remains WebSocket-based; E2 RC and CCC control paths are reserved for later tasks.
