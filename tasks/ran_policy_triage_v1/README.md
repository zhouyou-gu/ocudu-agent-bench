# `ran_policy_triage_v1`

## Agent Goal
Evaluate an LLM agent as a RAN management operator. The agent sees one stable task id, structured RAN evidence, and structured management context. It must diagnose the task condition and choose the minimum safe action without seeing internal episode labels.

## APIs Used
The task dynamics exercise the existing benchmark API catalog: OCUDU WebSocket PRB control, OCUDU WebSocket SSB power control, JSON metrics, FlexRIC E2SM-KPM v05 observation, E2SM-CCC PRB control, E2SM-RC DU PRB control, ping health, and the E2 PCAP/log oracle. Provisioning and conformance remain setup APIs, not agent-performance APIs.

## How To Trigger
Run the balanced suite with the reference controller:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ran_policy_triage_v1 \
  --controller triage_reference \
  --runs 12 \
  --duration 10 \
  --seed 1 \
  --json
```

If `--runs` is omitted for this task, the CLI defaults to 12 runs so each internal task condition is covered once before repetition.

## Perceive -> Reason -> Execute -> Feedback -> Repeat
Perceive: read the structured observation frame: ping health, metrics freshness, backend status, E2 KPM availability, UE identity, cell identity, management context, action catalog, and last action feedback.

Reason: decide whether the evidence calls for monitoring, waiting for fresh evidence, repairing a prior failed action, applying a PRB policy, applying an SSB power value, or choosing a standards E2 control path.

Execute: call `BenchmarkEnv.act(action_or_none, telemetry={...})`. Use `None` for `NO_ACTION`; it is recorded as a triage action decision but is never sent to OCUDU.

Feedback: inspect the next observation and `last_action` feedback. Repeat until the episode closes.

## Allowed Actions
The stable visible action catalog is:

```text
NO_ACTION
SET_PRB_POLICY_RATIO_WS
SET_SSB_BLOCK_POWER_WS
SET_PRB_POLICY_RATIO_CCC
SET_PRB_POLICY_RATIO_RC_DU
```

`NO_ACTION` is represented by Python `None`. The other actions use the schemas in `benchmark/schemas/actions.schema.json`.

## Observation Contract
The agent-facing observation type is always `ran_policy_triage_v1`. Observations include structured evidence only and do not expose internal task-condition labels, expected action type, scoring contract, or oracle summaries. The `management_context` provides the broad objective, safety constraints, desired values when control is needed, target scope, and repair context when a prior failed action is injected.

## Task Scoring
The task records `triage_success`, `rationale_complete`, `correct_api_selection`, `unnecessary_action_avoidance`, `repair_success`, `stale_wait_success`, `valid_action_accepted_rate`, `invalid_local_rejection_correctness`, `ping_success_ratio`, `metrics_continuity`, `e2_kpm_continuity`, `e2_oracle_available`, `e2_control_oracle_available`, and `clean_teardown`. Rationale is shape-checked through decision telemetry; missing rationale does not block dispatch but lowers `evidence_use`.

## Unscored Conditions
Setup, conformance, runtime, E2 oracle, or cleanup failures are benchmark failures and are not counted as agent-management failures. Agent behavior failures include unnecessary action, wrong API selection, unsafe action during stale evidence, missing repair action, repeated invalid control, or no action when the context requires control.

## Required Conformance
This task uses the union of the WebSocket, JSON metrics, SSB, E2 KPM, E2SM-CCC, and E2SM-RC DU conformance gates because its internal task conditions can exercise any of those APIs.

## Artifacts
Remote artifacts include episode metadata JSON, `decisions.jsonl`, `actions.jsonl`, `observations.jsonl`, `metrics_raw.jsonl`, E2 KPM/control/oracle files when applicable, `summary.json`, and logs. Internal task-condition labels appear only in metadata, final summaries, and suite reports.

## Limitations
The first version uses structured evidence only. Rationale content is stored for audit and shape-scored, but it is not semantically judged. Internal task conditions map to the existing scored task catalog; new runtime APIs should be added as separate API implementations before they become triage conditions.
