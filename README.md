# OCUDU Agent Benchmark

`benchmark/` is the executable testbed for measuring autonomous agent performance on OCUDU-based RAN management episodes. The local repo is the orchestrator; the remote Linux host is the OCUDU system under test.

```text
+---------------- Local repo ----------------+
| benchmark/          tracked orchestrator    |
| tasks/*/task.json   task metadata           |
| .config             ignored site config     |
+-------------------+------------------------+
                    |
                 SSH/rsync
                    |
+---------------- Remote host ---------------+
| remote.workspace   disposable runtime state |
| OCUDU/Open5GS/srsUE/FlexRIC runtime         |
| runs/*             logs, captures, summaries|
+--------------------------------------------+
```

The benchmark owns orchestration, local action validation, conformance gating, agent loops, scoring, and summaries. The remote workspace owns OCUDU source/build/install trees, runtime processes, raw logs, PCAPs, metrics, and run artifacts.

## Quick Start

Create an ignored site config from the generic template:

```bash
cp .config.example .config
```

Fill in `remote.*`, `runtime.*`, `sources.*`, and `ric.provider`, then initialize the remote workspace:

```bash
python3 benchmark/benchctl.py remote check --config .config --json
python3 benchmark/benchctl.py remote init --config .config --json
python3 benchmark/benchctl.py remote sync --config .config --json
python3 benchmark/benchctl.py remote provision --config .config --json
python3 benchmark/benchctl.py remote ric-prepare --config .config --json
```

Run a WebSocket PRB-control suite:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_ping_v1 \
  --agent fixed_prb \
  --runs 2 \
  --duration 5 \
  --seed 1 \
  --suite-id ws-prb-smoke \
  --json
```

Run the E2SM-KPM v05 task after FlexRIC preparation:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task e2_kpm_prb_ping_v1 \
  --agent fixed_prb \
  --runs 2 \
  --duration 10 \
  --seed 1 \
  --suite-id e2-kpm-smoke \
  --json
```

## Choosing A Task

Benchmark tasks are explicit episode contracts. A task defines the runtime stack, allowed actions, observation sources, required conformance checks, scoring dimensions, and expected artifacts.

Current tasks:

- `ws_prb_ping_v1`: Docker Open5GS, OCUDU gNB, srsUE, UE ping traffic, WebSocket PRB policy control, and JSON metrics.
- `e2_kpm_prb_ping_v1`: the same WebSocket PRB action path plus Dockerized FlexRIC and decoded E2SM-KPM v05 observations.

Each task has a machine-readable manifest under `tasks/<task_id>/task.json` and a human task card under `tasks/<task_id>/README.md`.

## Main Entry Points

- [Agent guide](agents/README.md): Python API, CLI suite usage, built-in baselines, scoring rules, and safety rules.
- [Task catalog](tasks/README.md): task comparison table and task manifest contract.
- [Action schema](schemas/actions.schema.json): shared action catalog.
- [Observation schema](schemas/observations.schema.json): shared observation catalog.
- [Task schema](schemas/task.schema.json): task manifest field definitions.
- [Conformance checks](conformance/tests.json): reusable setup and runtime checks.

## Guardrails

- Do not commit `.config`, raw logs, PCAPs, remote source trees, Docker build output, or generated run artifacts.
- Do not skip conformance for scored runs.
- Treat setup, conformance, runtime, and oracle failures as unscored benchmark failures, not agent failures.
- Keep examples generic so another operator can reuse the project with their own remote host and workspace.

## Research And Setup Docs

- [Runtime API survey](../skillful-ran-research/benchmark/benchmark_design.md)
- [Benchmark architecture](../skillful-ran-research/benchmark/benchmark_architecture.md)
- [Remote OCUDU API setup](../skillful-ran-research/benchmark/remote_ocudu_api_setup.md)
