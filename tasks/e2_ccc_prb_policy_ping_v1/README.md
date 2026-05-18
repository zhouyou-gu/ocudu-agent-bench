# `e2_ccc_prb_policy_ping_v1`

## Goal

Evaluate whether an LLM agent can control OCUDU slice PRB policy through the E2SM-CCC path while ping, JSON metrics, and KPM evidence remain healthy.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | E2SM-CCC control action `SET_PRB_POLICY_RATIO_CCC` |
| Observation | UE ping counters, OCUDU JSON metrics, decoded E2SM-KPM v05 records, E2 control outcome, last action result |
| Oracle | E2 setup, KPM continuity, CCC control outcome, E2 PCAP/log oracle, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, FlexRIC Near-RT RIC, CCC/KPM xApps |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_ccc_prb_policy_ping_v1 \
  --controller ccc_prb \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-ccc-prb-smoke \
  --json
```

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent waits for fresh metrics and E2 PRB evidence, emits one CCC PRB policy action, observes the E2 control outcome, and then stops acting. The correct control path is CCC, not WebSocket PRB or UE-scoped RC.

## Allowed Actions

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

The benchmark maps the action to the OCUDU E2SM-CCC `O-RRMPolicyRatio` payload through the FlexRIC-derived control tool.

## Observation Contract

Observations include ping counters, JSON metrics status, decoded KPM evidence, E2 control backend status, E2 control outcome records, and last action result.

## Scoring

Canonical score dimensions:

- `expected_action_type_correct`
- `valid_action_accepted_rate`
- `ping_success_ratio`
- `metrics_continuity`
- `e2_kpm_continuity`
- `e2_control_oracle_available`
- `clean_teardown`

The accepted valid action must use `SET_PRB_POLICY_RATIO_CCC`, and the E2 control oracle must confirm a CCC outcome.

## Unscored Conditions

Setup, FlexRIC/KPM conformance failure, missing CCC control tool, missing decoded KPM records, missing E2 control oracle, runtime launch failure, missing ping replies, missing JSON metrics, or cleanup failure can make the run unscored.

## Required Conformance

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`
- `e2_ccc_prb_control_path`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include episode metadata JSON, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_control_raw.jsonl`, `e2_oracle.json`, `summary.json`, and `logs/`.

## Limitations

The score validates standards-facing command correctness, oracle evidence, and episode health. It does not claim throughput fairness from one UE ping traffic.
