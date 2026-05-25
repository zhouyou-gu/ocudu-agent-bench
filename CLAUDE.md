# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

# OCUDUAgentBench

Executable 5G O-RAN OCUDU benchmark for LLM agents. The repo is self-contained:
the task contract is documented in `docs/design/`, the executable harness
in `benchmark_api/`, the real-LLM driver in `agents/`, the live container
stacks in `provision/`, and 342 unit tests in `tests/`.

## What this repo IS

- **A task contract** (`docs/design/benchmark_design.md`): every task is
  `T = <G, E, U, I, J>` plus private metadata `M`. The agent sees only the
  redacted view; stimulus and oracle are benchmark-private.
- **A simulated closed-loop runtime**: `benchmark_api/simulated_ocudu.py` is the
  default `runtime_adapter` for every checked-in task. No containers required
  to test the agent harness.
- **Live runtime adapters**: `live_core` (open5gs split-NF), `live_ocudu`
  (OCUDU gNB WS + stdin CLI), `live_e2` (FlexRIC KPM observation + RC-DU + CCC
  control). Each lifts a different action family from "simulated" to "actually
  hits a real container/binary."
- **A real-LLM agent harness**: `agents/runner.py` runs one episode against
  one model; `agents/sweep.py` orchestrates an N-models × M-tasks sweep over
  vLLM on a remote box via SSH tunnel.

## What this repo is NOT

- Not a 5G stack — it depends on prebuilt OCUDU + Open5GS + FlexRIC + srsRAN
  images. See `provision/<stack>/README.md` for image dependencies.
- Not the design records — `docs/design/` is the authoritative spec, but the
  upstream design conversation lives in a separate research workspace.

## Runtime adapter status (as of 2026-05-25)

Every action family in `RanActionType` dispatches against a real runtime when
the matching live adapter is selected:

| Capability | Status | Implementation |
|---|---|---|
| Closed-loop simulator | ✅ | `benchmark_api/simulated_ocudu.py` |
| Post-action evidence verification | ✅ | scoring metric `post_action_evidence_match` |
| Open5GS 5G core (split-NF) | ✅ | `provision/open5gs-core/` + acceptance script |
| Open5GS 5G core (all-in-one) | ✅ | `provision/open5gs-aio/` (used by the live attach smoke) |
| OCUDU gNB + srsUE attach + ping | ✅ | `provision/ocudu-gnb-ue/` |
| `live_core` adapter (RESTART_CORE_NF, UPDATE_CORE_UE_REGISTRATION) | ✅ | `benchmark_api/live_core.py` |
| `live_ocudu` adapter — WS (PRB, SSB) | ✅ | `benchmark_api/live_ocudu.py` |
| `live_ocudu` adapter — stdin CLI (HO, CHO, CFO, TX-time) | ✅ | `benchmark_api/live_ocudu.py` |
| `live_e2` adapter — KPM v05 observation | ✅ | `benchmark_api/live_e2.py` |
| `live_e2` adapter — RC-DU PRB quota | ✅ | `live_e2.dispatch_rc_du_prb_policy` + ocudu-rc-du-prb-control xApp |
| `live_e2` adapter — CCC PRB quota | ✅ | `live_e2.dispatch_ccc_prb_policy` + ocudu-ccc-prb-control xApp |
| FlexRIC failure-path robustness | ✅ | 3-patch chain on the fork (main HEAD `5364526`); image `skillful-ran/flexric-bench:patch-iapp-control-failure` |

The FlexRIC fork lives at <https://github.com/zhouyou-gu/flexric-ocudu-kpm-v05>
(main HEAD `5364526`). The two OCUDU-specific control xApps
(`ocudu_ccc_prb_control`, `ocudu_rc_du_prb_control`) and the three
control-failure patches were authored in this benchmark's lifecycle.

## Action APIs

Eight families, all dispatch end-to-end:

- PRB policy — `SET_PRB_POLICY_RATIO_WS` (OCUDU WS), `SET_PRB_POLICY_RATIO_CCC` (E2 CCC), `SET_PRB_POLICY_RATIO_RC_DU` (E2 RC DU per-UE)
- SSB block power — `SET_SSB_BLOCK_POWER_WS`
- Handover — `TRIGGER_HANDOVER_CLI`, `TRIGGER_CONDITIONAL_HANDOVER_CLI`
- CFO + TX time offset — `SET_CFO_CLI`, `SET_TX_TIME_OFFSET_CLI`
- Core NF restart — `RESTART_CORE_NF`
- Core UE registration repair — `UPDATE_CORE_UE_REGISTRATION`
- `NO_ACTION` — benchmark-only; never dispatched

## Stimulus drivers

16 in `StimulusDriverKind`: traffic load, UE ping/activity churn, mobility
path, radio condition profile, slice demand shift, telemetry gap, E2 KPM
availability window, RIC xApp lifecycle, core latency/UE registration misconfig,
backhaul impairment, cell identity change, ZMQ impairment, plus two infra
drivers (docker-launch + metrics staleness mask). Stimulus is benchmark-private
and never adapts to the agent.

## Test + run commands

```bash
# Tests (342 total)
python3 -m unittest discover tests
python3 -m unittest discover tests/agents             # LLM-harness only
python3 -m unittest discover tests/emulated_agents
python3 -m compileall -q .                            # quick syntax check

# Simulated controller (no LLM, no containers)
python3 benchctl.py --json tasks list --suite all_checked_in
python3 benchctl.py --json run --suite all_checked_in --controller auto

# Single real-LLM episode (needs vLLM reachable at 127.0.0.1:8000)
python3 agents/runner.py --provider custom --base-url http://127.0.0.1:8000/v1 \
    --model qwen2.5-1.5b --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 30

# Multi-model sweep (drives vLLM lifecycle on a remote box via SSH)
python3 agents/sweep.py \
    --model tier:tiny --model tier:small \
    --suite all_checked_in --suite standard \
    --output-dir <output-dir> \
    --decision-deadline-s 5 --ready-timeout-s 3600
python3 agents/sweep_status.py --output-dir <output-dir>

# Remote sync (for cross-machine validation)
python3 benchctl.py --json remote check --config .config
python3 benchctl.py --json remote sync --config .config --dry-run
```

## Repository layout

| Path | What |
|---|---|
| `benchmark_api/` | core harness (runtime adapters, scoring, types) |
| `agents/` | real-LLM agent harness + sweep tooling |
| `conformance/` | readiness checks + redacted-view validators |
| `task_sets/` | checked-in (`base/`, `regression/`, `compound/`) + generated (`generated/`) manifests |
| `provision/` | docker compose stacks: `open5gs-core/`, `open5gs-aio/`, `ocudu-gnb-ue/`, `flexric/` |
| `schemas/` | JSON schemas for manifests + traces |
| `tests/` | 342 unit tests |
| `docs/benchmark-doc.html` | operational reference for the harness |
| `docs/design/` | authoritative design specs (task contract, stimulus, runtime APIs, sum types, timeline) |
| `docs/plans/`, `docs/specs/` | per-feature implementation records |

## What NOT to touch

- `.benchmark-workspace/` (gitignored) — runtime workspace for external clones,
  build contexts, per-run artifacts. Cleanable.
- `.config` (gitignored) — per-machine remote sync target.

## Mission boundary

The benchmark scores LLM agents on a closed-loop runtime. Every live adapter
must pass readiness, implement dispatch / artifact / cleanup / oracle, and
have unit-test coverage before the docs widen any claim about it. Don't
introduce mocks, abstraction layers, or backwards-compatibility shims that
aren't needed by the next concrete task. The harness is meant to be read
top-down by a new contributor; keep it that way.
