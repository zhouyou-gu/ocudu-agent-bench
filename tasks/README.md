# Benchmark Tasks

A benchmark task is a complete episode contract for an agent. It states what remote runtime is launched, what actions are valid, what observations are produced, which conformance checks must pass, how scoring works, and where artifacts are written.

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

## Task Metadata Contract

Task manifests include:

- Stable task id and display name.
- Episode and suite stage labels.
- Runtime family.
- Allowed action types.
- Observation sources.
- Required conformance check ids.
- Scoring dimensions.
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
