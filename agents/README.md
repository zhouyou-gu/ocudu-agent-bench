# Agent Guide

This page is for agents and harness authors that need to run benchmark episodes. Use the Python API for live observe/act loops and the CLI for setup, conformance, smoke tests, cleanup, and deterministic baselines.

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
})

summary = env.close()
```

Use `conformance: "required"` for scored episodes. `conformance: "observe"` can launch an unscored diagnostic episode when conformance fails. `conformance: "skip"` is for non-episode stubs or explicit debugging only.

## CLI Suite Lifecycle

Run repeated scored episodes with a built-in baseline:

```bash
python3 benchmark/benchctl.py episode suite \
  --config .config \
  --task ws_prb_ping_v1 \
  --agent fixed_prb \
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

## Built-In Baselines

- `fixed_prb`: sends one valid `{min=10,max=90}` PRB policy action after the first observation.
- `sweep_prb`: cycles deterministic valid min/max PRB ranges.
- `invalid_then_fixed`: sends one locally invalid PRB action, then the fixed valid action.

Agents may return `None` to their own loop when they do not want to act on an observation. In that case, skip calling `act()`; `BenchmarkEnv.act()` accepts action dictionaries only.

## Action Contract

Current scored tasks accept `SET_PRB_POLICY_RATIO_WS`:

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

## Observation Rules

Observation frames are normalized dictionaries. Agents should:

- Use the `backend` field to decide whether optional data is available.
- Tolerate missing optional fields.
- Treat E2 fields as meaningful only for `e2_kpm_prb_ping_v1`.
- Use `last_action` for the most recent local validation and WebSocket dispatch result.

Common observation sources are ping counters, JSON metrics status, backend status, and last action result. E2 tasks add RIC, xApp, decoded KPM, and oracle status fields.

## Scoring Rules

Setup, conformance, runtime, and oracle failures make a run unscored. Once setup succeeds, agent behavior is scored through:

- Accepted valid action rate.
- Correct local rejection of invalid actions.
- Ping success ratio.
- JSON metrics continuity.
- E2 KPM continuity for E2 tasks.
- Clean teardown success.

Single-UE ping is a control-loop health signal, not a throughput or fairness benchmark.

## Safety Rules

- Do not skip conformance for scored runs.
- Do not commit `.config` or private site details.
- Do not copy raw remote artifacts into local git.
- Do not reuse stale remote runtime state for scored episodes.
