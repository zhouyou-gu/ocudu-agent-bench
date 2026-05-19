# `e2_control_api_consistency_v1`

## Agent Goal

Evaluate whether an LLM agent can select the correct E2 control API for a cell/slice PRB policy objective when both CCC and RC DU actions are available.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | E2SM-CCC `SET_PRB_POLICY_RATIO_CCC` and E2SM-RC DU `SET_PRB_POLICY_RATIO_RC_DU` are both allowed |
| Observation | UE ping counters, OCUDU JSON metrics, decoded E2SM-KPM v05 records, E2 control backend status, last action result |
| Oracle | Expected action type, E2 setup, KPM continuity, CCC/RC control outcome, E2 PCAP/log oracle, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, FlexRIC Near-RT RIC, CCC/RC/KPM xApps |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_control_api_consistency_v1 \
  --controller e2_control_consistency \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-control-api-smoke \
  --json
```

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent reads the task context and observations, recognizes that the objective is cell/slice PRB policy, waits for E2 evidence, sends one CCC action, and stops acting. Choosing the UE-scoped RC DU action is the wrong API selection for this task.

## Allowed Actions

Correct action:

```json
{
  "type": "SET_PRB_POLICY_RATIO_CCC",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null
}
```

Allowed but incorrect for the task objective:

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

## Observation Contract

Observations include ping counters, JSON metrics, decoded KPM evidence, E2 control backend status, E2 control outcome records, and last action result. The scoring contract expects the CCC action type for the cell/slice objective.

## Task Scoring

Canonical score dimensions:

- `expected_action_type_correct`
- `valid_action_accepted_rate`
- `ping_success_ratio`
- `metrics_continuity`
- `e2_kpm_continuity`
- `e2_control_oracle_available`
- `clean_teardown`

The accepted valid action must be `SET_PRB_POLICY_RATIO_CCC`, and the E2 control oracle must confirm the expected control outcome.

## Unscored Conditions

Setup, FlexRIC/KPM conformance failure, missing CCC or RC control tool, missing decoded KPM records, missing E2 control oracle, runtime launch failure, missing ping replies, missing JSON metrics, or cleanup failure can make the run unscored. Choosing RC DU after setup succeeds is an agent behavior failure.

## Required Conformance

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`
- `e2_ccc_prb_control_path`
- `e2_rc_du_prb_control_path`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include episode metadata JSON, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_control_raw.jsonl`, `e2_oracle.json`, `summary.json`, and `logs/`.

## Limitations

This task measures API selection and safe control behavior. It does not compare radio performance effects between CCC and RC controls.
