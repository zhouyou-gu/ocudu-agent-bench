"""Remote execution infrastructure helpers.

This module intentionally owns transport mechanics only. It does not encode
task logic, stimulus, scoring, or suite behavior.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Any

from benchmark.benchmark_api.config import SiteConfig


class RemoteCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteManager:
    config: SiteConfig

    def check(self, probe: bool = True) -> dict[str, Any]:
        if not self.config.ssh_target:
            return {
                "status": "local",
                "ssh_target": None,
                "workspace": self.config.runtime.workspace,
            }
        command = self._ssh_command(["true"])
        if not probe:
            return {
                "status": "configured",
                "ssh_target": self.config.ssh_target,
                "workspace": self.config.runtime.workspace,
                "command": command,
            }
        result = _run(command)
        return {
            "status": "ok" if result["returncode"] == 0 else "error",
            "ssh_target": self.config.ssh_target,
            "workspace": self.config.runtime.workspace,
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }

    def sync_benchmark(
        self,
        local_benchmark_dir: Path | str,
        dry_run: bool = False,
        delete: bool = True,
    ) -> dict[str, Any]:
        if not self.config.ssh_target:
            raise RemoteCommandError("remote.ssh is required for remote sync")
        local_dir = Path(local_benchmark_dir).resolve()
        if not local_dir.exists() or not local_dir.is_dir():
            raise RemoteCommandError(f"local benchmark directory does not exist: {local_dir}")
        remote_workspace = self.config.runtime.workspace.rstrip("/")
        remote_dir = f"{remote_workspace}/synced/benchmark"
        if not dry_run:
            mkdir = self._ssh_command(["mkdir", "-p", remote_dir])
            mkdir_result = _run(mkdir)
            if mkdir_result["returncode"] != 0:
                return {
                    "status": "error",
                    "stage": "mkdir",
                    "remote_dir": remote_dir,
                    **mkdir_result,
                }
        command = [
            "rsync",
            "-az",
            "--itemize-changes",
            "--exclude",
            "__pycache__/",
            "--exclude",
            "*.pyc",
            "--exclude",
            ".DS_Store",
        ]
        if dry_run:
            command.append("--dry-run")
        if delete:
            command.append("--delete")
        command.extend(["-e", self._rsync_ssh(), f"{local_dir}/", f"{self.config.ssh_target}:{remote_dir}/"])
        result = _run(command)
        itemized = [line for line in result["stdout"].splitlines() if line.strip()]
        return {
            "status": "ok" if result["returncode"] == 0 else "error",
            "dry_run": dry_run,
            "delete": delete,
            "local_dir": str(local_dir),
            "remote_dir": remote_dir,
            "itemized_change_count": len(itemized),
            "itemized_changes": itemized,
            **result,
        }

    def exec(self, command: list[str], dry_run: bool = True) -> dict[str, Any]:
        if dry_run:
            return {"status": "dry_run", "command": list(command)}
        result = _run(command)
        return {"status": "ok" if result["returncode"] == 0 else "error", **result}

    def _ssh_command(self, remote_command: list[str]) -> list[str]:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        if self.config.ssh_key:
            command.extend(["-i", str(Path(self.config.ssh_key).expanduser())])
        command.append(str(self.config.ssh_target))
        command.append(" ".join(remote_command))
        return command

    def _rsync_ssh(self) -> str:
        parts = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        if self.config.ssh_key:
            parts.extend(["-i", str(Path(self.config.ssh_key).expanduser())])
        return " ".join(shlex.quote(part) for part in parts)


def _run(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, check=False, text=True, capture_output=True)
    return {
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
