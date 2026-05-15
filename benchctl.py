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

from benchmark.benchmark_api.conformance import load_conformance_specs
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager


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
