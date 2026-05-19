# Task Authoring Guide

This guide defines how agents should propose new OCUDU-based RAN-management benchmark tasks. It is for task design and review, not for implementing runtime code or changing OCUDU source.

## Core Definition

Use the locked definition:

```text
Task = Agent Goal + Benchmark Stimulus + RAN APIs + Task Scoring
```

A task is a full scored RAN-management episode. An `L/D` pair is one transition unit inside the task's Benchmark Stimulus; it is not a task.

All task proposals must preserve the central system boundary: the benchmark is the intermediate layer between the LLM agent and the RAN. The task defines what this layer exposes to the agent, what it sends to the RAN, what it withholds, and how the resulting trace is scored.

The task hierarchy is:

```text
Benchmark
  -> Task
      -> Episode
          -> Step
              -> L/D transition unit
```

## Good Task Definition

A good task is bounded, reproducible, measurable, and useful for evaluating LLM-agent RAN-management behavior.

It must define:

- **Agent Goal**: what the agent must manage or decide,
- **Benchmark Stimulus**: the controlled input or event sequence injected by the benchmark into the RAN to create the management condition used for evaluation,
- **RAN APIs**: evidence reads, management actions/no-actions, validation, and feedback,
- **Task Scoring**: how objective artifacts prove success or failure.

The LLM-agent challenge should require decisions from incomplete, delayed, stale, or multi-source evidence. A task that a static script can solve with one unconditional action may still be useful as a smoke test, but it is weak as an LLM-agent benchmark unless it is explicitly a baseline task.

## Task ID And Versioning

Task IDs are stable benchmark contracts, not display names. Use lowercase snake case with a version suffix, such as `ws_prb_ping_v1`.

Create a new version when any of these change:

- allowed actions or structured output schema,
- observation fields that affect agent decisions,
- Benchmark Stimulus changes that affect task difficulty,
- task scoring formulas, thresholds, or weights,
- required conformance gates,
- runtime stack in a way that changes task meaning.

Do not bump the version for wording fixes, clarified docs, or implementation refactors that preserve the same task contract.

## API Role Model

Classify every API used by a task. Do not list an API unless the task explains why it is needed.

| API Role | Meaning | Examples |
| --- | --- | --- |
| Observation API | Provides structured evidence to the agent | JSON metrics, E2SM-KPM records, ping counters, backend status |
| Action API | Allows the agent to change runtime behavior | OCUDU WebSocket PRB, WebSocket SSB, E2SM-CCC, E2SM-RC |
| Oracle API | Proves success, failure, or ground truth | PCAP/log oracle, decoded KPM JSONL, cleanup postconditions |
| Environment or harness API | Controls the testbed, not OCUDU-native RAN management | Docker, SSH, ZMQ path, cleanup commands |
| Setup/provisioning API | Prepares assets before the benchmark | `remote provision`, `remote ric-prepare`, source manifests |

Conformance is not an agent-performance API. It covers both setup readiness and API checks before task scoring. If conformance fails, the run is unscored and should not count as an agent failure.

Scored agents normally must not call SSH, Docker, provisioning, cleanup, raw runtime tools, or remote file APIs directly. They operate through the benchmark-mediated RAN APIs and the task's allowed action contract.

## Benchmark Stimulus

Benchmark Stimulus defines the controlled input or event sequence injected by the benchmark into the RAN during the episode.

A task must define:

- episode duration or stopping rule,
- observation cadence,
- initial RAN condition,
- injected traffic, load, event, or evidence condition,
- evidence freshness or masking rules,
- backend availability rules,
- warmup time before task scoring starts, if any,
- replayability through seed or deterministic schedules when used.

Use the `L/D` language for transition design:

```text
L1: S_init -> S1
D_i: S_i + A_i -> T_i + F_i
L_i+1: T_i -> S_i+1
E_i+1: evidence from S_i+1
```

The agent does not see hidden RAN condition, task scoring answers, future evidence, or oracle labels.

## RAN APIs

Each task must define how an LLM agent is evaluated:

```text
Perceive -> Reason -> Execute -> Feedback -> Repeat
```

- **Perceive**: the agent receives benchmark-mediated structured evidence from `reset()` and `observe()`.
- **Reason**: the agent uses the Agent Goal, current evidence, and history to decide whether to wait, no-op, repair, or act.
- **Execute**: the agent returns one structured action or `None` for no-action to the benchmark layer.
- **Feedback**: validation errors, API responses, and runtime outcomes return through `act()` results and later observations.
- **Repeat**: the cycle continues until the episode ends and the benchmark scores the trace.

The task must define:

- normalized read APIs exposed as observation fields,
- allowed actions or no-action,
- what validation the benchmark layer performs before dispatch,
- decision cadence,
- maximum action count or cooldown when applicable,
- parse-error and validation-error handling,
- whether token, latency, or cost telemetry is expected,
- whether memory may span episodes in a suite.

Malformed JSON, schema violations, and out-of-range values are local agent action failures after setup succeeds. Direct remote access, artifact scraping, side-channel WebSocket clients, and direct container control are forbidden unless a future task explicitly defines them as agent actions.

## Oracle Visibility

Observation APIs are what the agent may see during the episode. Oracle APIs are scorer-only unless the task explicitly lists them as observations.

By default, these artifacts must not be exposed to the agent during a scored episode:

- final `summary.json`,
- future observations,
- raw PCAPs and oracle summaries,
- cleanup postconditions,
- logs that directly reveal the expected action or task condition.

If a diagnostic task intentionally exposes logs, PCAP summaries, labels, or oracle-derived fields as observations, the proposal must explain why that is legitimate and must use a separate oracle for task scoring.

## Task Markdown Template

Agents should propose new tasks with this exact structure.

```markdown
# `<task_id>`

## Agent Goal
State the RAN-management objective in one or two sentences.

## Benchmark Stimulus
Define duration, stopping rule, initial condition, injected traffic/load/event/evidence conditions, backend availability, and the intended L/D transition sequence.

## RAN APIs
Define the RAN evidence reads, management action/no-action calls, validation behavior, feedback, decision cadence, action budget, latency rules, and forbidden side channels.

## Task Scoring
Define objective oracle artifacts, raw scores, component scores, thresholds, anti-gaming checks, and unscored failure categories.

## Artifacts
List expected remote artifacts such as decisions, actions, observations, metrics, KPM records, logs, PCAP summaries, oracle JSON, and summary JSON.

## Readiness
Use one readiness level: idea, designed, conformance_needed, implemented_unscored, or scored.
```

Every proposal must answer:

- What does the LLM agent need to decide?
- How does the benchmark make the RAN evolve over time?
- Which APIs does the agent observe?
- Which APIs can it act through?
- Which harness APIs are forbidden to the agent?
- What oracle proves success or failure?
- How is oracle leakage prevented?
- How often can the agent act, and what happens when parsing or validation fails?
- What makes setup failure different from agent failure?
- Why is this measurable without human judgment?
- What runtime implementation is missing, if any?
- Would any change require a new task version?

## Measurability Rubric

| Rating | Use When | Required Evidence |
| --- | --- | --- |
| High | Task Scoring can be computed directly from structured artifacts with exact formulas | action logs, summary JSON, ping counters, decoded KPM records, cleanup postconditions |
| Medium | Task Scoring is objective but needs derived evidence or classifiers | log classifiers, PCAP summaries, consistency checks, replayable seeds |
| Low | Task Scoring depends on broad natural-language judgment or weak evidence | free-form diagnosis without labels, unverified throughput/fairness claims, missing oracle |

Prefer high-measurability tasks. Medium tasks are acceptable when they define oracle artifacts before task scoring. Low tasks should remain ideas until the oracle is redesigned.

## Implementability Rubric

| Rating | Use When | Current Benchmark Fit |
| --- | --- | --- |
| High | Uses executable APIs, conformance, task scoring, and cleanup already present | WebSocket PRB/SSB, JSON metrics, ping, Docker e2e, cleanup |
| Medium | Uses partially available APIs or needs a small runtime binding/scorer | E2 KPM triage, log/PCAP diagnosis, controlled dropout windows |
| Low | Needs new runtime control, new standards support, or unproven conformance | new E2 control families, unimplemented impairment controls |

Do not mark a task as scored until conformance can verify every required RAN API and oracle path.

## Readiness Levels

| Readiness | Meaning |
| --- | --- |
| `idea` | Useful benchmark concept, but API roles, oracle, Benchmark Stimulus, or task scoring are incomplete |
| `designed` | Complete task proposal exists, but runtime/conformance work is not implemented |
| `conformance_needed` | Runtime path is plausible, but pre-task scoring checks are missing or incomplete |
| `implemented_unscored` | Runtime can launch, but task scoring or oracle validation is not ready |
| `scored` | Runtime, conformance, task scoring, artifacts, and cleanup are implemented |

Task manifests that are present in the registry are not automatically runnable. Runtime implementation must explicitly support a task before CLI/API episode runs can execute it.

## Task Generation Workflow

1. Pick one RAN-management capability to test.
2. Choose a stable task ID and decide whether this is a new version.
3. Define Agent Goal.
4. Define Benchmark Stimulus, including `L/D` transition units.
5. Define RAN APIs and API roles.
6. Define Task Scoring and oracle visibility before formulas.
7. List conformance checks and mark missing checks explicitly.
8. Assign measurability, implementability, and readiness.
9. Only create `task.json` after the proposal is designed and reviewed.

## Examples

### `ws_prb_ping_v1`

- Agent Goal: keep a Docker OCUDU/Open5GS/srsUE ping episode healthy while issuing a valid PRB policy action.
- Benchmark Stimulus: one Docker gNB/UE/core episode with UE ping traffic and a fixed duration supplied by the suite or episode command.
- RAN APIs: WebSocket PRB action, JSON metrics, ping counters, and immediate action feedback.
- Task Scoring: action validity, ping health, metrics continuity, and cleanup.
- Readiness: `scored`.

### `e2_kpm_prb_ping_v1`

- Agent Goal: operate the same PRB WebSocket control path while requiring decoded E2SM-KPM v05 telemetry and oracle artifacts.
- Benchmark Stimulus: one Docker gNB/UE/core/FlexRIC episode with UE ping traffic, decoded KPM records, and fixed duration.
- RAN APIs: WebSocket PRB action, JSON metrics, ping counters, decoded KPM evidence, and immediate action feedback.
- Task Scoring: v3 scores plus KPM continuity and E2 oracle availability.
- Readiness: `scored` when KPM conformance and oracle validation pass.

### `json_metrics_to_ws_prb_recovery_v1`

- Agent Goal: detect degraded runtime health from JSON metrics and ping, then choose a valid PRB WebSocket action or no-action.
- Benchmark Stimulus: deterministic degradation and recovery timing with UE ping and JSON metrics.
- RAN APIs: bounded PRB actions or `None`; no direct access to logs or oracle summaries.
- Task Scoring: action timing, recovery evidence, ping health, metrics continuity, and cleanup.
- Readiness: `designed` or `conformance_needed`, depending on runtime support.

## Review Checklist

- The task is defined as Agent Goal + Benchmark Stimulus + RAN APIs + Task Scoring.
- The task is not one `L/D` pair.
- RAN APIs are listed as reusable APIs, not task definitions.
- Conformance is a setup gate, not a scored agent action.
- Oracle artifacts are hidden from the agent unless explicitly justified.
- Setup, conformance, runtime, oracle, agent, and cleanup failures are separated.
- Task Scoring is objective and computable from artifacts.
