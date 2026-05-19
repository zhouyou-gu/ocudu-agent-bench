# `e2_kpm_prb_ping_v1`

## Agent Goal

Evaluate whether an LLM agent can use WebSocket PRB control while the episode also proves standards-facing E2SM-KPM v05 observation through FlexRIC.

## APIs Used

| Role | APIs |
| --- | --- |
| Action | OCUDU WebSocket remote control action `SET_PRB_POLICY_RATIO_WS` |
| Observation | UE ping counters, OCUDU JSON metrics, decoded E2SM-KPM v05 records, WebSocket backend status, last action result |
| Oracle | KPM indication count, E2 PCAP/log oracle, action log, ping, metrics, cleanup |
| Harness | Docker Open5GS, OCUDU gNB, srsUE, ZMQ RF emulation, FlexRIC Near-RT RIC, KPM xApp |

## How To Trigger

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_kpm_prb_ping_v1 \
  --controller fixed_prb \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-kpm-smoke \
  --json
```

## Perceive -> Reason -> Execute -> Feedback -> Repeat

The agent observes ping, JSON metrics, and E2 KPM backend status, sends one valid WebSocket PRB action, then stops acting. KPM records are not the control path in this task; they are required observation and oracle evidence.

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

E2SM-CCC and E2SM-RC controls are out of scope for this task.

## Observation Contract

Observations include the WebSocket PRB task fields plus RIC connection state, KPM indication count, last decoded KPM record, PRB measurement evidence, xApp status, and E2 PCAP/log oracle status. Agents should treat E2 fields as meaningful only when the backend reports E2 availability.

## Task Scoring

Canonical score dimensions:

- `valid_action_accepted_rate`
- `invalid_local_rejection_correctness`
- `ping_success_ratio`
- `metrics_continuity`
- `e2_kpm_continuity`
- `e2_oracle_available`
- `clean_teardown`

The run is scored only when decoded E2SM-KPM v05 records and oracle artifacts are available.

## Unscored Conditions

Setup, FlexRIC/KPM conformance failure, missing decoded KPM records, missing E2 oracle artifacts, runtime launch failure, missing ping replies, missing JSON metrics, WebSocket backend failure, or cleanup failure can make the run unscored.

## Required Conformance

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`

## Artifacts

Remote artifacts under `<remote.workspace>/runs/<run_id>/episode/` include `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, `e2_kpm_raw.jsonl`, `e2_oracle.json`, `summary.json`, and `logs/`.

## Limitations

E2 KPM is observation-only here. The PRB control action remains WebSocket-based.
