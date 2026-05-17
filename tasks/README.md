# Benchmark Tasks

A benchmark task is a complete episode contract for an agent. It states what remote runtime is launched, what actions are valid, what observations are produced, which conformance checks must pass, how scoring works, and where artifacts are written.

Tasks are not APIs. A task consumes reusable benchmark APIs documented in [../API_REFERENCE.md](../API_REFERENCE.md), such as OCUDU WebSocket PRB control, WebSocket SSB control, JSON metrics, E2SM-KPM, E2SM-CCC, E2SM-RC, conformance, and artifact/oracle APIs. Task manifests reference action types and observation sources from that catalog; they must not define raw wire protocols or new OCUDU commands.

`NO_ACTION` is a task-level decision. Agents express it by returning Python `None` or by not emitting an action in a suite loop. It is not a runtime API command and is never sent to OCUDU.

Each task has:

- `task.json`: machine-readable metadata used by the registry.
- `README.md`: human task card for agents and operators.

Use [TASK_AUTHORING_GUIDE.md](TASK_AUTHORING_GUIDE.md) when proposing new tasks. It defines the task template, API role model, scenario and workload requirements, LLM-agent evaluation protocol, oracle visibility rules, measurability rubric, implementability rubric, and readiness levels for OCUDU RAN LLM-agent benchmarks.

## Current Task Catalog

| Task | Runtime | Control Path | Observation Path | Scoring Focus | Status |
| --- | --- | --- | --- | --- | --- |
| `ws_prb_ping_v1` | Docker Open5GS + OCUDU gNB + srsUE | OCUDU WebSocket PRB policy | UE ping + JSON metrics | action validity, ping health, metrics continuity, cleanup | scored |
| `e2_kpm_prb_ping_v1` | Docker Open5GS + OCUDU gNB + srsUE + FlexRIC | OCUDU WebSocket PRB policy | UE ping + JSON metrics + decoded E2SM-KPM v05 | v3 scores plus KPM continuity and E2 oracle availability | scored |
| `ws_prb_noop_guard_v1` | Docker Open5GS + OCUDU gNB + srsUE | no action expected | UE ping + JSON metrics | correct restraint, ping health, metrics continuity, cleanup | scored |
| `ws_prb_error_repair_v1` | Docker Open5GS + OCUDU gNB + srsUE | OCUDU WebSocket PRB policy | UE ping + JSON metrics + last action result | invalid local rejection, valid repair, traffic health | scored |
| `ws_prb_action_budget_v1` | Docker Open5GS + OCUDU gNB + srsUE | OCUDU WebSocket PRB policy | UE ping + JSON metrics | one accepted action, no action churn, cleanup | scored |
| `e2_kpm_json_consistency_v1` | Docker Open5GS + OCUDU gNB + srsUE + FlexRIC | OCUDU WebSocket PRB policy | UE ping + JSON metrics + decoded E2SM-KPM v05 | action after multi-source evidence, KPM continuity, cleanup | scored |
| `metrics_staleness_noop_v1` | Docker Open5GS + OCUDU gNB + srsUE | OCUDU WebSocket PRB policy after fresh metrics | UE ping + masked/fresh JSON metrics | no action on stale metrics, action after recovery, cleanup | scored |
| `e2_ccc_prb_policy_ping_v1` | Docker Open5GS + OCUDU gNB + srsUE + FlexRIC | E2SM-CCC PRB policy | UE ping + JSON metrics + decoded KPM + E2 control oracle | accepted CCC action, traffic health, KPM/control oracle, cleanup | scored |
| `e2_rc_du_prb_policy_ping_v1` | Docker Open5GS + OCUDU gNB + srsUE + FlexRIC | E2SM-RC DU PRB quota | UE ping + JSON metrics + decoded KPM + UE identity + E2 control oracle | accepted RC DU action after identity evidence, traffic health, cleanup | scored |
| `e2_control_api_consistency_v1` | Docker Open5GS + OCUDU gNB + srsUE + FlexRIC | E2SM-CCC or E2SM-RC DU | UE ping + JSON metrics + decoded KPM + E2 control oracle | correct API selection for a cell/slice PRB objective | scored |
| `ws_ssb_power_guard_v1` | Docker Open5GS + OCUDU gNB + srsUE | no action expected; SSB API available | UE ping + JSON metrics + cell identity | correct restraint, traffic health, metrics continuity, cleanup | scored |
| `ws_ssb_power_repair_v1` | Docker Open5GS + OCUDU gNB + srsUE | OCUDU WebSocket SSB block power | UE ping + JSON metrics + cell identity + last action result | invalid local rejection, valid `ssb_set` repair, traffic health | scored |

The v4.2 E2-control tasks are scored only when conformance proves the FlexRIC-derived runtime image exposes the required one-shot control tools and oracle artifacts.
The v3.3 SSB tasks are scored only for API validation, command acceptance, traffic health, metrics continuity, and cleanup; they do not claim RF-performance effects in ZMQ.

## Controller Trigger Matrix

Use `episode suite` for repeatable smoke tests and baseline comparisons. The suite runner performs the task conformance gate once, then launches one or more episodes. The `--controller` column below is the built-in deterministic controller that should pass the task when the runtime is healthy. A controller is not an LLM agent; it is a scripted reference policy. Custom LLM agents should implement the same task contract through the Python API or an equivalent suite loop.

| Task | Action API | Observation APIs | Oracle/Scoring APIs | Built-In Controller | Example Trigger |
| --- | --- | --- | --- | --- | --- |
| `ws_prb_ping_v1` | WebSocket PRB | ping, JSON metrics | action log, ping, metrics, cleanup | `fixed_prb` | `episode suite --task ws_prb_ping_v1 --controller fixed_prb` |
| `ws_prb_noop_guard_v1` | `NO_ACTION` decision; WebSocket PRB available | ping, JSON metrics | zero actions, ping, metrics, cleanup | `noop` | `episode suite --task ws_prb_noop_guard_v1 --controller noop` |
| `ws_prb_error_repair_v1` | WebSocket PRB | ping, JSON metrics, last action | local validation, accepted action, ping, metrics, cleanup | `invalid_then_fixed` | `episode suite --task ws_prb_error_repair_v1 --controller invalid_then_fixed` |
| `ws_prb_action_budget_v1` | WebSocket PRB | ping, JSON metrics | action count, accepted action, ping, metrics, cleanup | `fixed_prb` | `episode suite --task ws_prb_action_budget_v1 --controller fixed_prb` |
| `metrics_staleness_noop_v1` | `NO_ACTION` then WebSocket PRB | ping, masked/fresh JSON metrics | stale decision context, accepted action, ping, metrics, cleanup | `stale_guard_prb` | `episode suite --task metrics_staleness_noop_v1 --controller stale_guard_prb` |
| `ws_ssb_power_guard_v1` | `NO_ACTION` decision; WebSocket SSB available | ping, JSON metrics, cell identity | zero actions, ping, metrics, cleanup | `noop` | `episode suite --task ws_ssb_power_guard_v1 --controller noop` |
| `ws_ssb_power_repair_v1` | WebSocket SSB | ping, JSON metrics, cell identity, last action | local validation, accepted `ssb_set`, ping, metrics, cleanup | `invalid_then_ssb` | `episode suite --task ws_ssb_power_repair_v1 --controller invalid_then_ssb` |
| `e2_kpm_prb_ping_v1` | WebSocket PRB | ping, JSON metrics, E2SM-KPM v05 | KPM oracle, action log, ping, metrics, cleanup | `fixed_prb` | `episode suite --task e2_kpm_prb_ping_v1 --controller fixed_prb` |
| `e2_kpm_json_consistency_v1` | WebSocket PRB | ping, JSON metrics, E2SM-KPM v05 | decision context, KPM oracle, ping, metrics, cleanup | `evidence_gated_prb` | `episode suite --task e2_kpm_json_consistency_v1 --controller evidence_gated_prb` |
| `e2_ccc_prb_policy_ping_v1` | E2SM-CCC PRB | ping, JSON metrics, E2SM-KPM v05 | E2 control oracle, KPM oracle, ping, metrics, cleanup | `ccc_prb` | `episode suite --task e2_ccc_prb_policy_ping_v1 --controller ccc_prb` |
| `e2_rc_du_prb_policy_ping_v1` | E2SM-RC DU PRB | ping, JSON metrics, E2SM-KPM v05, UE identity | E2 control oracle, KPM oracle, ping, metrics, cleanup | `rc_du_prb` | `episode suite --task e2_rc_du_prb_policy_ping_v1 --controller rc_du_prb` |
| `e2_control_api_consistency_v1` | E2SM-CCC or E2SM-RC DU | ping, JSON metrics, E2SM-KPM v05 | expected action type, E2 control oracle, KPM oracle, cleanup | `e2_control_consistency` | `episode suite --task e2_control_api_consistency_v1 --controller e2_control_consistency` |

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

LLM agents usually operate through `BenchmarkEnv` using the benchmark loop: **Perceive -> Reason -> Execute -> Feedback -> Repeat**. In code, the agent calls `reset`, perceives each `observe` frame, reasons over task context and history, executes by returning one allowed action dictionary or `None`, receives feedback through `last_action` and later observations, then calls `close`. The CLI baseline controllers exercise the same episode contracts and are useful for smoke tests.

## Task Metadata Contract

Task manifests include:

- Stable task id and display name.
- Episode and suite stage labels.
- Runtime family.
- Allowed action types from the API catalog, not raw wire commands.
- Observation sources from the API catalog.
- Required conformance check ids.
- Canonical scoring dimensions emitted by episode summaries.
- Expected remote artifact groups.
- Readiness status.

The Python registry in `benchmark_api/tasks.py` loads these manifests and exposes supported task ids, conformance gates, and stage labels. Runtime launch behavior remains in `benchmark_api/episode.py`.

## Adding A Task

Add a new task only when it has:

- A task directory under `tasks/<task_id>/`.
- A valid `task.json` manifest.
- A human task README.
- Conformance checks that can block scored runs.
- Local action validation and observation normalization.
- Scoring and artifact rules.

Keep OCUDU-native APIs separate from benchmark harness APIs when describing the action or observation path.
