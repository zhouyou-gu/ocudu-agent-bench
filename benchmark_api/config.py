"""Configuration parsing for the benchmark remote harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RIC_PROVIDER = "flexric"


@dataclass(frozen=True)
class RuntimeConfig:
    open5gs_compose: str
    e2e_config_dir: str
    open5gs_image: str
    gnb_image: str
    ue_image: str


@dataclass(frozen=True)
class SourcesConfig:
    ocudu_repo: str = ""
    ocudu_ref: str = ""
    srsran_4g_repo: str = ""
    srsran_4g_ref: str = ""
    open5gs_ref: str = ""
    flexric_ocudu_repo: str = ""
    flexric_ocudu_ref: str = ""


@dataclass(frozen=True)
class ProvisionConfig:
    mode: str = "workspace-owned"


@dataclass(frozen=True)
class RemoteConfig:
    ssh_target: str
    ssh_key: str
    ocudu_root: str
    workspace: str
    runtime: RuntimeConfig
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    provision: ProvisionConfig = field(default_factory=ProvisionConfig)
    connect_timeout: int = 8
    ric_provider: str = DEFAULT_RIC_PROVIDER


def _strip_value(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _required(section: dict[str, str], section_name: str, key: str) -> str:
    value = section.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required config value: {section_name}.{key}")
    return value


def parse_config(path: Path) -> RemoteConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    section = None
    values: dict[str, dict[str, str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.endswith(":"):
            section = line[:-1].strip()
            values.setdefault(section, {})
            continue
        if section is None:
            continue
        stripped = line.strip()
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        values.setdefault(section, {})[key] = _strip_value(value)

    remote = values.get("remote", {})
    runtime = values.get("runtime", {})
    sources = values.get("sources", {})
    provision = values.get("provision", {})
    ric = values.get("ric", {})
    ssh_target = _required(remote, "remote", "ssh")
    ssh_key = _required(remote, "remote", "ssh-key")
    ocudu_root = _required(remote, "remote", "ocudu-root")
    workspace = _required(remote, "remote", "workspace")

    provider = ric.get("provider", remote.get("ric-provider", DEFAULT_RIC_PROVIDER))
    if provider != DEFAULT_RIC_PROVIDER:
        raise ValueError(
            "ric.provider must be flexric; external RIC providers were removed from the active benchmark path"
        )

    stale_sut_keys = [key for key in ["srsran-project-repo", "srsran-project-ref"] if sources.get(key)]
    if stale_sut_keys:
        raise ValueError(
            "srsRAN Project is no longer the benchmark SUT source; replace "
            + ", ".join(f"sources.{key}" for key in stale_sut_keys)
            + " with sources.ocudu-repo and sources.ocudu-ref"
        )

    return RemoteConfig(
        ssh_target=ssh_target,
        ssh_key=str(Path(ssh_key).expanduser()),
        ocudu_root=ocudu_root,
        workspace=workspace,
        runtime=RuntimeConfig(
            open5gs_compose=_required(runtime, "runtime", "open5gs-compose"),
            e2e_config_dir=_required(runtime, "runtime", "e2e-config-dir"),
            open5gs_image=_required(runtime, "runtime", "open5gs-image"),
            gnb_image=_required(runtime, "runtime", "gnb-image"),
            ue_image=_required(runtime, "runtime", "ue-image"),
        ),
        sources=SourcesConfig(
            ocudu_repo=sources.get("ocudu-repo", ""),
            ocudu_ref=sources.get("ocudu-ref", ""),
            srsran_4g_repo=sources.get("srsran-4g-repo", ""),
            srsran_4g_ref=sources.get("srsran-4g-ref", ""),
            open5gs_ref=sources.get("open5gs-ref", ""),
            flexric_ocudu_repo=sources.get("flexric-ocudu-repo", ""),
            flexric_ocudu_ref=sources.get("flexric-ocudu-ref", ""),
        ),
        provision=ProvisionConfig(
            mode=provision.get("mode", "workspace-owned"),
        ),
        ric_provider=provider,
    )
