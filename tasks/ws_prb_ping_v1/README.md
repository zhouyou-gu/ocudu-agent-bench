# `ws_prb_ping_v1`

## Agent Goal

Evaluate whether an LLM agent can issue a valid OCUDU WebSocket PRB policy action while a one-UE Docker/ZMQ episode remains healthy.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | OCUDU WebSocket remote control action `SET_PRB_POLICY_RATIO_WS` |
| Observation | UE ping counters, OCUDU JSON metrics, WebSocket backend status, last action result |
| Oracle | Local action log, JSON metrics continuity, ping success ratio, cleanup result |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, remote artifact writer |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_ping_v1 \
  --controller fixed_prb \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id ws-prb-smoke \
  --json
```

The suite runner performs the required conformance gate before scored episodes unless `--skip-conformance` is used for debugging, which makes the run unscored.

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent calls `reset`, observes ping and JSON metrics, emits one bounded PRB policy action, observes the action result, and then stops acting. A custom LLM agent should use `None` when it has no further action.

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

The action is translated to OCUDU's WebSocket `rrm_policy_ratio_set` command. `min_prb_policy_ratio` and `max_prb_policy_ratio` must be integers in `[0, 100]` with `min <= max`.

## Observation Contract

Observations include run state, task id, ping counters, JSON metrics presence and freshness, backend status, and the most recent local validation or WebSocket dispatch result. Agents should tolerate missing raw metric subfields and use backend status before acting.

## Task Scoring

Canonical score dimensions:

- `valid_action_accepted_rate`
- `invalid_local_rejection_correctness`
- `ping_success_ratio`
- `metrics_continuity`
- `clean_teardown`

The task scores one accepted valid PRB action while ping and metrics remain healthy.

## Unscored Conditions

Setup, conformance, runtime launch, missing ping replies, missing JSON metrics, WebSocket backend failure, or cleanup failure can make the run unscored. After setup succeeds, malformed or excessive actions are agent behavior failures.

## Required Conformance

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`
- `websocket_prb_policy_action`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and `logs/`.

## Limitations

This task verifies the control loop and runtime health. One UE with ping traffic is not a PRB fairness or throughput benchmark.
