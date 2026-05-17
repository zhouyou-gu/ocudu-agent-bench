# Implemented API Reference

This document describes the APIs implemented by the standalone `benchmark/` project. It is written for LLM agents, built-in baseline controllers, and operators using the benchmark harness. It does not document OCUDU internals or define new OCUDU features.

The benchmark exposes two API layers:

- **Benchmark harness APIs**: local Python and CLI interfaces for reset, observation, action dispatch, suites, provisioning, conformance, scoring, and artifacts.
- **Runtime RAN APIs**: OCUDU and O-RAN interfaces exercised during a running episode on the remote system under test.

```text
+---------------- benchmark/ ----------------+
| Python API, CLI, validation, task registry  |
| conformance, suites, scoring, artifacts     |
+----------------------+---------------------+
                       |
                    SSH/rsync
                       |
+---------------- remote.workspace ----------+
| OCUDU gNB, Open5GS, srsUE, FlexRIC/xApps   |
| WebSocket, JSON metrics, E2 KPM/CCC/RC      |
+--------------------------------------------+
```

## Boundary Model

The project keeps task definitions and API definitions separate:

- **Harness API**: local Python/CLI surface owned by `benchmark/`, including provisioning, conformance, task registry, suites, scoring, artifact summaries, and cleanup.
- **Runtime RAN API**: OCUDU or O-RAN interface exercised during an episode, such as WebSocket PRB control, WebSocket SSB control, JSON metrics, E2SM-KPM, E2SM-CCC, or E2SM-RC.
- **Task contract**: scored episode definition that selects runtime APIs, observation sources, allowed action types, conformance checks, score dimensions, and artifact groups.
- **Provisioning API**: setup interface that installs or builds workspace-owned remote assets from pinned sources. It is not a scored agent-performance API.
- **Conformance API**: setup validation interface that proves runtime APIs and oracles work before a scored run. It is not a scored agent-performance API.
- **Oracle API**: artifact and summary interface that proves success, failure, or ground truth, such as decoded KPM records, E2 control outcomes, PCAP/log summaries, cleanup postconditions, and action logs.
- **Scenario/environment API**: harness-controlled workload or condition source, such as Docker/ZMQ runtime launch, UE ping traffic, metrics staleness masking, and future impairment injection.

Task manifests consume APIs; they do not define wire protocols, runtime behavior, or new OCUDU commands. `action_types` must use benchmark action names such as `SET_PRB_POLICY_RATIO_WS`, not raw wire commands such as `rrm_policy_ratio_set` or `ssb_set`.

`NO_ACTION` is a task-level decision represented by Python `None`. It is not sent to OCUDU and is not a runtime API command.

Ping, cleanup, artifact collection, PCAP/log parsing, and ZMQ scenario control are harness/environment/oracle mechanisms. They are useful for scoring and repeatability, but they are not OCUDU-native RAN control APIs.

## Agent-Facing Harness APIs

The canonical LLM-agent loop is:

```text
Perceive -> Reason -> Execute -> Feedback -> Repeat
```

`BenchmarkEnv` implements the wrapper side of this loop. The LLM agent perceives normalized observations, reasons outside the benchmark, returns an action or `None` for execution, receives feedback through action results and later observations, and repeats until the episode is closed and scored.

### Python API

Primary module:

```python
from benchmark.benchmark_api.env import BenchmarkEnv
```

Lifecycle:

- `reset(config)`: loads local config, optionally runs conformance, starts an episode for implemented tasks, and returns the initial observation.
- `observe()`: returns the latest normalized observation frame.
- `act(action, telemetry=None)`: validates an action locally, dispatches it through the task's runtime API when valid, and records action context.
- `close()`: cleans up remote runtime state, finalizes scoring, and returns the run summary.

Important `reset` fields:

- `task`: task id from `benchmark/tasks/*/task.json`.
- `conformance`: `required` for scored episode use, `observe` for debugging, `skip` only for the legacy stub path.
- `duration`: episode duration in seconds.
- `ws_port`: OCUDU remote-control WebSocket port, default `8001`.

`act(None)` is a no-op decision for episode tasks. It is accepted by the Python API and is not written as an action record. It is written to `decisions.jsonl` so no-op LLM decisions can still be timed and associated with token telemetry.

The benchmark does not call a provider SDK. External LLM agents or wrappers may pass telemetry such as `decision_latency_s`, `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `total_tokens`, `provider`, `model`, and `estimated_cost_usd`.

### CLI API

Entrypoint:

```bash
python3 benchmark/benchctl.py <group> <command> ...
```

Implemented command groups:

| Group | Commands | Purpose |
| --- | --- | --- |
| `remote` | `check`, `init`, `sync`, `provision`, `deps`, `ric-prepare`, `reset-workspace`, `exec` | Prepare and inspect the remote benchmark workspace. |
| `conformance` | `list`, `run` | Verify that runtime APIs and task prerequisites work before scoring. |
| `episode` | `run`, `suite`, `cleanup` | Run one episode, run repeated baseline suites, or clean a run id. |

Provisioning and conformance are harness APIs. They are not agent-performance APIs:

- **Provisioning** installs or builds workspace-owned runtime assets from pinned config sources.
- **Conformance** launches focused probes to prove that a task's required runtime APIs are usable before scoring.

## Implemented Runtime APIs

### OCUDU WebSocket Remote Control

Protocol layer:

- RFC6455 WebSocket text frames.
- JSON request/response messages.
- Endpoint: `127.0.0.1:<ws_port>` inside the remote host runtime.

Service style:

- Request/response command calls.
- Local action validation happens before dispatch.
- Invalid local actions are rejected without reaching OCUDU.

Implemented commands:

| Benchmark Action | OCUDU Command | Role | Tasks |
| --- | --- | --- | --- |
| `SET_PRB_POLICY_RATIO_WS` | `rrm_policy_ratio_set` | RAN control | PRB WebSocket tasks and E2 observation tasks that still act through WebSocket |
| `SET_SSB_BLOCK_POWER_WS` | `ssb_set` | RAN control | SSB block-power tasks |

#### `SET_PRB_POLICY_RATIO_WS`

Action contract:

```json
{
  "type": "SET_PRB_POLICY_RATIO_WS",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null
}
```

Validation:

- `type` must be `SET_PRB_POLICY_RATIO_WS`.
- `min_prb_policy_ratio` and `max_prb_policy_ratio` are required integers in `[0, 100]`.
- `min_prb_policy_ratio <= max_prb_policy_ratio`.
- `plmn` defaults to `00101`.
- `sst` defaults to `1`.
- `sd` is optional and must be an integer in `[0, 16777215]` when supplied.
- `dedicated_ratio` is optional and validity-only in current scoring.

Wire request:

```json
{
  "cmd": "rrm_policy_ratio_set",
  "policies": {
    "resourceType": "PRB",
    "rRMPolicyMemberList": [
      {
        "plmn": "00101",
        "sst": 1
      }
    ],
    "min_prb_policy_ratio": 10,
    "max_prb_policy_ratio": 90
  }
}
```

Conformance check:

- `websocket_prb_policy_action`

#### `SET_SSB_BLOCK_POWER_WS`

Action contract:

```json
{
  "type": "SET_SSB_BLOCK_POWER_WS",
  "plmn": "00101",
  "nci": 6733824,
  "ssb_block_power_dbm": -16
}
```

Validation:

- `type` must be `SET_SSB_BLOCK_POWER_WS`.
- `nci` is required and must be an integer NR cell identity in `[0, 68719476735]`.
- `ssb_block_power_dbm` is required and must be an integer in OCUDU's native `[-60, 50]` range.
- `plmn` defaults to `00101`.

Wire request:

```json
{
  "cmd": "ssb_set",
  "cells": [
    {
      "plmn": "00101",
      "nci": 6733824,
      "ssb_block_power_dbm": -16
    }
  ]
}
```

The harness exposes `observation.cell.nci` and `observation.cell.plmn` for SSB tasks so agents do not need to parse OCUDU logs directly.

Conformance check:

- `websocket_ssb_power_action`

### OCUDU JSON Metrics

Protocol layer:

- Same OCUDU WebSocket remote-control endpoint.
- JSON text frames.

Service style:

- Subscribe with `metrics_subscribe`.
- Receive pushed JSON/text metric frames during the episode.
- Persist raw frames to `metrics_raw.jsonl`.
- Normalize availability and freshness into observation frames.

Observation fields:

- `metrics.present`: at least one parseable metrics frame is available.
- `metrics.fresh`: metrics are usable for agent decisions.
- `metrics.stale`: benchmark scenario marks metrics as stale.
- `metrics.error`: parsing or subscription error when present.
- `backend.json_metrics`: backend availability status.

Conformance checks:

- `websocket_command_path`
- `json_metrics_stream`
- Task-level Docker checks that require JSON metrics continuity.

### O-RAN E2SM-KPM v05 Observation

Protocol layer:

- O-RAN E2 interface between OCUDU gNB and a Dockerized FlexRIC NearRT-RIC.
- KPM monitor xApp using the benchmark-compatible FlexRIC-derived image.

Service style:

- RIC starts before OCUDU gNB.
- gNB completes E2 setup.
- KPM xApp subscribes to E2SM-KPM.
- Decoded KPM v05 records are written to `e2_kpm_raw.jsonl`.
- The reusable E2 oracle summarizes setup, decoded KPM continuity, PRB measurement evidence, and PCAP/log availability.

Observation fields:

- `e2.enabled`
- `e2.ric_connected`
- `e2.kpm_indications`
- `e2.has_prb_measurement`
- `e2.last_kpm`
- `e2.oracle_available`
- `backend.e2_kpm`

Conformance checks:

- `flexric_docker_assets`
- `near_rt_ric_health`
- `ocudu_e2_config`
- `e2_setup_path`
- `e2_kpm_subscription`
- `e2_pcap_log_oracle`

### O-RAN E2SM-CCC PRB Policy Control

Protocol layer:

- O-RAN E2 with E2SM-CCC control.
- One-shot control tool in the FlexRIC-derived benchmark image.

Service style:

- Agent submits a local action through the benchmark API.
- Harness validates the action.
- Harness dispatches a run-scoped Docker one-shot control container.
- Control request/outcome is appended to `e2_control_raw.jsonl`.
- E2 oracle must report an accepted CCC control record before the run is scored.

Action contract:

```json
{
  "type": "SET_PRB_POLICY_RATIO_CCC",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null
}
```

Mapped control:

- E2SM-CCC `O-RRMPolicyRatio`.
- Same PRB policy field model as WebSocket PRB control.

Conformance check:

- `e2_ccc_prb_control_path`

### O-RAN E2SM-RC DU PRB Quota Control

Protocol layer:

- O-RAN E2 with E2SM-RC DU control.
- One-shot control tool in the FlexRIC-derived benchmark image.

Service style:

- Agent submits a local action through the benchmark API.
- Harness validates the action.
- DU UE identity is required for dispatch. The harness can use `du_ue_id` supplied by the action or discovered runtime state.
- Missing UE identity is a setup/runtime unscored condition, not an agent failure.
- Control request/outcome is appended to `e2_control_raw.jsonl`.
- E2 oracle must report an accepted RC DU control record before the run is scored.

Action contract:

```json
{
  "type": "SET_PRB_POLICY_RATIO_RC_DU",
  "plmn": "00101",
  "sst": 1,
  "sd": null,
  "min_prb_policy_ratio": 10,
  "max_prb_policy_ratio": 90,
  "dedicated_ratio": null,
  "du_ue_id": null
}
```

Mapped control:

- E2SM-RC DU control style `2`, action `6`.
- Benchmark semantics: slice PRB quota control.

Conformance check:

- `e2_rc_du_prb_control_path`

### E2 PCAP And Log Oracle

Protocol layer:

- Remote files generated by OCUDU, FlexRIC, xApps, and tcpdump sidecars.

Service style:

- Raw logs, PCAPs, and decoded traces stay remote.
- Harness writes summarized oracle JSON to `e2_oracle.json`.
- Oracle failures make the run unscored.

Oracle summary groups:

- `e2_setup_oracle`: RIC/gNB E2 setup evidence.
- `kpm_oracle`: decoded KPM record count and PRB measurement evidence.
- `pcap_log_oracle`: non-empty E2 PCAP/log artifacts.
- `control_oracle`: accepted CCC/RC control records and action types.

Conformance check:

- `e2_pcap_log_oracle`

### Docker/ZMQ Runtime Harness

This is an environment API, not an OCUDU management API.

Components:

- Docker Open5GS core.
- Docker OCUDU gNB with ZMQ RF.
- Docker srsUE.
- UE ping traffic to `10.45.1.1`.
- Optional Docker FlexRIC RIC, KPM xApp, E2 PCAP sidecar, and E2 control one-shot containers.

Observation fields:

- `ping.packets_transmitted`
- `ping.packets_received`
- `ping.success_ratio`
- `backend.ping`

Conformance checks:

- `docker_e2e_assets`
- `open5gs_core_health`
- `srsue_zmq_attach`
- `ping_traffic_path`

## Task To API Map

| Task | Action API | Observation APIs | Oracle / Scoring APIs |
| --- | --- | --- | --- |
| `ws_prb_ping_v1` | WebSocket PRB | ping, JSON metrics | action log, metrics continuity, cleanup |
| `ws_prb_noop_guard_v1` | no action expected; WebSocket PRB available | ping, JSON metrics | zero action count, cleanup |
| `ws_prb_error_repair_v1` | WebSocket PRB | ping, JSON metrics, last action | local rejection plus accepted repair |
| `ws_prb_action_budget_v1` | WebSocket PRB | ping, JSON metrics | action budget, no invalid actions |
| `metrics_staleness_noop_v1` | WebSocket PRB after freshness returns | ping, masked JSON metrics | stale-window action avoidance |
| `ws_ssb_power_guard_v1` | no action expected; WebSocket SSB available | ping, JSON metrics, cell identity | zero action count, cleanup |
| `ws_ssb_power_repair_v1` | WebSocket SSB | ping, JSON metrics, cell identity, last action | local rejection plus accepted `ssb_set` |
| `e2_kpm_prb_ping_v1` | WebSocket PRB | ping, JSON metrics, E2 KPM | decoded KPM and PCAP/log oracle |
| `e2_kpm_json_consistency_v1` | WebSocket PRB after evidence | ping, JSON metrics, E2 KPM | decision context includes JSON and E2 evidence |
| `e2_ccc_prb_policy_ping_v1` | E2SM-CCC PRB | ping, JSON metrics, E2 KPM | E2 control oracle has CCC record |
| `e2_rc_du_prb_policy_ping_v1` | E2SM-RC DU PRB | ping, JSON metrics, E2 KPM, DU UE identity | E2 control oracle has RC DU record |
| `e2_control_api_consistency_v1` | E2SM-CCC or E2SM-RC DU | ping, JSON metrics, E2 KPM | selected action type matches task objective |

## Action Record Semantics

Valid dispatched actions are appended to `actions.jsonl` with:

- local validation result,
- normalized request,
- raw runtime response,
- accepted/rejected status,
- reason,
- latest observation decision context.

Invalid local actions are also appended to `actions.jsonl`, but `dispatched` remains `false`.

`None` no-op decisions are not appended to `actions.jsonl`.

## Decision Record Semantics

Every LLM or built-in-controller decision may be appended to `decisions.jsonl`, including no-op decisions. This file measures the agent decision loop; `actions.jsonl` measures runtime control attempts.

Decision records include timestamp, observation index, action type or `null`, `no_op`, optional decision latency, optional token usage, and optional estimated cost.

Token usage is efficiency telemetry only. It does not change correctness scores.

## Observation Frame Semantics

Every observation is a JSON object with:

- run state,
- task and stage,
- ping state,
- metrics state,
- backend availability,
- last action result,
- optional task-specific fields.

Task-specific fields:

- `cell`: PLMN, NCI, gNB id, gNB id bit length, sector id, and source for SSB actions.
- `scenario`: labels such as stale metrics windows.
- `e2`: RIC, KPM, oracle, control availability, and DU UE identity fields for E2 tasks.

Agents should tolerate missing optional fields and branch on backend/task status rather than assuming every field exists for every task.

## Scoring And Failure Boundaries

Setup, provisioning, conformance, runtime launch, oracle, and cleanup failures make runs unscored. They are not counted as agent failures.

Agent behavior is scored only after setup succeeds. Episode summaries keep the legacy raw `scores` object and add scoring v2 fields:

- `episode_success`: `1.0` for a fully successful scored episode, otherwise `0.0`.
- `scored`: whether the run produced a valid benchmark measurement; a scored run can still have `episode_success = 0.0` when the agent made a wrong decision.
- `failure_reason`: setup/runtime/oracle reason for unscored runs, or task-behavior reason for scored agent failures.
- `failure_category`: `setup`, `conformance`, `runtime`, `oracle`, `agent`, `cleanup`, or `unknown`.
- `score_components`: normalized component scores for `task_correctness`, `action_correctness`, `evidence_use`, `ran_health`, `safety`, and `cleanup`.
- `efficiency`: separate timing, token, and optional cost telemetry.

Raw score dimensions include:

- accepted valid action rate,
- invalid local rejection correctness,
- expected action type correctness,
- no-op correctness,
- action-budget correctness,
- stale metrics action avoidance,
- ping success ratio,
- JSON metrics continuity,
- E2 KPM continuity,
- E2 oracle availability,
- E2 control oracle availability,
- clean teardown.

The task manifest `scoring` field uses exact raw summary score keys, for example `metrics_continuity`, `clean_teardown`, and `e2_oracle_available`. Cross-agent comparisons should start from `score_components`, `episode_success`, and the separate `efficiency` block, then drill into raw `scores` for debugging.

## Artifact API

Remote artifact layout:

```text
<remote.workspace>/runs/<run_id>/episode/
  scenario.json
  actions.jsonl
  decisions.jsonl
  observations.jsonl
  metrics_raw.jsonl
  e2_kpm_raw.jsonl
  e2_control_raw.jsonl
  e2_oracle.json
  summary.json
  cleanup.json
  logs/
```

Raw runtime artifacts stay remote. Local git should contain only benchmark code, task metadata, schemas, and documentation.

## Future API Implementation Roadmap

Near-term work:

- Finish operational validation for the E2SM-CCC and E2SM-RC DU one-shot control tools in the FlexRIC-derived image, then keep the corresponding tasks gated by `e2_ccc_prb_control_path` and `e2_rc_du_prb_control_path`.
- Improve E2 oracle parsing for CCC/RC control outcome evidence, including richer summaries from E2 PCAPs, OCUDU logs, RIC logs, xApp output, and `e2_control_raw.jsonl`.

Next API work:

- Add WebSocket applied-state readback or log-oracle evidence for PRB and SSB commands if OCUDU exposes reliable confirmation beyond command response acceptance.
- Add SSB power effect tasks only after a measurable RF, pathloss, or emulator oracle exists. Until then, SSB tasks remain command-correctness and restraint benchmarks.

Later API work:

- Add E2SM-RC CU-CP mobility or handover control only after the benchmark has a multi-cell mobility runtime and an objective handover oracle.
- Add ZMQ impairment controls as benchmark scenario APIs, not OCUDU-native RAN APIs, once they can be reproducibly injected and scored.

Deferred API work:

- O1/NETCONF, A1/O2, O-RU/Open Fronthaul metrics, and arbitrary per-UE controls remain out of scope until they can be conformance-gated and objectively scored in the single-workstation runtime.

## Deferred Or Excluded APIs

The benchmark intentionally does not currently implement:

- O1/NETCONF management loops,
- A1 or O2 interfaces,
- O-RU/Open Fronthaul metrics,
- CU-CP handover control,
- arbitrary per-UE RRC or bearer controls,
- ZMQ impairment controls as scored RAN APIs.

These should be added only when they can be conformance-gated and objectively scored in a reproducible single-workstation episode.
