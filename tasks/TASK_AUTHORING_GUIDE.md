# Task Authoring Guide

This guide defines how agents should propose new OCUDU-based RAN management benchmark tasks. It is for task design and review, not for implementing runtime adapters or changing OCUDU source.

## Purpose

A task proposal should describe a benchmarkable episode for an LLM agent. The proposal must be specific enough that an implementer can later decide whether to create a `task.json`, runtime adapter, conformance checks, scorer, and task README without rediscovering the intended benchmark behavior.

The guide allows planned tasks to be broader than today's executable runtime support, but every proposal must clearly separate:

- what can run today,
- what needs conformance work,
- what needs runtime implementation,
- what can be scored objectively.

## Good Task Definition

A good task is a bounded, reproducible RAN-management episode in which an LLM agent uses the Perceive -> Reason -> Execute -> Feedback -> Repeat loop over structured observations and allowed actions, then is scored against objective oracles that measure operational correctness, safety, and task success.

```text
good task =
  bounded RAN objective
  + fixed scenario and workload
  + explicit API roles
  + structured observations
  + constrained action/output space
  + agent evaluation protocol
  + objective oracle with no answer leakage
  + conformance gate
  + reproducible setup
  + LLM-relevant decision challenge
```

A task should test one main capability. Multi-API tasks are encouraged only when each API has a clear role and the scoring remains objective.

The LLM-agent challenge must require decisions from incomplete, noisy, delayed, or multi-source operational evidence. A task that a static script can solve with one unconditional action may still be useful as a smoke test, but it is weak as an LLM-agent benchmark unless it is a baseline task.

## Task ID And Versioning

Task IDs are stable benchmark contracts, not display names. Use lowercase snake case with a version suffix, such as `ws_prb_ping_v1`.

Create a new version when any of these change:

- allowed actions or structured output schema,
- observation fields that affect agent decisions,
- scenario workload, labels, or impairment schedule,
- scoring formulas, thresholds, or weights,
- required conformance gates,
- runtime stack in a way that changes task difficulty.

Do not bump the version for wording fixes, clarified docs, or implementation refactors that preserve the same task contract. Once a task is marked `scored`, treat the task contract as immutable except for bug fixes that preserve old scores' meaning.

## API Role Model

Classify every API used by a task. Do not list an API unless the task explains why it is needed.

| API Role | Meaning | Examples |
| --- | --- | --- |
| Observation API | Provides state to the agent | JSON metrics, E2SM-KPM records, ping counters, backend status |
| Action API | Allows the agent to change runtime behavior | OCUDU WebSocket PRB action, future E2SM-RC/CCC controls |
| Oracle API | Proves success, failure, or ground truth | PCAP/log oracle, decoded KPM JSONL, cleanup postconditions |
| Environment or harness API | Controls the testbed, not OCUDU-native RAN management | Docker, SSH, ZMQ impairment harness, cleanup commands |
| Setup/provisioning API | Prepares assets before the benchmark | `remote provision`, `remote ric-prepare`, source manifests |

Provisioning and conformance are not agent-performance APIs. Provisioning prepares the remote runtime assets. Conformance verifies that the task's APIs and episode path work before scoring. If conformance fails, the run is unscored and should not count as an agent failure.

Scored agents normally must not call SSH, Docker, provisioning, cleanup, or remote file APIs directly. They operate through the benchmark API and the task's allowed action or output contract. If a task wants the agent to choose `no_op`, `retry`, or `cleanup`, those choices must be exposed as explicit benchmark actions, not as shell access to the harness.

## Scenario And Workload

Every task must define the episode conditions that make runs comparable:

- episode duration and observation cadence,
- random seed handling and scenario labels,
- UE count, traffic type, traffic target, and traffic rate,
- initial RAN policy or configuration state,
- impairment or fault schedule, if any,
- allowed warmup time before scoring starts,
- expected minimum observation evidence, such as ping samples or KPM records.

If any scenario element is randomized, the task must state how the seed is stored and how the scenario can be replayed. If the scenario uses labels or hidden ground truth, those labels are oracle artifacts and must not appear in the agent observation frame unless the task explicitly tests label use.

## Agent Evaluation Protocol

Each task must define how an LLM agent is evaluated. The default protocol is:

```text
Perceive -> Reason -> Execute -> Feedback -> Repeat
```

- **Perceive**: the agent sees only `reset`, `observe`, `act`, and `close` results plus its own prior actions and action results.
- **Reason**: the agent uses the task objective, observation frame, and history to decide whether to wait, no-op, repair a previous action, or choose an allowed RAN-management action.
- **Execute**: one structured action or `None` may be returned per decision point; `None` means the agent intentionally takes no RAN action and is not an invalid action.
- **Feedback**: validation errors, API responses, and updated RAN state return through `act` results and later observations.
- **Repeat**: the cycle continues until the episode ends and the benchmark scores the trace.

Malformed JSON, schema violations, and out-of-range values are local agent action failures after setup succeeds. Direct remote access, artifact scraping, side-channel WebSocket clients, and direct container control are forbidden.

Task proposals must state any deviations from the default, including decision cadence, maximum action count, action cooldown, retry budget, per-decision latency limit, whether token/cost telemetry is expected, whether memory may span episodes in a suite, and how free-form LLM output is converted into the structured action or output schema.

## Oracle Visibility

Observation APIs are what the agent may see during the episode. Oracle APIs are scorer-only unless the task explicitly lists them as observations.

By default, these artifacts must not be exposed to the agent during a scored episode:

- final `summary.json`,
- hidden scenario labels,
- future observations,
- raw PCAPs and oracle summaries,
- cleanup postconditions,
- logs that directly reveal the expected action or injected fault.

If a diagnostic task intentionally exposes logs, PCAP summaries, labels, or oracle-derived fields as observations, the proposal must explain why that is legitimate and must use a separate oracle for scoring. Do not score an agent for "discovering" evidence that the observation frame already gives away.

## Task Markdown Template

Agents should propose new tasks with this exact structure.

```markdown
# `<task_id>`

## Goal
State the RAN-management objective in one or two sentences.

## LLM-Agent Challenge
State what the LLM agent must decide, what evidence it must use, and what common failure mode this task tests.

## API Roles
List every API and its role: observation, action, oracle, environment/harness, or setup/provisioning.

## Runtime Stack
List the remote runtime components: OCUDU gNB, Open5GS, srsUE, FlexRIC/xApp, traffic source, captures, or harness components.

## Scenario And Workload
Define duration, seed behavior, UE count, traffic pattern, impairment schedule, warmup, labels, and minimum evidence counts.

## Agent Evaluation Protocol
Define decision cadence, action budget, latency limits, retry rules, memory scope, parse-error handling, and whether `None` is allowed.

## Allowed Actions Or Outputs
Define the allowed action JSON or the structured output schema. Observation-only tasks must say that no RAN control action is allowed.

## Observation Frame
Describe the fields the agent sees, including required fields, optional fields, backend status indicators, and missing-data behavior.

## Objective Oracle
Define how the benchmark knows the correct outcome without human judgment.

## Oracle Visibility
State which oracle artifacts are hidden from the agent and identify any oracle-derived fields intentionally exposed as observations.

## Scoring
List each raw score dimension with formula, threshold, minimum evidence requirement, source artifact, and anti-gaming check. Also state how the task maps into the standard component scores: `task_correctness`, `action_correctness`, `evidence_use`, `ran_health`, `safety`, and `cleanup`. Timing, token, and cost telemetry must be reported as efficiency metrics, not folded into correctness.

## Required Conformance
List required conformance checks. If a needed check does not exist yet, name it and mark it as missing.

## Artifacts
List expected remote artifacts such as decisions, actions, observations, metrics, KPM records, logs, PCAP summaries, oracle JSON, and summary JSON.

## Failure And Unscored Conditions
Separate setup/conformance/runtime/oracle failures from agent failures.

## Safety Constraints
List forbidden actions, bounds, harness-access restrictions, cleanup requirements, and any no-op requirements.

## Measurability Review
Rate high, medium, or low and explain why.

## Implementability Review
Rate high, medium, or low and explain what runtime or conformance work is missing.

## Readiness
Use one readiness level: idea, designed, conformance_needed, implemented_unscored, or scored.
```

Every proposal must answer:

- What does the LLM agent need to decide?
- What fixed scenario and workload is it evaluated under?
- Which APIs does it observe?
- Which APIs can it act through?
- Which harness APIs are forbidden to the agent?
- What oracle proves success or failure?
- How is oracle leakage prevented?
- How often can the agent act, and what happens when parsing or validation fails?
- What makes setup failure different from agent failure?
- Why is this measurable without human judgment?
- What exact formulas turn artifacts into raw scores and component scores?
- What efficiency telemetry, such as latency or token usage, should be recorded separately from correctness?
- What runtime implementation is missing, if any?
- Would any change require a new task version?

## Measurability Rubric

| Rating | Use When | Required Evidence |
| --- | --- | --- |
| High | Scoring can be computed directly from structured artifacts with hidden oracles and exact formulas | action logs, summary JSON, ping counters, decoded KPM records, cleanup postconditions, thresholds, weights |
| Medium | Scoring is objective but needs labeled scenarios or derived evidence | known failure labels, log classifiers, PCAP summaries, consistency checks, replayable seeds |
| Low | Scoring depends on broad natural-language judgment or weak evidence | free-form diagnosis without labels, unverified throughput/fairness claims, missing oracle, leaked answer |

Prefer high-measurability tasks. Medium tasks are acceptable when they define labels and oracle artifacts before scoring. Low tasks should remain ideas until the oracle is redesigned.

## Implementability Rubric

| Rating | Use When | Current Benchmark Fit |
| --- | --- | --- |
| High | Uses executable APIs, conformance, scenario control, scoring, and cleanup already present | WebSocket PRB, JSON metrics, ping, Docker e2e, cleanup |
| Medium | Uses partially available APIs or needs a small adapter/scorer | E2 KPM triage, log/PCAP diagnosis, controlled dropout scenarios |
| Low | Needs new runtime control, new standards support, new scenario injection, or unproven conformance | E2SM-RC control, E2SM-CCC control, ZMQ impairment control |

Do not mark a task as scored until conformance can verify every required runtime API and oracle path.

## Readiness Levels

| Readiness | Meaning |
| --- | --- |
| `idea` | Useful benchmark concept, but API roles, oracle, scenario, or scoring are incomplete |
| `designed` | Complete task proposal exists, but runtime/conformance work is not implemented |
| `conformance_needed` | Runtime path is plausible, but pre-scoring checks are missing or incomplete |
| `implemented_unscored` | Runtime can launch, but scoring or oracle validation is not ready |
| `scored` | Runtime, conformance, scoring, artifacts, and cleanup are implemented |

Task manifests that are present in the registry are not automatically runnable. Runtime implementation must explicitly support a task before CLI/API episode runs can execute it.

## Task Generation Workflow

Use this workflow when generating a task proposal:

1. Pick one RAN-management capability to test.
2. Choose a stable task ID and decide whether this is a new version.
3. Define the LLM-agent decision: diagnose, no-op, act, retry, or explain.
4. Define the fixed scenario and workload.
5. Classify every API by role.
6. Define the agent evaluation protocol and harness-access boundary.
7. Define the allowed action or structured output.
8. Define the observation frame and missing-data behavior.
9. Define the objective oracle and oracle visibility boundary before defining the score.
10. Define scoring formulas only from artifacts the benchmark can produce.
11. List conformance checks and mark missing checks explicitly.
12. Assign measurability, implementability, and readiness.
13. Only create `task.json` after the proposal is designed and reviewed.

## Examples

### `ws_prb_ping_v1`

- Goal: keep a Docker OCUDU/Open5GS/srsUE ping episode healthy while issuing a valid PRB policy action.
- Scenario: one Docker gNB/UE/core episode with UE ping traffic and a fixed duration supplied by the suite or episode command.
- Agent protocol: the agent acts through the benchmark action API; it cannot call Docker, SSH, or WebSocket directly.
- API roles: WebSocket is the action API; JSON metrics and ping are observation APIs; cleanup postconditions are oracle evidence.
- Oracle visibility: cleanup state and final summary are scorer-only.
- Measurability: high, because actions, ping counters, metrics frames, and cleanup state are structured.
- Implementability: high, already scored.
- Readiness: `scored`.

### `e2_kpm_prb_ping_v1`

- Goal: operate the same PRB WebSocket control path while requiring decoded E2SM-KPM v05 telemetry and oracle artifacts.
- Scenario: one Docker gNB/UE/core/FlexRIC episode with UE ping traffic, decoded KPM records, and a fixed duration supplied by the suite or episode command.
- Agent protocol: the agent still acts only through the WebSocket PRB action contract; E2 KPM is observation and oracle evidence, not an action path.
- API roles: WebSocket is the action API; JSON metrics, ping, and E2 KPM are observation APIs; decoded KPM and PCAP/log summaries are oracle APIs.
- Oracle visibility: decoded records may be observed when exposed in the task frame, but final oracle summaries and cleanup postconditions are scorer-only.
- Measurability: high when the KPM oracle is available.
- Implementability: high if FlexRIC KPM v05 provisioning and conformance pass.
- Readiness: `scored` in the registry; individual runs remain unscored when KPM v05 conformance or oracle validation fails.

### `json_metrics_to_ws_prb_recovery_v1`

- Goal: detect degraded runtime health from JSON metrics and ping, then choose a valid PRB WebSocket action or no-op.
- Scenario: deterministic degradation schedule with replayable seed, labeled degraded/healthy windows, UE ping, and JSON metrics.
- Agent protocol: the agent can submit bounded PRB actions or `None`; it cannot inspect hidden degradation labels or oracle summaries.
- API roles: JSON metrics and ping are observation APIs; WebSocket is the action API; before/after ping and action logs are oracle evidence.
- Oracle visibility: hidden degradation labels are scorer-only.
- Measurability: medium until a deterministic degradation injection and recovery oracle are implemented.
- Implementability: medium, because it reuses existing APIs but needs controlled scenario seeding and a recovery scorer.
- Readiness: `designed` or `conformance_needed`, depending on whether the degradation setup has an executable conformance check.

## Review Checklist

Before proposing a new task, verify:

- The task has one main RAN-management objective.
- The task is meaningful for LLM agents, not only scripted baselines.
- The task ID and version are stable and justified.
- Scenario duration, seed behavior, traffic, labels, and minimum evidence are specified.
- Every API is assigned a role.
- Multi-API tasks explain why each API is needed.
- Harness, setup, provisioning, cleanup, SSH, Docker, and direct remote file APIs are not exposed to scored agents unless explicitly modeled as benchmark actions.
- The action or output space is constrained.
- The agent evaluation protocol defines cadence, action budget, validation behavior, and memory scope.
- The observation frame does not leak scorer-only oracle data.
- The oracle is objective and artifact-backed.
- Scoring has formulas, thresholds, weights, minimum evidence, and anti-gaming checks.
- Scoring does not require subjective human judgment.
- Setup, conformance, runtime, oracle, and agent failures are separated.
- Required conformance checks are listed.
- Missing runtime or conformance work is stated.
- Safety constraints and cleanup expectations are explicit.
- The readiness level is honest.
