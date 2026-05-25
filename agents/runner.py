"""Episode runner and CLI for the real-LLM agent harness.

Why this module exists separately from :mod:`benchmark_api.episode`:
the checked-in task manifests set ``U.timing_policy.decision_deadline_s`` to
0.01 seconds (the deterministic baselines respond in microseconds). Any real
LLM call takes seconds, so we need to clone the selected task with a relaxed
timing policy before running. This runner mirrors ``episode.run_episode`` but
operates on a pre-cloned :class:`PrivateTask`.

CLI surface is intentionally small. Example invocations live in the package
README.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make ``python3 benchmark/agents/runner.py ...`` work when run from the
# repository root without installing the package.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_api.action import handle_agent_decision
from benchmark_api.agent_api_wrapper import AgentApiWrapper
from benchmark_api.conformance import run_readiness_checks
from benchmark_api.feedback import build_feedback
from benchmark_api.observation import build_observation
from benchmark_api.runtime_setup import cleanup_runtime, instantiate_runtime
from benchmark_api.scoring import score_episode
from benchmark_api.stimulus import (
    apply_pre_observation,
    expand_stimulus_plan,
    finish_in_step,
    start_in_step,
)
from benchmark_api.task_catalog import load_task_for_suite
from benchmark_api.task_definition import (
    PrivateTask,
    agent_visible_task,
    clone_task_with_overrides,
    task_provenance,
)
from benchmark_api.trace import TraceRecorder

from agents.backends import LLMBackend, build_backend
from agents.llm_agent import LLMAgent


@dataclass(frozen=True)
class RealAgentConfig:
    task_id: str
    run_id: str
    seed: int = 1
    suite: str = "base"
    suite_count: int | None = None
    family: str | None = None
    output_dir: Path | None = None
    decision_deadline_s: float = 30.0
    step_interval_padding_s: float = 0.5


def relax_task_clock(task: PrivateTask, decision_deadline_s: float, step_interval_padding_s: float) -> PrivateTask:
    """Return a clone of ``task`` with a relaxed timing policy.

    The benchmark requires ``decision_deadline_s <= step_interval_s``; we set
    ``step_interval_s = decision_deadline_s + padding`` so the per-step sleep
    after the agent responds is bounded by the padding.
    """

    if decision_deadline_s <= 0:
        raise ValueError("decision_deadline_s must be positive")
    if step_interval_padding_s < 0:
        raise ValueError("step_interval_padding_s must be non-negative")
    U = copy.deepcopy(task.U)
    timing = dict(U.get("timing_policy") or {})
    timing["clock_mode"] = "wall_clock"
    timing["step_interval_s"] = float(decision_deadline_s + step_interval_padding_s)
    timing["decision_deadline_s"] = float(decision_deadline_s)
    U["timing_policy"] = timing
    return clone_task_with_overrides(task, task_id=task.task_id, U=U)


def run_agent_episode(task: PrivateTask, agent, config: RealAgentConfig) -> dict[str, Any]:
    """Run one episode against an already-loaded (and clock-relaxed) task.

    Mirrors :func:`benchmark_api.episode.run_episode`. Kept here so
    the harness can use a cloned task without touching the core runner.
    """

    started_at = time.time()
    agent_view = agent_visible_task(task)
    suite_seed = config.seed
    provenance = task_provenance(
        task,
        suite=config.suite,
        episode_seed=config.seed,
        suite_seed=suite_seed,
        suite_count=config.suite_count,
        family=config.family,
    )
    runtime = instantiate_runtime(task.E, config.run_id)
    stimulus_plan = expand_stimulus_plan(task.U, config.seed)
    trace = TraceRecorder(
        run_id=config.run_id,
        run_metadata={
            "task_id": task.task_id,
            "seed": config.seed,
            "suite": config.suite,
            "suite_seed": suite_seed,
            "suite_count": config.suite_count,
            "family": config.family,
            "task_provenance": provenance,
            "started_at_s": started_at,
            "agent_session_id": f"{config.run_id}-session",
            "agent_harness": "agents",
            "decision_deadline_s": stimulus_plan.timing_policy.decision_deadline_s,
            "step_interval_s": stimulus_plan.timing_policy.step_interval_s,
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
        session_id=f"{config.run_id}-session",
        decision_timeout_s=stimulus_plan.timing_policy.decision_deadline_s,
    )
    previous_feedback: dict[str, Any] | None = None
    for step_id in range(1, task.step_count + 1):
        for event in apply_pre_observation(stimulus_plan, runtime, step_id):
            trace.record_stimulus(event.to_private_record(config.run_id, config.seed))
        observation = build_observation(task, runtime, step_id, previous_feedback)
        trace.record_observation(observation)
        observation_emitted_at = observation["observation_timestamp_s"]
        in_step_events = start_in_step(
            stimulus_plan, runtime, step_id, observation_emitted_at_s=observation_emitted_at
        )
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
            action_record.dispatch.completed_at_s
            if action_record.dispatch is not None
            else action_record.received_at_s
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
    package = trace.finalize_trace()
    summary = score_episode(task, package)
    summary, summary_path = _persist_summary(config.output_dir, config.run_id, summary)
    result = {"run_id": config.run_id, "task": task.task_id, "trace": package, "summary": summary}
    if summary_path is not None:
        result["scored_summary_path"] = summary_path
    return result


def run_with_real_agent(config: RealAgentConfig, backend: LLMBackend) -> dict[str, Any]:
    """Entry-point used by the CLI and by tests: load → relax clock → run."""

    task = load_task_for_suite(
        config.task_id,
        suite=config.suite,
        seed=config.seed,
        count=config.suite_count,
        family=config.family,
    )
    relaxed = relax_task_clock(task, config.decision_deadline_s, config.step_interval_padding_s)
    return run_agent_episode(relaxed, LLMAgent(backend=backend), config)


# ---------- CLI ---------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OCUDUAgentBench with a real LLM agent.")
    parser.add_argument("--task", required=True, help="Task id to run.")
    parser.add_argument("--suite", default="base", help="Suite to resolve the task from.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--count", type=int, default=None, help="Generated suite count (when applicable).")
    parser.add_argument("--family", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-dir", default=None, help="If set, writes trace + scored summary files here.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")

    parser.add_argument(
        "--provider",
        required=True,
        help="Backend provider: openai, anthropic, openrouter, together, groq, fireworks, "
             "mistral, deepseek, ollama, vllm, llama_cpp, lm_studio, custom.",
    )
    parser.add_argument("--model", required=True, help="Model identifier passed to the provider.")
    parser.add_argument("--base-url", default=None, help="Override the backend base URL (required for custom).")
    parser.add_argument("--api-key-env", default=None, help="Env var name holding the API key.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--request-timeout-s", type=float, default=60.0)

    parser.add_argument("--decision-deadline-s", type=float, default=30.0,
                        help="Per-step LLM time budget. The wrapper converts longer calls into NO_ACTION.")
    parser.add_argument("--step-interval-padding-s", type=float, default=0.5,
                        help="Buffer between decision deadline and step boundary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    import os

    api_key = None
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            print(f"warning: env var {args.api_key_env} is not set", file=sys.stderr)
    backend = build_backend(
        args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=api_key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        request_timeout_s=args.request_timeout_s,
    )
    run_id = args.run_id or f"{args.task}-real-{int(time.time())}"
    config = RealAgentConfig(
        task_id=args.task,
        run_id=run_id,
        seed=args.seed,
        suite=args.suite,
        suite_count=args.count,
        family=args.family,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        decision_deadline_s=args.decision_deadline_s,
        step_interval_padding_s=args.step_interval_padding_s,
    )
    result = run_with_real_agent(config, backend)
    summary = result["summary"]
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"task: {result['task']}")
        print(f"run_id: {result['run_id']}")
        print(f"outcome: {summary.get('outcome')}")
        if summary.get("failure_category"):
            print(f"failure_category: {summary['failure_category']}")
        scored_summary_path = result.get("scored_summary_path")
        if scored_summary_path:
            print(f"scored_summary_path: {scored_summary_path}")
        efficiency = summary.get("efficiency") or {}
        print(
            "tokens: prompt={p}, completion={c}, total={t}".format(
                p=efficiency.get("prompt_tokens_total", 0),
                c=efficiency.get("completion_tokens_total", 0),
                t=efficiency.get("total_tokens", 0),
            )
        )
    return 0 if summary.get("outcome") == "success" else 1


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


if __name__ == "__main__":
    raise SystemExit(main())
