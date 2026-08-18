# OCUDUAgentBench

`benchmark/` is the executable harness for the design in
`skillful-ran-research/benchmark_design/`. It is organized around the task
contract:

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

Benchmark-private stimulus drivers and task-selected runtime APIs are documented
in `skillful-ran-research/benchmark_design/benchmark_stimulus_list.md` and
`skillful-ran-research/benchmark_design/benchmark_runtime_api_list.md`.

## Documentation Boundary

This README is the short operational entry point for the executable harness:
task-set layout, common commands, and remote sync basics. Detailed implementation
mechanics live in `benchmark/docs/benchmark-doc.html`. The broader design
authority remains under `skillful-ran-research/benchmark_design/`.

Downloaded benchmark-design reference code, papers, and reports are kept out of
git under `.benchmark-workspace/external/benchmark-references/`; the tracked
index is `benchmark/docs/reference_index.md`.

The current simulated task surface is organized under `benchmark/task_sets/`:

- `base`: 25 primary checked-in tasks under `benchmark/task_sets/base/<family>/`.
- `regression`: 1 harness regression task under `benchmark/task_sets/regression/`.
- `compound`: 8 checked-in latent-cause diagnosis tasks under
  `benchmark/task_sets/compound/<family>/`.
- `all_checked_in`: aggregate view over `base`, `regression`, and `compound`.
- `generated` / `standard` / `diagnostic` / `stress`: deterministic in-memory
  single-anchor variants from `benchmark/task_sets/generated/axis_registry.json`
  and `suite_policies.json`.

Generated task IDs are opaque (`generated_sNNNN_hash_v1`). Axis names, sampled
values, and expected failure modes stay in private metadata and scored-summary
provenance so they do not leak into agent observations or feedback.

The current local task manifests declare `E.runtime_adapter =
simulated_ocudu`. This is an explicit executable adapter for local harness tests,
not a claim that a remote OCUDU/FlexRIC deployment is running. It applies
deterministic simulated state transitions so accepted actions can be reflected in
later redacted observations. A task that declares an unavailable live adapter
fails conformance readiness before scored interaction.

## Entry Points

- `benchmark/benchctl.py --json tasks list --suite all_checked_in`
- `benchmark/benchctl.py --json tasks list --suite standard --seed 1 --count 200`
- `benchmark/benchctl.py --json episode run --task base_prb_slice_congestion_rebalance_v1 --controller auto`
- `benchmark/benchctl.py --json run --task base_prb_slice_congestion_rebalance_v1 --controller auto --runs 2`
- `benchmark/benchctl.py --json run --suite compound --controller auto`
- `benchmark/benchctl.py --json run --suite standard --controller auto --seed 1 --count 200`
- `benchmark/benchctl.py --json remote check --config .config`
- `benchmark/benchctl.py --json remote sync --config .config --dry-run`

`controller.py` owns repeated-run execution. `suite.py` aggregates completed
scored summaries only.

When `--output-dir` is provided, each episode writes a private trace package
and a scored-summary sidecar after trace finalization.

`remote sync` copies the local `benchmark/` tree to
`<remote.workspace>/synced/benchmark/` with `rsync --delete`. It supports the
existing section-style `.config` format and the newer `key=value` format.
