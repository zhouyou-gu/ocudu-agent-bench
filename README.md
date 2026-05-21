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

The benchmark controls runtime setup and stimulus. The agent sees only the
redacted task view, selected RAN APIs, observations, accepted action schema, and
redacted feedback.

Benchmark-private stimulus drivers and task-selected runtime APIs are documented
in `skillful-ran-research/benchmark_design/benchmark_stimulus_list.md` and
`skillful-ran-research/benchmark_design/benchmark_runtime_api_list.md`.

The current local task manifests declare `E.runtime_adapter =
simulated_ocudu`. This is an explicit executable adapter for local harness tests,
not a claim that a remote OCUDU/FlexRIC deployment is running. A task that
declares an unavailable live adapter fails conformance readiness before scored
interaction.

## Entry Points

- `benchmark/benchctl.py --json tasks list`
- `benchmark/benchctl.py --json episode run --task slice_congestion_prb_rebalance_v1 --controller auto`
- `benchmark/benchctl.py --json run --task slice_congestion_prb_rebalance_v1 --controller auto --runs 2`
- `benchmark/benchctl.py --json remote check --config .config`
- `benchmark/benchctl.py --json remote sync --config .config --dry-run`

`controller.py` owns repeated-run execution. `suite.py` aggregates completed
scored summaries only.

When `--output-dir` is provided, each episode writes a private trace package
and a scored-summary sidecar after trace finalization.

`remote sync` copies the local `benchmark/` tree to
`<remote.workspace>/synced/benchmark/` with `rsync --delete`. It supports the
existing section-style `.config` format and the newer `key=value` format.
