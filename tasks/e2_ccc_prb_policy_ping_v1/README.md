# `e2_ccc_prb_policy_ping_v1`

## Goal

Control OCUDU slice PRB policy through the E2SM-CCC path while the one-UE ping episode remains healthy.

## LLM-Agent Challenge

The agent must recognize that this is a standards-facing cell/slice control task and choose the CCC action, not the WebSocket fallback or UE-scoped RC action.

## Runtime Stack

Docker Open5GS, OCUDU gNB, srsUE, FlexRIC RIC, KPM xApp, and ping traffic to `10.45.1.1`.

## Actions

Allowed action type:

- `SET_PRB_POLICY_RATIO_CCC`

The action uses the shared PRB policy fields: `plmn`, `sst`, optional `sd`, `min_prb_policy_ratio`, `max_prb_policy_ratio`, and optional `dedicated_ratio`.

## Observations

Observation frames include ping counters, JSON metrics status, decoded E2SM-KPM v05 evidence, E2 control outcome status, and last action result.

## Scoring

The run is scored when conformance passes, one valid CCC PRB action is accepted, ping succeeds, JSON metrics and KPM records continue, E2 control oracle evidence is available, and cleanup succeeds.

## Conformance

Required checks include the v4 FlexRIC/KPM gate plus `e2_ccc_prb_control_path`.

## Artifacts

Expected remote artifacts include `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_control_raw.jsonl`, `e2_oracle.json`, `summary.json`, and logs.

## Limitations

The score validates command correctness and episode health. It does not claim throughput fairness from a single UE ping workload.
