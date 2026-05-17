# `e2_rc_du_prb_policy_ping_v1`

## Goal

Control OCUDU DU PRB quota through E2SM-RC while a one-UE ping episode remains healthy.

## LLM-Agent Challenge

The agent must wait for sufficient E2/KPM and UE identity evidence before issuing the UE-associated RC DU control action.

## Runtime Stack

Docker Open5GS, OCUDU gNB, srsUE, FlexRIC RIC, KPM xApp, and ping traffic to `10.45.1.1`.

## Actions

Allowed action type:

- `SET_PRB_POLICY_RATIO_RC_DU`

The action uses the shared PRB policy fields and may include `du_ue_id`. If omitted, the benchmark attempts to discover the DU UE identity from runtime evidence before dispatch.

## Observations

Observation frames include ping counters, JSON metrics status, decoded E2SM-KPM v05 evidence, DU UE identity when available, E2 control outcome status, and last action result.

## Scoring

The run is scored when conformance passes, one valid RC DU PRB action is accepted, ping succeeds, JSON metrics and KPM records continue, E2 control oracle evidence is available, and cleanup succeeds.

## Conformance

Required checks include the v4 FlexRIC/KPM gate plus `e2_rc_du_prb_control_path`.

## Artifacts

Expected remote artifacts include `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_control_raw.jsonl`, `e2_oracle.json`, `summary.json`, and logs.

## Limitations

This task depends on runtime DU UE identity discovery. Missing identity evidence is a setup/runtime unscored condition, not an agent failure.
