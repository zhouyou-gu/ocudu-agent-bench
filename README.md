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

The current local task manifests declare `E.runtime_adapter =
simulated_ocudu`. This is an explicit executable adapter for local harness tests,
not a claim that a remote OCUDU/FlexRIC deployment is running. A task that
declares an unavailable live adapter fails conformance readiness before scored
interaction.

## Entry Points

- `benchmark/benchctl.py tasks list --json`
- `benchmark/benchctl.py episode run --task ws_prb_ping_v1 --controller auto --json`
- `benchmark/benchctl.py run --task ws_prb_ping_v1 --controller auto --runs 2 --json`
- `benchmark/benchctl.py remote check --config .config --json`
- `benchmark/benchctl.py remote sync --config .config --dry-run --json`

`controller.py` owns repeated-run execution. `suite.py` aggregates completed
scored summaries only.

`remote sync` copies the local `benchmark/` tree to
`<remote.workspace>/synced/benchmark/` with `rsync --delete`. It supports the
existing section-style `.config` format and the newer `key=value` format.
