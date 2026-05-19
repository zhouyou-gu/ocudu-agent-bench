"""Repeated-run controller and deterministic baseline agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.suite import aggregate_summaries


@dataclass(frozen=True)
class ControllerConfig:
    task_id: str
    controller_id: str = "fixed_prb"
    runs: int = 1
    seed: int = 1
    output_dir: Path | None = None
    tasks_dir: Path | None = None
    agent_session_policy: str = "isolated_per_run"


class BaselineController:
    def __init__(self, controller_id: str) -> None:
        self.controller_id = controller_id
        self.sent = False
        self.step = 0

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.step += 1
        if self.controller_id == "noop":
            return {"decision": None, "telemetry": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}}
        if self.controller_id == "invalid_then_fixed" and self.step == 1:
            return {
                "decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10},
                "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2},
            }
        if self.sent:
            return {"decision": None, "telemetry": {"prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}}
        self.sent = True
        actions = payload["task"]["api_projection"]["action_types"]
        if "SET_SSB_BLOCK_POWER_WS" in actions and self.controller_id in {"fixed_ssb", "auto"}:
            decision = {"type": "SET_SSB_BLOCK_POWER_WS", "nci": 6733824, "ssb_block_power_dbm": -16}
        elif "SET_PRB_POLICY_RATIO_CCC" in actions and self.controller_id in {"e2_ccc", "auto"}:
            decision = {"type": "SET_PRB_POLICY_RATIO_CCC", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}
        elif "SET_PRB_POLICY_RATIO_RC_DU" in actions and self.controller_id in {"rc_du", "auto"}:
            decision = {
                "type": "SET_PRB_POLICY_RATIO_RC_DU",
                "min_prb_policy_ratio": 20,
                "max_prb_policy_ratio": 80,
                "du_ue_id": payload["observation"].get("evidence", {}).get("ue_identity", {}).get("du_ue_id", 1),
            }
        else:
            decision = {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}
        return {"decision": decision, "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2}}


def run_repeated(config: ControllerConfig) -> dict[str, Any]:
    results = []
    summaries = []
    run_manifest = {
        "task_id": config.task_id,
        "controller_id": config.controller_id,
        "agent_session_policy": config.agent_session_policy,
        "seed_identifiers": [],
        "run_ids": [],
        "scored_summary_locations": [],
    }
    for index in range(1, config.runs + 1):
        run_id = f"{config.task_id}-seed{config.seed + index - 1}-r{index:03d}"
        run_seed = config.seed + index - 1
        agent = BaselineController(config.controller_id)
        result = run_episode(
            EpisodeConfig(
                task_id=config.task_id,
                run_id=run_id,
                seed=run_seed,
                tasks_dir=config.tasks_dir,
                output_dir=config.output_dir,
                agent_session_id=f"{run_id}-session",
            ),
            agent=agent,
        )
        results.append(result)
        summaries.append(result["summary"])
        run_manifest["seed_identifiers"].append(run_seed)
        run_manifest["run_ids"].append(run_id)
        run_manifest["scored_summary_locations"].append(f"memory://{run_id}/summary")
    return {
        "run_manifest": run_manifest,
        "runs": results,
        "suite_summary": aggregate_summaries(summaries, run_manifest),
    }
