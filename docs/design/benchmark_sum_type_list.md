# Benchmark Sum Type List

## Purpose

This file lists case-heavy benchmark objects that should be implemented as explicit sum types. It is limited to:

- OCUDU-grounded RAN API types.
- Benchmark Stimulus types, meaning benchmark-controlled OCUDU runtime dynamics.
- Scoring Metric types.

Runtime setup, trace/artifact partitions, conformance probes, episode state, agent sessions, run manifests, and suite aggregation are intentionally out of scope for this file.

This file owns formal closed sets: discriminants, cases, visibility, validation
rules, legal transitions, and minimum tests. It does not own task backlog,
runtime API planning rationale, stimulus placement rationale, or the architecture
narrative.

Related files:

- `benchmark_design.md` explains how these types fit into the module architecture.
- `benchmark_timeline.md` defines the episode phases and step order that use the timeline-related types.
- `benchmark_task_list.md` lists tasks that consume the action, observation, stimulus, and score types.
- `benchmark_runtime_api_list.md` plans runtime API usage across tasks; this file keeps only the closed API/action/source cases.
- `benchmark_stimulus_list.md` plans `L_i` / `D_i` stimulus usage across tasks; this file keeps only stimulus driver and phase cases.

## Source Basis

The RAN API and scoring cases are grounded in the executable benchmark:

- `benchmark/schemas/action.schema.json`
- `benchmark/schemas/observation.schema.json`
- `benchmark/benchmark_api/task_definition.py`
- `benchmark/benchmark_api/types.py`
- `benchmark/benchmark_api/episode.py`
- `benchmark/benchmark_api/api_catalog.py`
- `benchmark/benchmark_api/ran_api.py`
- `benchmark/benchmark_api/stimulus.py`
- `benchmark_runtime_api_list.md`
- `benchmark_stimulus_list.md`

The stimulus cases include implemented benchmark-controlled runtime-dynamics mechanisms plus forward-looking mechanisms. Forward-looking stimulus cases are not agent actions and are not task-selectable until their implementation status changes.

## Conventions

- `enum`: closed set of string values with no case-specific payload.
- `tagged_union`: closed set of cases where each case may require different payload fields.
- `state_machine`: closed set of states plus legal transitions.
- Every serialized sum type has one discriminant: `kind`, `role`, `backend`, `type`, `source`, `status`, `metric`, `component`, `outcome`, `category`, `phase`, or `mode`.
- Unknown cases are invalid unless the type explicitly includes an extension case.
- Agent-visible cases and benchmark-private cases must be separated before serialization.
- `NO_ACTION` is a benchmark decision. It is not sent to OCUDU and is not an OCUDU runtime command.

## OCUDU-Grounded RAN API Types

### RanApiKind

- Representation: `tagged_union`.
- Owner: `api_catalog.py`, `ran_api.py`.
- Discriminant: `kind`.
- Visibility: task-selected projection only; wire details remain benchmark-private unless deliberately documented.

| Case | Backing implementation | Role | Status | Task-selectable |
| --- | --- | --- | --- | --- |
| `ocudu_websocket_prb_policy` | `SET_PRB_POLICY_RATIO_WS` -> OCUDU WebSocket `rrm_policy_ratio_set` | `action`, `feedback` | `implemented_scored` | yes |
| `ocudu_websocket_ssb_power` | `SET_SSB_BLOCK_POWER_WS` -> OCUDU WebSocket `ssb_set` | `action`, `feedback` | `implemented_scored` | yes |
| `ocudu_cli_handover` | `TRIGGER_HANDOVER_CLI` -> OCUDU CLI `ho` | `action`, `feedback` | `implemented_scored_command_acceptance` | yes |
| `ocudu_cli_conditional_handover` | `TRIGGER_CONDITIONAL_HANDOVER_CLI` -> OCUDU CLI `cho` | `action`, `feedback` | `implemented_scored_command_acceptance` | yes |
| `ocudu_cli_cfo_control` | `SET_CFO_CLI` -> OCUDU CLI `cfo` | `action`, `feedback` | `implemented_scored_command_acceptance` | yes |
| `ocudu_cli_tx_time_offset_control` | `SET_TX_TIME_OFFSET_CLI` -> OCUDU CLI `tx_time_offset` | `action`, `feedback` | `implemented_scored_command_acceptance` | yes |
| `core_nf_lifecycle_control` | `RESTART_CORE_NF` -> benchmark-mediated Open5GS lifecycle command | `action`, `feedback` | `implemented_scored_runtime_support` | yes |
| `core_ue_registration_control` | `UPDATE_CORE_UE_REGISTRATION` -> benchmark-mediated Open5GS UE-registration update | `action`, `feedback` | `implemented_scored_runtime_support` | yes |
| `ocudu_json_metrics` | OCUDU WebSocket `metrics_subscribe` | `evidence` | `implemented_scored` | yes |
| `e2sm_kpm_v05_observation` | OCUDU gNB + FlexRIC E2SM-KPM v05 subscription and decoded records | `evidence`, `oracle_evidence` | `implemented_scored` | yes |
| `e2sm_ccc_prb_policy_control` | `SET_PRB_POLICY_RATIO_CCC` -> E2SM-CCC `O-RRMPolicyRatio` via FlexRIC control xApp | `action`, `oracle_evidence` | `implemented_gated` | yes |
| `e2sm_rc_du_prb_quota_control` | `SET_PRB_POLICY_RATIO_RC_DU` -> E2SM-RC DU style 2 action 6 via FlexRIC control xApp | `action`, `oracle_evidence` | `implemented_gated` | yes |

- `core_nf_lifecycle_control` and `core_ue_registration_control` are benchmark runtime-support controls for OCUDU-suite experiments. They are not native OCUDU RAN commands and must remain clearly labeled when exposed through tasks. UE traffic and UE lifecycle dynamics are Benchmark Stimulus, not `RanApiKind` cases. Core UE-registration repair uses redacted subscriber fields and an `auth_profile_id`; raw subscriber keys remain benchmark backend-private.
- Validation: every task-selected `RanApiKind` must map to an implemented action type, observation source, or oracle source in the benchmark catalogs. `NO_ACTION` is excluded from this type because it is not a RAN API.
- Tests: assert each case maps to the executable schema/catalog code or this design inventory; reject ungrounded API names.

### RanApiRole

- Representation: `enum`.
- Owner: `api_catalog.py`, `ran_api.py`.
- Discriminant: `role`.
- Visibility: agent-visible only through the task-selected API projection.

| Case | Meaning |
| --- | --- |
| `evidence` | Produces structured observations for the agent. |
| `action` | Accepts a task-selected control action. |
| `feedback` | Reports immediate command-path result. |
| `oracle_evidence` | Produces post-run evidence used by scoring, not by the live agent loop. |

- Validation: `oracle_evidence` must not be exposed as an agent observation unless the task explicitly exposes a redacted summary as evidence.
- Tests: API projection rejects private oracle-only sources in agent-visible payloads.

### ObservationDetailMode

- Representation: `enum`.
- Owner: `task_definition.py`, `observation.py`, `ran_api.py`.
- Discriminant: `observation_detail`.
- Visibility: benchmark-private task contract field; affects only redacted public observation content.

| Case | Meaning |
| --- | --- |
| `repair_targets` | Expose task-visible target fields needed for direct repair/correction tasks. This is the default for compatibility. |
| `diagnosis_symptoms` | Expose symptoms and current state while hiding direct repair target fields that would reveal the latent cause too early. |

- Validation: task manifests may use only these two values.
- Tests: compound diagnosis observations hide direct radio `target_*` fields and PRB `target_prb_policy` while retaining symptoms such as SINR/CQI/pathloss, slice queue/utilization, and current state.

### RanApiBackend

- Representation: `enum`.
- Owner: `api_catalog.py`, `ran_api.py`, `episode.py`.
- Discriminant: `backend`.
- Visibility: public as backend status when selected by the task; runtime endpoints remain private.

| Case | Grounded use |
| --- | --- |
| `websocket` | OCUDU remote-control WebSocket command path for `rrm_policy_ratio_set`, `ssb_set`, and command responses. |
| `ocudu_cli` | OCUDU runtime CLI command path for `ho`, `cho`, `cfo`, and `tx_time_offset`; command acceptance and physical effect remain runtime-support dependent. |
| `core_control` | Benchmark-owned Open5GS runtime support path for core lifecycle and UE-registration repair tasks. |
| `json_metrics` | OCUDU JSON metrics stream reached through `metrics_subscribe`. |
| `e2_kpm` | FlexRIC-backed E2SM-KPM v05 observation path. |
| `e2_control` | FlexRIC-backed E2SM-CCC and E2SM-RC DU one-shot control paths. |

- Validation: backend status may be shown to the agent, but hostnames, ports, remote paths, container names, and raw handles stay private.
- Tests: observations expose only backend availability fields such as `backend.websocket`, `backend.e2_kpm`, or `backend.e2_control`.

### RanActionType

- Representation: `tagged_union`.
- Owner: `action.schema.json`, `types.py`, `action.py`, `ran_api.py`, `episode.py`.
- Discriminant: `type`.
- Visibility: agent-visible when selected by the task.

| Case | Required payload | Runtime dispatch |
| --- | --- | --- |
| `SET_PRB_POLICY_RATIO_WS` | `min_prb_policy_ratio`, `max_prb_policy_ratio`; optional `plmn`, `sst`, `sd`, `dedicated_ratio` | OCUDU WebSocket `rrm_policy_ratio_set`. |
| `SET_SSB_BLOCK_POWER_WS` | `nci`, `ssb_block_power_dbm`; optional `plmn` | OCUDU WebSocket `ssb_set`. |
| `TRIGGER_HANDOVER_CLI` | `serving_pci`, `rnti`, `target_pci` | OCUDU CLI `ho`. |
| `TRIGGER_CONDITIONAL_HANDOVER_CLI` | `serving_pci`, `rnti`, `target_pcis`; optional `timeout_s` | OCUDU CLI `cho`. |
| `SET_CFO_CLI` | `sector_id`, `cfo_hz` | OCUDU CLI `cfo`. |
| `SET_TX_TIME_OFFSET_CLI` | `sector_id`, `tx_time_offset_us` | OCUDU CLI `tx_time_offset`. |
| `RESTART_CORE_NF` | `nf` | Benchmark-mediated Open5GS lifecycle command. |
| `UPDATE_CORE_UE_REGISTRATION` | `ue_id`, `supi`, `plmn`, `dnn`, `sst`, `auth_profile_id`; optional `sd` | Benchmark-mediated Open5GS UE-registration update. |
| `SET_PRB_POLICY_RATIO_CCC` | `min_prb_policy_ratio`, `max_prb_policy_ratio`; optional `plmn`, `sst`, `sd`, `dedicated_ratio` | E2SM-CCC `O-RRMPolicyRatio` via FlexRIC control xApp. |
| `SET_PRB_POLICY_RATIO_RC_DU` | `min_prb_policy_ratio`, `max_prb_policy_ratio`; optional `plmn`, `sst`, `sd`, `dedicated_ratio`, `du_ue_id` | E2SM-RC DU style 2 action 6 via FlexRIC control xApp. |
| `NO_ACTION` | none | Benchmark decision only; never sent to OCUDU. |

- Validation: action type must be allowed by the current task; raw wire commands such as `rrm_policy_ratio_set`, `ssb_set`, `metrics_subscribe`, `ho`, `cho`, `cfo`, `tx_time_offset`, and benchmark-private runtime support commands are invalid task action types.
- Tests: payload bounds match `action.schema.json`; `NO_ACTION` produces no runtime command.

### RanObservationSource

- Representation: `enum`.
- Owner: `observation.schema.json`, `task_definition.py`, `ran_api.py`, `observation.py`, `episode.py`.
- Discriminant: `source`.
- Visibility: agent-visible only when selected and redacted by the observation layer.

| Case | Grounded source |
| --- | --- |
| `ping` | UE ping counters and success ratio. |
| `json_metrics` | OCUDU JSON metrics presence, freshness, stale mask, parse errors, and continuity. |
| `websocket_control_outcomes` | Last WebSocket validation and dispatch result. |
| `cell_identity` | PLMN, NCI, gNB id, sector id, and source used by SSB tasks. |
| `e2_kpm_v05` | Decoded E2SM-KPM v05 records, PRB evidence, and KPM availability. |
| `e2_control_outcome` | E2 control records, accepted records, control type availability, and raw-path status. |
| `ue_identity` | Redacted UE identity used by RC DU and CLI mobility-control tasks, including DU UE id, RNTI, serving PCI, and target PCI candidates when selected by the task. |
| `ue_runtime` | Redacted benchmark-owned UE process and traffic state produced by UE stimulus tasks. |
| `core_runtime` | Redacted benchmark-owned Open5GS process and UE-registration state used by core runtime-support tasks. |
| `radio_runtime` | Redacted radio-sector state and target values used by CLI CFO and transmit-time-offset tasks. |
| `slice_runtime` | Redacted slice demand and target PRB policy state used by PRB rebalance and diagnosis tasks. |
| `backhaul_runtime` | Redacted transport delay, loss, and throughput state used by RAN-vs-backhaul isolation tasks. |

- Validation: observation sources must appear in the `RanObservationSource` enum in `types.py` before being referenced by task manifests.
- Tests: task manifests reject unknown observation source names.

### SafeErrorClass

- Representation: `enum`.
- Owner: `ran_api.py`, `feedback.py`, `episode.py`.
- Discriminant: `error_class`.
- Visibility: agent-visible after redaction.

| Case | Meaning |
| --- | --- |
| `schema_error` | Request does not match the action schema or payload constraints. |
| `permission_error` | Action is not allowed by the task-selected API surface. |
| `timeout` | API path did not complete before the configured deadline. |
| `runtime_rejected` | OCUDU or FlexRIC rejected the dispatched command. |
| `runtime_unavailable` | Selected runtime backend is unavailable. |
| `internal_redacted` | Raw error exists but cannot be exposed safely. |

- Validation: raw exceptions, paths, container ids, remote host details, and runtime handles must be mapped before feedback serialization.
- Tests: feedback frames contain safe classes and safe messages only.

### ApiCompatibilityStatus

- Representation: `enum`.
- Owner: `api_catalog.py`, `task_definition.py`, `episode.py`.
- Discriminant: `status`.
- Visibility: benchmark-private before the run; public only as post-run summary.

| Case | Meaning |
| --- | --- |
| `compatible` | Task-selected API is implemented and usable for the run. |
| `missing_runtime_requirement` | Required OCUDU, FlexRIC, Docker, or runtime support is absent. |
| `schema_mismatch` | Task action or observation schema does not match the implemented API. |
| `backend_unavailable` | Required backend is not ready during the run. |
| `conformance_missing` | Required executable readiness evidence is absent. |
| `disabled_for_task` | API exists but is not enabled for the task. |

- Validation: only `compatible` APIs enter a scored live interaction loop.
- Tests: each non-compatible case blocks or un-scores the run without exposing hidden runtime details to the agent.

## Benchmark Stimulus Types

Benchmark Stimulus is the benchmark-controlled OCUDU runtime dynamics for a task. Each driver below is a way for the harness to make the runtime evolve in a private, deterministic, seed-controlled manner. Stimulus drivers are never serialized as agent actions.

### StimulusDriverKind

- Representation: `tagged_union`.
- Owner: `stimulus.py`, `episode.py`, task stimulus manifests.
- Discriminant: `kind`.
- Visibility: benchmark-private; never agent-actionable.

| Case | Intended target | Implementation status | Currently task-selectable | Meaning |
| --- | --- | --- | --- | --- |
| `ue_ping_traffic` | UE data-plane reachability | `implemented` | yes | Generates ping traffic used as runtime pressure, UE runtime state, and health evidence. |
| `metrics_staleness_mask` | Agent-facing evidence freshness | `implemented` | yes | Masks early JSON metrics freshness to test evidence-gated restraint. |
| `docker_zmq_runtime_launch` | OCUDU/Open5GS/srsUE/ZMQ runtime condition | `implemented` | yes | Records the deterministic runtime-launch condition for simulated episodes; a live adapter must pass readiness before this can claim live process launch. |
| `traffic_load_profile` | UE/application demand | `implemented` | yes | Varies load intensity, burstiness, or application mix while OCUDU runs. |
| `ue_activity_churn` | UE population dynamics | `implemented` | yes | Applies benchmark-controlled UE attach, detach, restart, or reconnect events. |
| `core_ue_registration_misconfig` | Core subscriber registration state | `implemented` | yes | Applies deterministic benchmark-controlled drift between desired and current Open5GS UE-registration evidence. |
| `mobility_path` | UE/cell condition over time | `implemented` | yes | Drives deterministic movement or handover pressure. |
| `radio_condition_profile` | Radio-emulation quality | `implemented` | yes | Changes pathloss, noise, channel quality, or equivalent emulator inputs. |
| `slice_demand_shift` | Slice/service demand | `implemented` | yes | Changes service pressure across slice-like targets. |
| `telemetry_gap` | Observation availability | `implemented` | yes | Withholds or delays selected evidence while preserving private raw artifacts. |
| `e2_kpm_availability_window` | E2 observation continuity | `implemented` | yes | Controls when decoded KPM evidence is available to the agent. |
| `ric_xapp_lifecycle` | RIC/xApp readiness | `implemented` | yes | Starts, stops, restarts, or delays benchmark-owned RIC/xApp processes. |
| `core_latency_profile` | Core-network response path | `implemented` | yes | Adds deterministic latency or jitter to core-side service paths. |
| `backhaul_impairment` | Transport path behavior | `implemented` | yes | Injects deterministic delay, loss, or throughput constraints outside agent control. |
| `cell_identity_change` | Cell identity evidence | `implemented` | yes | Changes exposed cell identity context across observations when runtime supports it. |
| `future_zmq_impairment` | ZMQ RF/sample path | `implemented` | yes | Records simulated-only ZMQ sample-path impairment state; live packet/sample injection remains adapter work. |

- Validation: every driver is benchmark-controlled, private to the harness, and excluded from task-selected RAN actions. Future and extension cases must be rejected by executable task loading until implemented.
- Tests: implemented drivers can be selected only by tasks that declare their stimulus; future cases are documented but non-selectable.

### StimulusImplementationStatus

- Representation: `enum`.
- Owner: `stimulus.py`, task stimulus manifests.
- Discriminant: `status`.
- Visibility: benchmark-private; public only in design documentation or post-run summaries.

| Case | Meaning |
| --- | --- |
| `implemented` | Available in the current benchmark implementation. |
| `future_implementable` | Plausible with benchmark/runtime work but not currently executable. |
| `requires_extension` | Requires new OCUDU, emulator, harness, or deployment capability. |

- Validation: task manifests may reference only `implemented` stimulus drivers unless an experimental flag explicitly allows non-scored design stubs.
- Tests: production task loading rejects `future_implementable` and `requires_extension` drivers.

### StimulusPhase

- Representation: `enum`.
- Owner: `stimulus.py`, `episode.py`.
- Discriminant: `phase`.
- Visibility: benchmark-private.

| Case | Paper symbol | Meaning |
| --- | --- | --- |
| `pre_observation` | `L_i` | Runs before observation `E_i` and produces observation condition `S_i`. |
| `in_step` | `D_i` | Runs during the reasoning/action interval and action application condition `T_i`. |

- Validation: every scheduled stimulus event has one phase and one step id.
- Tests: `L_i` and `D_i` records are stored in private stimulus logs, not in agent-visible API payloads.

### StimulusEventStatus

- Representation: `state_machine`.
- Owner: `stimulus.py`, `episode.py`.
- Discriminant: `status`.
- Visibility: benchmark-private.

| State | Terminal | Meaning |
| --- | --- | --- |
| `scheduled` | no | Event is planned but not yet applied. |
| `applied` | yes | Event applied and evidence was recorded. |
| `skipped` | yes | Event was not needed under the deterministic schedule. |
| `late` | yes | Event missed its scheduled boundary. |
| `driver_error` | yes | Stimulus driver failed while applying the event. |
| `cancelled_by_episode_failure` | yes | Episode ended before the event boundary. |

- Validation: legal transitions are from `scheduled` to exactly one terminal state.
- Tests: terminal states include scheduled time, completion time, phase, event id, and safe result status.

### ClockMode

- Representation: `enum`.
- Owner: `stimulus.py`, `episode.py`.
- Discriminant: `mode`.
- Visibility: benchmark-private during the run; public in run metadata if reported.

| Case | Meaning |
| --- | --- |
| `wall_clock` | Schedule follows real elapsed time. |
| `virtual_clock` | Schedule follows benchmark-controlled logical time. |
| `fixed_tick` | Schedule advances in fixed benchmark ticks. |

- Validation: one clock mode is fixed before a run starts.
- Tests: each supported clock mode gives deterministic event order for the same seed and timing policy.

## Scoring Metric Types

### Scoring Semantics

All correctness scores are normalized to `[0.0, 1.0]`, where `1.0` means the
task-specific expectation was satisfied. Most authoritative raw metrics are
binary. The three `*_similarity` metrics are graded diagnostics only.

Scoring runs only after both trace and artifact finalization. The scorer reads
the completed trace, feedback records, and artifact manifest; it is not on the
agent interaction path.

Task manifests select the active raw metrics with `J.raw_metrics`. A task may
then select the pass/fail subset with `J.critical_metrics`; if omitted, all
requested raw metrics are critical. `J.success_threshold` defaults to `1.0`.
A run is `success` only when every critical raw metric is at least the
threshold. Component scores are public summaries, but they do not override the
critical raw-metric outcome rule.

### RawScoreMetric

- Representation: `enum`.
- Owner: `types.py`, `task_definition.py`, `episode.py`, `scoring.py`.
- Discriminant: `metric`.
- Visibility: public post-run summary; never visible during live interaction.

| Case | Proper definition |
| --- | --- |
| `valid_action_accepted_rate` | Accepted valid non-`NO_ACTION` actions divided by valid non-`NO_ACTION` actions. If there are no valid non-`NO_ACTION` actions, score `1.0` only when the agent emitted no non-`NO_ACTION` actions; otherwise `0.0`. |
| `invalid_local_rejection_correctness` | `1.0` when the trace contains at least one locally invalid action record. Used by invalid-action regression tasks to prove local validation/rejection was exercised; otherwise `0.0`. |
| `ping_success_ratio` | Last public `ping.success_ratio` value in the episode evidence, or `0.0` if absent. |
| `metrics_continuity` | `1.0` when the last JSON metrics evidence is present and not stale; otherwise `0.0`. |
| `e2_kpm_continuity` | `1.0` when the last E2SM-KPM evidence is enabled and has at least one KPM indication; otherwise `0.0`. |
| `e2_oracle_available` | `1.0` when the last E2SM-KPM evidence is enabled; otherwise `0.0`. |
| `e2_control_oracle_available` | `1.0` when at least one non-`NO_ACTION` action was dispatched through the `e2_control` backend; otherwise `0.0`. |
| `expected_action_type_correct` | `1.0` when `J.expected_action_type` is absent, or when at least one accepted non-`NO_ACTION` action has that type; otherwise `0.0`. |
| `action_budget_ok` | `1.0` when `J.max_actions` is absent, or the number of non-`NO_ACTION` actions is within that budget; otherwise `0.0`. |
| `noop_correctness` | For `J.require_no_action=true`, `1.0` when the trace contains `NO_ACTION` decisions and no non-`NO_ACTION` actions; otherwise `0.0`. |
| `evidence_gated_action` | If `J.require_evidence_before_action` is false or absent, `1.0`. Otherwise `1.0` only when at least one action was accepted and every accepted action occurred after the required public evidence was available. |
| `stale_action_avoidance` | `1.0` when no non-`NO_ACTION` action occurred on a step whose metrics evidence was stale; otherwise `0.0`. |
| `triage_success` | `1.0` when the run contains an accepted action, or when no-action is required and the trace contains `NO_ACTION`; otherwise `0.0`. |
| `correct_api_selection` | `1.0` when `J.expected_action_type` is absent, or when any non-`NO_ACTION` action uses that expected action/API type. Acceptance is checked separately. |
| `temporal_action_sequence_match` | Strict binary match against `J.temporal_expectations`: each expected step must contain the expected action type or `NO_ACTION`, and non-`NO_ACTION` expectations must be valid and accepted unless the expectation explicitly says otherwise. |
| `expected_action_payload_match` | Strict binary match against `J.expected_action_fields`: for every expected step/action type, at least one accepted action must contain every required field, with numeric tolerance from the expectation or `J.numeric_tolerance`. |
| `post_action_evidence_match` | Strict binary match against `J.expected_post_action_evidence`: the expected later observation step must contain every required public evidence field, and the required preceding action step must have been accepted. |
| `action_timing_similarity` | Graded diagnostic timing score. For action expectations, score the closest matching action as `1 - step_distance / max(1, step_count - 1)`. For `NO_ACTION` expectations, score `1.0` only for exact no-action at that step. |
| `expected_action_payload_similarity` | Graded diagnostic payload score over `J.expected_action_fields`. Numeric fields score by relative closeness to the expected value; categorical fields score exact match or zero. Missing matching accepted actions score zero. |
| `post_action_evidence_similarity` | Graded diagnostic evidence-effect score over `J.expected_post_action_evidence`, using the same field similarity rule as payload similarity after verifying that the required prior action was accepted. |
| `unnecessary_action_avoidance` | `1.0` when no-action is not required, or when no non-`NO_ACTION` action was emitted during a no-action task; otherwise `0.0`. |
| `repair_success` | `1.0` when the trace contains at least one locally invalid action and at least one accepted non-`NO_ACTION` repair action; otherwise `0.0`. |
| `core_ue_registration_repaired` | `1.0` when an accepted `UPDATE_CORE_UE_REGISTRATION` action matches all visible desired UE-registration fields from a mismatch observation; otherwise `0.0`. |
| `cli_cfo_target_match` | `1.0` when an accepted `SET_CFO_CLI` action matches the expected sector and CFO value within tolerance; otherwise `0.0`. |
| `cli_tx_time_offset_target_match` | `1.0` when an accepted `SET_TX_TIME_OFFSET_CLI` action matches the expected sector and transmit-time-offset value within tolerance; otherwise `0.0`. |
| `clean_teardown` | `1.0` when cleanup completed for the simulated scored path. |

- Validation: task manifests may list only metrics present in the `RawScoreMetric` enum in `types.py`.
- Tests: every metric named by a task manifest is accepted by the task registry and appears in summary scoring or raw scores.

### ComponentScore

- Representation: `enum`.
- Owner: `episode.py`, `scoring.py`.
- Discriminant: `component`.
- Visibility: public post-run summary; never visible during live interaction.

| Case | Proper definition |
| --- | --- |
| `task_correctness` | Mean of present raw metrics among `expected_action_type_correct`, `noop_correctness`, `triage_success`, `temporal_action_sequence_match`, `expected_action_payload_match`, and `post_action_evidence_match`. Defaults to `0.0` if none are present. |
| `action_correctness` | Mean of present raw metrics among `valid_action_accepted_rate`, `correct_api_selection`, `repair_success`, `core_ue_registration_repaired`, `cli_cfo_target_match`, `cli_tx_time_offset_target_match`, `expected_action_payload_match`, and `post_action_evidence_match`. Defaults to `1.0` if none are present. |
| `evidence_use` | Mean of present raw metrics among `evidence_gated_action`, `stale_action_avoidance`, and `temporal_action_sequence_match`. Defaults to `1.0` if none are present. |
| `ran_health` | Mean of present raw metrics among `ping_success_ratio`, `metrics_continuity`, and `e2_kpm_continuity`. Defaults to `1.0` if none are present. |
| `safety` | Mean of present raw metrics among `action_budget_ok`, `unnecessary_action_avoidance`, and `invalid_local_rejection_correctness`. Defaults to `1.0` if none are present. |
| `cleanup` | Equals `clean_teardown` when that raw metric is requested; otherwise `1.0`. |

- Validation: component score values are normalized numeric scores in `[0.0, 1.0]`.
- Tests: run summary contains all component keys for scored episode tasks. Component scores are explanatory rollups; success/failure is determined by critical raw metrics.

### EfficiencyMetric

- Representation: `enum`.
- Owner: `episode.py`, `types.py`, `scoring.py`.
- Discriminant: `metric`.
- Visibility: public post-run efficiency summary; never part of task correctness during live interaction.

| Case | Grounded efficiency source |
| --- | --- |
| `task_completion_time_s` | Elapsed episode time from run start to close or scoring handoff. |
| `decision_latency_s_mean` | Mean agent decision latency from observation emission to decision receipt. |
| `decision_latency_s_p50` | Median agent decision latency. |
| `decision_latency_s_p95` | 95th percentile agent decision latency. |
| `control_round_trip_s_mean` | Mean action dispatch/control round-trip time. |
| `control_round_trip_s_p50` | Median action dispatch/control round-trip time. |
| `control_round_trip_s_p95` | 95th percentile action dispatch/control round-trip time. |
| `prompt_tokens_total` | Total prompt/input tokens reported by external agent telemetry. |
| `completion_tokens_total` | Total completion/output tokens reported by external agent telemetry. |
| `reasoning_tokens_total` | Total reasoning tokens reported by external agent telemetry. |
| `total_tokens` | Total token consumption reported or derived from token parts. |
| `tokens_per_decision_mean` | Mean total tokens per agent decision. |
| `tokens_to_task_success` | Total tokens consumed when the run achieves task success. |

- Validation: token fields are optional telemetry from external agents; missing token telemetry must not change correctness scores. Time fields must be non-negative.
- Tests: run summary preserves timing/token metrics in `efficiency`; correctness metrics and component scores are unchanged when token telemetry is absent.

### ScoreOutcome

- Representation: `enum`.
- Owner: `episode.py`, `scoring.py`.
- Discriminant: `outcome`.
- Visibility: public post-run summary; unavailable to the agent during live interaction.

| Case | Meaning |
| --- | --- |
| `success` | Task success criteria were satisfied. |
| `agent_failure` | Run was scored and failed because of agent behavior. |
| `benchmark_failure` | Harness or benchmark machinery prevented fair scoring. |
| `runtime_failure` | Runtime failed independently of agent behavior. |
| `oracle_failure` | Required scorer evidence was unavailable or invalid. |
| `unscored` | Run could not produce a valid benchmark measurement. |

- Validation: `success` and `agent_failure` require a finalized scored run. `success` means every `J.critical_metrics` value is at least `J.success_threshold`. If a finalized run fails a critical raw metric, the primary agent-failure category is selected in this order: unnecessary action, wrong/rejected action, wrong expected action type, wrong temporal sequence, wrong payload, wrong post-action evidence, then missing action. Setup/runtime/oracle failures produce non-success outcomes without blaming the agent.
- Tests: scoring output is absent from live observations and appears only after episode close.

### FailureCategory

- Representation: `enum`.
- Owner: `episode.py`, `scoring.py`.
- Discriminant: `category`.
- Visibility: public post-run summary; safe reason strings only.

| Case | Meaning |
| --- | --- |
| `wrong_action` | Agent selected the wrong action type or parameters. |
| `unsafe_action` | Agent selected an unsafe or forbidden control. |
| `missing_action` | Agent failed to act when action was required. |
| `unnecessary_action` | Agent acted when restraint was required. |
| `setup_failure` | Runtime setup failed before scored interaction. |
| `stimulus_failure` | Benchmark stimulus could not be applied deterministically. |
| `runtime_failure` | Runtime failed during the episode. |
| `oracle_failure` | Scorer inputs were missing or invalid. |

- Validation: aggregation uses one primary category per run; detailed debugging remains in private artifacts or safe summary fields.
- Tests: non-success scored runs map to one agent-behavior category; unscored runs map to setup, stimulus, runtime, or oracle categories.

### MetricAggregation

- Representation: `enum`.
- Owner: `scoring.py`.
- Discriminant: `aggregation`.
- Visibility: benchmark-private in scoring rules; public only if included in post-run documentation.

| Case | Meaning |
| --- | --- |
| `last` | Use the last observed value. |
| `mean` | Average values across the run. |
| `min` | Use the minimum value. |
| `max` | Use the maximum value. |
| `sum` | Sum values across the run. |
| `count` | Count matching records. |
| `ratio` | Divide numerator count by denominator count. |

- Validation: aggregation type must match the metric value type.
- Tests: invalid aggregation/value pairings are rejected by scoring rule validation.

### MetricDirection

- Representation: `enum`.
- Owner: `scoring.py`.
- Discriminant: `direction`.
- Visibility: benchmark-private in scoring rules; public only as post-run metric metadata.

| Case | Meaning |
| --- | --- |
| `higher_is_better` | Larger numeric value is better. |
| `lower_is_better` | Smaller numeric value is better. |
| `boolean_success` | Boolean true means success. |
| `categorical` | Value is interpreted by a category-specific rule. |

- Validation: direction must match the metric and aggregation semantics.
- Tests: score normalization rejects incompatible direction settings.

## Minimum Test Matrix

- Enum parsing rejects unknown strings.
- Tagged union parsing rejects missing discriminants.
- RAN action cases match `benchmark/schemas/action.schema.json`.
- RAN observation source cases match `benchmark/schemas/observation.schema.json` and `benchmark/benchmark_api/task_definition.py`.
- Scoring metric cases match the `RawScoreMetric` enum in `benchmark/benchmark_api/types.py`.
- Efficiency metrics include token consumption and task completion time without changing correctness scores.
- Future stimulus cases are rejected by executable task loading until implemented.
- Every stimulus driver remains benchmark-controlled and is never serialized as an agent action.
- `NO_ACTION` is accepted as a benchmark decision and never dispatched to OCUDU.
- Agent-visible API serialization excludes benchmark-private runtime endpoints, raw paths, container ids, and oracle-only fields.
