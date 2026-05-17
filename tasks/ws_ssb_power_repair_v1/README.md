# `ws_ssb_power_repair_v1`

## Goal

Measure whether an LLM agent can repair an invalid OCUDU WebSocket SSB block-power action by issuing one valid `ssb_set` command.

## Runtime Stack

Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, UE ping traffic to `10.45.1.1`, JSON metrics, and WebSocket remote control.

## Allowed Actions

Allowed action type is `SET_SSB_BLOCK_POWER_WS`:

```json
{
  "type": "SET_SSB_BLOCK_POWER_WS",
  "plmn": "00101",
  "nci": 6733824,
  "ssb_block_power_dbm": -16
}
```

Validation requires `nci` to be a 36-bit NR cell identity integer and `ssb_block_power_dbm` to be an integer in OCUDU's native `[-60, 50]` range.

## Observations

Observations include ping counters, JSON metrics status, backend status, last action result, and the harness-derived `cell` identity fields needed to form the valid `ssb_set` request.

## Scoring

The run is scored when the first invalid SSB action is locally rejected, a later valid SSB action is accepted by OCUDU, no repeated invalid spam occurs, ping remains healthy, JSON metrics continue, and cleanup succeeds.

## Required Conformance

Uses the v3 Docker e2e gate plus `websocket_ssb_power_action`.

## Artifacts

Expected remote artifacts include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and logs under the remote run directory.

## Limitations

This task scores control correctness and recovery. It does not claim a measured RF performance effect from SSB power changes in the ZMQ emulator.
