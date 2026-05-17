# `e2_kpm_json_consistency_v1`

## Goal

Test whether an LLM agent can wait for consistent multi-source evidence before controlling PRB policy.

## LLM-Agent Challenge

The agent observes JSON metrics and decoded E2SM-KPM v05 records. It should issue a WebSocket PRB action only after both sources show usable evidence, not immediately after the first partial observation.

## Runtime Stack

- Docker Open5GS core.
- OCUDU gNB with WebSocket remote control, JSON metrics, and E2 KPM enabled.
- srsUE over ZMQ RF emulation.
- Dockerized FlexRIC Near-RT RIC and KPM monitor xApp.
- UE ping traffic to `10.45.1.1`.

## Scenario And Workload

One healthy UE ping episode with decoded E2SM-KPM v05 PRB evidence. The scenario is deterministic; setup or KPM decode failures make the run unscored.

## Allowed Actions Or Outputs

Allowed action type is `SET_PRB_POLICY_RATIO_WS`. The expected action is one valid PRB policy action after JSON metrics and E2 PRB evidence are both available.

## Observation Frame

Observations include ping counters, JSON metrics, backend status, last action result, E2 KPM indication count, last KPM record, PRB measurement evidence, and oracle availability status.

## Scoring

The run is scored when the accepted valid action is decision-context gated by fresh JSON metrics and E2 PRB evidence, ping succeeds, JSON metrics and KPM records continue, the E2 oracle is available, and cleanup succeeds.

## Required Conformance

Uses the v4 FlexRIC/E2SM-KPM v05 conformance gate.

## Artifacts

Expected artifacts include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_oracle.json`, `summary.json`, and logs under the remote run directory.

## Limitations

The action path remains WebSocket PRB control. E2 RC and CCC actions are out of scope.
