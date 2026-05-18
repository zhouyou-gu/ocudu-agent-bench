# `e2_rc_du_prb_policy_ping_v1`

## Goal

Evaluate whether an LLM agent can wait for DU UE identity evidence and then control OCUDU DU PRB quota through E2SM-RC.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | E2SM-RC DU control action `SET_PRB_POLICY_RATIO_RC_DU` |
| Observation | UE ping counters, OCUDU JSON metrics, decoded E2SM-KPM v05 records, DU UE identity, E2 control outcome |
| Oracle | E2 setup, KPM continuity, RC DU control outcome, UE identity discovery, E2 PCAP/log oracle, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, FlexRIC Near-RT RIC, RC/KPM xApps |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_rc_du_prb_policy_ping_v1 \
  --controller rc_du_prb \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-rc-du-prb-smoke \
  --json
```

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent returns `None` until fresh metrics, E2 PRB evidence, and DU UE identity are available. It then emits one RC DU PRB action, observes the control outcome, and stops acting.

## Allowed Actions

```json
{
  "type": "SET_PRB_POLICY_RATIO_RC_DU",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null,
  "du_ue_id": 1
}
```

`du_ue_id` may be supplied by the agent from observations or discovered by the harness before dispatch. If identity cannot be resolved, the run is unscored rather than counted as an agent failure.

## Observation Contract

Observations include ping counters, JSON metrics status, decoded KPM evidence, `e2.du_ue_id` when available, E2 control backend status, E2 control outcome records, and last action result.

## Scoring

Canonical score dimensions:

- `expected_action_type_correct`
- `valid_action_accepted_rate`
- `ping_success_ratio`
- `metrics_continuity`
- `e2_kpm_continuity`
- `e2_control_oracle_available`
- `clean_teardown`

The accepted valid action must use `SET_PRB_POLICY_RATIO_RC_DU` after identity evidence is available, and the E2 control oracle must confirm an RC DU outcome.

## Unscored Conditions

Setup, FlexRIC/KPM conformance failure, missing RC DU control tool, missing DU UE identity, missing decoded KPM records, missing E2 control oracle, runtime launch failure, missing ping replies, missing JSON metrics, or cleanup failure can make the run unscored.

## Required Conformance

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`
- `e2_rc_du_prb_control_path`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include episode metadata JSON, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_control_raw.jsonl`, `e2_oracle.json`, `summary.json`, and `logs/`.

## Limitations

This task depends on runtime DU UE identity discovery. Missing identity evidence is a setup/runtime problem, not an agent behavior failure.
