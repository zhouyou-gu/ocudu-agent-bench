# Benchmark Stimulus List

## Purpose

This file records and plans Benchmark Stimulus for multistep OCUDUAgentBench tasks.

Benchmark Stimulus means benchmark-controlled OCUDU runtime dynamics. It is private, deterministic, seed-controlled, and never an agent action. The agent controls only task-selected runtime APIs or `NO_ACTION`.

This file is a planning companion to:

- `benchmark_design.md`, which explains the architecture modules that apply and hide Benchmark Stimulus.
- `benchmark_timeline.md`, which defines `L_i` and `D_i` semantics.
- `benchmark_task_list.md`, which lists runnable tasks that consume stimulus patterns.
- `benchmark_runtime_api_list.md`, which records the agent-controlled APIs that remain separate from Benchmark Stimulus.
- `benchmark_sum_type_list.md`, which lists implemented stimulus driver sum types.

This file owns stimulus planning for task design: driver inventory, `L_i` and
`D_i` placement, step patterns, and task-to-stimulus mapping. It does not own
agent action APIs, task status inventory, raw runtime command names, or scoring
enum definitions.

## Timeline Semantics

For step `i`:

- `L_i` is pre-observation stimulus. It establishes the runtime condition visible in observation `E_i`.
- `D_i` is in-step stimulus. It keeps runtime pressure active while the agent reasons and while the action or no-action is applied.
- `D_i` must not introduce hidden facts required for the same-step decision. If the agent needs the fact to choose `A_i`, that fact belongs in `L_i`.

The current simulator supports `phase = pre_observation`, `phase = in_step`,
`apply_steps`, and inclusive `start_step` / `end_step`. Existing manifests
without step targeting expand each event across all steps.

## Current Driver Inventory

All drivers below are implemented in the current `simulated_ocudu` adapter. Live OCUDU/FlexRIC effects remain future adapter work unless a live adapter passes readiness.

| Driver | Current use | Main runtime state touched |
| --- | --- | --- |
| `docker_zmq_runtime_launch` | runtime setup condition | `runtime_condition` |
| `ue_ping_traffic` | reachability, health, load proxy | `ping`, `ue_runtime` |
| `metrics_staleness_mask` | evidence freshness gating | `metrics` |
| `ue_activity_churn` | UE attach/detach/restart/reconnect stimulus | `ue_runtime` |
| `core_ue_registration_misconfig` | core subscriber mismatch stimulus | `core_runtime.ue_registration` |
| `traffic_load_profile` | application demand pressure | `ue_runtime`, `traffic_load` |
| `mobility_path` | UE movement and target-cell pressure | `ue_identity` |
| `radio_condition_profile` | coverage and RF-quality pressure | `radio_runtime` |
| `slice_demand_shift` | slice/service demand evidence | `slice_runtime` |
| `telemetry_gap` | observation-source withholding | `telemetry_gap`, `metrics`, `e2` |
| `e2_kpm_availability_window` | E2 KPM evidence gating | `e2` |
| `ric_xapp_lifecycle` | RIC/xApp availability pressure | `e2`, `backend.e2_control` |
| `core_latency_profile` | core-path latency/loss pressure | `core_runtime.latency_profile` |
| `backhaul_impairment` | transport-path delay/loss/throughput pressure | `backhaul_runtime`, `ping` |
| `cell_identity_change` | current cell identity evidence | `cell_identity`, `ue_identity.serving_pci` |
| `future_zmq_impairment` | simulated-only sample-path impairment state | `radio_runtime.zmq_impairment` |

## `L_i` Stimulus Design

Use `L_i` for facts the agent may legitimately use in the same step.

| Driver | `L_i` fit | Planning notes |
| --- | --- | --- |
| `docker_zmq_runtime_launch` | Strong | Establishes launched runtime condition. Usually step 1 only after step targeting exists. |
| `ue_ping_traffic` | Strong | Establishes visible reachability and UE traffic health. Useful in baseline and verification steps. |
| `metrics_staleness_mask` | Strong | Establishes whether metrics are fresh enough to act. Evidence-gated tasks should put stale/fresh status in `L_i`. |
| `traffic_load_profile` | Strong | Establishes load pressure before PRB decisions. Pair with `ue_ping_traffic` for visible service impact. |
| `slice_demand_shift` | Strong | Establishes target slice and PRB target evidence. Best for PRB rebalance tasks. |
| `radio_condition_profile` | Strong | Establishes coverage-edge or radio-quality evidence before SSB, CFO, or timing decisions. |
| `mobility_path` | Strong | Establishes serving/target PCI and UE identity before HO or CHO actions. |
| `cell_identity_change` | Strong | Establishes current identity before SSB or stale-identity trap actions. |
| `telemetry_gap` | Strong | Establishes missing evidence; action should usually be restrained until evidence returns. |
| `e2_kpm_availability_window` | Strong | Establishes whether E2-dependent evidence is available. |
| `ric_xapp_lifecycle` | Strong | Establishes whether E2 control backend is usable or fallback is needed. |
| `core_ue_registration_misconfig` | Strong | Establishes core registration mismatch and desired repair fields. |
| `core_latency_profile` | Strong | Establishes core-path degradation for core-vs-RAN diagnosis. |
| `backhaul_impairment` | Strong | Establishes non-RAN transport impairment; expose enough redacted evidence to avoid RAN over-control. |
| `ue_activity_churn` | Medium | Useful as context for UE availability tasks; avoid making agent repair UE lifecycle unless UE control is reintroduced as an action. |
| `future_zmq_impairment` | Medium | Useful for simulated RF impairment evidence; do not claim live sample-path control. |

## `D_i` Stimulus Design

Use `D_i` for pressure that remains active during reasoning/action. Keep same-step decision clues in `L_i`.

| Driver | `D_i` fit | Planning notes |
| --- | --- | --- |
| `ue_ping_traffic` | Strong | Default in-step pressure. Shows service health while actions apply. |
| `traffic_load_profile` | Strong | Keeps load active during PRB decisions. Pair with `ue_ping_traffic` in congestion tasks. |
| `radio_condition_profile` | Strong | Keeps coverage-edge condition active while SSB or radio adjustments apply. |
| `mobility_path` | Strong | Keeps mobility pressure active during HO/CHO decisions. |
| `backhaul_impairment` | Strong | Keeps transport impairment active while scoring no-RAN-action restraint. |
| `core_latency_profile` | Strong | Keeps core impairment active during core repair or isolation tasks. |
| `future_zmq_impairment` | Medium | Suitable only as simulated-only pressure; agent-visible observation must stay redacted. |
| `telemetry_gap` | Medium | Safe if the task scores restraint during the gap. Do not hide needed same-step evidence in `D_i`. |
| `e2_kpm_availability_window` | Medium | Safe for backend availability pressure across a full step; decisive availability should still be visible from `L_i`. |
| `ric_xapp_lifecycle` | Medium | Safe for fallback pressure when backend status is already visible. Avoid surprise same-step backend changes. |
| `ue_activity_churn` | Medium | Can keep UE population unstable, but may blur attribution unless task goal is UE dynamics resilience. |
| `metrics_staleness_mask` | Low | Prefer `L_i`; freshness is decision evidence, not hidden in-step pressure. |
| `slice_demand_shift` | Low | Prefer `L_i`; target slice and desired PRB policy are decision facts. |
| `cell_identity_change` | Low | Prefer `L_i`; current identity must be visible before identity-dependent action. |
| `core_ue_registration_misconfig` | Low | Prefer `L_i`; desired/current mismatch is the repair target. |
| `docker_zmq_runtime_launch` | Avoid | Runtime launch is setup or step-1 context, not recurring in-step pressure. |

## Recommended Step Patterns

| Pattern | Steps | `L_i` role | `D_i` role | Expected sequence |
| --- | ---: | --- | --- | --- |
| Simple numeric correction | 2 | Expose target in step 1, repaired state in step 2 | Keep service/radio pressure active | action, then no repeat |
| Direct control | 3 | healthy baseline, fault, post-action evidence | Keep load/radio/mobility pressure active | no-action, action, no-action |
| Evidence-gated control | 3 | blocked evidence, valid evidence, post-action evidence | Keep service pressure active | no-action, action, no-action |
| Diagnosis | 3 | baseline, discriminating evidence, post-action evidence | Keep selected pressure active | no-action, correct action, no-action |
| Ambiguous diagnosis | 4 | baseline, ambiguous symptom, discriminating evidence, post-action evidence | Keep symptom pressure active | no-action, no-action, action, no-action |
| Feedback repair | 3 | unsafe/rejected condition, feedback-informed repair, post-repair evidence | Keep pressure active while repair applies | repair action, valid action, no-action |
| No-action restraint | 3 | healthy, mild transient, recovered or still non-RAN evidence | Keep transient pressure active | no-action throughout or one bounded action |

## Current Task Stimulus Matrix

These base and regression task names are current runnable simulated manifests
after the task-set restructure. The exact payloads live in
`benchmark/task_sets/{base,regression}/<family>/<task_id>/task.json`.

| Task | Steps | Primary `L_i` | Primary `D_i` | Expected agent benefit |
| --- | ---: | --- | --- | --- |
| `base_prb_slice_congestion_rebalance_v1` | 3 | L1 nominal ping; L2 `slice_demand_shift` + `traffic_load_profile`; L3 post-action slice evidence | D1 ping; D2 load + ping; D3 load + ping | Rebalance PRB only when slice pressure appears. |
| `base_ssb_coverage_edge_recovery_v1` | 3 | L1 healthy radio; L2 `radio_condition_profile` edge + `cell_identity_change`; L3 edge persists after action | D2/D3 radio edge + ping | Raise SSB power with current identity, then avoid repeat. |
| `base_diagnosis_congestion_prb_v1` | 3 | L1 baseline; L2 high load with healthy radio; L3 post-action evidence | D2/D3 load + ping | Choose PRB for congestion, not radio power. |
| `base_diagnosis_coverage_ssb_v1` | 3 | L1 baseline; L2 poor radio with normal demand; L3 post-action evidence | D2/D3 radio edge + ping | Choose SSB/radio action for coverage, not PRB. |
| `base_isolation_backhaul_not_ran_v1` | 3 | L1 healthy; L2 `backhaul_impairment` + healthy RAN evidence; L3 impairment persists or clears | D2/D3 backhaul + ping | Avoid RAN control when transport path is root cause. |
| `base_prb_stale_metrics_then_rebalance_v1` | 3 | L1 stale metrics; L2 fresh metrics + load; L3 post-action evidence | ping or load + ping | Wait for reliable evidence before PRB control. |
| `base_prb_telemetry_gap_fallback_v1` | 4 | L1 baseline; L2 `telemetry_gap`; L3 evidence returns with load; L4 post-action evidence | D2 telemetry gap only for restraint; D3/D4 load + ping | Avoid unsafe action during gap, act after decisive evidence. |
| `base_prb_e2_kpm_gated_v1` | 3 | L1 KPM unavailable; L2 KPM available + PRB evidence; L3 post-action evidence | ping/load | Gate E2-dependent action on KPM availability. |
| `base_prb_ric_xapp_ws_fallback_v1` | 3 | L1 xApp stopped/delayed; L2 fallback path visible; L3 post-action evidence | ping/load; optional xApp outage pressure | Fall back from unavailable E2 control to WebSocket PRB. |
| `base_prb_backend_e2_vs_ws_v1` | 3 | L1 both available; L2 one backend degraded; L3 post-action evidence | load + ping | Select the task-preferred safe backend. |
| `base_mobility_immediate_handover_v1` | 3 | L1 baseline mobility; L2 immediate target from `mobility_path`; L3 post-action UE identity | mobility + ping | Trigger immediate HO only for clear target. |
| `base_mobility_conditional_handover_planning_v1` | 3 | L1 baseline; L2 multi-target `mobility_path`; L3 post-action evidence | mobility + ping | Use CHO when path has future target candidates. |
| `base_ssb_wrong_cell_identity_trap_v1` | 3 | L1 old identity no-action; L2 `cell_identity_change`; L3 post-action identity | radio + ping | Use current NCI/PCI, not stale identity. |
| `base_radio_cli_cfo_correction_v1` | 2 | L1 radio target CFO; L2 post-action radio evidence | radio + ping | Apply one CFO correction and stop. |
| `base_radio_cli_tx_time_offset_correction_v1` | 2 | L1 radio target TX offset; L2 post-action radio evidence | radio + ping | Apply one TX timing correction and stop. |
| `base_radio_cli_diagnose_cfo_vs_timing_v1` | 4 | L1 baseline; L2 ambiguous radio issue; L3 discriminating CFO or timing evidence; L4 post-action evidence | radio + ping | Avoid early guess, then choose correct radio adjustment. |
| `base_core_ue_registration_repair_v1` | 2 | L1 `core_ue_registration_misconfig`; L2 repaired/current registration | core latency optional | Repair visible core UE registration mismatch once. |
| `base_core_nf_recovery_v1` | 3 | L1 core degraded; L2 NF restart target; L3 recovered/stable core evidence | core latency + ping | Recover core NF without repeated restarts. |
| `regression_harness_invalid_action_repair_v1` | 3 | L1 unsafe/rejection setup; L2 valid repair fields; L3 post-repair evidence | service pressure | Harness regression for feedback repair, not primary agent-benefit task. |
| `base_restraint_minimal_intervention_budget_v1` | 3 | L1 healthy; L2 mild transient; L3 recovered | mild ping/load pressure | Prefer no-action or at most one bounded action. |
| `base_prb_overcorrection_restraint_v1` | 3 | L1 healthy; L2 moderate slice pressure; L3 post-action slice evidence | moderate load + ping | Calibrate PRB ratios without overcorrection. |
| `base_ssb_power_boundary_precision_v1` | 3 | L1 healthy identity; L2 boundary SSB target; L3 post-action radio evidence | radio edge + ping | Hit exact SSB boundary power and stop. |
| `base_prb_e2_reject_ws_repair_v1` | 3 | L1 E2 appears usable but rejects in-step; L2 feedback-informed WS repair; L3 post-action evidence | load + backend pressure | Repair from E2 rejection through WebSocket. |
| `base_mobility_reject_then_current_identity_repair_v1` | 3 | L1 stale HO identity rejected; L2 current identity; L3 post-action UE evidence | mobility + ping | Recover from stale RNTI/PCI feedback. |
| `base_core_ue_auth_profile_repair_v1` | 2 | L1 auth-profile mismatch; L2 repaired registration | core runtime evidence | Repair redacted core UE auth profile once. |
| `base_core_nf_partial_recovery_no_repeat_v1` | 3 | L1 healthy; L2 degraded NF; L3 partial recovery after restart | core latency + ping | Avoid repeat restart under partial recovery. |

## Higher-Resolution Variant Stimulus Matrix

Generated parameter variants reuse the same driver set and change one stimulus
axis at a time. They apply the rule in memory from
`benchmark/task_sets/generated/axis_registry.json`: one base task plus a
deterministic variant vector. Each generated task carries private `M.variant`
metadata so analysis can group results without exposing the stimulus schedule to
the agent.

| Variant family | Tasks | Main `L_i` / `D_i` change |
| --- | --- | --- |
| PRB demand severity and precision | `slice_congestion_prb_rebalance_mild_v1`, `slice_congestion_prb_rebalance_severe_v1`, `slice_congestion_prb_ratio_precision_v1` | Change `slice_demand_shift` target ratios, `active_ues`, and `traffic_load_profile` intensity at the action step. |
| Delayed PRB demand | `slice_congestion_prb_delayed_demand_v1` | Keep nominal slice evidence through step 2, then introduce load and target PRB policy at step 3. |
| SSB coverage severity and identity | `coverage_edge_ssb_mild_v1`, `coverage_edge_ssb_severe_v1`, `coverage_edge_ssb_current_cell_shift_v1`, `coverage_edge_ssb_no_cell_change_v1` | Change `radio_condition_profile` severity and whether `cell_identity_change` updates current NCI before the action step. |
| Evidence gating | `stale_metrics_two_step_wait_prb_v1`, `telemetry_gap_long_wait_prb_v1`, `kpm_late_available_prb_v1`, `fresh_metrics_low_load_no_action_v1` | Extend stale/gap/KPM timing or keep fresh low-load evidence to test restraint. |
| Diagnosis | `diagnose_congestion_high_slice_good_radio_v1`, `diagnose_coverage_bad_radio_nominal_slice_v1`, `diagnose_congestion_vs_coverage_ambiguous_then_decisive_v1`, `diagnose_cfo_vs_timing_timing_branch_v1` | Pair one decisive condition with healthy alternative evidence; ambiguous variants wait until step 3 for decisive evidence. |
| Backend selection | `api_backend_selection_ws_only_v1`, `api_backend_selection_e2_late_v1`, `ric_xapp_recovery_prefers_e2_v1` | Change `e2_kpm_availability_window` and `ric_xapp_lifecycle` timing to isolate fallback versus E2 preference. |
| Mobility | `immediate_handover_stale_rnti_trap_v1`, `conditional_handover_multitarget_long_v1` | Change `mobility_path` identity freshness and target-set size before the action step. |
| Core control | `core_ue_registration_plmn_repair_v1`, `core_nf_upf_recovery_v1` | Change `core_ue_registration_misconfig` mismatch field or `core_latency_profile.degraded_nf`. |
| Restraint | `minimal_intervention_transient_recovery_v1` | Make a mild radio transient self-recover without any RAN control. |

## Latent-Cause Compound Stimulus Matrix

Compound tasks are loaded with `--suite compound`. They compose plausible
symptoms over time while keeping the root cause private; the agent must infer
the safe control path from public evidence only.

| Compound family | Tasks | Main `L_i` / `D_i` change |
| --- | --- | --- |
| Congestion versus coverage | `compound_diagnosis_congestion_vs_coverage_v1`, `compound_diagnosis_coverage_vs_backhaul_v1` | Delay decisive evidence so early symptoms are ambiguous, then expose either slice pressure or radio degradation. |
| Backhaul isolation | `compound_isolation_backhaul_not_ran_v1` | Keep RAN evidence healthy while backhaul loss/delay explains packet loss. |
| Backend fallback | `compound_fallback_e2_outage_ws_v1` | Keep PRB need visible while E2 control is unavailable. |
| Identity freshness | `compound_identity_stale_cell_then_ssb_v1` | Change cell identity over time before SSB repair is safe. |
| Radio CLI diagnosis | `compound_radio_cfo_vs_timing_v1` | Hold ambiguous radio symptoms before decisive timing evidence. |
| Core versus RAN | `compound_core_vs_ran_failure_v1` | Keep RAN evidence healthy while core NF evidence degrades. |
| Mobility versus coverage | `compound_mobility_vs_coverage_v1` | Pair edge-like symptoms with a visible UE mobility path. |

## Implemented Mechanics

- Per-event step targeting is implemented through `apply_steps` or inclusive `start_step` / `end_step`.
- Untargeted events still expand across all steps for parser compatibility.
- Temporal scoring is implemented through `J.temporal_expectations` and `temporal_action_sequence_match`.
- Generic accepted-payload scoring is implemented through `J.expected_action_fields` and `expected_action_payload_match`.
- Closed-loop simulated effect scoring is implemented through `J.expected_post_action_evidence` and `post_action_evidence_match`.
- The 25-task base suite, 1-task regression suite, 8-task compound suite, and generated single-anchor
  variants are runnable under `simulated_ocudu` with the `auto` controller.

## Review Rules

- Every `L_i` / `D_i` schedule must be deterministic for the same seed and timing policy.
- Private stimulus records must not leak driver parameters, future schedules, oracle labels, or runtime handles into agent-visible observations or feedback.
- `D_i` may maintain pressure, but it must not hide a same-step fact that the agent needs to choose the correct action.
- Current runnable tasks must keep `E.runtime_adapter = simulated_ocudu` until a live adapter is implemented and passes readiness.
- `NO_ACTION` remains benchmark-only and must never dispatch to OCUDU.
