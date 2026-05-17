# `ws_prb_error_repair_v1`

## Goal

Test whether an LLM agent can repair its own invalid WebSocket PRB action after local validation rejects it.

## LLM-Agent Challenge

The agent must read the last action result, identify the validation failure, and produce a valid bounded PRB policy action without repeatedly sending invalid commands.

## Runtime Stack

- Docker Open5GS core.
- OCUDU gNB with WebSocket remote control and JSON metrics.
- srsUE over ZMQ RF emulation.
- UE ping traffic to `10.45.1.1`.

## Scenario And Workload

The RAN episode is healthy. The task challenge comes from action validation and repair, not from an injected radio impairment.

## Allowed Actions Or Outputs

Allowed action type is `SET_PRB_POLICY_RATIO_WS`. The expected sequence is one locally invalid min/max action followed by one accepted valid action.

## Observation Frame

Observations include ping counters, JSON metrics, backend status, and the previous action result. The invalid action is rejected locally before dispatch to OCUDU.

## Scoring

The run is scored when the first action is locally invalid and not dispatched, a later valid action is accepted, no repeated invalid spam occurs, ping remains healthy, metrics are observed, and cleanup succeeds.

## Required Conformance

Uses the v3 Docker e2e/WebSocket conformance gate.

## Artifacts

Expected artifacts include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and logs under the remote run directory.

## Limitations

This task measures schema repair and safe retry behavior, not PRB performance effects.
