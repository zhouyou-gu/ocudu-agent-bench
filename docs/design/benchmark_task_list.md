# Benchmark Task List

## Purpose

This file records the executable benchmark task inventory and the replacement
history for retired task IDs.

It separates:

- current runnable task manifests under `benchmark/task_sets/{base,regression,compound}/<family>/<task_id>/task.json`;
- retired task families that were hard-replaced by the multistep task set;
- regression-only tasks that are runnable but not primary agent-benefit claims.

This file does not own runtime API definitions, Benchmark Stimulus driver definitions, raw OCUDU command inventory, or scoring enum definitions. Those belong in `benchmark_runtime_api_list.md`, `benchmark_stimulus_list.md`, and `benchmark_sum_type_list.md`.

Related files:

- `benchmark_design.md` explains the architecture used by current tasks.
- `benchmark_timeline.md` defines the 2-4 step episode model.
- `benchmark_runtime_api_list.md` maps tasks to control, evidence, and feedback APIs.
- `benchmark_stimulus_list.md` maps tasks to `L_i` and `D_i` stimulus choices.
- `benchmark_sum_type_list.md` defines the action, observation, stimulus, and scoring enums referenced by task manifests.

## Task Status Labels

| Status | Meaning |
| --- | --- |
| `runnable_simulated` | Exists as a task manifest and runs through `E.runtime_adapter = simulated_ocudu`. |
| `regression_only` | Useful for harness correctness, but not a primary agent-benefit benchmark task. |
| `generated_axis` | No checked-in task directory; materializes as a deterministic generated task instance. |
| `deferred_live_adapter` | Requires a live adapter or real runtime effect before it can support a live-runtime claim. |
| `retired_replaced` | Old runnable simulated task removed from the primary task surface after hard replacement. |

Current runnable tasks must not be described as live OCUDU/FlexRIC tasks unless a live adapter is implemented and passes readiness.

## Current Runnable Tasks

Source of truth: `benchmark/task_sets/{base,regression,compound}/<family>/<task_id>/task.json`,
`benchmark/task_sets/generated/{axis_registry.json,suite_policies.json}`, and
`python3 benchmark/benchctl.py --json tasks list --suite ...`.

The current simulated surface has four runnable layers:

- `base`: 25 primary checked-in base tasks under `benchmark/task_sets/base/`.
- `regression`: 1 checked-in harness task under
  `benchmark/task_sets/regression/`.
- `compound`: 8 checked-in latent-cause diagnosis tasks under
  `benchmark/task_sets/compound/`.
- `generated`: deterministic in-memory single-anchor variants from
  `benchmark/task_sets/generated/`.

### Base Tasks

| Task | Status | Steps | Main action expectation | Evidence focus | Scoring intent |
| --- | --- | ---: | --- | --- | --- |
| `base_prb_slice_congestion_rebalance_v1` | `runnable_simulated` | 3 | `NO_ACTION`, `SET_PRB_POLICY_RATIO_WS`, `NO_ACTION` | ping, JSON metrics, slice runtime | PRB control only under visible slice demand |
| `base_ssb_coverage_edge_recovery_v1` | `runnable_simulated` | 3 | `NO_ACTION`, `SET_SSB_BLOCK_POWER_WS`, `NO_ACTION` | ping, cell identity, radio runtime | SSB recovery with current cell identity |
| `base_diagnosis_congestion_prb_v1` | `runnable_simulated` | 3 | `NO_ACTION`, PRB action, `NO_ACTION` | slice/load evidence, healthy radio | congestion-vs-coverage sibling selecting PRB |
| `base_diagnosis_coverage_ssb_v1` | `runnable_simulated` | 3 | `NO_ACTION`, SSB action, `NO_ACTION` | radio edge evidence, nominal slice demand | congestion-vs-coverage sibling selecting SSB |
| `base_isolation_backhaul_not_ran_v1` | `runnable_simulated` | 3 | `NO_ACTION` throughout | backhaul runtime, healthy RAN evidence | avoid RAN control for transport impairment |
| `base_prb_stale_metrics_then_rebalance_v1` | `runnable_simulated` | 3 | stale no-action, PRB, no-action | stale/fresh JSON metrics, slice runtime | evidence-gated PRB control |
| `base_prb_telemetry_gap_fallback_v1` | `runnable_simulated` | 4 | no-action, no-action during gap, PRB, no-action | telemetry gap, fresh metrics, slice runtime | fallback/restraint across missing evidence |
| `base_prb_e2_kpm_gated_v1` | `runnable_simulated` | 3 | no-action until KPM, E2 PRB, no-action | E2 KPM, E2 outcome, slice runtime | E2 evidence-gated control |
| `base_prb_ric_xapp_ws_fallback_v1` | `runnable_simulated` | 3 | no-action, WebSocket PRB, no-action | RIC/xApp backend status, slice runtime | fallback when E2 control is unavailable |
| `base_prb_backend_e2_vs_ws_v1` | `runnable_simulated` | 3 | no-action, E2 PRB, no-action | E2 KPM/control outcome, slice runtime | select task-preferred backend |
| `base_mobility_immediate_handover_v1` | `runnable_simulated` | 3 | no-action, immediate HO, no-action | UE identity, mobility path | immediate handover command acceptance |
| `base_mobility_conditional_handover_planning_v1` | `runnable_simulated` | 3 | no-action, CHO, no-action | UE identity, multi-target mobility path | conditional handover command acceptance |
| `base_ssb_wrong_cell_identity_trap_v1` | `runnable_simulated` | 3 | stale identity no-action, current-ID SSB, no-action | cell identity, radio runtime | reject stale-cell payload use |
| `base_radio_cli_cfo_correction_v1` | `runnable_simulated` | 2 | CFO correction, no-action | radio runtime target | numeric CFO correction and no repeat |
| `base_radio_cli_tx_time_offset_correction_v1` | `runnable_simulated` | 2 | TX offset correction, no-action | radio runtime target | numeric timing correction and no repeat |
| `base_radio_cli_diagnose_cfo_vs_timing_v1` | `runnable_simulated` | 4 | no-action, no-action, CFO correction, no-action | ambiguous then discriminating radio evidence | avoid early radio-adjustment guess |
| `base_core_ue_registration_repair_v1` | `runnable_simulated` | 2 | UE registration repair, no-action | core runtime registration evidence | benchmark-owned core registration repair |
| `base_core_nf_recovery_v1` | `runnable_simulated` | 3 | no-action, core NF restart, no-action | core runtime, ping, metrics | benchmark-owned core recovery |
| `regression_harness_invalid_action_repair_v1` | `regression_only` | 3 | invalid local rejection, valid PRB repair, no-action | feedback, WebSocket outcome | harness regression for rejection/repair |
| `base_restraint_minimal_intervention_budget_v1` | `runnable_simulated` | 3 | `NO_ACTION` throughout | ping, metrics, mild radio/slice evidence | avoid over-control |

### Added Base Probes

| Task | Status | Steps | Main action expectation | Evidence focus | Scoring intent |
| --- | --- | ---: | --- | --- | --- |
| `base_prb_overcorrection_restraint_v1` | `runnable_simulated` | 3 | no-action, bounded WebSocket PRB, no-action | slice pressure and target policy | distinguish calibrated PRB from over-control |
| `base_ssb_power_boundary_precision_v1` | `runnable_simulated` | 3 | no-action, exact SSB boundary power, no-action | current cell and radio symptoms | payload precision at safe SSB boundary |
| `base_prb_e2_reject_ws_repair_v1` | `runnable_simulated` | 3 | rejected E2, WebSocket repair, no-action | E2 feedback and slice runtime | repair from runtime rejection feedback |
| `base_mobility_reject_then_current_identity_repair_v1` | `runnable_simulated` | 3 | rejected stale HO, current HO, no-action | UE identity and feedback | recover from stale RNTI/PCI feedback |
| `base_core_ue_auth_profile_repair_v1` | `runnable_simulated` | 2 | auth-profile registration repair, no-action | core UE registration | core payload precision beyond PLMN/SUPI |
| `base_core_nf_partial_recovery_no_repeat_v1` | `runnable_simulated` | 3 | no-action, NF restart, no-action | partially recovered core runtime | avoid repeat restart after accepted action |

### Generated Higher-Resolution Variant Axes

These old checked-in variant task IDs are not directories anymore. Their
semantics are represented as levels in
`benchmark/task_sets/generated/axis_registry.json` and materialize as
deterministic generated task IDs at runtime.

| Former task semantic | Generated status | Steps | Variant axis | Main action expectation |
| --- | --- | ---: | --- | --- |
| `slice_congestion_prb_rebalance_mild_v1` | `generated_axis` | 3 | PRB demand severity: mild | no-action, WebSocket PRB, no-action |
| `slice_congestion_prb_rebalance_severe_v1` | `generated_axis` | 3 | PRB demand severity: severe | no-action, WebSocket PRB, no-action |
| `slice_congestion_prb_ratio_precision_v1` | `generated_axis` | 3 | PRB payload precision | no-action, exact-ratio WebSocket PRB, no-action |
| `slice_congestion_prb_delayed_demand_v1` | `generated_axis` | 4 | PRB demand timing | no-action, no-action, WebSocket PRB, no-action |
| `coverage_edge_ssb_mild_v1` | `generated_axis` | 3 | coverage severity: mild | no-action, SSB power, no-action |
| `coverage_edge_ssb_severe_v1` | `generated_axis` | 3 | coverage severity: severe | no-action, SSB power, no-action |
| `coverage_edge_ssb_current_cell_shift_v1` | `generated_axis` | 3 | current-cell identity shift | no-action, SSB power with current NCI, no-action |
| `coverage_edge_ssb_no_cell_change_v1` | `generated_axis` | 3 | stable cell identity | no-action, SSB power with unchanged NCI, no-action |
| `stale_metrics_two_step_wait_prb_v1` | `generated_axis` | 4 | stale metrics duration | no-action, no-action, WebSocket PRB, no-action |
| `telemetry_gap_long_wait_prb_v1` | `generated_axis` | 4 | telemetry-gap restraint | no-action, no-action during gap, WebSocket PRB, no-action |
| `kpm_late_available_prb_v1` | `generated_axis` | 4 | KPM availability timing | no-action, no-action, E2 PRB, no-action |
| `fresh_metrics_low_load_no_action_v1` | `generated_axis` | 3 | fresh low-load restraint | no-action throughout |
| `diagnose_congestion_high_slice_good_radio_v1` | `generated_axis` | 3 | diagnosis root cause: congestion | no-action, WebSocket PRB, no-action |
| `diagnose_coverage_bad_radio_nominal_slice_v1` | `generated_axis` | 3 | diagnosis root cause: coverage | no-action, SSB power, no-action |
| `diagnose_congestion_vs_coverage_ambiguous_then_decisive_v1` | `generated_axis` | 4 | ambiguity then decisive congestion | no-action, no-action, WebSocket PRB, no-action |
| `diagnose_cfo_vs_timing_timing_branch_v1` | `generated_axis` | 4 | radio-adjustment branch: timing | no-action, no-action, TX offset, no-action |
| `api_backend_selection_ws_only_v1` | `generated_axis` | 3 | backend availability: WebSocket only | no-action, WebSocket PRB, no-action |
| `api_backend_selection_e2_late_v1` | `generated_axis` | 4 | backend availability: late E2 | no-action, no-action, E2 PRB, no-action |
| `ric_xapp_recovery_prefers_e2_v1` | `generated_axis` | 4 | RIC/xApp lifecycle recovery | no-action, no-action, E2 PRB, no-action |
| `immediate_handover_stale_rnti_trap_v1` | `generated_axis` | 3 | mobility identity freshness | no-action, immediate HO with current RNTI, no-action |
| `conditional_handover_multitarget_long_v1` | `generated_axis` | 3 | CHO target-set size | no-action, CHO with three targets, no-action |
| `core_ue_registration_plmn_repair_v1` | `generated_axis` | 2 | core registration mismatch field | PLMN registration repair, no-action |
| `core_nf_upf_recovery_v1` | `generated_axis` | 3 | core NF target | no-action, UPF restart, no-action |
| `minimal_intervention_transient_recovery_v1` | `generated_axis` | 3 | transient recovery restraint | no-action throughout |

### Latent-Cause Compound Tasks

These tasks are loaded with `--suite compound`. They are deterministic and
checked in, but the agent-visible task view does not disclose the underlying
cause sequence.

| Task | Status | Steps | Expected diagnosis/control |
| --- | --- | ---: | --- |
| `compound_diagnosis_congestion_vs_coverage_v1` | `runnable_simulated` | 4 | wait through ambiguity, then PRB |
| `compound_diagnosis_coverage_vs_backhaul_v1` | `runnable_simulated` | 3 | choose SSB over backhaul explanation |
| `compound_isolation_backhaul_not_ran_v1` | `runnable_simulated` | 3 | no RAN action |
| `compound_fallback_e2_outage_ws_v1` | `runnable_simulated` | 3 | WebSocket PRB fallback |
| `compound_identity_stale_cell_then_ssb_v1` | `runnable_simulated` | 3 | current-NCI SSB action |
| `compound_radio_cfo_vs_timing_v1` | `runnable_simulated` | 4 | wait, then TX time-offset correction |
| `compound_core_vs_ran_failure_v1` | `runnable_simulated` | 3 | core NF restart, not PRB/SSB |
| `compound_mobility_vs_coverage_v1` | `runnable_simulated` | 3 | mobility control, not SSB |

## Hard-Replaced Task Families

| Old family | Replacement |
| --- | --- |
| WebSocket PRB control | `base_prb_slice_congestion_rebalance_v1`, `base_prb_stale_metrics_then_rebalance_v1`, `base_restraint_minimal_intervention_budget_v1`, `regression_harness_invalid_action_repair_v1` |
| WebSocket SSB control | `base_ssb_coverage_edge_recovery_v1`, `base_diagnosis_coverage_ssb_v1`, `base_ssb_wrong_cell_identity_trap_v1` |
| FlexRIC/E2 control and evidence | `base_prb_e2_kpm_gated_v1`, `base_prb_ric_xapp_ws_fallback_v1`, `base_prb_backend_e2_vs_ws_v1` |
| Broad triage | deterministic sibling tasks: `base_diagnosis_congestion_prb_v1`, `base_diagnosis_coverage_ssb_v1`, `base_radio_cli_diagnose_cfo_vs_timing_v1`, `base_isolation_backhaul_not_ran_v1` |
| CLI mobility and radio adjustment | `base_mobility_immediate_handover_v1`, `base_mobility_conditional_handover_planning_v1`, `base_radio_cli_cfo_correction_v1`, `base_radio_cli_tx_time_offset_correction_v1`, `base_radio_cli_diagnose_cfo_vs_timing_v1` |
| Benchmark-owned core support | `base_core_nf_recovery_v1`, `base_core_ue_registration_repair_v1` |
| UE stimulus-only task IDs | retired; UE traffic/lifecycle behavior remains Benchmark Stimulus, not agent control |

## Implemented Multistep Mechanics

- Task events support `apply_steps` or inclusive `start_step` / `end_step`.
- Task scoring supports `J.temporal_expectations` via `temporal_action_sequence_match`.
- Task scoring supports `J.expected_action_fields` via `expected_action_payload_match`.
- Task scoring supports `J.expected_post_action_evidence` via `post_action_evidence_match` for simulated closed-loop effect checks.
- Variant tasks can carry private `M.variant` metadata for family, axis, level, and base task id.
- Generated variants add private `suite`, `variant_id`, `seed`,
  `axis_values`, and `expected_failure_modes` fields under `M.variant`.
- Variant tasks may request non-critical diagnostic similarity metrics for timing, payload, and post-action evidence.
- Compound diagnosis tasks may set `I.observation_detail =
  diagnosis_symptoms` to expose symptoms while hiding direct repair targets.
- `slice_runtime` and `backhaul_runtime` are redacted observation sources for the tasks that need them.

## Promotion Rules

A checked-in or generated task stays `runnable_simulated` only when:

- it has a task manifest under the relevant checked-in suite directory, or is
  generated deterministically from the generated axis registry;
- it uses `E.runtime_adapter = simulated_ocudu`;
- selected APIs exist in the static catalog;
- selected stimulus drivers are implemented and deterministic;
- observations and feedback are redacted;
- scoring runs only after trace and artifact finalization;
- local tests, compile checks, and a simulated `auto` episode pass;
- remote sync and remote simulated smoke pass when remote validation is in scope.

## Review Rules

- Use deterministic sibling tasks instead of hidden seed-random branches for diagnosis scenarios.
- Keep task length to 2-4 decision steps unless the task has explicit justification.
- Do not create a primary agent-benefit task that requires the agent to make a bad first action; keep those cases regression-only.
- Do not reclassify UE traffic or UE lifecycle behavior as agent control APIs.
- Do not claim live OCUDU/FlexRIC execution for a task until a live adapter passes readiness.
