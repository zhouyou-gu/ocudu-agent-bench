# `e2_control_api_consistency_v1`

## Goal

Evaluate whether an agent selects the correct standards-facing E2 control API for a cell/slice PRB policy objective.

## LLM-Agent Challenge

Both CCC and RC DU PRB control action shapes are available. The correct decision is `SET_PRB_POLICY_RATIO_CCC`, because the objective is a cell/slice policy rather than a UE-associated DU control operation.

## Runtime Stack

Docker Open5GS, OCUDU gNB, srsUE, FlexRIC RIC, KPM xApp, and ping traffic to `10.45.1.1`.

## Actions

Allowed action types:

- `SET_PRB_POLICY_RATIO_CCC`
- `SET_PRB_POLICY_RATIO_RC_DU`

The expected action type is `SET_PRB_POLICY_RATIO_CCC`.

## Observations

Observation frames include ping counters, JSON metrics status, decoded E2SM-KPM v05 evidence, available E2 control state, and last action result.

## Scoring

The run is scored when the accepted valid action uses the expected CCC action type, ping succeeds, JSON metrics and KPM records continue, E2 control oracle evidence is available, and cleanup succeeds.

## Conformance

Required checks include the v4 FlexRIC/KPM gate plus both CCC and RC DU control-path checks so the API choice is meaningful.

## Artifacts

Expected remote artifacts include `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_control_raw.jsonl`, `e2_oracle.json`, `summary.json`, and logs.

## Limitations

This task measures API selection and safe control behavior. It does not compare throughput effects between CCC and RC controls.
