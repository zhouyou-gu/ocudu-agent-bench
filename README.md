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

The benchmark owns orchestration, local action validation, conformance gating, agent loops, scoring, and summaries. The remote workspace owns OCUDU source/build/install trees, runtime processes, raw logs, PCAPs, metrics, and run artifacts. [API_REFERENCE.md](API_REFERENCE.md) is the standalone source of truth for implemented benchmark APIs and the boundary between reusable APIs and scored tasks.

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

The setup commands have two different jobs:

- **Provision** installs or builds the benchmark-owned remote runtime assets under `remote.workspace`. It prepares OCUDU, srsUE, Open5GS assets, Docker images, runtime dependency files, and the FlexRIC/KPM image. Provision answers: "is the remote testbed installed from pinned sources?"
- **Conformance** verifies that the provisioned runtime actually exposes the APIs and episode paths a task needs before an agent is scored. It checks launch paths, WebSocket control, JSON metrics, Docker e2e traffic, FlexRIC/E2 setup, decoded KPM records, E2SM-CCC/RC DU control tools, and oracle artifacts. Conformance answers: "is this setup valid enough to score agent performance?"

For scored suites, the benchmark runs the task's required conformance gate before launching scored episodes. You can also run conformance manually:

```bash
python3 benchmark/benchctl.py conformance list --json

python3 benchmark/benchctl.py conformance run \
  --config .config \
  --json \
  --run-id ws-prb-conf \
  --checks docker_e2e_assets,open5gs_core_health,srsue_zmq_attach,ping_traffic_path,websocket_prb_policy_action
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

Tasks consume APIs; they do not define APIs. A task manifest may reference action types such as `SET_PRB_POLICY_RATIO_WS` or observation sources such as `json_metrics`, but wire commands such as `rrm_policy_ratio_set` and `ssb_set` belong to the API reference. `NO_ACTION` in a task manifest means the agent should return Python `None`; it is not sent as a runtime command.

Current tasks:

- `ws_prb_ping_v1`: Docker Open5GS, OCUDU gNB, srsUE, UE ping traffic, WebSocket PRB policy control, and JSON metrics.
- `e2_kpm_prb_ping_v1`: the same WebSocket PRB action path plus Dockerized FlexRIC and decoded E2SM-KPM v05 observations.
- `ws_prb_noop_guard_v1`: healthy WebSocket/JSON metrics episode where the correct agent behavior is no RAN action.
- `ws_prb_error_repair_v1`: WebSocket PRB episode that scores local invalid-action rejection followed by valid repair.
- `ws_prb_action_budget_v1`: WebSocket PRB episode that scores one accepted action without repeated control churn.
- `e2_kpm_json_consistency_v1`: E2 KPM plus JSON metrics episode that scores action only after multi-source evidence is available.
- `metrics_staleness_noop_v1`: WebSocket PRB episode that masks early metrics as stale and scores waiting until freshness returns.
- `e2_ccc_prb_policy_ping_v1`: E2SM-CCC PRB policy control episode with ping, JSON metrics, decoded KPM, and E2 control oracle evidence.
- `e2_rc_du_prb_policy_ping_v1`: E2SM-RC DU PRB quota control episode that waits for UE identity evidence before dispatch.
- `e2_control_api_consistency_v1`: E2 control selection episode where the agent must choose CCC for a cell/slice PRB policy objective.
- `ws_ssb_power_guard_v1`: healthy episode where the SSB block-power API is available but the correct behavior is no action.
- `ws_ssb_power_repair_v1`: WebSocket SSB block-power episode that scores invalid local rejection followed by one valid `ssb_set` repair.

Each task has a machine-readable manifest under `tasks/<task_id>/task.json` and a human task card under `tasks/<task_id>/README.md`.

## Provision And Conformance Workflow

Use this order for a fresh or reset remote host:

```text
local .config
  -> remote check/init/sync
  -> remote provision
  -> remote ric-prepare
  -> conformance run
  -> episode suite
  -> cleanup/archive
```

`remote check` confirms the remote host and required host tools are reachable. `remote init` creates the remote workspace layout. `remote sync` copies the tracked benchmark harness into `remote.workspace/synced/`.

`remote provision` is the reproducible installation step. It is workspace-owned and can be run as one command or by stage:

```bash
python3 benchmark/benchctl.py remote provision --config .config --json
python3 benchmark/benchctl.py remote provision --config .config --stage assets --json
python3 benchmark/benchctl.py remote provision --config .config --stage images --json
python3 benchmark/benchctl.py remote provision --config .config --stage ocudu --json
python3 benchmark/benchctl.py remote provision --config .config --stage runtime-deps --json
python3 benchmark/benchctl.py remote provision --config .config --stage ric --json
```

Use `--dry-run` before a first install or after changing source pins:

```bash
python3 benchmark/benchctl.py remote provision --config .config --dry-run --json
```

`remote ric-prepare` is the FlexRIC-specific provisioning path for the E2SM-KPM v05 task. Run it after OCUDU is provisioned, and rerun it with `--force` when the FlexRIC source ref or OCUDU KPM decoder source changes.

Conformance is the pre-scoring validation step. Task manifests list the required conformance checks:

- `ws_prb_ping_v1`: Docker e2e assets, Open5GS health, srsUE attach, ping traffic, and WebSocket PRB policy action.
- `e2_kpm_prb_ping_v1`: FlexRIC assets, RIC health, OCUDU E2 config, E2 setup, KPM subscription, and E2 PCAP/log oracle.
- `e2_ccc_prb_policy_ping_v1`: the v4 E2/KPM gate plus the E2SM-CCC PRB control path.
- `e2_rc_du_prb_policy_ping_v1`: the v4 E2/KPM gate plus the E2SM-RC DU PRB control path.
- `e2_control_api_consistency_v1`: the v4 E2/KPM gate plus both CCC and RC DU control paths.
- `metrics_staleness_noop_v1`: the v3 WebSocket gate plus a scenario-mask check proving early observation frames are marked stale before scoring.
- `ws_ssb_power_guard_v1` and `ws_ssb_power_repair_v1`: the v3 WebSocket gate plus `websocket_ssb_power_action`, which verifies the native OCUDU `ssb_set` path.

Run conformance manually after provisioning, after changing config/source pins, or when debugging a failed suite. `episode suite` runs the required gate automatically unless `--skip-conformance` is used; skipped conformance marks the suite unscored.

Use workspace reset only when you intentionally want a clean remote install:

```bash
python3 benchmark/benchctl.py remote reset-workspace --config .config --force --json
```

This deletes prior source/build/install state and run artifacts under `remote.workspace`. It does not prune Docker daemon images.

## Main Entry Points

- [Implemented API reference](API_REFERENCE.md): benchmark harness APIs, OCUDU runtime APIs, action contracts, observations, and task/API mapping.
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
