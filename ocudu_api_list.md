# OCUDU API List

This document tracks the OCUDU runtime APIs that OCUDUAgentBench can expose to an LLM agent, use as benchmark-controlled runtime machinery, or implement in future work. It is aligned with the manuscript model:

```text
T = <G, E, U, I, J>
```

where `G` is the Agent Goal, `E` is OCUDU Runtime Setup, `U` is Benchmark Stimulus, `I` is the task-selected RAN API surface, and `J` is Task Scoring. The benchmark manages `E`, `U`, traces, artifacts, and `J`; the LLM agent interacts with OCUDU only through the task-selected APIs in `I`.

This tracker is not a replacement for source inspection. Promote an API from future work to task-selectable `I` only after source evidence and conformance evidence show that the interface is process-external, reproducible, and objectively scorable.

## Source Baseline

The current tracker is grounded in:

- `API_REFERENCE.md`
- `schemas/actions.schema.json`
- `conformance/tests.json`
- `tasks/*/task.json`
- Official OCUDU docs: <https://ocudu-docs-604e90.gitlab.io/>
- Official OCUDU source: <https://gitlab.com/ocudu/ocudu>
- Local benchmark target: OCUDU `release_26_04`

If any of those sources change, update this file in the same turn as the design or implementation change.

## Status Vocabulary

| Status | Meaning |
| --- | --- |
| `implemented-scored` | Implemented in the local benchmark and used by at least one scored task. |
| `implemented-gated` | Implemented, but scored use depends on conformance/oracle evidence being available for the run. |
| `implemented-unscored` | Implemented as support machinery, but not scored as agent behavior. |
| `available-setup` | Available as setup, provisioning, runtime launch, or artifact machinery, not as an agent-facing RAN API. |
| `future-implementable` | Plausible future benchmark API, but not yet conformance-gated and scored. |
| `requires-extension` | Requires new OCUDU or harness capability beyond the currently tracked stock/runtime interface. |
| `excluded` | Out of scope for current single-workstation OCUDUAgentBench tasks. |

## Capability Classes

| Class | Definition |
| --- | --- |
| `stock OCUDU API` | A process-external runtime interface exposed by OCUDU without source modification. |
| `O-RAN/E2 API via OCUDU + RIC` | An O-RAN E2 path between OCUDU and the benchmark-owned RIC/xApp runtime. |
| `benchmark harness API` | Benchmark-owned interface for runtime setup, stimulus, validation, traces, or scoring. |
| `oracle/source artifact` | Runtime evidence used by `J` to decide whether a task is scored and whether the agent succeeded. |
| `setup/provision mechanism` | Remote workspace, dependency, launch, and cleanup machinery owned by the benchmark. |
| `future candidate` | A candidate API that needs source evidence, conformance, and scoring design before use. |

## Agent-Facing RAN API Surface

These APIs are task-selectable through `I`. A task may expose only a subset of this table.

| API | Class | Role | Current action or wire command | Status | Conformance | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `NO_ACTION` | benchmark decision | `I` action choice | `NO_ACTION`; not sent to OCUDU | `implemented-scored` | task scoring only | Represents restraint. It is recorded for decision accounting and triage-style tasks, but it is not an OCUDU runtime command. |
| OCUDU WebSocket PRB policy | `stock OCUDU API` | `I` action plus feedback | `SET_PRB_POLICY_RATIO_WS` -> `rrm_policy_ratio_set` | `implemented-scored` | `websocket_prb_policy_action` | Scores min/max PRB policy validity and dispatch. `dedicated_ratio` is validity-only in current scoring. |
| OCUDU WebSocket SSB block power | `stock OCUDU API` | `I` action plus feedback | `SET_SSB_BLOCK_POWER_WS` -> `ssb_set` | `implemented-scored` | `websocket_ssb_power_action` | Scores command correctness, repair, and restraint. RF-effect scoring is not yet implemented. |
| OCUDU JSON metrics stream | `stock OCUDU API` | `I` evidence | `metrics_subscribe` | `implemented-scored` | `websocket_command_path`, `json_metrics_stream` | Provides metrics presence, freshness, stale masking, parsing errors, and continuity evidence. |
| E2SM-KPM v05 observation | `O-RAN/E2 API via OCUDU + RIC` | `I` evidence and `J` oracle | E2 setup plus KPM subscription | `implemented-scored` | `flexric_docker_assets`, `near_rt_ric_health`, `ocudu_e2_config`, `e2_setup_path`, `e2_kpm_subscription`, `e2_pcap_log_oracle` | Requires decoded KPM v05 records with PRB-named measurement evidence. |
| E2SM-CCC PRB policy control | `O-RAN/E2 API via OCUDU + RIC` | `I` action plus `J` oracle | `SET_PRB_POLICY_RATIO_CCC` -> E2SM-CCC `O-RRMPolicyRatio` | `implemented-gated` | `e2_ccc_prb_control_path` | Uses a one-shot control tool in the FlexRIC-derived image. A run must have accepted CCC control evidence before scoring. |
| E2SM-RC DU PRB quota control | `O-RAN/E2 API via OCUDU + RIC` | `I` action plus `J` oracle | `SET_PRB_POLICY_RATIO_RC_DU` -> E2SM-RC DU style 2 action 6 | `implemented-gated` | `e2_rc_du_prb_control_path` | Requires DU UE identity from the action or runtime discovery. A run must have accepted RC DU control evidence before scoring. |

## Runtime, Stimulus, And Oracle Mechanisms

These mechanisms are not direct RAN actions for the agent. They support `E`, `U`, or `J`.

| Mechanism | Class | Role | Status | Conformance or artifact | Notes |
| --- | --- | --- | --- | --- | --- |
| Docker OCUDU/Open5GS/srsUE runtime | `setup/provision mechanism` | `E` | `available-setup` | `docker_e2e_assets`, `open5gs_core_health`, `srsue_zmq_attach` | Establishes the live RAN runtime used by scored episodes. |
| ZMQ RF/sample path | `benchmark harness API` | `E` and `U` | `available-setup` | `srsue_zmq_attach`, `zmq_rf_path` | Runtime/sample transport. It is not an OCUDU RAN-management API. |
| UE ping traffic | `benchmark harness API` | `U` and `J` | `implemented-scored` | `ping_traffic_path` | Stimulus and health evidence for task scoring. |
| Metrics staleness mask | `benchmark harness API` | `U` evidence mask | `implemented-scored` | `scenario_metrics_staleness_mask` | Deliberately masks JSON metrics freshness to evaluate evidence-gated restraint. The identifier is historical; prefer Benchmark Stimulus in prose. |
| PCAP and log oracle | `oracle/source artifact` | `J` | `implemented-gated` | `e2_pcap_log_oracle`, `pcap_log_oracle` | Summarizes runtime logs, PCAPs, E2 setup, KPM continuity, and control outcomes. |
| E2 control raw/oracle summaries | `oracle/source artifact` | `J` | `implemented-gated` | `e2_control_raw.jsonl`, `e2_oracle.json` | Required evidence for CCC and RC DU control tasks. |
| SSH, rsync, remote provisioning | `setup/provision mechanism` | `E` | `available-setup` | `remote_tools_ocudu_root`, `remote_workspace_artifacts`, `ocudu_runtime_dependencies`, `ocudu_launch` | Remote workspace and launch machinery. It is not agent-facing. |

## Task-To-API Map

| Task | Task-selected `I` APIs | Setup/stimulus/oracle dependencies |
| --- | --- | --- |
| `ws_prb_ping_v1` | WebSocket PRB policy, JSON metrics | Docker runtime, ping traffic, action log, metrics continuity, cleanup |
| `ws_prb_noop_guard_v1` | WebSocket PRB policy, `NO_ACTION`, JSON metrics | Docker runtime, ping traffic, zero-action scoring, cleanup |
| `ws_prb_error_repair_v1` | WebSocket PRB policy, JSON metrics | Local rejection evidence, accepted repair, ping, metrics, cleanup |
| `ws_prb_action_budget_v1` | WebSocket PRB policy, JSON metrics | Action budget scoring, ping, metrics, cleanup |
| `metrics_staleness_noop_v1` | WebSocket PRB policy, `NO_ACTION`, JSON metrics | Metrics staleness mask, evidence-gated action scoring, ping, cleanup |
| `ws_ssb_power_guard_v1` | WebSocket SSB block power, `NO_ACTION`, JSON metrics | Cell identity, zero-action scoring, ping, metrics, cleanup |
| `ws_ssb_power_repair_v1` | WebSocket SSB block power, JSON metrics | Local rejection evidence, accepted `ssb_set`, cell identity, ping, cleanup |
| `e2_kpm_prb_ping_v1` | WebSocket PRB policy, JSON metrics, E2SM-KPM v05 | FlexRIC runtime, decoded KPM PRB evidence, PCAP/log oracle, cleanup |
| `e2_kpm_json_consistency_v1` | WebSocket PRB policy, JSON metrics, E2SM-KPM v05 | Evidence-gated action scoring, decoded KPM evidence, PCAP/log oracle, cleanup |
| `e2_ccc_prb_policy_ping_v1` | E2SM-CCC PRB policy, JSON metrics, E2SM-KPM v05 | E2 control oracle with accepted CCC record, ping, metrics, cleanup |
| `e2_rc_du_prb_policy_ping_v1` | E2SM-RC DU PRB quota, JSON metrics, E2SM-KPM v05 | DU UE identity, E2 control oracle with accepted RC DU record, ping, metrics, cleanup |
| `e2_control_api_consistency_v1` | E2SM-CCC PRB policy or E2SM-RC DU PRB quota | API selection scoring, E2 control oracle, ping, metrics, cleanup |
| `ran_policy_triage_v1` | Stable catalog: `NO_ACTION`, WebSocket PRB, WebSocket SSB, E2SM-CCC, E2SM-RC DU | Hidden task condition, structured RAN evidence, correct API selection, RAN health, cleanup |

## Future Implementable APIs

| Candidate | Intended role | Status | Required before promotion |
| --- | --- | --- | --- |
| WebSocket applied-state readback or log-confirmation | `I` evidence or `J` oracle | `future-implementable` | Reliable confirmation beyond command response acceptance for PRB and SSB commands. |
| PRB policy effect oracle | `J` | `future-implementable` | Reproducible throughput, scheduler, or PRB allocation evidence tied to the action. |
| SSB block-power effect oracle | `J` | `future-implementable` | Measurable RF, pathloss, or emulator evidence; current SSB tasks score command correctness only. |
| Richer E2SM-CCC and E2SM-RC oracle parsing | `J` | `future-implementable` | Robust summaries from PCAPs, OCUDU logs, RIC logs, xApp output, and `e2_control_raw.jsonl`. |
| E2SM-RC CU-CP mobility or handover control | `I` action plus `J` oracle | `future-implementable` | Multi-cell runtime, mobility stimulus, and objective handover oracle. |
| ZMQ impairment controls | `U`, not OCUDU-native `I` | `future-implementable` | Reproducible impairment injection and scoring rules. |
| O1/NETCONF management loop | `I` action/evidence | `requires-extension` | Process-external management endpoint, source-backed command model, conformance gate, and task scorer. |
| A1/O2 controls | `I` action/evidence | `requires-extension` | Compatible local runtime component and objective scorer. |
| O-RU/Open Fronthaul metrics | `I` evidence or `J` oracle | `requires-extension` | Available runtime source and artifact path in the single-workstation setup. |
| Arbitrary per-UE RRC, bearer, direct scheduler, or DRX controls | `I` action | `excluded` | Do not add unless current OCUDU source exposes a stable process-external API and a conformance-scored oracle. |

## Maintenance Checklist

- Update this file whenever the action schema, conformance registry, task manifests, OCUDU source pin, or API validation status changes.
- Keep each API classified by role before describing protocol details: `I` observation/action, `J` feedback-oracle, `E` setup/provision, `U` stimulus, or excluded.
- Require source evidence and conformance evidence before promoting a future candidate into a task-selectable RAN API.
- Do not expose setup/provisioning, stimulus-only controls, or oracle-only artifacts as agent actions.
- Keep standards-facing E2 paths distinct from local OCUDU WebSocket paths, even when they manage similar RAN concepts.
- Use Benchmark Stimulus terminology in prose. Historical registry identifiers may retain older names for compatibility.
