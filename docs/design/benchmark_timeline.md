# Benchmark Timeline Model

## Purpose

This file defines the timeline model for one scored OCUDUAgentBench task episode.

It owns episode-order semantics: when runtime setup, `L_i`, observation `E_i`,
`D_i`, action `A_i`, feedback `F_i`, trace recording, and scoring handoff occur.
It does not own concrete task inventory, runtime API payloads, stimulus driver
payloads, or scoring enum definitions.

Related files:

- `benchmark_design.md` explains the full module architecture that uses this timeline.
- `benchmark_task_list.md` lists current runnable tasks that conform to this timeline.
- `benchmark_runtime_api_list.md` maps current tasks to task-selected control/evidence/feedback APIs.
- `benchmark_stimulus_list.md` maps current tasks to concrete `L_i` and `D_i` stimulus choices.
- `benchmark_sum_type_list.md` defines the formal phase, API, action, and score enums used by the executable benchmark.

Benchmark Stimulus means the benchmark-controlled OCUDU runtime dynamics for the task. It is implemented as a private deterministic schedule of RAN-side drivers that make OCUDU evolve in repeatable ways. It is not an agent action path.

The core rule is that Benchmark Stimulus is predetermined by the task contract, run seed, and timing policy before scored interaction begins. It is not selected from, adapted to, or intensified because of agent behavior.

Agent actions still affect OCUDU runtime state, immediate feedback, and later observations. That effect belongs to the OCUDU runtime transition under the task-selected RAN APIs, not to the stimulus scheduler.

## Episode Contract

A task episode is defined as:

```text
T = <G, E, U, I, J>
```

where:

- `G` is the agent goal visible to the agent.
- `E` is the OCUDU runtime setup.
- `U` is the private Benchmark Stimulus plan.
- `I` is the task-selected RAN API surface.
- `J` is the post-run scoring rule.

Before the live interaction loop starts, the benchmark expands:

```text
U(seed, timing_policy) -> (L_1, D_1, L_2, D_2, ...)
```

where:

- `L_i` is pre-observation stimulus for step `i`.
- `D_i` is in-step stimulus during the reasoning/action interval for step `i`.

The expanded stimulus plan is benchmark-private. The agent receives only the goal, task-selected API projection, current observation, allowed action schema, and previous redacted feedback.

## Step Timeline

For each decision step `i`, the benchmark executes the following order:

```text
Step i starts
Benchmark applies L_i
OCUDU reaches observation condition S_i
Benchmark reads E_i from OCUDU through task-selected RAN APIs
Benchmark emits E_i to the agent
Benchmark starts D_i under the fixed step timing policy
Agent returns A_i, or the wrapper emits no-action on timeout
Benchmark applies A_i or no-action through the task-selected RAN APIs while D_i remains active
RAN APIs return immediate feedback F_i
Benchmark records (L_i, E_i, A_i, D_i, F_i)
OCUDU continues to the next runtime condition
```

The corresponding abstract transition is:

```text
S_1 = f_L(S_init, L_1)
E_i = read_I(S_i)
(T_i, F_i) = f_D(S_i, A_i, D_i)
S_{i+1} = f_L(T_i, L_{i+1})
```

`S_i` is the OCUDU runtime condition at observation step `i`. `T_i` is the applying condition while `A_i` or no-action is handled under `D_i`. `L_i` and `D_i` are controlled runtime-dynamics phases. `F_i` is immediate command-path feedback, not task success.

## Stimulus Dependency Rule

Allowed stimulus forms:

- Fully fixed stimulus schedules.
- Seeded schedules generated before the run.
- Phase-gated schedules where application waits for fixed timeline boundaries such as before observation or during the reasoning/action interval.
- Fixed decision windows where `D_i` ends at the configured step boundary, not when the agent returns early.

Disallowed in the core benchmark:

- Stimulus content selected from the agent's action.
- Stimulus intensity adjusted because the agent is succeeding or failing.
- Adversarial stimulus that reacts to a specific agent.
- Future stimulus events exposed to the agent.

Future extension:

- Predeclared conditional stimulus policies may be added later as a separate task class.
- Such policies must be deterministic, private, seed-controlled, logged, and conformance-gated.
- Conditional stimulus must never become an agent action path and must not expose future stimulus state to the agent.

## Action Effects Versus Stimulus Effects

Stimulus effects are benchmark-controlled runtime dynamics. They are fixed by `U`, the run seed, and the timing policy.

Action effects are endogenous runtime effects caused by the agent's selected `A_i` or no-action through `I`. Accepted actions may change later OCUDU evidence, but they do not rewrite the future stimulus schedule.

This distinction is required for attribution:

- If two agents receive the same `T`, seed, and timing policy, they face the same controlled runtime dynamics.
- If their traces diverge, the divergence is attributed to their actions, no-actions, latency, or runtime/API failures, not to the benchmark changing the stimulus in response to them.

## Timing Policy

The timing policy is fixed before the run and records:

- clock mode,
- decision deadline,
- late-action policy,
- action-apply policy,
- step boundary rules.

`D_i` starts when `E_i` is emitted and remains governed by the configured step boundary. If no valid action arrives before the decision deadline, the wrapper emits no-action. If the agent returns early, the benchmark does not shorten `D_i` unless the task explicitly defines that behavior in the timing policy.

## Recording Requirements

Every step records:

- `run_id`,
- `step_id`,
- `L_i` event ids and statuses,
- `E_i` observation timestamp,
- `A_i` action id or no-action record,
- `D_i` event ids and statuses,
- `F_i` feedback timestamp,
- scheduled and applied stimulus times,
- action receive, dispatch, and completion times when an action exists.

Stimulus records are private benchmark trace records. Agent-visible payloads must not include the stimulus schedule, future stimulus events, seed internals, driver state, runtime handles, raw logs, output paths, or oracle labels.

## Rationale

Predetermined stimulus preserves comparability because two agents face the same controlled runtime dynamics.

Agent-dependent stimulus confounds attribution because failure could come from the agent decision or from the benchmark changing the runtime condition after observing that decision.

Live dynamics are still preserved. OCUDU continues to evolve during the agent's reasoning/action interval, `D_i` can remain active while the agent responds, accepted actions can change later evidence, and scoring uses the resulting runtime trajectory.

The benchmark controls runtime dynamics. The agent controls only task-selected RAN API actions or no-action.
