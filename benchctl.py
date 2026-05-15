#!/usr/bin/env python3
"""CLI for the Skillful RAN benchmark v1 remote harness skeleton."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.benchmark_api.conformance import (
    DEFAULT_LAUNCH_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_WS_PORT,
    conformance_exit_code,
    load_conformance_specs,
    parse_checks,
    run_conformance,
)
from benchmark.benchmark_api.episode import (
    DEFAULT_ATTACH_TIMEOUT as DEFAULT_EPISODE_ATTACH_TIMEOUT,
    DEFAULT_EPISODE_DURATION,
    DEFAULT_LAUNCH_TIMEOUT as DEFAULT_EPISODE_LAUNCH_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT as DEFAULT_EPISODE_PROBE_TIMEOUT,
    DEFAULT_WS_PORT as DEFAULT_EPISODE_WS_PORT,
    TASK_WS_PRB_PING_V1,
    cleanup_episode,
    episode_exit_code,
    run_episode,
)
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager
from benchmark.benchmark_api.suite import BUILTIN_AGENTS, run_suite, suite_exit_code


V3_EPISODE_GATE_CHECKS = {
    "docker_e2e_assets",
    "open5gs_core_health",
    "srsue_zmq_attach",
    "ping_traffic_path",
    "websocket_prb_policy_action",
}


def emit(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    status = data.get("status", "ok")
    print(f"status: {status}")
    for key, value in data.items():
        if key == "status":
            continue
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def remote_manager(args: argparse.Namespace) -> RemoteManager:
    return RemoteManager.from_config(Path(args.config))


def cmd_remote_check(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = manager.check()
    emit(result, args.json)
    return 0 if result.get("status") == "ok" else 1


def cmd_remote_init(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = manager.init_workspace(dry_run=args.dry_run)
    emit(result, args.json)
    return 0 if result.get("status") == "ok" else 1


def cmd_remote_sync(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = manager.sync(source=ROOT / "benchmark", repo_root=ROOT, dry_run=args.dry_run)
    emit(result, args.json)
    return 0 if result.get("status") == "ok" else 1


def cmd_remote_deps(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = manager.prepare_runtime_deps(dry_run=args.dry_run)
    emit(result, args.json)
    return 0 if result.get("status") == "ok" else 1


def cmd_remote_exec(args: argparse.Namespace) -> int:
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        emit({"status": "error", "error": "remote exec requires a command after --"}, args.json)
        return 2

    manager = remote_manager(args)
    result = manager.exec(command, shell=args.shell)
    emit(result, args.json)
    return int(result.get("returncode", 1))


def cmd_conformance_list(args: argparse.Namespace) -> int:
    specs = load_conformance_specs(ROOT / "benchmark" / "conformance" / "tests.json")
    data = {
        "status": "ok",
        "count": len(specs),
        "tests": [spec.to_dict() for spec in specs],
    }
    emit(data, args.json)
    return 0


def cmd_conformance_run(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = run_conformance(
        remote=manager,
        repo_root=ROOT,
        specs_path=ROOT / "benchmark" / "conformance" / "tests.json",
        run_id=args.run_id,
        checks=parse_checks(args.checks),
        ws_port=args.ws_port,
        launch_timeout=args.launch_timeout,
        probe_timeout=args.probe_timeout,
    )
    emit(result, args.json)
    return conformance_exit_code(result)


def cmd_episode_run(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    conformance: dict[str, Any] | None = None
    if not args.skip_conformance:
        conformance = run_conformance(
            remote=manager,
            repo_root=ROOT,
            specs_path=ROOT / "benchmark" / "conformance" / "tests.json",
            run_id=f"{args.run_id}-gate" if args.run_id else None,
            checks=V3_EPISODE_GATE_CHECKS,
            ws_port=args.ws_port,
            launch_timeout=args.launch_timeout,
            probe_timeout=args.probe_timeout,
        )
        if conformance.get("status") != "pass":
            result = {
                "status": "error",
                "stage": "v3_episode",
                "task": args.task,
                "run_id": args.run_id,
                "scored": False,
                "unscored_reason": "required conformance failed",
                "conformance": conformance,
            }
            emit(result, args.json)
            return 1

    result = run_episode(
        remote=manager,
        run_id=args.run_id,
        task=args.task,
        duration=args.duration,
        ws_port=args.ws_port,
        launch_timeout=args.launch_timeout,
        attach_timeout=args.attach_timeout,
        probe_timeout=args.probe_timeout,
        unscored_reason="conformance skipped" if args.skip_conformance else None,
    )
    result["conformance"] = conformance or {
        "status": "skip",
        "scored": False,
        "reason": "--skip-conformance was used; episode result is for debugging only",
    }
    emit(result, args.json)
    return episode_exit_code(result)


def cmd_episode_cleanup(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = cleanup_episode(remote=manager, run_id=args.run_id)
    emit(result, args.json)
    return 0 if result.get("status") == "ok" else 1


def cmd_episode_suite(args: argparse.Namespace) -> int:
    manager = remote_manager(args)
    result = run_suite(
        remote=manager,
        repo_root=ROOT,
        specs_path=ROOT / "benchmark" / "conformance" / "tests.json",
        suite_id=args.suite_id,
        task=args.task,
        agent=args.agent,
        runs=args.runs,
        duration=args.duration,
        seed=args.seed,
        ws_port=args.ws_port,
        launch_timeout=args.launch_timeout,
        attach_timeout=args.attach_timeout,
        probe_timeout=args.probe_timeout,
        skip_conformance=args.skip_conformance,
    )
    emit(result, args.json)
    return suite_exit_code(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skillful RAN benchmark control CLI")
    subparsers = parser.add_subparsers(dest="command_group", required=True)

    remote = subparsers.add_parser("remote", help="Remote workspace and command helpers")
    remote_sub = remote.add_subparsers(dest="remote_command", required=True)

    check = remote_sub.add_parser("check", help="Check remote SSH/tools/OCUDU/workspace state")
    check.add_argument("--config", default=".config", help="Path to local remote config")
    check.add_argument("--json", action="store_true", help="Emit JSON output")
    check.set_defaults(func=cmd_remote_check)

    init = remote_sub.add_parser("init", help="Initialize remote benchmark workspace")
    init.add_argument("--config", default=".config", help="Path to local remote config")
    init.add_argument("--json", action="store_true", help="Emit JSON output")
    init.add_argument("--dry-run", action="store_true", help="Show planned remote changes without executing them")
    init.set_defaults(func=cmd_remote_init)

    sync = remote_sub.add_parser("sync", help="Sync local benchmark helpers to remote workspace")
    sync.add_argument("--config", default=".config", help="Path to local remote config")
    sync.add_argument("--json", action="store_true", help="Emit JSON output")
    sync.add_argument("--dry-run", action="store_true", help="Show planned rsync command without executing it")
    sync.set_defaults(func=cmd_remote_sync)

    deps = remote_sub.add_parser("deps", help="Prepare workspace-local OCUDU runtime dependencies")
    deps.add_argument("--config", default=".config", help="Path to local remote config")
    deps.add_argument("--json", action="store_true", help="Emit JSON output")
    deps.add_argument("--dry-run", action="store_true", help="Show planned dependency preparation without executing it")
    deps.set_defaults(func=cmd_remote_deps)

    exec_parser = remote_sub.add_parser("exec", help="Run a command in the remote benchmark workspace")
    exec_parser.add_argument("--config", default=".config", help="Path to local remote config")
    exec_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    exec_parser.add_argument("--shell", action="store_true", help="Treat the command as remote shell text")
    exec_parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    exec_parser.set_defaults(func=cmd_remote_exec)

    conformance = subparsers.add_parser("conformance", help="Conformance test helpers")
    conformance_sub = conformance.add_subparsers(dest="conformance_command", required=True)

    list_cmd = conformance_sub.add_parser("list", help="List conformance test stubs")
    list_cmd.add_argument("--json", action="store_true", help="Emit JSON output")
    list_cmd.set_defaults(func=cmd_conformance_list)

    run_cmd = conformance_sub.add_parser("run", help="Run executable conformance checks")
    run_cmd.add_argument("--config", default=".config", help="Path to local remote config")
    run_cmd.add_argument("--json", action="store_true", help="Emit JSON output")
    run_cmd.add_argument("--run-id", default=None, help="Conformance run id")
    run_cmd.add_argument("--checks", default=None, help="Comma-separated conformance check ids")
    run_cmd.add_argument("--ws-port", type=int, default=DEFAULT_WS_PORT, help="Remote-control WebSocket port")
    run_cmd.add_argument(
        "--launch-timeout", type=int, default=DEFAULT_LAUNCH_TIMEOUT, help="Seconds to wait for gNB readiness"
    )
    run_cmd.add_argument(
        "--probe-timeout", type=int, default=DEFAULT_PROBE_TIMEOUT, help="Seconds to wait for WebSocket probes"
    )
    run_cmd.set_defaults(func=cmd_conformance_run)

    episode = subparsers.add_parser("episode", help="Scored episode helpers")
    episode_sub = episode.add_subparsers(dest="episode_command", required=True)

    episode_run = episode_sub.add_parser("run", help="Run a benchmark episode")
    episode_run.add_argument("--config", default=".config", help="Path to local remote config")
    episode_run.add_argument("--task", default=TASK_WS_PRB_PING_V1, help="Benchmark task id")
    episode_run.add_argument("--duration", type=int, default=DEFAULT_EPISODE_DURATION, help="Episode duration in seconds")
    episode_run.add_argument("--json", action="store_true", help="Emit JSON output")
    episode_run.add_argument("--run-id", default=None, help="Episode run id")
    episode_run.add_argument("--ws-port", type=int, default=DEFAULT_EPISODE_WS_PORT, help="Remote-control WebSocket port")
    episode_run.add_argument(
        "--launch-timeout",
        type=int,
        default=DEFAULT_EPISODE_LAUNCH_TIMEOUT,
        help="Seconds to wait for Open5GS/gNB readiness",
    )
    episode_run.add_argument(
        "--attach-timeout",
        type=int,
        default=DEFAULT_EPISODE_ATTACH_TIMEOUT,
        help="Seconds to wait for srsUE attach evidence",
    )
    episode_run.add_argument(
        "--probe-timeout",
        type=int,
        default=DEFAULT_EPISODE_PROBE_TIMEOUT,
        help="Seconds to wait for WebSocket probes",
    )
    episode_run.add_argument(
        "--skip-conformance",
        action="store_true",
        help="Run without the required v3 conformance gate and mark the run unscored",
    )
    episode_run.set_defaults(func=cmd_episode_run)

    episode_cleanup = episode_sub.add_parser("cleanup", help="Cleanup a remote episode by run id")
    episode_cleanup.add_argument("--config", default=".config", help="Path to local remote config")
    episode_cleanup.add_argument("--run-id", required=True, help="Episode run id")
    episode_cleanup.add_argument("--json", action="store_true", help="Emit JSON output")
    episode_cleanup.set_defaults(func=cmd_episode_cleanup)

    episode_suite = episode_sub.add_parser("suite", help="Run a repeated benchmark suite")
    episode_suite.add_argument("--config", default=".config", help="Path to local remote config")
    episode_suite.add_argument("--task", default=TASK_WS_PRB_PING_V1, help="Benchmark task id")
    episode_suite.add_argument(
        "--agent", choices=sorted(BUILTIN_AGENTS), default="fixed_prb", help="Built-in baseline agent"
    )
    episode_suite.add_argument("--runs", type=int, default=3, help="Number of suite episodes")
    episode_suite.add_argument("--duration", type=int, default=DEFAULT_EPISODE_DURATION, help="Episode duration in seconds")
    episode_suite.add_argument("--seed", type=int, default=1, help="Deterministic baseline seed")
    episode_suite.add_argument("--suite-id", default=None, help="Suite id; run ids use <suite-id>-rNNN")
    episode_suite.add_argument("--json", action="store_true", help="Emit JSON output")
    episode_suite.add_argument("--ws-port", type=int, default=DEFAULT_EPISODE_WS_PORT, help="Remote-control WebSocket port")
    episode_suite.add_argument(
        "--launch-timeout",
        type=int,
        default=DEFAULT_EPISODE_LAUNCH_TIMEOUT,
        help="Seconds to wait for Open5GS/gNB readiness",
    )
    episode_suite.add_argument(
        "--attach-timeout",
        type=int,
        default=DEFAULT_EPISODE_ATTACH_TIMEOUT,
        help="Seconds to wait for srsUE attach evidence",
    )
    episode_suite.add_argument(
        "--probe-timeout",
        type=int,
        default=DEFAULT_EPISODE_PROBE_TIMEOUT,
        help="Seconds to wait for WebSocket probes",
    )
    episode_suite.add_argument(
        "--skip-conformance",
        action="store_true",
        help="Run without the required v3 conformance gate and mark the suite unscored",
    )
    episode_suite.set_defaults(func=cmd_episode_suite)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RemoteCommandError) as exc:
        as_json = bool(getattr(args, "json", False))
        emit({"status": "error", "error": str(exc)}, as_json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
