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
    FLEXRIC_IMAGE,
    RIC_PROVIDER_DRAX_EXISTING,
    drax_manifest,
    drax_workspace_paths,
    flexric_manifest,
    flexric_workspace_paths,
    generate_flexric_dockerfile,
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
            "benchmark_api/provision.py",
            "benchmark_api/remote.py",
            "benchmark_api/ric.py",
            "benchmark_api/suite.py",
            "benchmark_api/websocket_client.py",
            "conformance/tests.json",
            "schemas/actions.schema.json",
            "schemas/observations.schema.json",
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
SRSRAN_PROJECT_SRC="$BENCHMARK_WORKSPACE/sources/srsran-project"
SRSRAN_4G_SRC="$BENCHMARK_WORKSPACE/sources/srsran-4g"
if git -C "$SRSRAN_PROJECT_SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo srsran_project_is_git=1
  echo srsran_project_commit=$(git -C "$SRSRAN_PROJECT_SRC" rev-parse --short=12 HEAD 2>/dev/null || true)
  echo srsran_project_origin=$(git -C "$SRSRAN_PROJECT_SRC" remote get-url origin 2>/dev/null || true)
else
  echo srsran_project_is_git=0
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
                "srsran_project_is_git": data.get("srsran_project_is_git") == "1",
                "srsran_project_commit": data.get("srsran_project_commit", ""),
                "srsran_project_origin": data.get("srsran_project_origin", ""),
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
                    "srsran_project_ref": self.config.sources.srsran_project_ref,
                    "srsran_4g_ref": self.config.sources.srsran_4g_ref,
                    "open5gs_ref": self.config.sources.open5gs_ref,
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
        if self.config.ric_provider == RIC_PROVIDER_DRAX_EXISTING:
            return self._prepare_drax_existing_ric(dry_run=dry_run)
        paths = flexric_workspace_paths(self.config.workspace)
        dockerfile = generate_flexric_dockerfile()
        manifest = flexric_manifest()
        payload = {
            "paths": paths,
            "image": FLEXRIC_IMAGE,
            "dockerfile": dockerfile,
            "manifest": manifest,
            "force": force,
        }
        script = f"""
set -eu
RIC_ROOT_RAW={shlex.quote(paths["root"])}
RIC_ROOT="$(expand_remote_path "$RIC_ROOT_RAW")"
IMAGE={shlex.quote(FLEXRIC_IMAGE)}
mkdir -p "$RIC_ROOT"
cat > "$RIC_ROOT/Dockerfile" <<'DOCKERFILE'
{dockerfile}
DOCKERFILE
cat > "$RIC_ROOT/expected_manifest.json" <<'JSON'
{json.dumps(manifest, indent=2, sort_keys=True)}
JSON
if [ "{'1' if force else '0'}" != "1" ] && docker image inspect "$IMAGE" >/dev/null 2>&1 && [ -f "$RIC_ROOT/manifest.json" ]; then
  echo status=ok
  echo image=$IMAGE
  echo manifest=$RIC_ROOT/manifest.json
  echo build_log=$RIC_ROOT/build.log
  echo reused=1
  exit 0
fi
docker build -t "$IMAGE" "$RIC_ROOT" > "$RIC_ROOT/build.log" 2>&1
docker run --rm "$IMAGE" cat /opt/flexric-bench/manifest.json > "$RIC_ROOT/manifest.json"
echo status=ok
echo image=$IMAGE
echo manifest=$RIC_ROOT/manifest.json
echo build_log=$RIC_ROOT/build.log
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
                "manifest": manifest,
                "planned_remote_command": remote_command,
                "dockerfile": dockerfile,
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
            "reused": data.get("reused") == "1",
            "init": init_result,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    def _prepare_drax_existing_ric(self, dry_run: bool = False) -> dict[str, Any]:
        paths = drax_workspace_paths(self.config.workspace)
        manifest = drax_manifest(self.config)
        script = f"""
set -eu
RIC_ROOT_RAW={shlex.quote(paths["root"])}
RIC_ROOT="$(expand_remote_path "$RIC_ROOT_RAW")"
mkdir -p "$RIC_ROOT"
cat > "$RIC_ROOT/manifest.json" <<'JSON'
{json.dumps(manifest, indent=2, sort_keys=True)}
JSON
echo status=ok
echo provider={RIC_PROVIDER_DRAX_EXISTING}
echo manifest=$RIC_ROOT/manifest.json
"""
        remote_command = self._remote_shell(script)
        if dry_run:
            return {
                "status": "ok",
                "dry_run": True,
                "provider": RIC_PROVIDER_DRAX_EXISTING,
                "paths": paths,
                "manifest": manifest,
                "planned_remote_command": remote_command,
            }
        init_result = self.init_workspace()
        if init_result.get("status") != "ok":
            return {
                "status": "error",
                "returncode": init_result.get("returncode", 1),
                "provider": RIC_PROVIDER_DRAX_EXISTING,
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
            "provider": RIC_PROVIDER_DRAX_EXISTING,
            "paths": paths,
            "manifest": data.get("manifest", paths["manifest"]),
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
                "srsran_project_repo": self.config.sources.srsran_project_repo,
                "srsran_project_ref": self.config.sources.srsran_project_ref,
                "srsran_4g_repo": self.config.sources.srsran_4g_repo,
                "srsran_4g_ref": self.config.sources.srsran_4g_ref,
                "open5gs_ref": self.config.sources.open5gs_ref,
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
