"""Single-episode orchestration only."""

from __future__ import annotations

import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmark.benchmark_api.action import handle_agent_decision
from benchmark.benchmark_api.agent_api_wrapper import AgentApiWrapper
from benchmark.benchmark_api.conformance import run_readiness_checks
from benchmark.benchmark_api.feedback import build_feedback
from benchmark.benchmark_api.observation import build_observation
from benchmark.benchmark_api.runtime_setup import cleanup_runtime, instantiate_runtime
from benchmark.benchmark_api.scoring import score_episode
from benchmark.benchmark_api.stimulus import apply_pre_observation, expand_stimulus_plan, finish_in_step, start_in_step
from benchmark.benchmark_api.task_definition import agent_visible_task, load_task
from benchmark.benchmark_api.trace import TraceRecorder


@dataclass(frozen=True)
class EpisodeConfig:
    task_id: str
    run_id: str
    seed: int = 1
    tasks_dir: Path | None = None
    output_dir: Path | None = None
    agent_session_id: str | None = None


def run_episode(config: EpisodeConfig, agent: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    started_at = time.time()
    task = load_task(config.task_id, config.tasks_dir)
    agent_view = agent_visible_task(task)
    runtime = instantiate_runtime(task.E, config.run_id)
    stimulus_plan = expand_stimulus_plan(task.U, config.seed)
    trace = TraceRecorder(
        run_id=config.run_id,
        run_metadata={
            "task_id": task.task_id,
            "seed": config.seed,
            "started_at_s": started_at,
            "agent_session_id": config.agent_session_id or f"{config.run_id}-session",
        },
    )

    readiness = run_readiness_checks(task, runtime, stimulus_plan)
    trace.record_readiness(readiness)
    if readiness["status"] != "pass":
        trace.run_metadata["closed_at_s"] = time.time()
        trace.finalize_artifacts(config.output_dir)
        package = trace.finalize_trace()
        summary = _unscored_summary(task.task_id, config.run_id)
        summary, summary_path = _persist_summary(config.output_dir, config.run_id, summary)
        result = {"run_id": config.run_id, "task": task.task_id, "trace": package, "summary": summary}
        if summary_path is not None:
            result["scored_summary_path"] = summary_path
        return result

    wrapper = AgentApiWrapper(
        agent=agent,
        agent_view=agent_view,
        session_id=config.agent_session_id or f"{config.run_id}-session",
        decision_timeout_s=stimulus_plan.timing_policy.decision_deadline_s,
    )
    previous_feedback: dict[str, Any] | None = None
    for step_id in range(1, task.step_count + 1):
        for event in apply_pre_observation(stimulus_plan, runtime, step_id):
            trace.record_stimulus(event.to_private_record(config.run_id, config.seed))
        observation = build_observation(task, runtime, step_id, previous_feedback)
        trace.record_observation(observation)
        observation_emitted_at = observation["observation_timestamp_s"]
        in_step_events = start_in_step(stimulus_plan, runtime, step_id, observation_emitted_at_s=observation_emitted_at)
        agent_decision = wrapper.request_decision(observation, previous_feedback)
        telemetry = dict(agent_decision.telemetry or {})
        telemetry["timed_out"] = agent_decision.timed_out
        telemetry["malformed"] = agent_decision.malformed
        action_record = handle_agent_decision(
            task=task,
            runtime=runtime,
            step_id=step_id,
            decision=agent_decision.decision,
            telemetry=telemetry,
        )
        action_completed_at = (
            action_record.dispatch.completed_at_s if action_record.dispatch is not None else action_record.received_at_s
        )
        for event in finish_in_step(stimulus_plan, in_step_events, action_completed_at_s=action_completed_at):
            trace.record_stimulus(event.to_private_record(config.run_id, config.seed))
        trace.record_action(action_record.public_dict())
        feedback = build_feedback(action_record)
        trace.record_feedback(feedback)
        previous_feedback = feedback

    trace.run_metadata["closed_at_s"] = time.time()
    trace.finalize_artifacts(config.output_dir)
    cleanup = cleanup_runtime(runtime)
    trace.record_oracle({"cleanup": cleanup, "runtime_closed": runtime.closed})
    trace_package = trace.finalize_trace()
    summary = score_episode(task, trace_package)
    summary, summary_path = _persist_summary(config.output_dir, config.run_id, summary)
    result = {"run_id": config.run_id, "task": task.task_id, "trace": trace_package, "summary": summary}
    if summary_path is not None:
        result["scored_summary_path"] = summary_path
    return result


def _persist_summary(output_dir: Path | None, run_id: str, summary: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if output_dir is None:
        return summary, None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_id}.summary.json"
    payload = dict(summary)
    payload["scored_summary_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return payload, str(path)


def _unscored_summary(task_id: str, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "outcome": "unscored",
        "failure_category": "benchmark_failure",
        "raw_metrics": {},
        "component_scores": {},
        "efficiency": {},
        "scored": False,
    }
