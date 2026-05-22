# Remote Validation Runbook

This file is the remote workstation and live-readiness runbook. It describes
how benchmark files reach the configured workstation and which runtime paths
must be proven before a task can claim live OCUDU/FlexRIC/UE/core execution.

## Boundary

This file owns:

- Remote workstation access, sync, and validation workflow.
- Runtime readiness classes for OCUDU, Open5GS, srsUE/ZMQ, and FlexRIC.
- Live-adapter prerequisites and remote smoke commands.

This file does not own:

- Agent-facing API names, payload fields, and raw OCUDU/FlexRIC command inventory. Those belong in
  `skillful-ran-research/benchmark_design/benchmark_runtime_api_list.md`.
- Task scoring, trace partitioning, or stimulus scheduling.

## Config Contract

The local `.config` file supplies the remote SSH target, key, and workspace.
`benchctl.py remote sync` copies the local `benchmark/` tree to:

```text
<remote.workspace>/synced/benchmark/
```

Remote commands should run from the equivalent synced repository root:

```text
<remote.workspace>/synced/
```

## Local Commands

`benchctl.py` currently expects global flags before the subcommand:

```bash
python3 -m unittest discover benchmark/tests
python3 -m compileall -q benchmark
python3 benchmark/benchctl.py --json remote check --config .config
python3 benchmark/benchctl.py --json remote sync --config .config --dry-run
python3 benchmark/benchctl.py --json remote sync --config .config
```

## Remote Validation Commands

Run these after sync from `<remote.workspace>/synced/`:

```bash
python3 -m unittest discover benchmark/tests
python3 -m compileall -q benchmark
python3 benchmark/benchctl.py --json tasks list --suite all_checked_in
python3 benchmark/benchctl.py --json tasks list --suite standard --seed 1 --count 200
python3 benchmark/benchctl.py --json episode run --task base_prb_slice_congestion_rebalance_v1 --controller auto --seed 1 --output-dir /tmp/ocuduagentbench_remote_smoke
python3 benchmark/benchctl.py --json run --task base_prb_slice_congestion_rebalance_v1 --controller auto --runs 3 --seed 10 --output-dir /tmp/ocuduagentbench_remote_runs
python3 benchmark/benchctl.py --json run --suite all_checked_in --controller auto --seed 1 --output-dir /tmp/ocuduagentbench_remote_checked_in
python3 benchmark/benchctl.py --json run --suite standard --controller auto --seed 1 --count 200 --output-dir /tmp/ocuduagentbench_remote_standard
```

For expanded simulated coverage, use suite-level commands. Checked-in manifests
live under `benchmark/task_sets/{base,regression,compound}/<family>/<task_id>/`;
generated variants are in-memory tasks produced from
`benchmark/task_sets/generated/`.

## Required Readiness Classes

The files under `benchmark/provision/` are scaffolds for this readiness work,
not a completed live OCUDU/Open5GS/srsUE/FlexRIC deployment. They are useful as
operator-owned starting points for Docker/ZMQ/core setup, but they must be
replaced or completed and then validated by a live adapter before any task can
claim live-runtime execution.

| Readiness class | Required for | Evidence expected before live claim |
| --- | --- | --- |
| OCUDU WebSocket command path | WebSocket PRB and SSB actions | reachable endpoint, accepted safe command, redacted feedback |
| OCUDU JSON metrics path | metrics evidence tasks | metrics subscribe/unsubscribe and fresh parsed samples |
| OCUDU runtime CLI path | CLI HO/CHO tasks | CLI availability and command acceptance; mobility completion requires UE/topology evidence |
| Docker/ZMQ launch assets | simulated-radio OCUDU-suite tasks | reproducible OCUDU/Open5GS/srsUE launch plan and cleanup |
| FlexRIC and E2SM-KPM assets | E2 observation tasks | FlexRIC readiness, KPM indications, decoded PRB evidence |
| FlexRIC control xApp assets | E2 control tasks | control xApp readiness and accepted safe control action |
| Benchmark UE stimulus support | private UE traffic/lifecycle stimulus drivers | benchmark-owned UE traffic/lifecycle state and artifact collection |
| Benchmark core support backend | core lifecycle and UE-registration support tasks | benchmark-owned Open5GS/NF process control, redacted subscriber registration editing, and artifact collection |

## Live-Claim Rule

Runnable benchmark tasks currently use `E.runtime_adapter = simulated_ocudu`.
Remote execution of the simulated adapter is still simulation. A task may claim
live OCUDU/FlexRIC/UE/core execution only after a live adapter implements the
matching readiness class, dispatch path, artifact collection, cleanup, and
tests.
