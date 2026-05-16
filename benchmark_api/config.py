"""Configuration parsing for the benchmark remote harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RIC_PROVIDER = "flexric"
DEFAULT_DRAX_NAMESPACE = "default"
DEFAULT_DRAX_KUBECTL_IMAGE = "bitnami/kubectl:1.30.8"


@dataclass(frozen=True)
class RuntimeConfig:
    open5gs_compose: str
    e2e_config_dir: str
    open5gs_image: str
    gnb_image: str
    ue_image: str


@dataclass(frozen=True)
class SourcesConfig:
    srsran_project_repo: str = ""
    srsran_project_ref: str = ""
    srsran_4g_repo: str = ""
    srsran_4g_ref: str = ""
    open5gs_ref: str = ""


@dataclass(frozen=True)
class ProvisionConfig:
    mode: str = "workspace-owned"


@dataclass(frozen=True)
class DraxConfig:
    kubeconfig: str = ""
    namespace: str = DEFAULT_DRAX_NAMESPACE
    kubectl_image: str = DEFAULT_DRAX_KUBECTL_IMAGE
    e2_endpoint: str = ""
    e2_bind_addr: str = "0.0.0.0"
    kpm_api_url: str = ""


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
    drax: DraxConfig = field(default_factory=DraxConfig)


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
    drax = values.get("drax", {})
    ssh_target = _required(remote, "remote", "ssh")
    ssh_key = _required(remote, "remote", "ssh-key")
    ocudu_root = _required(remote, "remote", "ocudu-root")
    workspace = _required(remote, "remote", "workspace")

    provider = ric.get("provider", remote.get("ric-provider", DEFAULT_RIC_PROVIDER))
    if provider not in {"flexric", "drax-existing"}:
        raise ValueError("ric.provider must be one of: flexric, drax-existing")

    if provider == "drax-existing":
        for key in ["kubeconfig", "e2-endpoint", "kpm-api-url"]:
            _required(drax, "drax", key)

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
            srsran_project_repo=sources.get("srsran-project-repo", ""),
            srsran_project_ref=sources.get("srsran-project-ref", ""),
            srsran_4g_repo=sources.get("srsran-4g-repo", ""),
            srsran_4g_ref=sources.get("srsran-4g-ref", ""),
            open5gs_ref=sources.get("open5gs-ref", ""),
        ),
        provision=ProvisionConfig(
            mode=provision.get("mode", "workspace-owned"),
        ),
        ric_provider=provider,
        drax=DraxConfig(
            kubeconfig=drax.get("kubeconfig", ""),
            namespace=drax.get("namespace", DEFAULT_DRAX_NAMESPACE),
            kubectl_image=drax.get("kubectl-image", DEFAULT_DRAX_KUBECTL_IMAGE),
            e2_endpoint=drax.get("e2-endpoint", ""),
            e2_bind_addr=drax.get("e2-bind-addr", "0.0.0.0"),
            kpm_api_url=drax.get("kpm-api-url", ""),
        ),
    )
