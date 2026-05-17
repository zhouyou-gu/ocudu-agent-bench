# `ws_prb_error_repair_v1`

## Goal

Evaluate whether an LLM agent can repair an invalid WebSocket PRB policy action after local validation rejects it.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | OCUDU WebSocket remote control action `SET_PRB_POLICY_RATIO_WS` |
| Observation | UE ping counters, OCUDU JSON metrics, WebSocket backend status, last action result |
| Oracle | Local validation record, accepted action record, JSON metrics continuity, ping success ratio, cleanup result |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, remote artifact writer |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_error_repair_v1 \
  --controller invalid_then_fixed \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id ws-prb-repair-smoke \
  --json
```

## Agent Interaction Loop

The agent first emits a malformed PRB action, reads the local validation failure from the next observation, then emits one corrected PRB action. A good LLM agent should not repeatedly send invalid variants after the error is explained.

## Allowed Actions

Invalid example expected at the first step:

```json
{
  "type": "SET_PRB_POLICY_RATIO_WS",
  "min_prb_policy_ratio": 90,
  "max_prb_policy_ratio": 10
}
```

Repair action:

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

Invalid actions are rejected locally and are not dispatched to OCUDU.

## Observation Contract

Observations include ping counters, JSON metrics status, backend status, and `last_action` validation details. The validation error is the agent's repair signal.

## Scoring

Canonical score dimensions:

- `invalid_local_rejection_correctness`
- `valid_action_accepted_rate`
- `task_success`
- `ping_success_ratio`
- `metrics_continuity`
- `clean_teardown`

The expected sequence is one locally rejected invalid action followed by one accepted valid action.

## Unscored Conditions

Setup, conformance, runtime launch, missing ping replies, missing JSON metrics, WebSocket backend failure, or cleanup failure can make the run unscored. Repeated invalid actions after setup are agent behavior failures.

## Required Conformance

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`
- `websocket_prb_policy_action`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and `logs/`.

## Limitations

This task measures schema repair and safe retry behavior, not PRB performance effects.
