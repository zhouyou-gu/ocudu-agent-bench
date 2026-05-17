# `e2_kpm_json_consistency_v1`

## Goal

Evaluate whether an LLM agent can wait for both OCUDU JSON metrics and decoded E2SM-KPM v05 PRB evidence before controlling PRB policy.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | OCUDU WebSocket remote control action `SET_PRB_POLICY_RATIO_WS` |
| Observation | UE ping counters, OCUDU JSON metrics, decoded E2SM-KPM v05 records, WebSocket backend status, last action result |
| Oracle | Action decision context, KPM indication count, E2 PCAP/log oracle, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, FlexRIC Near-RT RIC, KPM xApp |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_kpm_json_consistency_v1 \
  --agent evidence_gated_prb \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-json-consistency-smoke \
  --json
```

## Agent Interaction Loop

The agent returns `None` until an observation contains fresh JSON metrics and E2 PRB evidence. It then sends one valid WebSocket PRB action and stops acting. The action log snapshots the observation used for the decision.

## Allowed Actions

```json
{
  "type": "SET_PRB_POLICY_RATIO_WS",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null
}
```

## Observation Contract

Observations include ping counters, JSON metrics presence and freshness, backend status, last action result, E2 KPM indication count, last KPM record, PRB measurement evidence, and E2 oracle availability.

## Scoring

Canonical score dimensions:

- `evidence_gated_action`
- `valid_action_accepted_rate`
- `ping_success_ratio`
- `metrics_continuity`
- `e2_kpm_continuity`
- `e2_oracle_available`
- `clean_teardown`

The first accepted valid action must be associated with an observation containing both fresh JSON metrics and E2 PRB evidence.

## Unscored Conditions

Setup, FlexRIC/KPM conformance failure, missing decoded KPM records, missing E2 oracle artifacts, missing JSON metrics, runtime launch failure, or cleanup failure can make the run unscored. Acting before both evidence sources are available is an agent behavior failure.

## Required Conformance

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include `scenario.json`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_oracle.json`, `summary.json`, and `logs/`.

## Limitations

The task measures evidence-gated control timing. It does not ask the agent to reconcile throughput or fairness effects.
