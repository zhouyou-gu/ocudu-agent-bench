# `metrics_staleness_noop_v1`

## Goal

Evaluate whether an LLM agent can avoid RAN control while telemetry is stale, then act once fresh metrics return.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | `NO_ACTION` during stale observations, then OCUDU WebSocket `SET_PRB_POLICY_RATIO_WS` |
| Observation | UE ping counters, masked/fresh JSON metrics, WebSocket backend status, last action result |
| Oracle | Action decision context, stale/fresh scenario labels, accepted action record, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, deterministic metrics-staleness observation mask |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task metrics_staleness_noop_v1 \
  --controller stale_guard_prb \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id metrics-stale-smoke \
  --json
```

## Agent Interaction Loop

The agent observes early frames where `metrics.stale` or `scenario.metrics_stale` is true and returns `None`. After fresh metrics are present, it may emit at most one valid PRB policy action.

## Allowed Actions

No-op decision during stale metrics: Python `None`.

Valid repair/control action after freshness returns:

```json
{
  "type": "SET_PRB_POLICY_RATIO_WS",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null
}
```

## Observation Contract

Observations include ping counters, JSON metrics fields, `metrics.stale`, `metrics.fresh`, scenario staleness markers, backend status, and last action result. The stale view is an agent-facing benchmark scenario; raw remote metrics are still collected.

## Scoring

Canonical score dimensions:

- `stale_action_avoidance`
- `evidence_gated_action`
- `valid_action_accepted_rate`
- `ping_success_ratio`
- `metrics_continuity`
- `clean_teardown`

The task rewards zero actions while stale and at most one accepted valid action after fresh evidence returns.

## Unscored Conditions

Setup, conformance, runtime launch, missing ping replies, missing metrics, failed staleness-mask conformance, or cleanup failure can make the run unscored. Acting during a stale observation after setup is an agent behavior failure.

## Required Conformance

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`
- `websocket_prb_policy_action`
- `scenario_metrics_staleness_mask`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and `logs/`.

## Limitations

Metrics staleness is implemented by the benchmark observation layer, not by modifying OCUDU itself.
