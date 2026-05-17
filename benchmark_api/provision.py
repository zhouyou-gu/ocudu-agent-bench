"""Workspace-owned remote provisioning helpers."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.config import RemoteConfig

PROVISION_STAGE_CHOICES = ("all", "assets", "images", "ocudu", "runtime-deps", "ric")
WORKSPACE_PROVISION_MODE = "workspace-owned"


class ProvisionConfigError(ValueError):
    """Raised when a config cannot support workspace-owned provisioning."""


def provision_assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "provision"


def expand_provision_stages(stage: str) -> list[str]:
    if stage not in PROVISION_STAGE_CHOICES:
        raise ProvisionConfigError(f"provision stage must be one of: {', '.join(PROVISION_STAGE_CHOICES)}")
    if stage == "all":
        return ["assets", "images", "ocudu", "runtime-deps", "ric"]
    return [stage]


def _ssh_user(ssh_target: str) -> str:
    if "@" not in ssh_target:
        return ""
    return ssh_target.split("@", 1)[0].split("/")[-1]


def _remote_path_forms(path: str, ssh_user: str) -> set[str]:
    clean = path.rstrip("/")
    forms = {clean}
    if clean.startswith("~/") and ssh_user:
        forms.add(f"/home/{ssh_user}/{clean[2:]}")
    return forms


def _path_has_workspace_prefix(path: str, workspace: str, ssh_user: str = "") -> bool:
    for clean_path in _remote_path_forms(path, ssh_user):
        for clean_workspace in _remote_path_forms(workspace, ssh_user):
            if clean_path == clean_workspace or clean_path.startswith(clean_workspace + "/"):
                return True
    return False


def validate_workspace_owned_paths(config: RemoteConfig) -> list[str]:
    outside: list[str] = []
    workspace = config.workspace
    ssh_user = _ssh_user(config.ssh_target)
    candidates = {
        "remote.ocudu-root": config.ocudu_root,
        "runtime.open5gs-compose": config.runtime.open5gs_compose,
        "runtime.e2e-config-dir": config.runtime.e2e_config_dir,
    }
    for name, value in candidates.items():
        if not _path_has_workspace_prefix(value, workspace, ssh_user=ssh_user):
            outside.append(name)
    return outside


def validate_provision_config(config: RemoteConfig) -> None:
    if config.provision.mode != WORKSPACE_PROVISION_MODE:
        raise ProvisionConfigError(f"provision.mode must be {WORKSPACE_PROVISION_MODE!r}")
    missing = []
    source_fields = {
        "sources.ocudu-repo": config.sources.ocudu_repo,
        "sources.ocudu-ref": config.sources.ocudu_ref,
        "sources.srsran-4g-repo": config.sources.srsran_4g_repo,
        "sources.srsran-4g-ref": config.sources.srsran_4g_ref,
        "sources.open5gs-ref": config.sources.open5gs_ref,
        "sources.flexric-ocudu-repo": config.sources.flexric_ocudu_repo,
        "sources.flexric-ocudu-ref": config.sources.flexric_ocudu_ref,
    }
    for name, value in source_fields.items():
        if not value:
            missing.append(name)
    if missing:
        raise ProvisionConfigError("Missing required provision source pins: " + ", ".join(missing))
    outside = validate_workspace_owned_paths(config)
    if outside:
        raise ProvisionConfigError(
            "Workspace-owned provisioning requires these paths under remote.workspace: " + ", ".join(outside)
        )


def provision_payload(config: RemoteConfig, stage_names: list[str], force: bool) -> dict[str, Any]:
    return {
        "workspace": config.workspace,
        "ocudu_root": config.ocudu_root,
        "open5gs_compose": config.runtime.open5gs_compose,
        "e2e_config_dir": config.runtime.e2e_config_dir,
        "open5gs_image": config.runtime.open5gs_image,
        "gnb_image": config.runtime.gnb_image,
        "ue_image": config.runtime.ue_image,
        "sources": {
            "ocudu_repo": config.sources.ocudu_repo,
            "ocudu_ref": config.sources.ocudu_ref,
            "srsran_4g_repo": config.sources.srsran_4g_repo,
            "srsran_4g_ref": config.sources.srsran_4g_ref,
            "open5gs_ref": config.sources.open5gs_ref,
            "flexric_ocudu_repo": config.sources.flexric_ocudu_repo,
            "flexric_ocudu_ref": config.sources.flexric_ocudu_ref,
        },
        "stages": stage_names,
        "force": force,
    }


def build_provision_script(config: RemoteConfig, stage_names: list[str], force: bool) -> str:
    payload = provision_payload(config, stage_names, force)
    payload_text = json.dumps(payload, sort_keys=True)
    return f"""
set -eu
python3 - {shlex.quote(payload_text)} <<'PY'
import json
import os
import pathlib
import shutil
import subprocess
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
ocudu_root = pathlib.Path(expand_remote_path(payload["ocudu_root"]))
open5gs_compose = pathlib.Path(expand_remote_path(payload["open5gs_compose"]))
e2e_config_dir = pathlib.Path(expand_remote_path(payload["e2e_config_dir"]))
asset_root = workspace / "tmp" / "provision-assets"
manifest_dir = workspace / "manifests"
log_dir = manifest_dir / "provision-logs"
manifest = {{
    "kind": "skillful-ran-benchmark-provision",
    "mode": "workspace-owned",
    "workspace": str(workspace),
    "ocudu_root": str(ocudu_root),
    "open5gs_compose": str(open5gs_compose),
    "e2e_config_dir": str(e2e_config_dir),
    "images": {{
        "open5gs": payload["open5gs_image"],
        "gnb": payload["gnb_image"],
        "ue": payload["ue_image"],
    }},
    "sources": payload["sources"],
    "requested_stages": payload["stages"],
    "commands": [],
    "resolved": {{}},
    "artifacts": {{}},
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}}

def inside_workspace(path):
    try:
        pathlib.Path(path).resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False

for name, path in {{
    "ocudu_root": ocudu_root,
    "open5gs_compose": open5gs_compose,
    "e2e_config_dir": e2e_config_dir,
}}.items():
    if not inside_workspace(path):
        raise SystemExit(f"{{name}} must be inside workspace: {{path}}")

workspace.mkdir(parents=True, exist_ok=True)
manifest_dir.mkdir(parents=True, exist_ok=True)
log_dir.mkdir(parents=True, exist_ok=True)

def write_manifest(status):
    manifest["status"] = status
    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_path = manifest_dir / "provision.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    return manifest_path

def run(cmd, log_name, cwd=None, check=True):
    log_path = log_dir / log_name
    proc = subprocess.run(cmd, cwd=cwd, check=False, text=True, capture_output=True)
    log_path.write_text(
        "$ " + " ".join(str(part) for part in cmd) + "\\n\\n"
        + "STDOUT\\n" + proc.stdout + "\\nSTDERR\\n" + proc.stderr,
        encoding="utf-8",
    )
    record = {{"cmd": cmd, "returncode": proc.returncode, "log": str(log_path)}}
    manifest["commands"].append(record)
    if check and proc.returncode != 0:
        write_manifest("error")
        raise SystemExit(f"command failed: {{cmd}}; see {{log_path}}")
    return proc

def fail_preflight(message):
    manifest.setdefault("preflight_errors", []).append(message)
    write_manifest("error")
    raise SystemExit(message)

def require_file(path, message):
    if not pathlib.Path(path).is_file():
        fail_preflight(message + f": {{path}}")

def require_dir(path, message):
    if not pathlib.Path(path).is_dir():
        fail_preflight(message + f": {{path}}")

def require_docker_image(image, message):
    proc = subprocess.run(["docker", "image", "inspect", image], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        fail_preflight(message + f": {{image}}")

def rewrite_open5gs_compose(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    output = []
    for line in lines:
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if stripped.startswith("image:"):
            output.append(f"{{indent}}image: {{payload['open5gs_image']}}")
        elif stripped.startswith("OPEN5GS_VERSION:"):
            output.append(f"{{indent}}OPEN5GS_VERSION: \\"{{payload['sources']['open5gs_ref']}}\\"")
        else:
            output.append(line)
    path.write_text("\\n".join(output) + "\\n", encoding="utf-8")

def clone_or_update(repo, ref, dest, log_prefix):
    if (dest / ".git").is_dir():
        run(["git", "-C", str(dest), "remote", "set-url", "origin", repo], f"{{log_prefix}}-set-url.log")
        run(["git", "-C", str(dest), "fetch", "--tags", "--prune", "origin"], f"{{log_prefix}}-fetch.log")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", repo, str(dest)], f"{{log_prefix}}-clone.log")
    run(["git", "-C", str(dest), "checkout", ref], f"{{log_prefix}}-checkout.log")
    proc = run(["git", "-C", str(dest), "rev-parse", "HEAD"], f"{{log_prefix}}-rev-parse.log")
    return proc.stdout.strip()

stages = set(payload["stages"])
force = bool(payload["force"])

if "assets" in stages:
    if not asset_root.is_dir():
        raise SystemExit(f"provision assets were not synced to {{asset_root}}")
    docker_asset_dir = workspace / "assets" / "docker"
    compose_dir = open5gs_compose.parent
    docker_asset_dir.mkdir(parents=True, exist_ok=True)
    compose_dir.mkdir(parents=True, exist_ok=True)
    e2e_config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(asset_root / "docker", docker_asset_dir, dirs_exist_ok=True)
    shutil.copytree(asset_root / "open5gs-core" / "compose", compose_dir, dirs_exist_ok=True)
    shutil.copytree(asset_root / "ocudu-zmq-open5gs-e2e" / "config", e2e_config_dir, dirs_exist_ok=True)
    rewrite_open5gs_compose(open5gs_compose)
    manifest["artifacts"]["dockerfiles"] = str(docker_asset_dir)
    manifest["artifacts"]["open5gs_compose"] = str(open5gs_compose)
    manifest["artifacts"]["e2e_config_dir"] = str(e2e_config_dir)

if "images" in stages:
    docker_asset_dir = workspace / "assets" / "docker"
    require_file(
        docker_asset_dir / "ocudu-build.Dockerfile",
        "provision assets missing for image stage; run remote provision --stage assets first",
    )
    require_file(
        docker_asset_dir / "srsran-4g-ue-build.Dockerfile",
        "provision assets missing for image stage; run remote provision --stage assets first",
    )
    require_file(
        open5gs_compose,
        "Open5GS compose asset missing for image stage; run remote provision --stage assets first",
    )
    require_file(
        open5gs_compose.parent / "open5gs" / "Dockerfile",
        "Open5GS Docker context missing for image stage; run remote provision --stage assets first",
    )
    run(["docker", "build", "-t", payload["gnb_image"], "-f", str(docker_asset_dir / "ocudu-build.Dockerfile"), str(docker_asset_dir)], "image-ocudu-build.log")
    run(["docker", "build", "-t", payload["ue_image"], "-f", str(docker_asset_dir / "srsran-4g-ue-build.Dockerfile"), str(docker_asset_dir)], "image-srsran-4g-ue-build.log")
    run(["docker", "compose", "-f", str(open5gs_compose), "build"], "image-open5gs-build.log")

if "ocudu" in stages:
    require_docker_image(
        payload["gnb_image"],
        "gNB build image missing for OCUDU stage; run remote provision --stage images first",
    )
    require_docker_image(
        payload["ue_image"],
        "UE build image missing for OCUDU stage; run remote provision --stage images first",
    )
    require_dir(
        workspace,
        "benchmark workspace missing for OCUDU stage; run remote provision --stage assets first",
    )
    sources_dir = workspace / "sources"
    ocudu_src = sources_dir / "ocudu"
    ue_src = sources_dir / "srsran-4g"
    ocudu_commit = clone_or_update(payload["sources"]["ocudu_repo"], payload["sources"]["ocudu_ref"], ocudu_src, "source-ocudu")
    ue_commit = clone_or_update(payload["sources"]["srsran_4g_repo"], payload["sources"]["srsran_4g_ref"], ue_src, "source-srsran-4g")
    manifest["resolved"]["ocudu_commit"] = ocudu_commit
    manifest["resolved"]["srsran_4g_commit"] = ue_commit
    (ocudu_root / "src").mkdir(parents=True, exist_ok=True)
    for name, src in {{"ocudu": ocudu_src, "srsran-4g": ue_src}}.items():
        link = ocudu_root / "src" / name
        if link.is_symlink() or (force and link.exists()):
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        if not link.exists():
            link.symlink_to(src, target_is_directory=True)
    ocudu_build = ocudu_root / "build" / "ocudu"
    ocudu_install = ocudu_root / "install" / "ocudu"
    ue_build = ocudu_root / "build" / "srsran-4g"
    ue_install = ocudu_root / "install" / "srsran-4g"
    for path in [ocudu_build, ocudu_install, ue_build, ue_install]:
        path.mkdir(parents=True, exist_ok=True)
    run([
        "docker", "run", "--rm",
        "-v", f"{{ocudu_src}}:/src:ro",
        "-v", f"{{ocudu_build}}:/build",
        "-v", f"{{ocudu_install}}:/install",
        payload["gnb_image"], "bash", "-lc",
        "git config --global --add safe.directory /src && "
        "cmake -S /src -B /build -GNinja -DCMAKE_INSTALL_PREFIX=/install -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF && "
        "cmake --build /build --target gnb -j$(nproc) && "
        "cmake --install /build/apps/gnb",
    ], "build-ocudu.log")
    run([
        "docker", "run", "--rm",
        "-v", f"{{ue_src}}:/src:ro",
        "-v", f"{{ue_build}}:/build",
        "-v", f"{{ue_install}}:/install",
        payload["ue_image"], "bash", "-lc",
        "git config --global --add safe.directory /src && "
        "cmake -S /src -B /build -GNinja -DCMAKE_INSTALL_PREFIX=/install -DENABLE_ZEROMQ=ON -DENABLE_UHD=OFF "
        "-DCMAKE_CXX_FLAGS='-Wno-error=array-bounds' && "
        "cmake --build /build --target srsue srsran_rf_zmq -j$(nproc) && "
        "cmake --install /build/srsue/src && "
        "mkdir -p /install/lib && find /build -name 'libsrs*.so*' -exec cp -a {{}} /install/lib/ \\\\;",
    ], "build-srsran-4g.log")
    manifest["artifacts"]["gnb_binary"] = str(ocudu_install / "bin" / "gnb")
    manifest["artifacts"]["srsue_binary"] = str(ue_install / "bin" / "srsue")

manifest_path = write_manifest("ok")
print("status=ok")
print(f"manifest={{manifest_path}}")
print(f"workspace={{workspace}}")
print(f"ocudu_root={{ocudu_root}}")
print(f"open5gs_compose={{open5gs_compose}}")
print(f"e2e_config_dir={{e2e_config_dir}}")
print(f"command_count={{len(manifest['commands'])}}")
PY
"""
