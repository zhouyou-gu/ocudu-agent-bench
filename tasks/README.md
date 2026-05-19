# Benchmark Tasks

A benchmark task is a full scored RAN-management episode.

```text
Task = Agent Goal + Benchmark Stimulus + RAN APIs + Task Scoring
```

An `L/D` pair is one transition unit inside the task's Benchmark Stimulus. It is not a task.

The benchmark is the intermediate layer between the LLM agent and the RAN. Task docs describe the contract implemented by that layer: what evidence reaches the agent, what actions the agent may return, how valid actions are dispatched to RAN APIs, and how the resulting trace is scored.

| Part | Meaning |
| --- | --- |
| Agent Goal | What the agent is supposed to manage or decide. |
| Benchmark Stimulus | The controlled input or event sequence injected by the benchmark into the RAN to create the management condition used for evaluation. |
| RAN APIs | The RAN APIs the agent uses for evidence, actions/no-actions, and feedback. |
| Task Scoring | How the benchmark decides whether the full episode succeeded. |

Tasks are not APIs. A task consumes reusable benchmark APIs documented in [../API_REFERENCE.md](../API_REFERENCE.md), such as OCUDU WebSocket PRB control, WebSocket SSB control, JSON metrics, E2SM-KPM, E2SM-CCC, E2SM-RC, conformance, and artifact/oracle APIs. Task manifests reference action types and observation sources from that catalog; they must not define raw wire protocols or new OCUDU commands.

`NO_ACTION` is a task-level decision. Agents express it by returning Python `None` or by not emitting an action in a suite loop. It is not a runtime API command and is never sent to OCUDU.

Each task has:

- `task.json`: machine-readable metadata used by the registry.
- `README.md`: human task card for agents and operators.

Use [TASK_AUTHORING_GUIDE.md](TASK_AUTHORING_GUIDE.md) when proposing new tasks.

## Current Task Catalog

| Task | Agent Goal | Benchmark Stimulus | RAN APIs | Task Scoring |
| --- | --- | --- | --- | --- |
| `ws_prb_ping_v1` | Apply one valid PRB policy action while keeping the run healthy. | Docker Open5GS + OCUDU gNB + srsUE with UE ping traffic. | WebSocket PRB action, ping, JSON metrics. | Action validity, ping health, metrics continuity, cleanup. |
| `e2_kpm_prb_ping_v1` | Operate PRB control while standards-facing KPM evidence remains available. | Docker e2e runtime plus FlexRIC and KPM xApp. | WebSocket PRB action, ping, JSON metrics, decoded E2SM-KPM v05. | v3 scores plus KPM continuity and E2 oracle availability. |
| `ws_prb_noop_guard_v1` | Avoid unnecessary RAN control when the run is healthy. | Healthy Docker e2e runtime. | No-action decision, ping, JSON metrics. | Correct restraint, ping health, metrics continuity, cleanup. |
| `ws_prb_error_repair_v1` | Repair one rejected PRB action with a valid action. | Healthy Docker e2e runtime with prior failed feedback in the loop. | WebSocket PRB action, ping, JSON metrics, last action result. | Invalid local rejection, valid repair, traffic health. |
| `ws_prb_action_budget_v1` | Act once, then stop. | Healthy Docker e2e runtime. | WebSocket PRB action, ping, JSON metrics. | One accepted action, no repeated control churn, cleanup. |
| `e2_kpm_json_consistency_v1` | Wait for JSON and E2 evidence before acting. | Docker e2e runtime plus FlexRIC/KPM evidence. | WebSocket PRB action, ping, JSON metrics, decoded KPM. | Action after multi-source evidence, KPM continuity, cleanup. |
| `metrics_staleness_noop_v1` | Avoid action while metrics evidence is stale. | Early metrics are masked stale, then freshness returns. | No-action then WebSocket PRB, ping, masked/fresh metrics. | No action on stale metrics, action after recovery, cleanup. |
| `e2_ccc_prb_policy_ping_v1` | Use E2SM-CCC for cell/slice PRB policy control. | Docker e2e runtime plus FlexRIC control path. | E2SM-CCC action, ping, JSON metrics, KPM, E2 control feedback. | Accepted CCC action, traffic health, KPM/control oracle, cleanup. |
| `e2_rc_du_prb_policy_ping_v1` | Use E2SM-RC DU control after UE identity is available. | Docker e2e runtime plus FlexRIC and UE identity evidence. | E2SM-RC DU action, ping, JSON metrics, KPM, UE identity. | Accepted RC DU action after identity evidence, traffic health, cleanup. |
| `e2_control_api_consistency_v1` | Select the correct E2 control API for the management objective. | Docker e2e runtime plus both E2 control paths. | E2SM-CCC or E2SM-RC DU action, KPM, control feedback. | Correct API selection for a cell/slice PRB objective. |
| `ws_ssb_power_guard_v1` | Avoid unnecessary SSB block-power action. | Healthy Docker e2e runtime with SSB API available. | No-action decision, ping, JSON metrics, cell identity. | Correct restraint, traffic health, metrics continuity, cleanup. |
| `ws_ssb_power_repair_v1` | Repair one rejected SSB action with a valid action. | Healthy Docker e2e runtime with cell identity evidence. | WebSocket SSB action, ping, JSON metrics, last action result. | Invalid local rejection, valid `ssb_set` repair, traffic health. |
| `ran_policy_triage_v1` | Diagnose the task condition and choose the minimum safe action. | Benchmark selects internal benchmark stimulus from the implemented API families. | Stable action catalog, structured RAN evidence, management context. | Correct API selection, rationale shape, restraint, repair, stale-wait behavior, RAN health, cleanup. |

The E2-control tasks are scored only when conformance proves the FlexRIC-derived runtime image exposes the required one-shot control tools and oracle artifacts. The SSB tasks are scored for API validation, command acceptance, traffic health, metrics continuity, and cleanup; they do not claim RF-performance effects in ZMQ.

## Controller Trigger Matrix

Use `episode suite` for repeatable smoke tests and baseline comparisons. The suite runner performs the task conformance gate once, then launches one or more episodes. The `--controller` column below is the built-in deterministic controller that should pass the task when the runtime is healthy. A controller is not an LLM agent; it is a scripted reference policy.

| Task | Built-In Controller | Example Trigger |
| --- | --- | --- |
| `ws_prb_ping_v1` | `fixed_prb` | `episode suite --task ws_prb_ping_v1 --controller fixed_prb` |
| `ws_prb_noop_guard_v1` | `noop` | `episode suite --task ws_prb_noop_guard_v1 --controller noop` |
| `ws_prb_error_repair_v1` | `invalid_then_fixed` | `episode suite --task ws_prb_error_repair_v1 --controller invalid_then_fixed` |
| `ws_prb_action_budget_v1` | `fixed_prb` | `episode suite --task ws_prb_action_budget_v1 --controller fixed_prb` |
| `metrics_staleness_noop_v1` | `stale_guard_prb` | `episode suite --task metrics_staleness_noop_v1 --controller stale_guard_prb` |
| `ws_ssb_power_guard_v1` | `noop` | `episode suite --task ws_ssb_power_guard_v1 --controller noop` |
| `ws_ssb_power_repair_v1` | `invalid_then_ssb` | `episode suite --task ws_ssb_power_repair_v1 --controller invalid_then_ssb` |
| `e2_kpm_prb_ping_v1` | `fixed_prb` | `episode suite --task e2_kpm_prb_ping_v1 --controller fixed_prb` |
| `e2_kpm_json_consistency_v1` | `evidence_gated_prb` | `episode suite --task e2_kpm_json_consistency_v1 --controller evidence_gated_prb` |
| `e2_ccc_prb_policy_ping_v1` | `ccc_prb` | `episode suite --task e2_ccc_prb_policy_ping_v1 --controller ccc_prb` |
| `e2_rc_du_prb_policy_ping_v1` | `rc_du_prb` | `episode suite --task e2_rc_du_prb_policy_ping_v1 --controller rc_du_prb` |
| `e2_control_api_consistency_v1` | `e2_control_consistency` | `episode suite --task e2_control_api_consistency_v1 --controller e2_control_consistency` |
| `ran_policy_triage_v1` | `triage_reference` | `episode suite --task ran_policy_triage_v1 --controller triage_reference --runs 12` |

For a concrete run, add the usual shared options:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task <task_id> \
  --controller <baseline_controller> \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id <suite_id> \
  --json
```

LLM agents usually operate through `BenchmarkEnv` using the benchmark loop: **Perceive -> Reason -> Execute -> Feedback -> Repeat**. In code, the agent calls `reset`, perceives each benchmark-mediated `observe` frame, reasons over task context and history, executes by returning one allowed action dictionary or `None` to the benchmark layer, receives feedback through `last_action` and later observations, then calls `close`.

## Task Metadata Contract

Task manifests include:

- stable task id and display name,
- episode and suite stage labels,
- runtime family,
- allowed action types from the API catalog, not raw wire commands,
- observation sources from the API catalog,
- required conformance check ids,
- canonical task scoring dimensions emitted by episode summaries,
- expected remote artifact groups,
- readiness status.

The Python registry in `benchmark_api/tasks.py` loads these manifests and exposes supported task ids, conformance gates, and stage labels. Runtime launch behavior remains in `benchmark_api/episode.py`.

## Adding A Task

Add a new task only when it has:

- Agent Goal, Benchmark Stimulus, RAN APIs, and Task Scoring written down,
- a task directory under `tasks/<task_id>/`,
- a valid `task.json` manifest,
- a human task README,
- conformance checks that can block scored runs,
- local action validation and observation normalization,
- task scoring and artifact rules.

Keep OCUDU-native APIs separate from benchmark harness APIs when describing the action or observation path.
