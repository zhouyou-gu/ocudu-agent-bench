# OCUDUAgentBench

Executable benchmark harness for evaluating LLM agents on a 5G O-RAN OCUDU
runtime. Organized around the task contract:

```text
T = <G, E, U, I, J>
```

- `G`: agent-visible goal.
- `E`: benchmark-private OCUDU runtime setup.
- `U`: benchmark-private deterministic stimulus plan.
- `I`: task-selected RAN API projection.
- `J`: post-run oracle scoring rule.

Checked-in and generated manifests also carry private metadata `M` for task-set,
family, role, and generated-variant grouping. `M` is never agent-visible and is
not part of scoring.

The benchmark controls runtime setup and stimulus. The agent sees only the
redacted task view, selected RAN APIs, observations, accepted action schema, and
redacted feedback.

## Repository Layout

```
.
├── benchctl.py              # CLI entry point
├── benchmark_api/           # core harness modules (runtime adapters, scoring, types)
├── agents/                  # real-LLM agent harness + multi-model sweep tooling
├── conformance/             # readiness checks + redacted-view validators
├── task_sets/               # checked-in + generated task manifests
├── provision/               # docker compose stacks (open5gs core, OCUDU gNB+UE, FlexRIC)
├── schemas/                 # JSON schemas for task manifests + traces
├── tests/                   # 342 unit tests
└── docs/
    ├── benchmark-doc.html   # operational reference
    ├── design/              # authoritative design specs
    │   ├── benchmark_design.md
    │   ├── benchmark_task_list.md
    │   ├── benchmark_stimulus_list.md
    │   ├── benchmark_runtime_api_list.md
    │   ├── benchmark_sum_type_list.md
    │   └── benchmark_timeline.md
    ├── plans/               # implementation plans
    ├── specs/               # implementation specs
    └── reference_index.md   # pointer to off-tree reference cache
```

The runtime workspace at `.benchmark-workspace/` (gitignored) holds external
reference clones, build contexts, and per-run artifacts.

## Task Surface

`task_sets/` contains the simulated task surface:

- `base`: 25 primary checked-in tasks under `task_sets/base/<family>/`.
- `regression`: 1 harness regression task under `task_sets/regression/`.
- `compound`: 8 checked-in latent-cause diagnosis tasks under
  `task_sets/compound/<family>/`.
- `all_checked_in`: aggregate view over `base`, `regression`, and `compound`.
- `generated` / `standard` / `diagnostic` / `stress`: deterministic in-memory
  single-anchor variants from `task_sets/generated/axis_registry.json` and
  `suite_policies.json`.

Generated task IDs are opaque (`generated_sNNNN_hash_v1`). Axis names, sampled
values, and expected failure modes stay in private metadata and scored-summary
provenance so they do not leak into agent observations or feedback.

## Runtime Adapters

Task manifests pick a `runtime_adapter`:

| Adapter | What it dispatches against |
|---|---|
| `simulated_ocudu` (default) | Closed-loop simulator in `benchmark_api/simulated_ocudu.py`. No containers required. |
| `live_core` | Real open5gs split-NF stack (`provision/open5gs-core/`). `RESTART_CORE_NF` + `UPDATE_CORE_UE_REGISTRATION`. |
| `live_ocudu` | Real OCUDU gNB (`provision/ocudu-gnb-ue/`). WS-backed PRB/SSB + stdin-CLI handover/CFO/TX-time. |
| `live_e2` | FlexRIC stack (`provision/flexric/`). E2 KPM v05 observation + E2 control (RC-DU + CCC) via OCUDU-specific xApps. |

A task that declares an unavailable live adapter fails conformance readiness
before scored interaction.

## Common Commands

```bash
# Tests
python3 -m unittest discover tests                       # 342 tests
python3 -m unittest discover tests/agents                # LLM-harness only
python3 -m compileall -q .                               # quick syntax check

# Simulated controller (no LLM, no containers)
python3 benchctl.py --json tasks list --suite all_checked_in
python3 benchctl.py --json run --suite all_checked_in --controller auto

# Single real-LLM episode (assumes vLLM tunnel at 127.0.0.1:8000)
python3 agents/runner.py --provider custom \
    --base-url http://127.0.0.1:8000/v1 \
    --model qwen2.5-1.5b \
    --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 30

# Multi-model real-LLM sweep
python3 agents/sweep.py \
    --model tier:tiny --model tier:small \
    --suite all_checked_in --suite standard \
    --output-dir <output-dir> \
    --decision-deadline-s 5 --ready-timeout-s 3600
python3 agents/sweep_status.py --output-dir <output-dir>

# Remote sync (for distributed validation)
python3 benchctl.py --json remote check --config .config
python3 benchctl.py --json remote sync --config .config --dry-run
```

`controller.py` owns repeated-run execution. `suite.py` aggregates completed
scored summaries only. When `--output-dir` is provided, each episode writes a
private trace package and a scored-summary sidecar after trace finalization.

`remote sync` copies the local repo into `<remote.workspace>/synced/benchmark/`
with `rsync --delete`. Both section-style and `key=value` `.config` formats are
supported.

## Live Stack Bringup

Each `provision/` subdirectory has its own README with bringup/teardown and
sharp edges. Quick order for a full live stack (open5gs core → FlexRIC → OCUDU
gNB + srsUE):

```bash
docker compose -f provision/open5gs-aio/compose/docker-compose.open5gs-aio.yml up -d
bash provision/open5gs-aio/tests/check_aio_ready.sh

docker compose -f provision/flexric/compose/docker-compose.flexric.yml up -d

docker compose -f provision/ocudu-gnb-ue/compose/docker-compose.gnb-ue.yml up -d
bash provision/ocudu-gnb-ue/tests/check_attach_ping.sh
```

See `provision/<stack>/README.md` for image dependencies and the corresponding
adapter at `benchmark_api/live_{core,ocudu,e2}.py`.

## Documentation

- `docs/benchmark-doc.html` — operational reference for the executable harness.
- `docs/design/` — authoritative design specs (task contract, stimulus drivers,
  runtime API surface, closed sum-type catalog, timeline).
- `docs/plans/` and `docs/specs/` — per-feature implementation records.
- `docs/reference_index.md` — pointer to the off-tree reference cache under
  `.benchmark-workspace/external/`.
