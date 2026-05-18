# `ws_ssb_power_repair_v1`

## Goal

Evaluate whether an LLM agent can repair an invalid OCUDU WebSocket SSB block-power action by issuing one valid command.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | OCUDU WebSocket remote control action `SET_SSB_BLOCK_POWER_WS` |
| Observation | UE ping counters, OCUDU JSON metrics, cell identity, WebSocket backend status, last action result |
| Oracle | Local validation record, accepted `ssb_set` action record, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, remote artifact writer |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_ssb_power_repair_v1 \
  --controller invalid_then_ssb \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id ws-ssb-repair-smoke \
  --json
```

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent first emits an invalid SSB power value, reads the local validation failure, then builds a valid action using the observed `cell.nci` and `cell.plmn`.

## Allowed Actions

```json
{
  "type": "SET_SSB_BLOCK_POWER_WS",
  "plmn": "00101",
  "nci": 6733824,
  "ssb_block_power_dbm": -16
}
```

`nci` must be a 36-bit NR cell identity integer. `ssb_block_power_dbm` must be an integer in OCUDU's native `[-60, 50]` range.

## Observation Contract

Observations include ping counters, JSON metrics status, backend status, last action result, and harness-derived cell identity fields needed to form the valid `ssb_set` request.

## Scoring

Canonical score dimensions:

- `invalid_local_rejection_correctness`
- `valid_action_accepted_rate`
- `ping_success_ratio`
- `metrics_continuity`
- `clean_teardown`

The expected sequence is one locally rejected invalid SSB action followed by one accepted valid SSB action.

## Unscored Conditions

Setup, conformance, runtime launch, missing ping replies, missing JSON metrics, missing cell identity, WebSocket backend failure, or cleanup failure can make the run unscored. Repeated invalid actions after setup are agent behavior failures.

## Required Conformance

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`
- `websocket_ssb_power_action`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include episode metadata JSON, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and `logs/`.

## Limitations

This task scores command correctness and recovery. It does not claim a measured RF performance effect from SSB power changes in the ZMQ emulator.
