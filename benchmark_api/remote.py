"""Remote SSH and rsync helpers for the benchmark v1 skeleton."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.config import RemoteConfig, parse_config

RUNTIME_DEP_PACKAGES = [
    "libzmq5",
    "libmbedcrypto7t64",
    "libsctp1",
    "libyaml-cpp0.8",
    "libsodium23",
    "libpgm-5.3-0t64",
    "libnorm1t64",
]


class RemoteCommandError(RuntimeError):
    """Raised when local remote-command construction or execution fails."""


class RemoteManager:
    def __init__(self, config: RemoteConfig) -> None:
        self.config = config

    @classmethod
    def from_config(cls, path: Path) -> "RemoteManager":
        return cls(parse_config(path))

    def ssh_argv(self, remote_command: str) -> list[str]:
        return [
            "ssh",
            "-i",
            self.config.ssh_key,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout}",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            self.config.ssh_target,
            remote_command,
        ]

    def rsync_argv(self, source: Path, dry_run: bool = False) -> list[str]:
        ssh = " ".join(shlex.quote(part) for part in self.ssh_argv("true")[:-2])
        destination = f"{self.config.ssh_target}:{self.config.workspace}/synced/benchmark/"
        argv = [
            "rsync",
            "-az",
            "--delete",
            "--exclude",
            "__pycache__/",
            "--exclude",
            "*.pyc",
        ]
        if dry_run:
            argv.append("--dry-run")
        argv.extend(["-e", ssh, f"{source}/", destination])
        return argv

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, check=False, text=True, capture_output=True)

    def _remote_shell(self, script: str) -> str:
        env = {
            "OCUDU_ROOT": self.config.ocudu_root,
            "BENCHMARK_WORKSPACE": self.config.workspace,
        }
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
        return f"{exports} /bin/sh -lc {shlex.quote(script)}"

    def _parse_key_values(self, stdout: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    def _git_tracked_files(self, repo_root: Path, source: Path) -> list[Path]:
        repo_root = repo_root.resolve()
        source = source.resolve()
        try:
            source_rel = source.relative_to(repo_root)
        except ValueError as exc:
            raise RemoteCommandError(f"Sync source is outside repo root: {source}") from exc

        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z", "--", source_rel.as_posix()],
            check=False,
            text=False,
            capture_output=True,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RemoteCommandError(f"Unable to list tracked benchmark files: {stderr}")
        files = [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]
        return sorted(files, key=lambda path: path.as_posix())

    def _bootstrap_manifest_files(self, repo_root: Path, source: Path) -> list[Path]:
        """Fallback for an uncommitted v1 skeleton under review.

        Normal sync is driven by git-tracked files. This exact manifest keeps
        initial development usable before the new package has been added to the
        index, without copying arbitrary scratch files from benchmark/.
        """

        repo_root = repo_root.resolve()
        source_rel = source.resolve().relative_to(repo_root)
        expected = [
            "__init__.py",
            "benchctl.py",
            "benchmark_api/__init__.py",
            "benchmark_api/config.py",
            "benchmark_api/conformance.py",
            "benchmark_api/env.py",
            "benchmark_api/episode.py",
            "benchmark_api/remote.py",
            "benchmark_api/suite.py",
            "benchmark_api/websocket_client.py",
            "conformance/tests.json",
            "schemas/actions.schema.json",
            "schemas/observations.schema.json",
        ]
        files = [source_rel / item for item in expected if (repo_root / source_rel / item).is_file()]
        return sorted(files, key=lambda path: path.as_posix())

    def sync_manifest(self, repo_root: Path, source: Path) -> tuple[list[Path], str]:
        tracked = self._git_tracked_files(repo_root=repo_root, source=source)
        bootstrap = self._bootstrap_manifest_files(repo_root=repo_root, source=source)
        files = sorted(set(tracked) | set(bootstrap), key=lambda path: path.as_posix())
        if files and tracked:
            source_policy = "git_tracked" if files == tracked else "git_tracked_plus_bootstrap_manifest"
            return files, source_policy
        if files:
            return files, "bootstrap_manifest"
        raise RemoteCommandError(f"No tracked benchmark files found under {source}")

    def check(self) -> dict[str, Any]:
        script = r"""
echo host=$(hostname 2>/dev/null || true)
echo home=${HOME:-}
        for tool in python3 git rsync ss ldd; do
  path=$(command -v "$tool" 2>/dev/null || true)
  echo tool_${tool}=$path
done
if [ -d "$OCUDU_ROOT" ]; then
  echo ocudu_exists=1
  if git -C "$OCUDU_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo ocudu_is_git=1
    echo ocudu_commit=$(git -C "$OCUDU_ROOT" rev-parse --short=12 HEAD 2>/dev/null || true)
    echo ocudu_branch=$(git -C "$OCUDU_ROOT" branch --show-current 2>/dev/null || true)
    echo ocudu_origin=$(git -C "$OCUDU_ROOT" remote get-url origin 2>/dev/null || true)
  else
    echo ocudu_is_git=0
  fi
else
  echo ocudu_exists=0
fi
if [ -e "$BENCHMARK_WORKSPACE" ]; then
  echo workspace_exists=1
  if [ -d "$BENCHMARK_WORKSPACE" ]; then
    echo workspace_is_dir=1
    echo workspace_entries=$(find "$BENCHMARK_WORKSPACE" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
  else
    echo workspace_is_dir=0
  fi
else
  echo workspace_exists=0
fi
"""
        proc = self._run(self.ssh_argv(self._remote_shell(script)))
        data = self._parse_key_values(proc.stdout)
        tools = {
            "python3": data.get("tool_python3", ""),
            "git": data.get("tool_git", ""),
            "rsync": data.get("tool_rsync", ""),
            "ss": data.get("tool_ss", ""),
            "ldd": data.get("tool_ldd", ""),
        }
        status = "ok" if proc.returncode == 0 else "error"
        return {
            "status": status,
            "returncode": proc.returncode,
            "ssh": self.config.ssh_target,
            "workspace": self.config.workspace,
            "ocudu_root": self.config.ocudu_root,
            "remote": {
                "host": data.get("host", ""),
                "home": data.get("home", ""),
                "tools": tools,
                "ocudu_exists": data.get("ocudu_exists") == "1",
                "ocudu_is_git": data.get("ocudu_is_git") == "1",
                "ocudu_commit": data.get("ocudu_commit", ""),
                "ocudu_branch": data.get("ocudu_branch", ""),
                "ocudu_origin": data.get("ocudu_origin", ""),
                "workspace_exists": data.get("workspace_exists") == "1",
                "workspace_is_dir": data.get("workspace_is_dir") == "1",
                "workspace_entries": int(data.get("workspace_entries", "0") or 0),
            },
            "stderr": proc.stderr.strip(),
        }

    def init_workspace(self, dry_run: bool = False) -> dict[str, Any]:
        metadata = {
            "workspace_kind": "skillful-ran-benchmark-workspace",
            "ocudu_root": self.config.ocudu_root,
            "workspace": self.config.workspace,
        }
        script = f"""
set -eu
if [ -e "$BENCHMARK_WORKSPACE" ] && [ ! -d "$BENCHMARK_WORKSPACE" ]; then
  echo "target exists and is not a directory" >&2
  exit 2
fi
if [ -d "$BENCHMARK_WORKSPACE" ] && [ "$(find "$BENCHMARK_WORKSPACE" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" != "0" ] && [ ! -f "$BENCHMARK_WORKSPACE/metadata.json" ]; then
  echo "target exists, is non-empty, and has no benchmark metadata" >&2
  exit 3
fi
mkdir -p "$BENCHMARK_WORKSPACE/synced" "$BENCHMARK_WORKSPACE/configs" "$BENCHMARK_WORKSPACE/runs" "$BENCHMARK_WORKSPACE/tmp"
cat > "$BENCHMARK_WORKSPACE/metadata.json" <<'JSON'
{json.dumps(metadata, indent=2, sort_keys=True)}
JSON
echo workspace=$BENCHMARK_WORKSPACE
echo metadata=$BENCHMARK_WORKSPACE/metadata.json
"""
        remote_command = self._remote_shell(script)
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "ssh": self.config.ssh_target,
                "workspace": self.config.workspace,
                "planned_remote_command": remote_command,
            }
        proc = self._run(self.ssh_argv(remote_command))
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "workspace": self.config.workspace,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def sync(self, source: Path, repo_root: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
        if not source.exists():
            raise RemoteCommandError(f"Sync source does not exist: {source}")
        repo_root = (repo_root or source.parent).resolve()
        source = source.resolve()
        tracked_files, source_policy = self.sync_manifest(repo_root=repo_root, source=source)
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "source_policy": source_policy,
                "tracked_file_count": len(tracked_files),
                "tracked_files": [path.as_posix() for path in tracked_files],
                "planned_source": "<temporary tracked-file staging>/benchmark/",
                "argv": self.rsync_argv(Path("<temporary tracked-file staging>") / source.name, dry_run=True),
                "destination": f"{self.config.workspace}/synced/benchmark/",
            }

        with tempfile.TemporaryDirectory(prefix="skillful-ran-benchmark-sync-") as tmpdir:
            stage_root = Path(tmpdir)
            for rel_path in tracked_files:
                src = repo_root / rel_path
                dst = stage_root / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            staged_source = stage_root / source.relative_to(repo_root)
            argv = self.rsync_argv(staged_source, dry_run=False)
            proc = self._run(argv)
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "source_policy": source_policy,
            "tracked_file_count": len(tracked_files),
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def prepare_runtime_deps(self, dry_run: bool = False) -> dict[str, Any]:
        packages = RUNTIME_DEP_PACKAGES
        package_args = " ".join(shlex.quote(package) for package in packages)
        metadata = {
            "runtime_deps_kind": "workspace-local-apt-extract",
            "packages": packages,
            "workspace": self.config.workspace,
        }
        script = f"""
set -eu
LIB_ROOT="$BENCHMARK_WORKSPACE/runtime-libs"
mkdir -p "$LIB_ROOT/downloads" "$LIB_ROOT/root"
cd "$LIB_ROOT/downloads"
apt-get download {package_args}
for deb in *.deb; do
  dpkg-deb -x "$deb" "$LIB_ROOT/root"
done
cat > "$LIB_ROOT/metadata.json" <<'JSON'
{json.dumps(metadata, indent=2, sort_keys=True)}
JSON
echo runtime_root=$LIB_ROOT/root
echo metadata=$LIB_ROOT/metadata.json
echo package_count={len(packages)}
echo library_count=$(find "$LIB_ROOT/root" \\( -type f -o -type l \\) | grep -E '/lib[^/]*\\.so(\\.|$)' | wc -l | tr -d ' ')
"""
        remote_command = self._remote_shell(script)
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "packages": packages,
                "runtime_root": f"{self.config.workspace}/runtime-libs/root",
                "planned_remote_command": remote_command,
            }
        init_result = self.init_workspace()
        if init_result.get("status") != "ok":
            return {
                "status": "error",
                "returncode": init_result.get("returncode", 1),
                "packages": packages,
                "runtime_root": f"{self.config.workspace}/runtime-libs/root",
                "init": init_result,
                "stdout": "",
                "stderr": "remote workspace init failed",
            }
        proc = self._run(self.ssh_argv(remote_command))
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "packages": packages,
            "runtime_root": f"{self.config.workspace}/runtime-libs/root",
            "init": init_result,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def create_run_metadata(self, run_id: str, metadata: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in run_id):
            raise RemoteCommandError(f"Invalid run_id for remote metadata path: {run_id!r}")
        metadata_text = json.dumps(metadata, indent=2, sort_keys=True)
        run_dir = f"$BENCHMARK_WORKSPACE/runs/{run_id}"
        script = f"""
set -eu
mkdir -p "{run_dir}"
cat > "{run_dir}/metadata.json" <<'JSON'
{metadata_text}
JSON
echo run_dir={run_dir}
echo metadata={run_dir}/metadata.json
"""
        remote_command = self._remote_shell(script)
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "run_id": run_id,
                "planned_remote_command": remote_command,
            }
        proc = self._run(self.ssh_argv(remote_command))
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "run_id": run_id,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def exec(self, command: list[str], shell: bool = False) -> dict[str, Any]:
        if not command:
            raise RemoteCommandError("remote exec requires a command")
        if shell:
            command_text = " ".join(command)
        elif len(command) == 1:
            command_text = command[0]
        else:
            command_text = " ".join(shlex.quote(part) for part in command)
        script = (
            f"cd {shlex.quote(self.config.workspace)} && "
            f"export OCUDU_ROOT={shlex.quote(self.config.ocudu_root)} && "
            f"export BENCHMARK_WORKSPACE={shlex.quote(self.config.workspace)} && "
            f"{command_text}"
        )
        proc = self._run(self.ssh_argv(script))
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
