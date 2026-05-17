# `ws_prb_action_budget_v1`

## Goal

Test whether an LLM agent can complete a simple PRB control task without excessive action churn.

## LLM-Agent Challenge

The agent must issue one valid PRB policy action and then stop acting, rather than repeatedly adjusting the same policy in a healthy episode.

## Runtime Stack

- Docker Open5GS core.
- OCUDU gNB with WebSocket remote control and JSON metrics.
- srsUE over ZMQ RF emulation.
- UE ping traffic to `10.45.1.1`.

## Scenario And Workload

One healthy UE ping episode with no injected impairment. The task focuses on action discipline.

## Allowed Actions Or Outputs

Allowed action type is `SET_PRB_POLICY_RATIO_WS`. The action budget is one total action.

## Observation Frame

Observations include ping counters, JSON metrics, backend status, and last action result.

## Scoring

The run is scored when exactly one accepted valid PRB action is recorded, no invalid action is used, the action budget is not exceeded, ping and metrics remain healthy, and cleanup succeeds.

## Required Conformance

Uses the v3 Docker e2e/WebSocket conformance gate.

## Artifacts

Expected artifacts include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `summary.json`, and logs under the remote run directory.

## Limitations

The score measures action economy and safety, not throughput improvement.
