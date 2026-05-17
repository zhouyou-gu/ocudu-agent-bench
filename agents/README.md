# LLM Agent And Controller Guide

This page separates two roles that are easy to confuse:

- **LLM agent**: the external system being evaluated. It follows the Perceive -> Reason -> Execute -> Feedback -> Repeat loop and is scored by the task.
- **Built-in baseline controller**: a deterministic scripted policy selected by CLI `--controller`, such as `fixed_prb` or `noop`. It is used for smoke tests and reference baselines; it is not an LLM agent.

Use the Python API for real LLM-agent execution loops. Use the CLI for setup, conformance, smoke tests, cleanup, and deterministic baseline controllers. The old CLI spelling `--agent` is still accepted as a compatibility alias for `--controller`.

Suite JSON summaries use `controller` as the canonical field. They may also include a legacy `agent` field for older consumers; treat that field as deprecated and equivalent to `controller`.

## Perceive -> Reason -> Execute -> Feedback -> Repeat

Use this loop to frame every LLM-agent episode:

```text
Perceive -> Reason -> Execute -> Feedback -> Repeat
```

- **Perceive**: call `observe()` or read the observation returned by `reset()`. The observation is structured RAN context, not raw remote shell access.
- **Reason**: use the task objective, current observation, and prior history to decide whether the safest next decision is no-op, retry, repair, or a RAN-management action.
- **Execute**: return either `None` or one allowed action dictionary. `BenchmarkEnv` validates it locally and executes valid actions through OCUDU or FlexRIC.
- **Feedback**: inspect the next observation and `last_action` result for validation errors, API acceptance, updated metrics, and E2 evidence.
- **Repeat**: continue until the episode reaches its duration or terminal state, then call `close()` and use the returned summary for evaluation.

## Setup Workflow Concepts

LLM agents and operators should distinguish provisioning from conformance:

- **Provision** prepares the remote testbed. It installs or builds workspace-owned OCUDU, srsUE, Open5GS assets, runtime libraries, Docker images, and FlexRIC/KPM assets from the configured source pins.
- **Conformance** validates that the provisioned testbed can run the task APIs and episode path before the agent is scored.

Provision changes the remote workspace; conformance tests the workspace. A scored agent episode should run only after the task's conformance gate passes.

Typical operator workflow:

```bash
python3 benchmark/benchctl.py remote check --config .config --json
python3 benchmark/benchctl.py remote init --config .config --json
python3 benchmark/benchctl.py remote sync --config .config --json
python3 benchmark/benchctl.py remote provision --config .config --json
python3 benchmark/benchctl.py remote ric-prepare --config .config --json
python3 benchmark/benchctl.py conformance run --config .config --json
```

After this, LLM agents can use `BenchmarkEnv.reset({"conformance": "required"})` or operators can use `episode suite` for scored runs. Rerun provisioning when source pins, Docker images, or workspace-owned runtime assets change. Rerun conformance after provisioning, after config changes, or after a runtime failure that may have left stale state.

## Python API Lifecycle

```python
from benchmark.benchmark_api.env import BenchmarkEnv

env = BenchmarkEnv(config_path=".config")

reset = env.reset({
    "task": "ws_prb_ping_v1",
    "conformance": "required",
    "duration": 30,
})

observation = env.observe()
action_result = env.act({
    "type": "SET_PRB_POLICY_RATIO_WS",
    "plmn": "00101",
    "sst": 1,
    "sd": None,
    "min_prb_policy_ratio": 10,
    "max_prb_policy_ratio": 90,
    "dedicated_ratio": None,
}, telemetry={
    "provider": "generic",
    "model": "my-llm-agent",
    "prompt_tokens": 1200,
    "completion_tokens": 180,
    "reasoning_tokens": 60,
    "estimated_cost_usd": 0.03,
})

summary = env.close()
```

Use `conformance: "required"` for scored episodes. `conformance: "observe"` can launch an unscored diagnostic episode when conformance fails. `conformance: "skip"` is for non-episode stubs or explicit debugging only.

## CLI Suite Lifecycle

Run repeated scored episodes with a built-in baseline controller:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_ping_v1 \
  --controller fixed_prb \
  --runs 3 \
  --duration 10 \
  --seed 1 \
  --suite-id ws-prb-suite \
  --json
```

Clean up a failed run:

```bash
python3 benchmark/benchctl.py episode cleanup \
  --config .config \
  --run-id <run_id> \
  --json
```

## Built-In Baseline Controllers

- `fixed_prb`: sends one valid `{min=10,max=90}` PRB policy action after the first observation.
- `sweep_prb`: cycles deterministic valid min/max PRB ranges.
- `invalid_then_fixed`: sends one locally invalid PRB action, then the fixed valid action.
- `noop`: returns `None` for every observation and never calls the RAN control path.
- `evidence_gated_prb`: waits for fresh JSON metrics, and for E2 tasks decoded PRB KPM evidence, then sends one fixed PRB action.
- `stale_guard_prb`: returns `None` while metrics are marked stale, then sends one fixed PRB action after fresh metrics return.
- `ccc_prb`: waits for fresh metrics and E2 PRB evidence, then sends one E2SM-CCC PRB policy action.
- `rc_du_prb`: waits for fresh metrics, E2 PRB evidence, and DU UE identity, then sends one E2SM-RC DU PRB quota action.
- `e2_control_consistency`: waits for E2 evidence, then chooses the CCC action for cell/slice PRB policy selection.
- `ssb_power`: sends one valid WebSocket SSB block-power action using the observed cell identity.
- `invalid_then_ssb`: sends one locally invalid SSB block-power action, then a valid SSB action using the observed cell identity.

Recommended built-in controller by task:

| Task | Built-In Controller |
| --- | --- |
| `ws_prb_ping_v1` | `fixed_prb` |
| `e2_kpm_prb_ping_v1` | `fixed_prb` |
| `ws_prb_noop_guard_v1` | `noop` |
| `ws_prb_error_repair_v1` | `invalid_then_fixed` |
| `ws_prb_action_budget_v1` | `fixed_prb` |
| `e2_kpm_json_consistency_v1` | `evidence_gated_prb` |
| `metrics_staleness_noop_v1` | `stale_guard_prb` |
| `e2_ccc_prb_policy_ping_v1` | `ccc_prb` |
| `e2_rc_du_prb_policy_ping_v1` | `rc_du_prb` |
| `e2_control_api_consistency_v1` | `e2_control_consistency` |
| `ws_ssb_power_guard_v1` | `noop` |
| `ws_ssb_power_repair_v1` | `invalid_then_ssb` |

LLM agents may return `None` when they do not want to act on an observation. Suite loops skip `None` actions, and `BenchmarkEnv.act(None, telemetry=...)` records a no-op decision in `decisions.jsonl` without adding an action to `actions.jsonl`.

## Action Contract

The current action family controls PRB policy ratios. WebSocket tasks accept `SET_PRB_POLICY_RATIO_WS`:

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

Validation rules:

- `type` must be `SET_PRB_POLICY_RATIO_WS`.
- `min_prb_policy_ratio` and `max_prb_policy_ratio` are required integers in `[0, 100]`.
- `min_prb_policy_ratio <= max_prb_policy_ratio`.
- `plmn` defaults to `00101`; `sst` defaults to `1`; `sd` is optional.
- `dedicated_ratio` is validity-only in the current tasks and is not independently scored.

Invalid actions are rejected locally before dispatch to OCUDU.

E2 control tasks use the same PRB fields with different action types:

- `SET_PRB_POLICY_RATIO_CCC`: E2SM-CCC cell/slice PRB policy through the FlexRIC control xApp.
- `SET_PRB_POLICY_RATIO_RC_DU`: E2SM-RC DU PRB quota control; `du_ue_id` may be supplied or discovered by the harness.

For `e2_control_api_consistency_v1`, the correct action is `SET_PRB_POLICY_RATIO_CCC`.

SSB power tasks use OCUDU's native WebSocket `ssb_set` command:

```json
{
  "type": "SET_SSB_BLOCK_POWER_WS",
  "plmn": "00101",
  "nci": 6733824,
  "ssb_block_power_dbm": -16
}
```

`nci` is the 36-bit NR cell identity. `ssb_block_power_dbm` must be an integer in `[-60, 50]`. The harness exposes `cell.nci` in observations so agents do not need to infer it from logs.

## Observation Rules

Observation frames are normalized dictionaries. Agents should:

- Use the `backend` field to decide whether optional data is available.
- Tolerate missing optional fields.
- Treat E2 fields as meaningful only for E2 tasks.
- Use `last_action` for the most recent local validation and WebSocket dispatch result.
- Use `metrics.stale` and `metrics.fresh` in staleness tasks before deciding whether an action is safe.
- Use `cell.nci` and `cell.plmn` for SSB block-power actions.

Common observation sources are ping counters, JSON metrics status, backend status, and last action result. E2 tasks add RIC, xApp, decoded KPM, and oracle status fields.

## Scoring Rules

Setup, conformance, runtime, and oracle failures make a run unscored. Bad LLM-agent behavior after setup succeeds remains a scored measurement with `episode_success = 0.0`. Each summary reports `failure_category` and `failure_reason` so those failures are separated from setup/runtime failures.

The primary comparison surface is component scoring, not a single all-up score:

- `task_correctness`: task-specific objective success.
- `action_correctness`: valid actions accepted, invalid actions locally rejected, and expected action type used.
- `evidence_use`: action timing relative to JSON metrics, E2 KPM, stale/fresh observations, and oracle evidence.
- `ran_health`: ping health plus required JSON metrics and E2 KPM continuity.
- `safety`: action budget, no invalid dispatch, no unnecessary churn, and no action during unsafe windows.
- `cleanup`: clean teardown and closed runtime resources.

The legacy raw `scores` dictionary remains for debugging and includes:

- Accepted valid action rate.
- Correct local rejection of invalid actions.
- Ping success ratio.
- JSON metrics continuity.
- E2 KPM continuity for E2 tasks.
- E2 control oracle availability for CCC/RC tasks.
- Task-specific behavior such as no-op correctness, action-budget compliance, evidence-gated action, and stale-metrics action avoidance.
- Clean teardown success.

The `efficiency` block reports time, token, and optional cost telemetry separately from correctness. Built-in controllers report decision timing only. Real LLM agents should pass provider/model and token counts through `BenchmarkEnv.act(..., telemetry=...)` so suites can compare cost and latency alongside score components.

Single-UE ping is a control-loop health signal, not a throughput or fairness benchmark.

## Safety Rules

- Do not skip conformance for scored runs.
- Do not commit `.config` or private site details.
- Do not copy raw remote artifacts into local git.
- Do not reuse stale remote runtime state for scored episodes.
