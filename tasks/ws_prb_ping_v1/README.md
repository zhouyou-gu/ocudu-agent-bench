# `ws_prb_ping_v1`

## Purpose

This is the first scored OCUDU agent task. It validates that an agent can issue WebSocket PRB policy actions while a Docker-based OCUDU/Open5GS/srsUE episode carries live UE ping traffic and JSON metrics.

## Runtime Stack

- Open5GS core from the configured compose file.
- OCUDU gNB from the workspace-owned OCUDU install tree.
- srsUE from the workspace-owned srsRAN 4G UE install tree.
- ZMQ RF emulation between gNB and UE.
- UE ping traffic to `10.45.1.1`.
- OCUDU WebSocket remote control on port `8001`.

## Required Conformance

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`
- `websocket_prb_policy_action`

## Action Contract

Allowed action type:

- `SET_PRB_POLICY_RATIO_WS`

The action sets min/max PRB policy ratios through OCUDU's WebSocket `rrm_policy_ratio_set` command. Min/max ratios are scored; `dedicated_ratio` is accepted only as a validity field in this task.

## Observations

Observation frames include:

- Run state and task id.
- Ping counters and success ratio.
- JSON metrics presence, component keys, timestamp, raw frame, and errors.
- Backend status for WebSocket, ping, and metrics.
- Last action validation and dispatch result.

## Scoring

Scored dimensions:

- Accepted valid action rate.
- Invalid local rejection correctness.
- Ping success ratio.
- JSON metrics continuity.
- Clean teardown success.

Setup, conformance, runtime, or cleanup failures make the run unscored.

## CLI Example

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_ping_v1 \
  --agent fixed_prb \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id ws-prb-smoke \
  --json
```

## Artifacts

Expected remote artifacts under `<remote.workspace>/runs/<run_id>/episode/`:

- `actions.jsonl`
- `observations.jsonl`
- `metrics_raw.jsonl`
- `summary.json`
- `logs/gnb.log`
- `logs/ue.log`
- `logs/ping.log`
- `logs/core.log`

## Limitations

This task uses one UE and ping traffic only. It verifies the control loop and runtime health, not PRB fairness or throughput impact.
