# OCUDUAgentBench Design

## Source Basis

This document grounds the benchmark module list and folder/file structure in two named paper sections, then reconciles them with the current repo-root `benchmark/` implementation:

- Benchmark Framework: OCUDU Runtime, Benchmark Stimulus, RAN APIs, and Oracle Scorer.
- Benchmark Task Design: `\mathcal{T} = <G, \mathcal{E}, \mathcal{U}, \mathcal{I}, \mathcal{J}>` and the task episode timeline.

This document is an architecture explanation for the current repo-root
`benchmark/` implementation and its intended live-adapter boundary. Current
runnable task manifests use `E.runtime_adapter = simulated_ocudu`; live
OCUDU/FlexRIC/UE/core execution must be described only after a live adapter
implements and passes the matching readiness checks.

## Design Documentation Map

This file owns the architecture narrative: benchmark modules, information-flow
boundaries, folder structure, and system-level invariants. It does not own task
inventory, runtime API payload planning, stimulus placement planning, or formal
sum-type tables.

| File | Owns | Does not own |
| --- | --- | --- |
| `benchmark_design.md` | Architecture explanation, module responsibilities, interaction flow, implementation folder map, design constraints. | Per-task backlog, detailed API payload planning, detailed `L_i` / `D_i` placement, formal enum tables. |
| `benchmark_timeline.md` | Episode timeline, `T = <G, E, U, I, J>`, step order, `L_i` / `D_i` semantics, action-vs-stimulus attribution. | Concrete driver catalog, task backlog, runtime API catalog. |
| `benchmark_task_list.md` | Current runnable task inventory, hard-replaced task-family history, task promotion rules. | Runtime API definitions, stimulus driver definitions, scoring enum definitions. |
| `benchmark_runtime_api_list.md` | Runtime API planning for task design: control/evidence/feedback APIs, support controls, non-selected runtime surfaces, task-to-API mapping. | Raw command setup procedures, stimulus drivers, task manifests. |
| `benchmark_stimulus_list.md` | Benchmark Stimulus planning for task design: implemented drivers, `L_i` / `D_i` fit, step patterns, task-to-stimulus mapping. | Agent action APIs, raw OCUDU commands, task inventory status. |
| `benchmark_sum_type_list.md` | Formal closed sets: API kinds, action types, observation sources, stimulus phases/drivers, score/outcome types, discriminants, validation/test obligations. | Narrative architecture, task backlog, API/stimulus planning rationale. |
| `benchmark/remote_validation_runbook.md` | Remote workstation sync, validation workflow, readiness checks, and live-claim boundary. | API payloads, raw command inventory, task/stimulus planning. |

When design notes and executable code disagree about current behavior, the
executable benchmark source, schemas, and task manifests are the current
behavior. The design files should then be updated to match rather than used to
override the implementation.

## Benchmark Module List

### Task Definition Module

- Paper role: task specification `\mathcal{T} = <G, \mathcal{E}, \mathcal{U}, \mathcal{I}, \mathcal{J}>`.
- Responsibility: load and validate the static task contract before an episode starts, then split it into benchmark-private and agent-visible views.
- Inputs: task id, task file, schema, API catalog.
- Outputs: private task object with `G`, `\mathcal{E}`, `\mathcal{U}`, `\mathcal{I}`, and `\mathcal{J}`; agent-visible task view with `G`, task-selected API descriptions, and observation/action/feedback schemas.
- Interactions: feeds private task fields to Runtime Setup, Stimulus, RAN APIs, Episode Orchestration, and Oracle Scoring; feeds only the agent-visible task view to Agent-Facing API Wrapper.
- Leakage boundary: `\mathcal{E}`, `\mathcal{U}`, `\mathcal{J}`, setup metadata, stimulus schedule, oracle requirements, and hidden success labels are never included in the agent-visible task view.

### API Catalog Module

- Paper role: implementation support for task-selected RAN APIs `\mathcal{I}`; not a separate paper role.
- Responsibility: define the implemented API capabilities that a task may select.
- Inputs: API descriptor files, API schemas, static runtime requirement tags.
- Outputs: private API catalog, task-selected API projection, agent-visible evidence/action/feedback schema projection.
- Interactions: supplies Task Definition, RAN APIs, Conformance, and Agent-Facing API Wrapper.
- Feasibility contract: API catalog loading is static. Selected-adapter runtime compatibility is verified by Conformance, not by the catalog loader.
- Leakage boundary: the external agent receives only the task-selected projection, never the full API catalog, disabled APIs, setup APIs, stimulus controls, oracle APIs, or benchmark harness APIs.

### Runtime Setup Module

- Paper role: OCUDU Runtime Setup / `\mathcal{E}`; instantiates OCUDU runtime `\mathcal{R}`.
- Responsibility: instantiate the task runtime handle required by the task setup. In the current executable harness this is the deterministic `simulated_ocudu` adapter; live OCUDU/FlexRIC/UE/core adapters are future readiness-gated extensions.
- Inputs: task runtime setup, site config, runtime component requirements.
- Outputs: runtime handle, setup metadata, readiness state, cleanup plan.
- Interactions: provides the runtime handle to the Stimulus module and the RAN APIs module; provides setup evidence to Conformance and the Trace Module.
- Feasibility contract: runtime setup records whether the adapter is simulated or live. Simulated runs must not claim live OCUDU/FlexRIC execution. A live adapter must implement dispatch, artifact collection, cleanup, and readiness checks before it can enter a scored interaction.

### Stimulus Module

- Paper role: Benchmark Stimulus / `\mathcal{U}`.
- Definition: Benchmark Stimulus is the benchmark-controlled OCUDU runtime dynamics for a task. It is implemented as a private, deterministic schedule of RAN-side drivers that make the runtime evolve before observation and during agent operation.
- Responsibility: define and apply those controlled runtime dynamics before observation and during agent operation.
- Inputs: task stimulus schedule, runtime handle, deterministic seed, benchmark clock, step timing policy, stimulus phase labels.
- Outputs: applied stimulus records with event id, step id, phase label, scheduled time, applied time, seed, and stimulus evidence.
- Interactions: drives OCUDU runtime evolution through benchmark-controlled dynamics; coordinates with Episode Orchestration; writes stimulus evidence to the Trace Module.
- Feasibility contract: each step has explicit stimulus phases. `L_i` is pre-observation stimulus that settles OCUDU into state `S_i`. `D_i` is in-step stimulus that runs under the task step timing policy during the agent reasoning/action interval while `A_i` or no-action is selected and applied. All stimulus events use one benchmark clock and are logged relative to observation and reasoning/action intervals.
- Leakage boundary: stimulus schedule, future stimulus events, and stimulus driver state are private benchmark records.

#### Stimulus Scheduling

For each decision step `i`, the Stimulus module expands the task stimulus schedule into two phase windows:

- `L_i` window: starts after the previous step completes and before observation `E_i` is read. It applies pre-observation stimulus until the benchmark clock reaches the configured observation boundary. The resulting OCUDU condition is `S_i`.
- `D_i` window: starts when `E_i` has been emitted to the Agent-Facing API Wrapper and remains active while the external agent reasons, returns `A_i` or no-action, and the benchmark applies that decision through the task-selected API path. The resulting applying condition is `T_i`, and immediate command-path feedback is `F_i`.

The scheduler is deterministic. It derives all `L_i` and `D_i` events from the task stimulus schedule, the run seed, the step timing policy, and the benchmark clock. The step timing policy declares the clock mode, decision deadline, late-action policy, and action-apply policy. `D_i` starts when `E_i` is emitted and ends at the configured step boundary, not when the agent returns early. If no valid action arrives before the decision deadline, the wrapper emits no-action. The scheduler records, for every event, `run_id`, `step_id`, `phase_label`, `event_id`, scheduled time, applied time, driver parameters, and result status. These records go to the private trace partition and are not exposed to the external agent.

### RAN APIs Module

- Paper role: RAN APIs / `\mathcal{I}`.
- Responsibility: define the task-selected benchmark API surface exposed to the agent.
- Inputs: task API selection, task-selected API projection, runtime handle, evidence/action/feedback schemas.
- Outputs: evidence API bindings, action API bindings, feedback API bindings, agent-visible API projection.
- Interactions: supplies Observation, Action, and Feedback modules; hides setup, stimulus, scoring, and raw runtime internals from the agent.
- Feasibility contract: every selected API binding must declare request schema, response schema, timeout policy, safe error classes, and static runtime requirements. Conformance verifies selected-adapter runtime compatibility before scored interaction.
- Boundary: native OCUDU/FlexRIC controls, evidence APIs, and benchmark-owned core runtime-support controls can all be represented in the catalog, but task projection must label them accurately. UE traffic and UE lifecycle dynamics are Benchmark Stimulus, not agent control APIs. Core UE-registration repair exposes redacted subscriber registration fields only; raw authentication material remains benchmark backend-private.
- Leakage boundary: only the agent-visible projection reaches the Agent-Facing API Wrapper.

### Observation Module

- Paper role: evidence APIs in `\mathcal{I}`.
- Responsibility: build structured observation/evidence frames `E_i` visible to the agent; `E_i` is runtime evidence, not task setup `\mathcal{E}`.
- Inputs: selected evidence APIs, runtime samples, previous redacted feedback, allowlisted observation context.
- Outputs: observation frame `E_i` with step id and observation timestamp.
- Interactions: called through the Agent-Facing API Wrapper during the episode loop; writes observation records to the Trace module.
- Feasibility contract: observation context is schema-allowlisted per task and cannot include arbitrary task fields.
- Leakage boundary: observations exclude setup metadata, stimulus schedule, future stimulus events, conformance reports, oracle requirements, artifact paths, scorer state, and private trace records.

### Action Module

- Paper role: action APIs in `\mathcal{I}`.
- Responsibility: validate and dispatch the agent action `A_i` or no-action decision.
- Inputs: agent decision, step id, action id, task-selected action schema, validation rules, runtime handle.
- Outputs: dispatch record, local rejection, or no-action record, each keyed by step id and action id.
- Interactions: sends valid controls through RAN APIs; passes command-path outcome to Feedback; writes action records to the Trace module.
- Feasibility contract: every action has a correlation id, received timestamp, dispatch timestamp, terminal dispatch state, and timeout state.
- Leakage boundary: validation errors report only safe schema or permission failures, not hidden API inventory or benchmark internals.

### Feedback Module

- Paper role: feedback APIs in `\mathcal{I}`.
- Responsibility: normalize immediate execution feedback `F_i`.
- Inputs: action id, validation result, dispatch result, timeout, safe runtime error class, or no-action record.
- Outputs: redacted feedback frame `F_i` with action id, status, safe message, and feedback timestamp.
- Interactions: returns feedback to Agent-Facing API Wrapper; writes feedback to the Trace module; does not decide task success.
- Feasibility contract: raw runtime exceptions are mapped to safe feedback classes before they can reach the agent.
- Leakage boundary: feedback excludes container paths, setup state, stimulus state, hidden scorer criteria, raw logs, and oracle artifacts.

### Trace Module

- Paper role: recorded trace `tau` and oracle artifacts.
- Responsibility: persist partitioned interaction, benchmark-private, and oracle trace records.
- Inputs: `E_i`, `A_i`, `F_i`, step ids, action ids, applied `L_i` and `D_i` stimulus records, readiness records, runtime artifacts, timing, agent protocol metadata.
- Outputs: interaction trace, private benchmark trace, oracle trace partition, artifact manifest, compact run metadata.
- Interactions: receives records from Episode Orchestration, Observation, Action, Feedback, Stimulus, and Runtime Setup; supplies completed trace to Oracle Scoring.
- Feasibility contract: artifact collection and manifest finalization happen before destructive cleanup. Agent protocol metadata is limited to session id, request/response timing, transport status, and timeout status; it is not a free-form rationale channel.
- Leakage boundary: Agent-Facing API Wrapper may receive only the current observation and previous redacted feedback; it never reads private trace partitions.

### Oracle Scoring Module

- Paper role: Oracle Scorer / `\mathcal{J}`.
- Responsibility: score the completed episode after the interaction path ends.
- Inputs: task scoring rule, completed private trace package, artifact manifest.
- Outputs: task outcome, component scores, failure category, summary.
- Interactions: reads the Trace module and task `\mathcal{J}`; must not feed hidden labels back to Observation, Action, Feedback, or Agent-Facing API Wrapper during the episode.
- Feasibility contract: scoring runs only after trace finalization and artifact manifest finalization. Command-level checks (`temporal_action_sequence_match`, `expected_action_payload_match`) are separate from closed-loop simulated effect checks (`post_action_evidence_match`) over later public observations. Non-critical similarity metrics can add diagnostic resolution for timing, payload, and post-action evidence without replacing the critical pass/fail metrics.
- Leakage boundary: scoring outputs are not available to the external agent until after the episode is closed.

### Task Catalog And Variant Generation

- Paper role: executable task-family catalog.
- Responsibility: load checked-in base, regression, and compound tasks, or
  deterministic generated single-anchor variants.
- Inputs: suite selector, seed, count, checked-in task manifests, and variant
  axis registry/policies.
- Outputs: private task objects that pass the same validation path before
  episode execution.
- Feasibility contract: `benchmark/task_sets/base/` is the 25-task primary
  surface, `benchmark/task_sets/regression/` is the harness-regression surface,
  `benchmark/task_sets/compound/` holds latent-cause diagnosis tasks, and
  generated variants are in-memory concrete tasks from
  `benchmark/task_sets/generated/`. Generated variants are not hidden random
  branches inside an episode.
- Leakage boundary: generated metadata such as variant axes, seed, axis values,
  and expected failure modes stays in private `M.variant` and is not serialized
  in the agent-visible task view, observations, or feedback.

### Episode Orchestration Module

- Paper role: task timeline model.
- Responsibility: coordinate one complete episode from runtime setup through conformance, stimulus, observation, action, feedback, artifact finalization, cleanup, trace finalization, and scoring handoff.
- Inputs: private task object, agent-visible task view, run config, agent-facing API wrapper, step timing policy.
- Outputs: completed trace, artifact manifest, run metadata.
- Interactions: calls Runtime Setup, Conformance, Stimulus, Agent-Facing API Wrapper, Trace, and cleanup; returns the finalized trace package and artifact manifest for Oracle Scoring.
- Feasibility contract: the episode owns the step clock, action timeout policy, artifact finalization point, cleanup point, and scoring handoff point. Conformance must not mutate the scored runtime state; any mutating probe runs on a disposable runtime or forces runtime recreation before `S_1`.
- Leakage boundary: the episode passes only the agent-visible task view, current observation, allowed action schema, and previous redacted feedback to the Agent-Facing API Wrapper.

### Agent-Facing API Wrapper

- Paper role: wrapper over the task-selected RAN API surface `\mathcal{I}`; not a separate paper role.
- Responsibility: expose only `E_i`, allowed `A_i` or no-action, and `F_i` at the boundary to the external agent.
- Inputs: agent-visible task view, observation frame, task-selected action schema, previous redacted feedback, external agent endpoint or callback, agent session id, decision timeout.
- Outputs: agent decision `A_i` or no-action received from the external agent, with step id, action id, and receive timestamp.
- Interactions: sits between Episode Orchestration and the external agent process; uses Observation, Action, and Feedback modules; must not expose runtime internals, stimulus controls, oracle labels, setup machinery, or scoring state.
- Feasibility contract: wrapper behavior is deterministic for malformed decisions, timeouts, and no-action choices. Each run uses an isolated agent session unless the task explicitly declares a different public session policy.
- Leakage boundary: the wrapper never exposes full task files, full API catalog, private trace partitions, raw runtime handles, artifact paths, or benchmark configuration.

### Benchmark Controller Module

- Paper role: execution driver for repeated comparable runs; not a separate paper role.
- Responsibility: launch repeated episodes for a task, agent, and seed set.
- Inputs: task id, agent endpoint or controller id, agent session policy, seed set, run count, output directory.
- Outputs: run manifest, per-run trace locations, per-run scored summaries, suite input list.
- Interactions: calls Task Definition once per task configuration; starts one isolated agent session per run unless the task declares otherwise; invokes Episode Orchestration and Oracle Scoring per run; sends completed scored summaries to Suite And Aggregation.
- Feasibility contract: prior run outputs, scores, private traces, and seed internals are not injected into later agent sessions.
- Leakage boundary: controller never passes private task fields, seed internals, output paths, or prior run scores to the external agent.

### Suite And Aggregation Module

- Paper role: comparison across repeated executions of the same task.
- Responsibility: aggregate completed scored runs for the same task contract.
- Inputs: scored run summaries, task id, agent id, seed identifiers.
- Outputs: suite summary, scored/unscored counts, aggregate component scores.
- Interactions: reads scored run summaries and run manifests only; does not read trace partitions, start episodes, select agents, perform scoring, or alter the task contract.
- Feasibility contract: aggregation is deterministic over immutable scored run summaries.
- Leakage boundary: suite output is post-run only and does not affect any episode.

### Conformance Module

- Paper role: implementation support derived from `\mathcal{E}`, `\mathcal{U}`, `\mathcal{I}`, and `\mathcal{J}`; not a separate paper role.
- Responsibility: verify that runtime setup, deterministic stimulus control, selected RAN APIs, and required oracle sources are usable before scored interaction without contaminating the scored runtime state.
- Inputs: task `\mathcal{E}`, `\mathcal{U}`, `\mathcal{I}`, and `\mathcal{J}`; runtime handle; stimulus driver; API bindings; oracle requirements.
- Outputs: readiness report and blocking failure reason.
- Interactions: runs inside Episode Orchestration before the Agent-Facing API Wrapper starts; blocks the episode when readiness fails; writes readiness evidence to the Trace Module.
- Feasibility contract: readiness checks include setup availability, stimulus schedule validity, seedability, API binding availability, oracle source/config/collector availability, and artifact directory writability. Checks against the scored runtime are static, read-only, or no-op; mutating checks require a disposable runtime or runtime recreation before the first scored observation.
- Leakage boundary: readiness reports are private benchmark trace records and are not included in observations or feedback.

## Decision Rationale

- Private deterministic stimulus is required so task difficulty is reproducible across agents and seeds. If stimulus were adaptive to an agent action, the benchmark would mix environment scheduling with agent behavior and weaken run-to-run comparability.
- `NO_ACTION` is a benchmark decision rather than a runtime command because “do nothing” is a valid agent choice but not an OCUDU control surface. Recording it preserves the interaction trace without creating a fake RAN API.
- The simulated adapter is explicit so the harness can be tested end-to-end before live OCUDU/FlexRIC integration. Live execution is a separate adapter readiness claim, not an implication of the task name or runtime label.
- `simulated_ocudu` owns deterministic closed-loop state transitions for the simulated runtime. Accepted actions update later redacted observations where the task asks for an effect check, and runtime-domain rejections are returned only as safe feedback.
- Controller-owned repeated runs keep seed management, baseline decisions, and agent-session isolation outside `suite.py`. This keeps suite aggregation deterministic over completed scored summaries only.
- Post-run oracle scoring prevents hidden labels, artifact paths, and success criteria from influencing the agent during the episode. Immediate feedback remains command-path feedback, not task success.
- Benchmark-owned UE traffic and lifecycle dynamics are modeled as stimulus so agents cannot control workload/churn directly. Core runtime support remains a separately labeled benchmark API because it acts on benchmark-owned Open5GS process and subscriber-registration state, not on native OCUDU RAN control.

## Folder And File Structure

```text
benchmark/
  benchctl.py
    contains: CLI entry points for tasks, single episodes, repeated runs, and remote sync/check.
  README.md
    contains: harness overview, simulated-adapter boundary, and command examples.
  remote_validation_runbook.md
    contains: remote workstation sync workflow, validation commands, readiness classes, and live-claim rule.
  benchmark_api/
    types.py
      contains: closed benchmark enums for API kinds, roles, backends, action types, observation sources, stimulus cases, score metrics, outcomes, and failure categories.
    task_definition.py
      contains: task schema validation; private task object; agent-visible task view.
    api_catalog.py
      contains: static implemented API descriptors; task-selection validation; private API catalog loader; agent-visible API projection builder.
    runtime_setup.py
      contains: simulated OCUDU runtime instantiation; readiness metadata; runtime handle; cleanup plan; future live-adapter boundary.
    stimulus.py
      contains: controlled runtime-dynamics plan; deterministic seed handling; benchmark clock; step timing policy; `L_i`/`D_i` phase scheduling; stimulus event log.
    ran_api.py
      contains: evidence/action/feedback API bindings; timeout policy; safe error class mapping.
    observation.py
      contains: allowlisted observation context assembly; `E_i` frame construction.
    action.py
      contains: action schema validation; permission validation; action id assignment; dispatch; timeout handling; no-action handling.
    feedback.py
      contains: safe feedback status mapping; raw-error redaction; `F_i` frame construction.
    trace.py
      contains: interaction trace; private benchmark trace; oracle trace; artifact manifest finalization; compact run metadata.
    scoring.py
      contains: post-episode scoring; component score calculation; failure category assignment; scored run summary.
    conformance.py
      contains: non-mutating setup, stimulus, API, oracle-source, and artifact-directory readiness checks; disposable-runtime path for mutating probes.
    episode.py
      contains: single-run orchestration; step clock; step timing policy; clean scored-runtime boundary; episode loop; artifact finalization point; cleanup point; scoring handoff.
    agent_api_wrapper.py
      contains: agent-visible payload construction; isolated agent session handling; external call/response handling; decision timeout; malformed decision handling; no-action handling.
    controller.py
      contains: built-in deterministic baseline controllers and repeated-run execution.
    task_catalog.py
      contains: suite-aware loading for base tasks, compound tasks, and generated
      variants.
    variant_generator.py
      contains: deterministic in-memory single-anchor variant expansion.
    suite.py
      contains: deterministic aggregation over completed scored run summaries and public run-manifest fields only.
    config.py
      contains: local and remote config parsing.
    remote.py
      contains: infrastructure-only remote check and rsync-based benchmark sync.
    provision.py
      contains: provisioning helpers and constants.
    ric.py
      contains: FlexRIC infrastructure constants.
    websocket_client.py
      contains: WebSocket transport helper code for future live/runtime integrations.
  task_sets/
    contains: checked-in base, regression, and compound manifests plus the
    generated axis registry and suite policies.
  schemas/
    scope:
      JSON schemas define the persisted envelope and top-level serialization
      contract for task, trace, action, observation, feedback, scoring, and
      artifact records. Detailed semantic validation remains in the executable
      Python validators: `task_definition.py`, `stimulus.py`, `api_catalog.py`,
      `action.py`, `observation.py`, `trace.py`, and `scoring.py`.
    api_catalog.schema.json
      object: API descriptor envelope, including API id, role, backend, action
      type when applicable, safe error classes, timeout, static runtime
      requirement tags, and agent visibility flags.
    api_projection.schema.json
      object: task-selected agent-visible API projection; hidden catalog
      references remain private to the harness.
    task.schema.json
      object: task manifest envelope with `G`, `E`, `U`, `I`, and `J` sections;
      Python validation enforces API-selection consistency, allowed action
      backing, step targeting, and temporal expectation semantics.
    agent_view.schema.json
      object: task id; agent goal; task-selected API descriptions; observation schema reference; action schema reference; feedback schema reference; no-action allowance; public constraints.
    stimulus.schema.json
      object: controlled runtime-dynamics plan, timing policy, event phases,
      event parameters, and per-event step targeting. `stimulus.py` expands and
      validates deterministic schedules.
    action.schema.json
      object: agent action payload union keyed by action `type`; `action.py`
      enforces per-action payload bounds and task-selected API permission.
    feedback.schema.json
      object: step id; action id; feedback timestamp; status; safe error class; safe message; dispatch state.
    observation.schema.json
      object: agent-visible observation frame with selected evidence fields,
      previous feedback reference, and allowlisted public context. Evidence
      projection is filtered by task-selected APIs in `ran_api.py`.
    trace.schema.json
      object: finalized trace package envelope with interaction records,
      applied `L_i`/`D_i` stimulus records, private benchmark records, oracle
      records, run metadata, finalization flags, and artifact manifest.
    artifact_manifest.schema.json
      object: artifact manifest record with private path/URI, checksum,
      collection time, visibility class, and retention rule.
    scoring.schema.json
      object: scoring rule id; required trace fields; required artifacts; component scores; final outcome; failure category; post-run summary.
    run_manifest.schema.json
      object: task id; agent id; agent session policy; run ids; seed identifiers; run config hash; private trace locations; scored summary locations; public suite input list.
  tasks/
    README.md
      contains: task manifest layout and maintenance notes.
    <task_id>/
      task.json
        contains: one private task specification matching `task.schema.json`; never sent directly to the external agent.
  conformance/
    checks.json
      contains: readiness checks for runtime setup, stimulus driver, selected APIs, oracle source/config/collector setup, artifact output, and mutating-probe isolation mode.
  artifacts/
    README.md
      contains: artifact layout; naming rules; visibility classes; retention rules; cleanup expectations.
  tests/
    test_benchctl.py
      validates: repeated-run invocation, per-run agent session policy, and run manifest contract.
    test_config.py
      validates: local and remote configuration parsing.
    test_remote.py
      validates: remote command construction and sync/check wrappers without owning task logic.
    test_repo_generic.py
      validates: repository-level benchmark hygiene.
    test_task_definition.py
      validates: private task loading and agent-visible task view redaction.
    test_api_catalog.py
      validates: static API catalog loading, task projection, and hidden API exclusion.
    test_runtime_setup.py
      validates: runtime handle creation, readiness metadata, and cleanup plan.
    test_stimulus.py
      validates: deterministic seed, benchmark clock, step timing policy, decision deadline behavior, and stimulus event log.
    test_action.py
      validates: action schema, action id, dispatch lifecycle, timeout, and no-action.
    test_observation_feedback_trace.py
      validates: allowlisted observation context, redacted feedback, trace partitions, and private-record isolation.
    test_episode.py
      validates: full episode order, artifact finalization before cleanup, and scoring handoff.
    test_suite.py
      validates: deterministic aggregation over completed scored run summaries and run manifests only.
```

## Module Interaction Flow

```text
benchctl
  -> task_definition / api_catalog
  -> controller
      -> isolated_agent_session
      -> episode
          -> runtime_setup
          -> conformance
              -> static/read-only/no-op checks on scored runtime
              -> mutating probes only on disposable runtime or before runtime recreation
          -> stimulus
              -> schedules L_i before observation
              -> schedules D_i during reasoning/action interval
          -> agent_api_wrapper
              -> observation
              -> external_agent
              -> action
              -> feedback
          -> trace
              -> finalize_artifacts
          -> cleanup
          -> trace
              -> finalize_trace
          -> scoring
  -> suite
```

The per-step interaction loop is:

```text
Step i starts
Stimulus applies L_i before the agent is called
Runtime reaches observation state S_i
Observation reads E_i from the runtime through task-selected evidence APIs
Agent-Facing API Wrapper sends E_i to the external agent
Stimulus applies D_i under the fixed step timing policy during the reasoning/action interval
External agent returns A_i or no-action through the wrapper
Action applies A_i or no-action while D_i is the active in-step stimulus
Task-selected APIs return immediate feedback F_i
Trace records (L_i, E_i, A_i, D_i, F_i)
Next step starts from the resulting runtime condition
```

## Design Constraints

- The benchmark controls runtime setup and stimulus; the agent controls only task-selected RAN API actions or no-action.
- Stimulus is never an agent action.
- Stimulus has two explicit step phases: `L_i` before observation and `D_i` during the agent reasoning/action interval.
- The step timing policy is fixed before the run and declares clock mode, decision deadline, late-action policy, and action-apply policy.
- Task-selected benchmark APIs are the only interaction path between agent and the runtime. Native OCUDU/FlexRIC controls, evidence APIs, and benchmark-owned core support controls must be labeled distinctly; UE traffic and UE lifecycle changes belong to Benchmark Stimulus.
- Feedback `F_i` is immediate command-path feedback, not task success.
- Oracle scoring is outside the episode interaction path and consumes only completed trace/artifact outputs.
- Conformance happens before scored interaction and is not agent behavior.
- Conformance must not mutate the scored runtime state; mutating probes use a disposable runtime or require runtime recreation before the first scored observation.
- Each run uses an isolated agent session unless the task explicitly declares a public cross-run session policy.
- Setup, cleanup, artifacts, oracle labels, and scoring state are not exposed through the Agent-Facing API Wrapper.
- Suite aggregation is a thin post-processing step over completed scored summaries and run manifests only.
- Every agent-visible payload is generated from an allowlisted agent-visible task view, API projection, observation frame, or redacted feedback frame.
- The full task file, full API catalog, private trace partitions, raw errors, raw logs, output paths, runtime handles, and seed internals are benchmark-private.
- Every observation, action, dispatch result, and feedback record carries step id, timestamp, and action id when an action exists.
- Stimulus execution uses a deterministic seed, benchmark clock, timing policy, and event log.
- Artifact manifest finalization happens before destructive cleanup; scoring uses finalized trace/artifact outputs only.
