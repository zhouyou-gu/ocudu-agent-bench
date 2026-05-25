# Benchmark Runtime API List

## Purpose

This file records and plans runtime APIs for multistep OCUDUAgentBench tasks.

Runtime APIs are the interfaces through which the agent may observe runtime state, request runtime control, or receive command-path feedback after task projection. Benchmark Stimulus remains separate: it is benchmark-controlled runtime dynamics and never an agent action.

This file is a planning companion to:

- `benchmark_design.md`, which explains the architecture modules that consume runtime APIs.
- `benchmark_timeline.md`, which defines where evidence, action, and feedback APIs appear in each decision step.
- `benchmark_task_list.md`, which lists runnable tasks that select these APIs.
- `benchmark_sum_type_list.md`, which lists the executable API sum types.
- `benchmark_stimulus_list.md`, which records benchmark-private stimulus drivers.

## Boundary

This file owns planning-level organization of runtime APIs for task design:
control APIs, evidence APIs, feedback APIs, benchmark-owned runtime-support
controls, non-selected runtime surfaces, and task-to-API mapping.

It does not own raw command syntax, remote host setup, live adapter readiness, schema validation code, or Benchmark Stimulus definitions.

Current runnable benchmark tasks use `E.runtime_adapter = simulated_ocudu`. Do not claim live OCUDU/FlexRIC execution from any API below unless a live adapter is implemented and passes readiness.

## API Categories

| Category | Agent-visible? | Runtime effect | Examples |
| --- | --- | --- | --- |
| Control API | yes, if task-selected | Agent can request a runtime action | PRB policy, SSB power, HO, CHO, CFO, TX time offset, core repair |
| Evidence API | yes, if task-selected | Agent reads redacted runtime evidence | JSON metrics, E2 KPM, ping, cell identity, UE identity |
| Feedback API | yes, after an action | Agent receives safe command-path result | accepted, rejected, safe error class |
| Oracle evidence | no during live loop | Scoring reads private or post-run evidence | E2 control oracle, KPM continuity, trace artifacts |
| Benchmark Stimulus | no as action | Benchmark changes runtime dynamics | load, mobility, radio condition, telemetry gap |

`NO_ACTION` is a benchmark decision, not a runtime API. It is recordable and scorable but must never dispatch to OCUDU.

## Task-Selectable Control APIs

| API kind | Action type | Backend | Runtime surface | Control target | Current status |
| --- | --- | --- | --- | --- | --- |
| `ocudu_websocket_prb_policy` | `SET_PRB_POLICY_RATIO_WS` | `websocket` | OCUDU WebSocket `rrm_policy_ratio_set` | PRB policy ratios | implemented simulated catalog binding |
| `ocudu_websocket_ssb_power` | `SET_SSB_BLOCK_POWER_WS` | `websocket` | OCUDU WebSocket `ssb_set` | SSB block power | implemented simulated catalog binding |
| `ocudu_cli_handover` | `TRIGGER_HANDOVER_CLI` | `ocudu_cli` | OCUDU CLI `ho` | immediate handover trigger | implemented simulated command-acceptance task |
| `ocudu_cli_conditional_handover` | `TRIGGER_CONDITIONAL_HANDOVER_CLI` | `ocudu_cli` | OCUDU CLI `cho` | conditional handover trigger | implemented simulated command-acceptance task |
| `ocudu_cli_cfo_control` | `SET_CFO_CLI` | `ocudu_cli` | OCUDU CLI `cfo` | carrier frequency offset compensation | implemented simulated command-acceptance task |
| `ocudu_cli_tx_time_offset_control` | `SET_TX_TIME_OFFSET_CLI` | `ocudu_cli` | OCUDU CLI `tx_time_offset` | transmit time offset | implemented simulated command-acceptance task |
| `e2sm_ccc_prb_policy_control` | `SET_PRB_POLICY_RATIO_CCC` | `e2_control` | FlexRIC E2SM-CCC `O-RRMPolicyRatio` | PRB policy ratios | implemented gated binding |
| `e2sm_rc_du_prb_quota_control` | `SET_PRB_POLICY_RATIO_RC_DU` | `e2_control` | FlexRIC E2SM-RC DU style 2 action 6 | DU PRB quota | implemented gated binding |
| `core_nf_lifecycle_control` | `RESTART_CORE_NF` | `core_control` | benchmark-mediated Open5GS lifecycle path | core NF restart | implemented simulated runtime-support task |
| `core_ue_registration_control` | `UPDATE_CORE_UE_REGISTRATION` | `core_control` | benchmark-mediated Open5GS subscriber update path | UE registration repair | implemented simulated runtime-support task |

`core_nf_lifecycle_control` and `core_ue_registration_control` are benchmark-owned runtime-support controls for OCUDU-suite experiments. They are not native OCUDU RAN commands.

## Control Payload Summary

| Action type | Required fields | Optional fields | Main safety constraint |
| --- | --- | --- | --- |
| `SET_PRB_POLICY_RATIO_WS` | `min_prb_policy_ratio`, `max_prb_policy_ratio` | `plmn`, `sst`, `sd`, `dedicated_ratio` | PRB ratios must remain bounded and ordered. |
| `SET_SSB_BLOCK_POWER_WS` | `nci`, `ssb_block_power_dbm` | `plmn` | Cell identity must match current task evidence. |
| `TRIGGER_HANDOVER_CLI` | `serving_pci`, `rnti`, `target_pci` | none | Serving and target identity must match visible UE mobility evidence. |
| `TRIGGER_CONDITIONAL_HANDOVER_CLI` | `serving_pci`, `rnti`, `target_pcis` | `timeout_s` | Target list must match visible multi-target path evidence. |
| `SET_CFO_CLI` | `sector_id`, `cfo_hz` | none | Sector and target CFO must match visible radio evidence. |
| `SET_TX_TIME_OFFSET_CLI` | `sector_id`, `tx_time_offset_us` | none | Sector and target offset must match visible radio evidence. |
| `SET_PRB_POLICY_RATIO_CCC` | `min_prb_policy_ratio`, `max_prb_policy_ratio` | `plmn`, `sst`, `sd`, `dedicated_ratio` | E2 control must be available and evidence-gated. |
| `SET_PRB_POLICY_RATIO_RC_DU` | `min_prb_policy_ratio`, `max_prb_policy_ratio` | `plmn`, `sst`, `sd`, `dedicated_ratio`, `du_ue_id` | DU UE identity must match visible evidence when used. |
| `RESTART_CORE_NF` | `nf` | none | Restart only benchmark-owned core NF targets. |
| `UPDATE_CORE_UE_REGISTRATION` | `ue_id`, `supi`, `plmn`, `dnn`, `sst`, `auth_profile_id` | `sd` | Raw subscriber keys stay backend-private. |

## Evidence APIs And Observation Sources

Catalog evidence APIs:

| API kind | Observation source | Backend | Agent-visible evidence |
| --- | --- | --- | --- |
| `ocudu_json_metrics` | `json_metrics` | `json_metrics` | metrics presence, freshness, sample count, parse errors |
| `e2sm_kpm_v05_observation` | `e2_kpm_v05` | `e2_kpm` | KPM availability, indication count, PRB-measurement availability |

Task-selected support observation sources:

| Source | Use in task design | Main redaction rule |
| --- | --- | --- |
| `ping` | service health and reachability | expose counters and success ratio, not raw runtime handles |
| `websocket_control_outcomes` | WebSocket action feedback context | expose safe command outcome only |
| `cell_identity` | SSB and stale-cell-identity tasks | expose current task cell identity only |
| `e2_control_outcome` | E2 control action context | expose accepted/safe status, not raw xApp handles |
| `ue_identity` | HO, CHO, and RC DU tasks | expose RNTI/PCI/DU UE id fields required by task |
| `ue_runtime` | future UE stimulus-observation tasks only | expose redacted UE process/traffic state only |
| `core_runtime` | core NF and UE-registration tasks | expose redacted NF state and registration fields |
| `radio_runtime` | CFO, TX offset, coverage/radio tasks | expose sector and target values; hide raw CLI/session details |
| `slice_runtime` | PRB rebalance and diagnosis tasks | expose active slice, demand, current policy, and repair targets only when the task uses `repair_targets` observation detail |
| `backhaul_runtime` | RAN-vs-transport isolation tasks | expose delay/loss/throughput summary only |

`slice_runtime` and `backhaul_runtime` are support observation sources, not agent
control APIs. They expose redacted runtime state produced by Benchmark Stimulus.

## Feedback API Rules

All task-selected action APIs produce safe feedback records.

| Feedback field class | Agent-visible content | Private content that must stay hidden |
| --- | --- | --- |
| acceptance | accepted or rejected status | raw command responses beyond safe summary |
| safe message | short task-safe explanation | stack traces, process ids, hostnames, ports, paths |
| safe error class | `schema_error`, `permission_error`, `timeout`, `runtime_rejected`, `runtime_unavailable`, `internal_redacted` | raw exceptions and backend handles |
| dispatch metadata | task-safe backend family if selected | raw sockets, container ids, shell command internals |

Feedback is immediate command-path feedback. It is not task success. Scoring runs only after trace and artifact finalization.

## Raw Runtime Surfaces Not Currently Task-Selected

| Runtime surface | Channel | Observed status | Planning decision |
| --- | --- | --- | --- |
| `tx_gain` | OCUDU CLI on ZMQ radio setup | command exists, but tested ZMQ runtime returned unsupported/not successful | do not expose as task API yet |
| `rx_gain` | OCUDU CLI on ZMQ radio setup | command exists, but tested ZMQ runtime returned unsupported/not successful | do not expose as task API yet |

Promotion requires source grounding, catalog entry, schema validation, tests, task projection, and clear simulated-vs-live labeling.

## Runtime API Mapping For Current Tasks

The 25-task base suite, 1-task regression suite, 8-task compound suite, and generated single-anchor
variants are executable with `E.runtime_adapter = simulated_ocudu`.

### Base Task Mapping

| Task | Control API expectation | Evidence API expectation | Notes |
| --- | --- | --- | --- |
| `base_prb_slice_congestion_rebalance_v1` | `SET_PRB_POLICY_RATIO_WS` | `ping`, `json_metrics`, `slice_runtime` | WebSocket PRB sibling for slice congestion. |
| `base_ssb_coverage_edge_recovery_v1` | `SET_SSB_BLOCK_POWER_WS` | `ping`, `cell_identity`, `radio_runtime` | Current cell identity must be visible before action. |
| `base_diagnosis_congestion_prb_v1` | `SET_PRB_POLICY_RATIO_WS` | `ping`, `json_metrics`, `slice_runtime`, `radio_runtime` | Sibling of coverage diagnosis. |
| `base_diagnosis_coverage_ssb_v1` | `SET_SSB_BLOCK_POWER_WS` | `ping`, `cell_identity`, `radio_runtime`, `slice_runtime` | Sibling of congestion diagnosis. |
| `base_isolation_backhaul_not_ran_v1` | `NO_ACTION` expected | `backhaul_runtime`, `radio_runtime`, `json_metrics` | Explicit transport evidence supports no RAN control. |
| `base_prb_stale_metrics_then_rebalance_v1` | PRB policy action after fresh evidence | `json_metrics`, `ping` | Temporal scoring should enforce no action during stale window. |
| `base_prb_telemetry_gap_fallback_v1` | delayed PRB or no action until evidence returns | `json_metrics`, `e2_kpm_v05`, `ping` | Gap itself is stimulus; action depends on later evidence. |
| `base_prb_e2_kpm_gated_v1` | `SET_PRB_POLICY_RATIO_CCC` | `e2_kpm_v05`, `slice_runtime` | E2 evidence availability gates E2 control. |
| `base_prb_ric_xapp_ws_fallback_v1` | WebSocket PRB fallback | backend availability, `ping`, `json_metrics` | E2 control unavailable, WebSocket still usable. |
| `base_prb_backend_e2_vs_ws_v1` | `SET_PRB_POLICY_RATIO_CCC` | backend status plus load evidence | Selects task-preferred E2 backend. |
| `base_mobility_immediate_handover_v1` | `TRIGGER_HANDOVER_CLI` | `ue_identity`, `ping` | Command acceptance only until live handover effect is proven. |
| `base_mobility_conditional_handover_planning_v1` | `TRIGGER_CONDITIONAL_HANDOVER_CLI` | `ue_identity`, `ping` | Multi-target path evidence belongs in `L_i`. |
| `base_ssb_wrong_cell_identity_trap_v1` | SSB or identity-dependent action | `cell_identity`, `radio_runtime` | Payload scoring should require current ID. |
| `base_radio_cli_cfo_correction_v1` | `SET_CFO_CLI` | `radio_runtime`, `ping` | Command acceptance and target-match scoring in simulator. |
| `base_radio_cli_tx_time_offset_correction_v1` | `SET_TX_TIME_OFFSET_CLI` | `radio_runtime`, `ping` | Command acceptance and target-match scoring in simulator. |
| `base_radio_cli_diagnose_cfo_vs_timing_v1` | `SET_CFO_CLI` | `radio_runtime`, `ping` | Current deterministic sibling chooses CFO after ambiguity. |
| `base_core_ue_registration_repair_v1` | `UPDATE_CORE_UE_REGISTRATION` | `core_runtime` | Uses `auth_profile_id`, never raw subscriber keys. |
| `base_core_nf_recovery_v1` | `RESTART_CORE_NF` | `core_runtime`, `ping` | Benchmark-owned runtime-support control. |
| `regression_harness_invalid_action_repair_v1` | task-specific repair action | feedback plus relevant evidence | Harness regression task, not primary benefit task. |
| `base_restraint_minimal_intervention_budget_v1` | `NO_ACTION` or at most one bounded action | `ping`, `json_metrics`, selected support evidence | Enforce action budget and no over-control. |

### Added Base Probe Mapping

| Task | Control API expectation | Evidence API expectation | Notes |
| --- | --- | --- | --- |
| `base_prb_overcorrection_restraint_v1` | bounded `SET_PRB_POLICY_RATIO_WS` | `slice_runtime`, `json_metrics`, `ping` | Tests ratio calibration rather than any PRB action. |
| `base_ssb_power_boundary_precision_v1` | exact `SET_SSB_BLOCK_POWER_WS` | `cell_identity`, `radio_runtime`, `ping` | Tests SSB target precision near a safe boundary. |
| `base_prb_e2_reject_ws_repair_v1` | rejected E2 PRB, then WebSocket PRB | `e2_kpm_v05`, feedback, `slice_runtime` | Tests runtime-rejection repair. |
| `base_mobility_reject_then_current_identity_repair_v1` | rejected stale HO, then current HO | `ue_identity`, feedback | Tests stale RNTI/PCI repair from feedback. |
| `base_core_ue_auth_profile_repair_v1` | `UPDATE_CORE_UE_REGISTRATION` | `core_runtime` | Tests auth-profile registration repair. |
| `base_core_nf_partial_recovery_no_repeat_v1` | `RESTART_CORE_NF` once | `core_runtime` | Tests no repeat under partial recovery evidence. |

### Generated Variant Axis Mapping

The former checked-in variant IDs below are now generated axis semantics. They
materialize as deterministic generated task IDs from
`benchmark/task_sets/generated/axis_registry.json`.

| Variant family | Tasks | Control API expectation | Evidence focus |
| --- | --- | --- | --- |
| PRB demand severity and precision | `slice_congestion_prb_rebalance_mild_v1`, `slice_congestion_prb_rebalance_severe_v1`, `slice_congestion_prb_ratio_precision_v1`, `slice_congestion_prb_delayed_demand_v1` | `SET_PRB_POLICY_RATIO_WS` | `slice_runtime`, `json_metrics`, `ping` |
| SSB coverage severity and identity | `coverage_edge_ssb_mild_v1`, `coverage_edge_ssb_severe_v1`, `coverage_edge_ssb_current_cell_shift_v1`, `coverage_edge_ssb_no_cell_change_v1` | `SET_SSB_BLOCK_POWER_WS` | `cell_identity`, `radio_runtime`, `ping` |
| Evidence gating | `stale_metrics_two_step_wait_prb_v1`, `telemetry_gap_long_wait_prb_v1`, `kpm_late_available_prb_v1`, `fresh_metrics_low_load_no_action_v1` | WebSocket PRB, E2 PRB, or `NO_ACTION` per task | metrics freshness, telemetry availability, KPM availability, low-load evidence |
| Diagnosis | `diagnose_congestion_high_slice_good_radio_v1`, `diagnose_coverage_bad_radio_nominal_slice_v1`, `diagnose_congestion_vs_coverage_ambiguous_then_decisive_v1`, `diagnose_cfo_vs_timing_timing_branch_v1` | PRB, SSB, or TX offset per decisive evidence | paired positive and negative evidence sources |
| Backend selection | `api_backend_selection_ws_only_v1`, `api_backend_selection_e2_late_v1`, `ric_xapp_recovery_prefers_e2_v1` | WebSocket PRB or E2 PRB per backend availability | backend status, KPM evidence, slice runtime |
| Mobility | `immediate_handover_stale_rnti_trap_v1`, `conditional_handover_multitarget_long_v1` | immediate HO or CHO | `ue_identity`, current RNTI/PCI, target candidates |
| Core control | `core_ue_registration_plmn_repair_v1`, `core_nf_upf_recovery_v1` | UE-registration repair or UPF restart | `core_runtime` registration/NF status |
| Restraint | `minimal_intervention_transient_recovery_v1` | `NO_ACTION` | fresh metrics, mild transient radio evidence |

### Compound Suite Mapping

| Compound family | Tasks | Control API expectation | Evidence focus |
| --- | --- | --- | --- |
| Latent congestion/coverage/backhaul | `compound_diagnosis_congestion_vs_coverage_v1`, `compound_diagnosis_coverage_vs_backhaul_v1`, `compound_isolation_backhaul_not_ran_v1` | PRB, SSB, or no-action | slice, radio, backhaul, ping |
| Latent backend/identity | `compound_fallback_e2_outage_ws_v1`, `compound_identity_stale_cell_then_ssb_v1` | WebSocket PRB or current-NCI SSB | backend/KPM feedback or cell identity |
| Latent radio/mobility/core | `compound_radio_cfo_vs_timing_v1`, `compound_core_vs_ran_failure_v1`, `compound_mobility_vs_coverage_v1` | TX offset, core NF restart, or HO | radio symptoms, core runtime, UE identity |

Compound tasks use `I.observation_detail = diagnosis_symptoms` where direct
repair targets would make root-cause inference trivial.

## Promotion Checklist

Before a runtime surface becomes a task-selectable API:

1. Record the runtime surface and source basis in this file.
2. Add or update `RanApiKind`, `RanActionType`, `RanApiBackend`, and observation sources as needed.
3. Add a static catalog descriptor with roles, backend, runtime requirements, request fields, response fields, and safe error classes.
4. Add action schema validation and runtime request serialization.
5. Add observation or feedback redaction tests.
6. Add task manifests that select only the needed API projection.
7. Add scoring that checks action type, accepted payload fields, temporal behavior, and simulated post-action evidence when the task declares an effect expectation.
8. Validate locally and remotely under `simulated_ocudu` before any live-runtime claim.

## Review Rules

- Runtime API projection is task-selected; agents must not see APIs outside the current task.
- Raw command names are not agent action types.
- `NO_ACTION` is benchmark-only and must never dispatch.
- UE traffic and UE lifecycle behavior are Benchmark Stimulus, not current control APIs.
- Core runtime-support controls must stay labeled as benchmark-owned support controls, not native OCUDU RAN commands.
- Live OCUDU/FlexRIC claims require a live adapter and readiness evidence.
