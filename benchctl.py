#!/usr/bin/env python3
"""CLI for the design-aligned OCUDUAgentBench harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.benchmark_api.controller import ControllerConfig, run_repeated
from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.controller import BaselineController
from benchmark.benchmark_api.config import parse_config
from benchmark.benchmark_api.remote import RemoteManager
from benchmark.benchmark_api.task_catalog import load_tasks_for_suite
from benchmark.benchmark_api.task_definition import task_summary


def emit(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    print(f"status: {data.get('status', 'ok')}")
    for key, value in data.items():
        if key != "status":
            print(f"{key}: {json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value}")


def cmd_tasks_list(args: argparse.Namespace) -> int:
    tasks = load_tasks_for_suite(suite=args.suite, seed=args.seed, count=args.count, family=args.family)
    emit({"status": "ok", "tasks": [task_summary(task) for task in tasks.values()]}, args.json)
    return 0


def cmd_episode_run(args: argparse.Namespace) -> int:
    agent = BaselineController(args.controller)
    result = run_episode(
        EpisodeConfig(
            task_id=args.task,
            run_id=args.run_id,
            seed=args.seed,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            suite=args.suite,
            suite_count=args.count,
            family=args.family,
        ),
        agent=agent,
    )
    emit({"status": "ok", **result}, args.json)
    return 0 if result["summary"].get("scored") else 1


def cmd_run(args: argparse.Namespace) -> int:
    result = run_repeated(
        ControllerConfig(
            task_id=args.task,
            controller_id=args.controller,
            runs=args.runs,
            seed=args.seed,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            suite=args.suite,
            suite_count=args.count,
            family=args.family,
        )
    )
    emit({"status": "ok", **result}, args.json)
    return 0


def cmd_remote_check(args: argparse.Namespace) -> int:
    manager = RemoteManager(parse_config(args.config))
    result = manager.check(probe=not args.no_probe)
    emit(result, args.json)
    return 0 if result["status"] in {"ok", "configured", "local"} else 1


def cmd_remote_sync(args: argparse.Namespace) -> int:
    manager = RemoteManager(parse_config(args.config))
    result = manager.sync_benchmark(
        local_benchmark_dir=ROOT / "benchmark",
        dry_run=args.dry_run,
        delete=not args.no_delete,
    )
    emit(result, args.json)
    return 0 if result["status"] == "ok" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OCUDUAgentBench")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tasks = subparsers.add_parser("tasks", help="Task catalog commands")
    task_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    task_list = task_sub.add_parser("list", help="List task contracts")
    task_list.add_argument(
        "--suite",
        default="base",
        help="Task suite: base, regression, compound, all_checked_in, generated, standard, diagnostic, or stress",
    )
    task_list.add_argument("--family", default=None, help="Optional task family filter")
    task_list.add_argument("--seed", type=int, default=1, help="Generated-suite seed")
    task_list.add_argument("--count", type=int, default=None, help="Generated-suite task count")
    task_list.set_defaults(func=cmd_tasks_list)

    episode = subparsers.add_parser("episode", help="Single episode commands")
    episode_sub = episode.add_subparsers(dest="episode_command", required=True)
    episode_run = episode_sub.add_parser("run", help="Run one episode")
    episode_run.add_argument("--task", required=True)
    episode_run.add_argument("--run-id", default="episode-001")
    episode_run.add_argument("--seed", type=int, default=1)
    episode_run.add_argument("--controller", default="auto")
    episode_run.add_argument("--output-dir", default=None)
    episode_run.add_argument("--suite", default="base", help="Task suite for resolving --task")
    episode_run.add_argument("--family", default=None, help="Optional task family filter")
    episode_run.add_argument("--count", type=int, default=None, help="Generated-suite task count")
    episode_run.set_defaults(func=cmd_episode_run)

    run = subparsers.add_parser("run", help="Run repeated benchmark episodes through controller.py")
    run.add_argument("--task", default=None)
    run.add_argument("--controller", default="auto")
    run.add_argument("--runs", type=int, default=1)
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--output-dir", default=None)
    run.add_argument("--suite", default="base", help="Task suite; omit --task to run every task in the suite")
    run.add_argument("--family", default=None, help="Optional task family filter")
    run.add_argument("--count", type=int, default=None, help="Generated-suite task count")
    run.set_defaults(func=cmd_run)

    remote = subparsers.add_parser("remote", help="Remote workstation workspace commands")
    remote_sub = remote.add_subparsers(dest="remote_command", required=True)
    remote_check = remote_sub.add_parser("check", help="Check remote workstation config and SSH reachability")
    remote_check.add_argument("--config", default=".config")
    remote_check.add_argument("--no-probe", action="store_true", help="Only parse config; do not open SSH")
    remote_check.set_defaults(func=cmd_remote_check)
    remote_sync = remote_sub.add_parser("sync", help="Sync benchmark/ to remote workspace synced/benchmark")
    remote_sync.add_argument("--config", default=".config")
    remote_sync.add_argument("--dry-run", action="store_true", help="Show rsync changes without writing remote files")
    remote_sync.add_argument("--no-delete", action="store_true", help="Do not delete stale files in remote synced/benchmark")
    remote_sync.set_defaults(func=cmd_remote_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        emit({"status": "error", "error": str(exc)}, bool(getattr(args, "json", False)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
