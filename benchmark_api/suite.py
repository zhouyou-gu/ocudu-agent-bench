"""Repeatable v3 suite runner and built-in baseline agents."""

from __future__ import annotations

import json
import itertools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.conformance import run_conformance
from benchmark.benchmark_api.episode import (
    DEFAULT_ATTACH_TIMEOUT,
    DEFAULT_EPISODE_DURATION,
    DEFAULT_LAUNCH_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_WS_PORT,
    TASK_WS_PRB_PING_V1,
    EpisodeOptions,
    EpisodeRuntime,
    episode_paths,
    safe_run_id,
)
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager
from benchmark.benchmark_api.tasks import (
    V3_EPISODE_GATE_CHECKS,
    V4_EPISODE_GATE_CHECKS,
    conformance_checks_for_task,
    episode_stage_for_task,
    suite_stage_for_task,
    supported_task_ids,
)


BUILTIN_AGENTS = {"fixed_prb", "sweep_prb", "invalid_then_fixed"}
V3_SUITE_CONFORMANCE_CHECKS = set(V3_EPISODE_GATE_CHECKS)
V4_SUITE_CONFORMANCE_CHECKS = set(V4_EPISODE_GATE_CHECKS)
SUPPORTED_SUITE_TASKS = supported_task_ids()
_SUITE_COUNTER = itertools.count()


@dataclass(frozen=True)
class SuiteOptions:
    suite_id: str
    task: str = TASK_WS_PRB_PING_V1
    agent: str = "fixed_prb"
    runs: int = 3
    duration: int = DEFAULT_EPISODE_DURATION
    seed: int = 1
    ws_port: int = DEFAULT_WS_PORT
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT
    attach_timeout: int = DEFAULT_ATTACH_TIMEOUT
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT
    skip_conformance: bool = False


def default_suite_id() -> str:
    epoch_ns = time.time_ns()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(epoch_ns // 1_000_000_000))
    return f"suite-{stamp}-{epoch_ns % 1_000_000_000:09d}-{next(_SUITE_COUNTER):04d}"


def suite_run_id(suite_id: str, index: int) -> str:
    if index <= 0:
        raise ValueError("suite run index must be positive")
    return f"{safe_run_id(suite_id)}-r{index:03d}"


def suite_paths(workspace: str, suite_id: str) -> dict[str, str]:
    suite_dir = f"{workspace}/runs/{suite_id}/suite"
    return {
        "suite_dir": suite_dir,
        "summary": f"{suite_dir}/summary.json",
    }


def suite_stage(task: str) -> str:
    return suite_stage_for_task(task)


def episode_stage(task: str) -> str:
    return episode_stage_for_task(task)


class BaselineAgent:
    def __init__(self, name: str, seed: int) -> None:
        if name not in BUILTIN_AGENTS:
            raise ValueError(f"Unknown built-in agent: {name}")
        self.name = name
        self.seed = seed
        self.step = 0
        self.sent_fixed = False

    def next_action(self, observation: dict[str, Any]) -> dict[str, Any] | None:
        _ = observation
        if self.name == "fixed_prb":
            if self.sent_fixed:
                return None
            self.sent_fixed = True
            return fixed_prb_action()
        if self.name == "invalid_then_fixed":
            self.step += 1
            if self.step == 1:
                return {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10}
            if self.step == 2:
                return fixed_prb_action()
            return None
        self.step += 1
        pairs = [(0, 100), (10, 90), (20, 80), (30, 70), (40, 60)]
        offset = self.seed % len(pairs)
        min_ratio, max_ratio = pairs[(offset + self.step - 1) % len(pairs)]
        return {
            "type": "SET_PRB_POLICY_RATIO_WS",
            "plmn": "00101",
            "sst": 1,
            "sd": 0xFFFFFF,
            "min_prb_policy_ratio": min_ratio,
            "max_prb_policy_ratio": max_ratio,
            "dedicated_ratio": 0,
        }


def fixed_prb_action() -> dict[str, Any]:
    return {
        "type": "SET_PRB_POLICY_RATIO_WS",
        "plmn": "00101",
        "sst": 1,
        "sd": 0xFFFFFF,
        "min_prb_policy_ratio": 10,
        "max_prb_policy_ratio": 90,
        "dedicated_ratio": 0,
    }


def aggregate_suite(
    options: SuiteOptions,
    conformance: dict[str, Any],
    run_results: list[dict[str, Any]],
    paths: dict[str, str],
    remote: RemoteManager,
) -> dict[str, Any]:
    summaries = [result.get("summary", {}) for result in run_results]
    scored = [summary for summary in summaries if summary.get("scored")]
    unscored = [summary for summary in summaries if not summary.get("scored")]
    score_keys = sorted({key for summary in summaries for key in summary.get("scores", {})})
    aggregate_scores: dict[str, dict[str, float | int | None]] = {}
    for key in score_keys:
        values = [summary.get("scores", {}).get(key) for summary in scored]
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        aggregate_scores[key] = {
            "mean": (sum(numeric) / len(numeric)) if numeric else None,
            "min": min(numeric) if numeric else None,
            "max": max(numeric) if numeric else None,
            "count": len(numeric),
        }

    cleanup_failures = [
        result.get("run_id")
        for result in run_results
        if result.get("cleanup", {}).get("status") not in {"ok", "skip"}
        or result.get("cleanup", {}).get("leftover_containers")
        or result.get("cleanup", {}).get("ws_port_open")
        or result.get("cleanup", {}).get("ric_port_open")
    ]
    remote_state = conformance.get("remote", {})
    return {
        "status": "ok" if len(scored) == options.runs and not cleanup_failures else "error",
        "stage": suite_stage(options.task),
        "suite_id": options.suite_id,
        "task": options.task,
        "agent": options.agent,
        "seed": options.seed,
        "duration": options.duration,
        "requested_runs": options.runs,
        "run_ids": [result.get("run_id") for result in run_results],
        "scored_runs": len(scored),
        "unscored_runs": len(unscored),
        "cleanup_failure_runs": cleanup_failures,
        "aggregate_scores": aggregate_scores,
        "runs": [
            {
                "run_id": result.get("run_id"),
                "status": result.get("status"),
                "scored": result.get("summary", {}).get("scored"),
                "unscored_reason": result.get("summary", {}).get("unscored_reason"),
                "scores": result.get("summary", {}).get("scores", {}),
                "counts": result.get("summary", {}).get("counts", {}),
                "cleanup": {
                    "status": result.get("cleanup", {}).get("status"),
                    "reason": result.get("cleanup", {}).get("reason"),
                    "leftover_containers": result.get("cleanup", {}).get("leftover_containers", []),
                    "ws_port_open": result.get("cleanup", {}).get("ws_port_open"),
                    "ric_port_open": result.get("cleanup", {}).get("ric_port_open"),
                    "errors": result.get("cleanup", {}).get("errors", []),
                },
                "artifacts": result.get("summary", {}).get("artifacts", {}),
            }
            for result in run_results
        ],
        "conformance": conformance,
        "remote": {
            "ssh": remote.config.ssh_target,
            "workspace": remote.config.workspace,
            "ocudu_root": remote.config.ocudu_root,
            "ocudu_commit": remote_state.get("ocudu_commit", "") or remote_state.get("ocudu_source_commit", ""),
            "ocudu_branch": remote_state.get("ocudu_branch", ""),
        },
        "artifacts": paths,
    }


class SuiteRunner:
    def __init__(self, remote: RemoteManager, repo_root: Path, specs_path: Path) -> None:
        self.remote = remote
        self.repo_root = repo_root
        self.specs_path = specs_path

    def run(self, options: SuiteOptions) -> dict[str, Any]:
        self._validate_options(options)
        conformance = self._run_conformance(options)
        paths = suite_paths(self.remote.config.workspace, options.suite_id)
        if not options.skip_conformance and conformance.get("status") != "pass":
            run_results = self._blocked_runs(options, "required conformance failed")
            summary = aggregate_suite(options, conformance, run_results, paths, self.remote)
            summary["status"] = "error"
            summary["unscored_reason"] = "required conformance failed"
            return self._finalize_suite_summary(paths["summary"], summary)

        run_results = []
        for index in range(1, options.runs + 1):
            run_results.append(self._run_single(options, index))
        summary = aggregate_suite(options, conformance, run_results, paths, self.remote)
        return self._finalize_suite_summary(paths["summary"], summary)

    def _validate_options(self, options: SuiteOptions) -> None:
        safe_run_id(options.suite_id)
        if options.task not in SUPPORTED_SUITE_TASKS:
            raise ValueError(f"Unsupported suite task: {options.task}")
        if options.agent not in BUILTIN_AGENTS:
            raise ValueError(f"Unknown built-in agent: {options.agent}")
        if options.runs <= 0:
            raise ValueError("runs must be positive")
        if options.duration < 0:
            raise ValueError("duration must be non-negative")
        if options.launch_timeout <= 0 or options.attach_timeout <= 0 or options.probe_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if not (0 < options.ws_port <= 65535):
            raise ValueError(f"Invalid WebSocket port: {options.ws_port}")

    def _run_conformance(self, options: SuiteOptions) -> dict[str, Any]:
        if options.skip_conformance:
            return {
                "status": "skip",
                "scored": False,
                "reason": "--skip-conformance was used; suite and runs are unscored",
            }
        checks = conformance_checks_for_task(options.task)
        return run_conformance(
            remote=self.remote,
            repo_root=self.repo_root,
            specs_path=self.specs_path,
            run_id=f"{options.suite_id}-gate",
            checks=set(checks),
            ws_port=options.ws_port,
            launch_timeout=options.launch_timeout,
            probe_timeout=options.probe_timeout,
        )

    def _run_single(self, options: SuiteOptions, index: int) -> dict[str, Any]:
        run_id = suite_run_id(options.suite_id, index)
        runtime = EpisodeRuntime(self.remote, repo_root=self.repo_root)
        episode_options = EpisodeOptions(
            run_id=run_id,
            task=options.task,
            duration=options.duration,
            ws_port=options.ws_port,
            launch_timeout=options.launch_timeout,
            attach_timeout=options.attach_timeout,
            probe_timeout=options.probe_timeout,
        )
        agent = BaselineAgent(options.agent, seed=options.seed + index - 1)
        observations: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        cleanup: dict[str, Any] = {"status": "error", "run_id": run_id, "errors": ["cleanup did not run"]}
        start: dict[str, Any] | None = None
        error_reason: str | None = None

        try:
            start = runtime.start(episode_options)
            if start.get("status") != "ok":
                error_reason = start.get("summary", "episode start failed")
            else:
                deadline = time.monotonic() + max(0, options.duration)
                while True:
                    observation = runtime.observe()
                    observations.append(observation)
                    action = agent.next_action(observation)
                    if action is not None:
                        actions.append(runtime.act(action))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(1.0, remaining))
        except Exception as exc:
            error_reason = str(exc)
        finally:
            cleanup = runtime._cleanup_after_error(run_id)

        final_reason = error_reason or ("conformance skipped" if options.skip_conformance else None)
        summary = runtime._finalize_after_error(
            reason=final_reason,
            cleanup_success=cleanup.get("status") == "ok",
        )
        return {
            "status": "ok" if summary.get("scored") else "error",
            "run_id": run_id,
            "start": start,
            "actions": actions,
            "observations": observations,
            "cleanup": cleanup,
            "summary": summary,
        }

    def _blocked_runs(self, options: SuiteOptions, reason: str) -> list[dict[str, Any]]:
        return [self._blocked_run(options, index, reason) for index in range(1, options.runs + 1)]

    def _blocked_run(self, options: SuiteOptions, index: int, reason: str) -> dict[str, Any]:
        run_id = suite_run_id(options.suite_id, index)
        paths = episode_paths(self.remote.config.workspace, run_id)
        return {
            "status": "blocked",
            "run_id": run_id,
            "start": None,
            "actions": [],
            "observations": [],
            "cleanup": {
                "status": "skip",
                "reason": "episode was not started",
                "leftover_containers": [],
                "ws_port_open": False,
                "ric_port_open": False,
                "errors": [],
            },
            "summary": {
                "status": "ok",
                "stage": episode_stage(options.task),
                "task": options.task,
                "run_id": run_id,
                "scored": False,
                "unscored_reason": reason,
                "scores": {},
                "counts": {},
                "artifacts": paths,
            },
        }

    def _finalize_suite_summary(self, path: str, summary: dict[str, Any]) -> dict[str, Any]:
        try:
            self._write_suite_summary(path, summary)
            return summary
        except RemoteCommandError as exc:
            summary["status"] = "error"
            summary["artifact_write_error"] = str(exc)
            summary["artifact_write"] = {
                "status": "error",
                "path": path,
                "error": str(exc),
            }
            return summary

    def _write_suite_summary(self, path: str, summary: dict[str, Any]) -> None:
        payload = {"path": path, "summary": summary}
        data = self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
path = pathlib.Path(expand_remote_path(payload["path"]))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload["summary"], indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({{"status": "ok", "path": str(path)}}))
"""
        )
        if data.get("status") != "ok":
            raise RemoteCommandError(f"Failed to write suite summary: {data}")

    def _remote_json(self, python_body: str) -> dict[str, Any]:
        command = f"python3 - <<'PY'\n{python_body.rstrip()}\nPY"
        proc = self.remote.exec([command], shell=True)
        if proc.get("status") != "ok":
            raise RemoteCommandError(
                f"Remote command failed with code {proc.get('returncode')}: {proc.get('stderr') or proc.get('stdout')}"
            )
        try:
            return json.loads(proc.get("stdout", "") or "{}")
        except json.JSONDecodeError as exc:
            raise RemoteCommandError(f"Remote command did not return JSON: {proc.get('stdout', '')}") from exc


def suite_exit_code(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "ok" and result.get("scored_runs", 0) > 0 else 1


def run_suite(
    remote: RemoteManager,
    repo_root: Path,
    specs_path: Path,
    suite_id: str | None = None,
    task: str = TASK_WS_PRB_PING_V1,
    agent: str = "fixed_prb",
    runs: int = 3,
    duration: int = DEFAULT_EPISODE_DURATION,
    seed: int = 1,
    ws_port: int = DEFAULT_WS_PORT,
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT,
    attach_timeout: int = DEFAULT_ATTACH_TIMEOUT,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
    skip_conformance: bool = False,
) -> dict[str, Any]:
    options = SuiteOptions(
        suite_id=safe_run_id(suite_id or default_suite_id()),
        task=task,
        agent=agent,
        runs=runs,
        duration=duration,
        seed=seed,
        ws_port=ws_port,
        launch_timeout=launch_timeout,
        attach_timeout=attach_timeout,
        probe_timeout=probe_timeout,
        skip_conformance=skip_conformance,
    )
    return SuiteRunner(remote=remote, repo_root=repo_root, specs_path=specs_path).run(options)
