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
from benchmark.benchmark_api.provision import (
    build_provision_script,
    expand_provision_stages,
    provision_assets_dir,
    validate_provision_config,
)
from benchmark.benchmark_api.ric import (
    DEFAULT_FLEXRIC_OCUDU_REF,
    DEFAULT_FLEXRIC_OCUDU_REPO,
    FLEXRIC_CONTEXT_PREP_SCRIPT,
    FLEXRIC_DOCKERFILE_REL,
    FLEXRIC_IMAGE,
    FLEXRIC_OCUDU_ASN_HEADER,
    FLEXRIC_OCUDU_ASN_SOURCE,
    FLEXRIC_SOURCE_DIRNAME,
    flexric_manifest,
    flexric_workspace_paths,
)

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

    def rsync_path_argv(self, source: Path, remote_destination: str, dry_run: bool = False) -> list[str]:
        ssh = " ".join(shlex.quote(part) for part in self.ssh_argv("true")[:-2])
        argv = [
            "rsync",
            "-az",
            "--delete",
        ]
        if dry_run:
            argv.append("--dry-run")
        argv.extend(["-e", ssh, f"{source}/", f"{self.config.ssh_target}:{remote_destination}"])
        return argv

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, check=False, text=True, capture_output=True)

    def _remote_shell(self, script: str) -> str:
        env = {
            "OCUDU_ROOT_RAW": self.config.ocudu_root,
            "BENCHMARK_WORKSPACE_RAW": self.config.workspace,
            "OPEN5GS_COMPOSE_RAW": self.config.runtime.open5gs_compose,
            "E2E_CONFIG_DIR_RAW": self.config.runtime.e2e_config_dir,
            "RIC_PROVIDER": self.config.ric_provider,
        }
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
        prelude = """
expand_remote_path() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}
OCUDU_ROOT="$(expand_remote_path "$OCUDU_ROOT_RAW")"
BENCHMARK_WORKSPACE="$(expand_remote_path "$BENCHMARK_WORKSPACE_RAW")"
OPEN5GS_COMPOSE="$(expand_remote_path "$OPEN5GS_COMPOSE_RAW")"
E2E_CONFIG_DIR="$(expand_remote_path "$E2E_CONFIG_DIR_RAW")"
export OCUDU_ROOT BENCHMARK_WORKSPACE OPEN5GS_COMPOSE E2E_CONFIG_DIR RIC_PROVIDER
"""
        return f"{exports} /bin/sh -lc {shlex.quote(prelude + script)}"

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
        files = [path for path in files if (repo_root / path).is_file()]
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
            "README.md",
            "API_REFERENCE.md",
            "__init__.py",
            "benchctl.py",
            "benchmark_api/__init__.py",
            "benchmark_api/config.py",
            "benchmark_api/conformance.py",
            "benchmark_api/env.py",
            "benchmark_api/episode.py",
            "benchmark_api/provision.py",
            "benchmark_api/remote.py",
            "benchmark_api/ric.py",
            "benchmark_api/suite.py",
            "benchmark_api/tasks.py",
            "benchmark_api/websocket_client.py",
            "agents/README.md",
            "conformance/tests.json",
            "schemas/actions.schema.json",
            "schemas/observations.schema.json",
            "schemas/task.schema.json",
            "tasks/README.md",
            "tasks/TASK_AUTHORING_GUIDE.md",
            "tasks/ws_prb_ping_v1/README.md",
            "tasks/ws_prb_ping_v1/task.json",
            "tasks/e2_kpm_prb_ping_v1/README.md",
            "tasks/e2_kpm_prb_ping_v1/task.json",
            "tasks/ws_prb_noop_guard_v1/README.md",
            "tasks/ws_prb_noop_guard_v1/task.json",
            "tasks/ws_prb_error_repair_v1/README.md",
            "tasks/ws_prb_error_repair_v1/task.json",
            "tasks/ws_prb_action_budget_v1/README.md",
            "tasks/ws_prb_action_budget_v1/task.json",
            "tasks/e2_kpm_json_consistency_v1/README.md",
            "tasks/e2_kpm_json_consistency_v1/task.json",
            "tasks/metrics_staleness_noop_v1/README.md",
            "tasks/metrics_staleness_noop_v1/task.json",
            "tasks/e2_ccc_prb_policy_ping_v1/README.md",
            "tasks/e2_ccc_prb_policy_ping_v1/task.json",
            "tasks/e2_rc_du_prb_policy_ping_v1/README.md",
            "tasks/e2_rc_du_prb_policy_ping_v1/task.json",
            "tasks/e2_control_api_consistency_v1/README.md",
            "tasks/e2_control_api_consistency_v1/task.json",
            "tasks/ws_ssb_power_guard_v1/README.md",
            "tasks/ws_ssb_power_guard_v1/task.json",
            "tasks/ws_ssb_power_repair_v1/README.md",
            "tasks/ws_ssb_power_repair_v1/task.json",
        ]
        files = [source_rel / item for item in expected if (repo_root / source_rel / item).is_file()]
        provision_root = repo_root / source_rel / "provision"
        if provision_root.is_dir():
            for item in provision_root.rglob("*"):
                if item.is_file():
                    files.append(item.relative_to(repo_root))
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
inside_workspace() {
  case "$1" in
    "$BENCHMARK_WORKSPACE"|"$BENCHMARK_WORKSPACE"/*) printf '1' ;;
    *) printf '0' ;;
  esac
}
        for tool in python3 git rsync ss ldd docker; do
  path=$(command -v "$tool" 2>/dev/null || true)
  echo tool_${tool}=$path
done
if docker compose version >/dev/null 2>&1; then
  echo tool_docker_compose=1
else
  echo tool_docker_compose=0
fi
echo ocudu_inside_workspace=$(inside_workspace "$OCUDU_ROOT")
echo open5gs_compose_inside_workspace=$(inside_workspace "$OPEN5GS_COMPOSE")
echo e2e_config_dir_inside_workspace=$(inside_workspace "$E2E_CONFIG_DIR")
echo open5gs_compose_exists=$([ -f "$OPEN5GS_COMPOSE" ] && echo 1 || echo 0)
echo e2e_config_dir_exists=$([ -d "$E2E_CONFIG_DIR" ] && echo 1 || echo 0)
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
OCUDU_SRC="$BENCHMARK_WORKSPACE/sources/ocudu"
SRSRAN_4G_SRC="$BENCHMARK_WORKSPACE/sources/srsran-4g"
if git -C "$OCUDU_SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo ocudu_source_is_git=1
  echo ocudu_source_commit=$(git -C "$OCUDU_SRC" rev-parse --short=12 HEAD 2>/dev/null || true)
  echo ocudu_source_origin=$(git -C "$OCUDU_SRC" remote get-url origin 2>/dev/null || true)
else
  echo ocudu_source_is_git=0
fi
if git -C "$SRSRAN_4G_SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo srsran_4g_is_git=1
  echo srsran_4g_commit=$(git -C "$SRSRAN_4G_SRC" rev-parse --short=12 HEAD 2>/dev/null || true)
  echo srsran_4g_origin=$(git -C "$SRSRAN_4G_SRC" remote get-url origin 2>/dev/null || true)
else
  echo srsran_4g_is_git=0
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
            "docker": data.get("tool_docker", ""),
        }
        status = "ok" if proc.returncode == 0 else "error"
        return {
            "status": status,
            "returncode": proc.returncode,
            "ssh": self.config.ssh_target,
            "workspace": self.config.workspace,
            "ocudu_root": self.config.ocudu_root,
            "runtime": {
                "open5gs_compose": self.config.runtime.open5gs_compose,
                "e2e_config_dir": self.config.runtime.e2e_config_dir,
                "open5gs_image": self.config.runtime.open5gs_image,
                "gnb_image": self.config.runtime.gnb_image,
                "ue_image": self.config.runtime.ue_image,
            },
            "workspace_owned_runtime": {
                "ocudu_root": data.get("ocudu_inside_workspace") == "1",
                "open5gs_compose": data.get("open5gs_compose_inside_workspace") == "1",
                "e2e_config_dir": data.get("e2e_config_dir_inside_workspace") == "1",
            },
            "ric_provider": self.config.ric_provider,
            "remote": {
                "host": data.get("host", ""),
                "home": data.get("home", ""),
                "tools": tools,
                "docker": data.get("tool_docker", ""),
                "docker_compose": data.get("tool_docker_compose") == "1",
                "ocudu_exists": data.get("ocudu_exists") == "1",
                "ocudu_is_git": data.get("ocudu_is_git") == "1",
                "ocudu_commit": data.get("ocudu_commit", ""),
                "ocudu_branch": data.get("ocudu_branch", ""),
                "ocudu_origin": data.get("ocudu_origin", ""),
                "ocudu_source_is_git": data.get("ocudu_source_is_git") == "1",
                "ocudu_source_commit": data.get("ocudu_source_commit", ""),
                "ocudu_source_origin": data.get("ocudu_source_origin", ""),
                "srsran_4g_is_git": data.get("srsran_4g_is_git") == "1",
                "srsran_4g_commit": data.get("srsran_4g_commit", ""),
                "srsran_4g_origin": data.get("srsran_4g_origin", ""),
                "workspace_exists": data.get("workspace_exists") == "1",
                "workspace_is_dir": data.get("workspace_is_dir") == "1",
                "workspace_entries": int(data.get("workspace_entries", "0") or 0),
                "open5gs_compose_exists": data.get("open5gs_compose_exists") == "1",
                "e2e_config_dir_exists": data.get("e2e_config_dir_exists") == "1",
            },
            "stderr": proc.stderr.strip(),
        }

    def init_workspace(self, dry_run: bool = False) -> dict[str, Any]:
        metadata = {
            "workspace_kind": "skillful-ran-benchmark-workspace",
            "ocudu_root": self.config.ocudu_root,
            "workspace": self.config.workspace,
            "runtime": {
                "open5gs_compose": self.config.runtime.open5gs_compose,
                "e2e_config_dir": self.config.runtime.e2e_config_dir,
                "open5gs_image": self.config.runtime.open5gs_image,
                "gnb_image": self.config.runtime.gnb_image,
                "ue_image": self.config.runtime.ue_image,
            },
            "provision": {
                "mode": self.config.provision.mode,
                "source_pins": {
                    "ocudu_ref": self.config.sources.ocudu_ref,
                    "srsran_4g_ref": self.config.sources.srsran_4g_ref,
                    "open5gs_ref": self.config.sources.open5gs_ref,
                    "flexric_ocudu_ref": self.config.sources.flexric_ocudu_ref,
                },
            },
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

    def reset_workspace(self, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        local_errors = self._local_reset_workspace_errors()
        script = r"""
set -eu
python3 - <<'PY'
import json
import pathlib
import shutil
import shlex
import subprocess

workspace = pathlib.Path(__import__("os").environ["BENCHMARK_WORKSPACE"]).resolve()
home = pathlib.Path.home().resolve()
failures = []
if str(workspace) in {"", "/", "."}:
    failures.append("workspace path is empty or root")
if workspace == home:
    failures.append("workspace path must not be the remote home directory")
try:
    workspace.relative_to(home)
except ValueError:
    failures.append("workspace path must be inside the remote home directory")
if len(workspace.parts) < len(home.parts) + 1:
    failures.append("workspace path must name a child directory under remote home")
if failures:
    print(json.dumps({"status": "error", "errors": failures, "workspace": str(workspace), "home": str(home)}))
    raise SystemExit(2)
existed = workspace.exists()
if existed:
    try:
        shutil.rmtree(workspace)
    except PermissionError:
        proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "-v",
                f"{workspace.parent}:/host",
                "ubuntu:24.04",
                "bash",
                "-lc",
                "rm -rf -- /host/" + shlex.quote(workspace.name),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "errors": ["workspace deletion failed", proc.stderr.strip()],
                        "workspace": str(workspace),
                        "home": str(home),
                    }
                )
            )
            raise SystemExit(proc.returncode)
workspace.mkdir(parents=True, exist_ok=True)
print(json.dumps({"status": "ok", "workspace": str(workspace), "home": str(home), "deleted": existed, "recreated": True}))
PY
"""
        remote_command = self._remote_shell(script)
        if not force:
            return {
                "status": "error",
                "error": "remote reset-workspace requires --force",
                "workspace": self.config.workspace,
                "dry_run": dry_run,
            }
        if local_errors:
            return {
                "status": "error",
                "error": "unsafe remote workspace path",
                "errors": local_errors,
                "workspace": self.config.workspace,
                "dry_run": dry_run,
            }
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "force": True,
                "workspace": self.config.workspace,
                "planned_remote_command": remote_command,
            }
        proc = self._run(self.ssh_argv(remote_command))
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            payload = {}
        return {
            "status": "ok" if proc.returncode == 0 and payload.get("status") == "ok" else "error",
            "returncode": proc.returncode,
            "force": True,
            "workspace": payload.get("workspace", self.config.workspace),
            "deleted": payload.get("deleted", False),
            "recreated": payload.get("recreated", False),
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "errors": payload.get("errors", []),
        }

    def _local_reset_workspace_errors(self) -> list[str]:
        workspace = self.config.workspace.strip()
        errors = []
        if workspace in {"", "/", ".", "~"}:
            errors.append("workspace path is empty, root, current directory, or remote home")
        if "/../" in workspace or workspace.endswith("/..") or workspace == "..":
            errors.append("workspace path must not contain parent-directory traversal")
        if workspace.startswith("/"):
            user = self.config.ssh_target.rsplit("@", 1)[0] if "@" in self.config.ssh_target else ""
            if user and not workspace.startswith(f"/home/{user}/"):
                errors.append("absolute workspace path must be under the configured SSH user's home")
        return errors

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

    def prepare_ric(self, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
        paths = flexric_workspace_paths(self.config.workspace)
        flexric_repo = self.config.sources.flexric_ocudu_repo or DEFAULT_FLEXRIC_OCUDU_REPO
        flexric_ref = self.config.sources.flexric_ocudu_ref or DEFAULT_FLEXRIC_OCUDU_REF
        manifest = flexric_manifest(
            repo=flexric_repo,
            ref=flexric_ref,
            ocudu_repo=self.config.sources.ocudu_repo,
            ocudu_ref=self.config.sources.ocudu_ref,
        )
        ocudu_asn_sources = [
            FLEXRIC_OCUDU_ASN_HEADER,
            FLEXRIC_OCUDU_ASN_SOURCE,
            "lib/asn1/e2sm/e2sm_common_ies.cpp",
            "lib/asn1/asn1_utils.cpp",
            "lib/support/byte_buffer.cpp",
            "external/fmt/src/format.cc",
            "lib/ocudulog/",
        ]
        script = f"""
set -eu
RIC_ROOT_RAW={shlex.quote(paths["root"])}
RIC_ROOT="$(expand_remote_path "$RIC_ROOT_RAW")"
OCUDU_ROOT_RAW={shlex.quote(self.config.ocudu_root)}
OCUDU_ROOT="$(expand_remote_path "$OCUDU_ROOT_RAW")"
IMAGE={shlex.quote(FLEXRIC_IMAGE)}
FLEXRIC_REPO={shlex.quote(flexric_repo)}
FLEXRIC_REF={shlex.quote(flexric_ref)}
OCUDU_REF={shlex.quote(self.config.sources.ocudu_ref)}
FLEXRIC_SRC="$BENCHMARK_WORKSPACE/sources/{FLEXRIC_SOURCE_DIRNAME}"
BUILD_CONTEXT="$RIC_ROOT/build-context"
if [ ! -d "$OCUDU_ROOT/src/ocudu" ]; then
  echo status=error
  echo error='OCUDU source is missing; run remote provision --stage ocudu first'
  exit 2
fi
missing=""
test -d "$OCUDU_ROOT/src/ocudu/include" || missing="$missing $OCUDU_ROOT/src/ocudu/include"
test -d "$OCUDU_ROOT/src/ocudu/external" || missing="$missing $OCUDU_ROOT/src/ocudu/external"
test -d "$OCUDU_ROOT/src/ocudu/lib/ocudulog" || missing="$missing $OCUDU_ROOT/src/ocudu/lib/ocudulog"
test -f "$OCUDU_ROOT/src/ocudu/{FLEXRIC_OCUDU_ASN_HEADER}" || missing="$missing $OCUDU_ROOT/src/ocudu/{FLEXRIC_OCUDU_ASN_HEADER}"
test -f "$OCUDU_ROOT/src/ocudu/{FLEXRIC_OCUDU_ASN_SOURCE}" || missing="$missing $OCUDU_ROOT/src/ocudu/{FLEXRIC_OCUDU_ASN_SOURCE}"
if [ -n "$missing" ]; then
  echo status=error
  echo error='OCUDU KPM v05 decoder inputs are missing; run remote provision --stage ocudu first'
  echo missing="$missing"
  exit 2
fi
mkdir -p "$RIC_ROOT"
if [ "{'1' if force else '0'}" = "1" ]; then
  rm -rf "$RIC_ROOT/build-context" "$RIC_ROOT/ocudu-asn1" "$RIC_ROOT/patches" "$RIC_ROOT/Dockerfile" "$RIC_ROOT/expected_manifest.json" "$RIC_ROOT/manifest.json" "$RIC_ROOT/build.log" "$FLEXRIC_SRC"
fi
mkdir -p "$RIC_ROOT" "$BENCHMARK_WORKSPACE/sources"
if [ ! -d "$FLEXRIC_SRC/.git" ]; then
  rm -rf "$FLEXRIC_SRC"
  git clone "$FLEXRIC_REPO" "$FLEXRIC_SRC"
else
  git -C "$FLEXRIC_SRC" remote set-url origin "$FLEXRIC_REPO"
fi
git -C "$FLEXRIC_SRC" fetch --tags origin
git -C "$FLEXRIC_SRC" checkout "$FLEXRIC_REF"
if [ ! -x "$FLEXRIC_SRC/{FLEXRIC_CONTEXT_PREP_SCRIPT}" ]; then
  echo status=error
  echo error='dedicated FlexRIC source is missing the OCUDU KPM v05 build-context helper'
  exit 2
fi
FLEXRIC_COMMIT="$(git -C "$FLEXRIC_SRC" rev-parse --short=12 HEAD 2>/dev/null || true)"
OCUDU_COMMIT="$(git -C "$OCUDU_ROOT/src/ocudu" rev-parse --short=12 HEAD 2>/dev/null || true)"
OCUDU_REPO="$(git -C "$OCUDU_ROOT/src/ocudu" remote get-url origin 2>/dev/null || true)"
export IMAGE FLEXRIC_REPO FLEXRIC_REF FLEXRIC_COMMIT OCUDU_REPO OCUDU_REF OCUDU_COMMIT
python3 - "$RIC_ROOT/expected_manifest.json" <<'PY'
import json
import os
import pathlib
import sys

manifest = json.loads({json.dumps(json.dumps(manifest, sort_keys=True))})
manifest.update(
    {{
        "image": os.environ["IMAGE"],
        "repo": os.environ["FLEXRIC_REPO"],
        "ref": os.environ["FLEXRIC_REF"],
        "commit": os.environ["FLEXRIC_COMMIT"],
        "ocudu_repo": os.environ["OCUDU_REPO"],
        "ocudu_ref": os.environ["OCUDU_REF"],
        "ocudu_commit": os.environ["OCUDU_COMMIT"],
    }}
)
pathlib.Path(sys.argv[1]).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
if [ "{'1' if force else '0'}" != "1" ] && docker image inspect "$IMAGE" >/dev/null 2>&1 && [ -f "$RIC_ROOT/manifest.json" ]; then
  REUSE_OK="$(python3 - "$RIC_ROOT/manifest.json" "$RIC_ROOT/reuse_mismatch.txt" <<'PY'
import json
import os
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
mismatch_path = pathlib.Path(sys.argv[2])
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    mismatch_path.write_text(f"manifest_unreadable: {{exc}}\\n", encoding="utf-8")
    print("0")
    raise SystemExit(0)

expected = {{
    "image": os.environ["IMAGE"],
    "repo": os.environ["FLEXRIC_REPO"],
    "ref": os.environ["FLEXRIC_REF"],
    "commit": os.environ["FLEXRIC_COMMIT"],
    "ocudu_repo": os.environ["OCUDU_REPO"],
    "ocudu_ref": os.environ["OCUDU_REF"],
    "ocudu_commit": os.environ["OCUDU_COMMIT"],
    "supports_e2sm_kpm_v05": True,
    "kpm_asn_release": "E2SM-KPM-R003-v05.00",
    "decoder_source": "ocudu-generated-asn1-cpp",
    "kpm_indication_decode_per_syntax": "ATS_UNALIGNED_BASIC_PER",
    "kpm_subscription_encode_per_syntax": "ATS_ALIGNED_BASIC_PER",
}}
mismatches = []
for key, expected_value in expected.items():
    actual = manifest.get(key)
    if actual != expected_value:
        mismatches.append(f"{{key}} expected={{expected_value!r}} actual={{actual!r}}")
if mismatches:
    mismatch_path.write_text("\\n".join(mismatches) + "\\n", encoding="utf-8")
    print("0")
else:
    mismatch_path.write_text("", encoding="utf-8")
    print("1")
PY
)"
  if [ "$REUSE_OK" = "1" ]; then
    echo status=ok
    echo image=$IMAGE
    echo manifest=$RIC_ROOT/manifest.json
    echo build_log=$RIC_ROOT/build.log
    echo flexric_source=$FLEXRIC_SRC
    echo flexric_commit=$FLEXRIC_COMMIT
    echo reused=1
    exit 0
  fi
  echo reuse_mismatch="$(tr '\\n' ';' < "$RIC_ROOT/reuse_mismatch.txt")"
fi
OCUDU_ASN1_ROOT="$OCUDU_ROOT/src/ocudu" FLEXRIC_SOURCE_ROOT="$FLEXRIC_SRC" BUILD_CONTEXT="$BUILD_CONTEXT" "$FLEXRIC_SRC/{FLEXRIC_CONTEXT_PREP_SCRIPT}" > "$RIC_ROOT/context.log" 2>&1
docker build -t "$IMAGE" \\
  --build-arg FLEXRIC_REPO="$FLEXRIC_REPO" \\
  --build-arg FLEXRIC_REF="$FLEXRIC_REF" \\
  --build-arg FLEXRIC_COMMIT="$FLEXRIC_COMMIT" \\
  --build-arg OCUDU_REPO="$OCUDU_REPO" \\
  --build-arg OCUDU_REF="$OCUDU_REF" \\
  --build-arg OCUDU_COMMIT="$OCUDU_COMMIT" \\
  --build-arg FLEXRIC_IMAGE="$IMAGE" \\
  -f "$BUILD_CONTEXT/flexric/{FLEXRIC_DOCKERFILE_REL}" \\
  "$BUILD_CONTEXT" > "$RIC_ROOT/build.log" 2>&1
docker run --rm "$IMAGE" cat /opt/flexric-bench/manifest.json > "$RIC_ROOT/manifest.json"
echo status=ok
echo image=$IMAGE
echo manifest=$RIC_ROOT/manifest.json
echo build_log=$RIC_ROOT/build.log
echo context_log=$RIC_ROOT/context.log
echo flexric_source=$FLEXRIC_SRC
echo flexric_commit=$FLEXRIC_COMMIT
echo reused=0
"""
        remote_command = self._remote_shell(script)
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "force": force,
                "image": FLEXRIC_IMAGE,
                "paths": paths,
                "flexric_repo": flexric_repo,
                "flexric_ref": flexric_ref,
                "source_dir": f"{self.config.workspace}/sources/{FLEXRIC_SOURCE_DIRNAME}",
                "context_prep_script": FLEXRIC_CONTEXT_PREP_SCRIPT,
                "dockerfile_rel": FLEXRIC_DOCKERFILE_REL,
                "manifest": manifest,
                "planned_remote_command": remote_command,
                "ocudu_asn_sources": ocudu_asn_sources,
            }
        init_result = self.init_workspace()
        if init_result.get("status") != "ok":
            return {
                "status": "error",
                "returncode": init_result.get("returncode", 1),
                "image": FLEXRIC_IMAGE,
                "paths": paths,
                "init": init_result,
                "stdout": "",
                "stderr": "remote workspace init failed",
            }
        proc = self._run(self.ssh_argv(remote_command))
        data = self._parse_key_values(proc.stdout)
        status = "ok" if proc.returncode == 0 and data.get("status") == "ok" else "error"
        return {
            "status": status,
            "returncode": proc.returncode,
            "image": FLEXRIC_IMAGE,
            "paths": paths,
            "manifest": data.get("manifest", paths["manifest"]),
            "build_log": data.get("build_log", paths["build_log"]),
            "context_log": data.get("context_log", f'{paths["root"]}/context.log'),
            "flexric_source": data.get("flexric_source", f"{self.config.workspace}/sources/{FLEXRIC_SOURCE_DIRNAME}"),
            "flexric_commit": data.get("flexric_commit", ""),
            "reuse_mismatch": data.get("reuse_mismatch", ""),
            "reused": data.get("reused") == "1",
            "init": init_result,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def _provision_stage_summaries(self, steps: dict[str, Any]) -> dict[str, Any]:
        summaries: dict[str, Any] = {}
        if "asset_sync" in steps:
            asset_sync = steps["asset_sync"]
            summaries["assets"] = {
                "status": asset_sync.get("status"),
                "returncode": asset_sync.get("returncode"),
            }
        if "provision" in steps:
            provision = steps["provision"]
            summaries["assets_images_ocudu"] = {
                "status": provision.get("status"),
                "returncode": provision.get("returncode"),
                "manifest": provision.get("manifest", ""),
                "command_count": provision.get("command_count", 0),
            }
        if "runtime_deps" in steps:
            deps = steps["runtime_deps"]
            summaries["runtime-deps"] = {
                "status": deps.get("status"),
                "returncode": deps.get("returncode"),
                "packages": deps.get("packages", []),
                "runtime_root": deps.get("runtime_root", ""),
            }
        if "ric" in steps:
            ric = steps["ric"]
            summaries["ric"] = {
                "status": ric.get("status"),
                "returncode": ric.get("returncode"),
                "provider": ric.get("provider", self.config.ric_provider),
                "image": ric.get("image", ""),
                "manifest": ric.get("manifest", ""),
                "reused": ric.get("reused", False),
            }
        return summaries

    def _update_provision_manifest(
        self,
        stage: str,
        stage_names: list[str],
        steps: dict[str, Any],
        force: bool,
    ) -> dict[str, Any]:
        payload = {
            "stage": stage,
            "stages": stage_names,
            "force": force,
            "workspace": self.config.workspace,
            "ocudu_root": self.config.ocudu_root,
            "runtime": {
                "open5gs_compose": self.config.runtime.open5gs_compose,
                "e2e_config_dir": self.config.runtime.e2e_config_dir,
                "open5gs_image": self.config.runtime.open5gs_image,
                "gnb_image": self.config.runtime.gnb_image,
                "ue_image": self.config.runtime.ue_image,
            },
            "sources": {
                "ocudu_repo": self.config.sources.ocudu_repo,
                "ocudu_ref": self.config.sources.ocudu_ref,
                "srsran_4g_repo": self.config.sources.srsran_4g_repo,
                "srsran_4g_ref": self.config.sources.srsran_4g_ref,
                "open5gs_ref": self.config.sources.open5gs_ref,
                "flexric_ocudu_repo": self.config.sources.flexric_ocudu_repo,
                "flexric_ocudu_ref": self.config.sources.flexric_ocudu_ref,
            },
            "stage_summaries": self._provision_stage_summaries(steps),
        }
        script = f"""
set -eu
python3 - {shlex.quote(json.dumps(payload, sort_keys=True))} <<'PY'
import json
import pathlib
import sys
import time

payload = json.loads(sys.argv[1])

def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value

workspace = pathlib.Path(expand_remote_path(payload["workspace"]))
manifest_dir = workspace / "manifests"
manifest_dir.mkdir(parents=True, exist_ok=True)
manifest_path = manifest_dir / "provision.json"
if manifest_path.is_file():
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        manifest = {{}}
else:
    manifest = {{}}
manifest.setdefault("kind", "skillful-ran-benchmark-provision")
manifest.setdefault("mode", "workspace-owned")
manifest["workspace"] = str(workspace)
manifest["ocudu_root"] = expand_remote_path(payload["ocudu_root"])
manifest["runtime"] = payload["runtime"]
manifest["sources"] = payload["sources"]
manifest.setdefault("stage_summaries", {{}}).update(payload["stage_summaries"])
manifest.setdefault("requested_stage_history", []).append({{
    "stage": payload["stage"],
    "stages": payload["stages"],
    "force": payload["force"],
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}})
manifest["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print("status=ok")
print(f"manifest={{manifest_path}}")
PY
"""
        proc = self._run(self.ssh_argv(self._remote_shell(script)))
        data = self._parse_key_values(proc.stdout)
        return {
            "status": "ok" if proc.returncode == 0 and data.get("status") == "ok" else "error",
            "returncode": proc.returncode,
            "manifest": data.get("manifest", f"{self.config.workspace}/manifests/provision.json"),
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def provision(self, stage: str = "all", dry_run: bool = False, force: bool = False) -> dict[str, Any]:
        validate_provision_config(self.config)
        stage_names = expand_provision_stages(stage)
        remote_script_stages = [name for name in stage_names if name in {"assets", "images", "ocudu"}]
        assets_source = provision_assets_dir()
        if "assets" in stage_names and not assets_source.is_dir():
            raise RemoteCommandError(f"Provision assets directory does not exist: {assets_source}")
        remote_assets_dir = f"{self.config.workspace.rstrip('/')}/tmp/provision-assets/"
        asset_sync_argv = self.rsync_path_argv(assets_source, remote_assets_dir, dry_run=dry_run)
        remote_command = (
            self._remote_shell(build_provision_script(self.config, remote_script_stages, force))
            if remote_script_stages
            else ""
        )
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "stage": stage,
                "stages": stage_names,
                "force": force,
                "workspace": self.config.workspace,
                "ocudu_root": self.config.ocudu_root,
                "asset_sync_argv": asset_sync_argv if "assets" in stage_names else None,
                "planned_remote_command": remote_command,
                "runtime_deps": self.prepare_runtime_deps(dry_run=True) if "runtime-deps" in stage_names else None,
                "ric": self.prepare_ric(dry_run=True, force=force) if "ric" in stage_names else None,
            }

        init_result = self.init_workspace()
        if init_result.get("status") != "ok":
            return {
                "status": "error",
                "stage": stage,
                "stages": stage_names,
                "init": init_result,
                "stderr": "remote workspace init failed",
            }

        steps: dict[str, Any] = {"init": init_result}
        if "assets" in stage_names:
            sync_proc = self._run(asset_sync_argv)
            steps["asset_sync"] = {
                "status": "ok" if sync_proc.returncode == 0 else "error",
                "returncode": sync_proc.returncode,
                "stdout": sync_proc.stdout.strip(),
                "stderr": sync_proc.stderr.strip(),
            }
            if sync_proc.returncode != 0:
                steps["manifest_update"] = self._update_provision_manifest(stage, stage_names, steps, force)
                return {
                    "status": "error",
                    "stage": stage,
                    "stages": stage_names,
                    "steps": steps,
                    "stderr": "provision asset sync failed",
                }

        if remote_script_stages:
            proc = self._run(self.ssh_argv(remote_command))
            data = self._parse_key_values(proc.stdout)
            steps["provision"] = {
                "status": "ok" if proc.returncode == 0 and data.get("status") == "ok" else "error",
                "returncode": proc.returncode,
                "manifest": data.get("manifest", ""),
                "command_count": int(data.get("command_count", "0") or 0),
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
            if steps["provision"]["status"] != "ok":
                steps["manifest_update"] = self._update_provision_manifest(stage, stage_names, steps, force)
                return {
                    "status": "error",
                    "stage": stage,
                    "stages": stage_names,
                    "steps": steps,
                    "manifest": data.get("manifest", ""),
                    "stderr": proc.stderr.strip(),
                }

        if "runtime-deps" in stage_names:
            deps = self.prepare_runtime_deps()
            steps["runtime_deps"] = deps
            if deps.get("status") != "ok":
                steps["manifest_update"] = self._update_provision_manifest(stage, stage_names, steps, force)
                return {
                    "status": "error",
                    "stage": stage,
                    "stages": stage_names,
                    "steps": steps,
                    "stderr": "runtime dependency preparation failed",
                }

        if "ric" in stage_names:
            ric = self.prepare_ric(force=force)
            steps["ric"] = ric
            if ric.get("status") != "ok":
                steps["manifest_update"] = self._update_provision_manifest(stage, stage_names, steps, force)
                return {
                    "status": "error",
                    "stage": stage,
                    "stages": stage_names,
                    "steps": steps,
                    "stderr": "RIC preparation failed",
                }

        manifest = ""
        provision_step = steps.get("provision")
        if isinstance(provision_step, dict):
            manifest = str(provision_step.get("manifest", ""))
        manifest_update = self._update_provision_manifest(stage, stage_names, steps, force)
        steps["manifest_update"] = manifest_update
        if manifest_update.get("status") != "ok":
            return {
                "status": "error",
                "stage": stage,
                "stages": stage_names,
                "force": force,
                "workspace": self.config.workspace,
                "ocudu_root": self.config.ocudu_root,
                "manifest": manifest_update.get("manifest", manifest or f"{self.config.workspace}/manifests/provision.json"),
                "steps": steps,
                "stderr": "provision manifest update failed",
            }
        return {
            "status": "ok",
            "stage": stage,
            "stages": stage_names,
            "force": force,
            "workspace": self.config.workspace,
            "ocudu_root": self.config.ocudu_root,
            "manifest": manifest or f"{self.config.workspace}/manifests/provision.json",
            "steps": steps,
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
        script = f'cd "$BENCHMARK_WORKSPACE" && {command_text}'
        proc = self._run(self.ssh_argv(self._remote_shell(script)))
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
