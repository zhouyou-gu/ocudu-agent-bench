# `ws_prb_action_budget_v1`

## Goal

Evaluate whether an LLM agent can complete a PRB control objective with at most one valid action and no repeated control churn.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | OCUDU WebSocket remote control action `SET_PRB_POLICY_RATIO_WS` |
| Observation | UE ping counters, OCUDU JSON metrics, WebSocket backend status, last action result |
| Oracle | Action count, accepted action record, JSON metrics continuity, ping success ratio, cleanup result |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, remote artifact writer |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_action_budget_v1 \
  --controller fixed_prb \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id ws-prb-budget-smoke \
  --json
```

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent observes the healthy episode, emits one valid PRB policy action, confirms the action result, and then returns `None` for subsequent observations.

## Allowed Actions

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

The action budget is one total logged action.

## Observation Contract

Observations include ping counters, JSON metrics presence and freshness, backend status, and last action result. Agents should use the last action result to stop after the first accepted command.

## Scoring

Canonical score dimensions:

- `valid_action_accepted_rate`
- `action_budget_ok`
- `ping_success_ratio`
- `metrics_continuity`
- `clean_teardown`

The task scores one accepted valid action and marks down invalid actions or repeated control churn.

## Unscored Conditions

Setup, conformance, runtime launch, missing ping replies, missing JSON metrics, WebSocket backend failure, or cleanup failure can make the run unscored. Exceeding the action budget after setup is an agent behavior failure.

## Required Conformance

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`
- `websocket_prb_policy_action`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and `logs/`.

## Limitations

The score measures action economy and safety, not throughput improvement.
