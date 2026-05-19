"""Generic site configuration parsing for benchmark infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    workspace: str = ".benchmark-workspace"
    ocudu_root: str = "ocudu"
    flexric_root: str = "flexric"


@dataclass(frozen=True)
class SiteConfig:
    ssh_target: str | None = None
    ssh_key: str | None = None
    runtime: RuntimeConfig = RuntimeConfig()


def parse_config(path: str | Path) -> SiteConfig:
    config_path = Path(path)
    if not config_path.exists():
        return SiteConfig()
    values = _parse_config_values(config_path.read_text(encoding="utf-8"))
    runtime = RuntimeConfig(
        workspace=values.get("remote.workspace", values.get("runtime.workspace", values.get("workspace", ".benchmark-workspace"))),
        ocudu_root=values.get("remote.ocudu_root", values.get("runtime.ocudu_root", values.get("ocudu_root", "ocudu"))),
        flexric_root=values.get("remote.flexric_root", values.get("runtime.flexric_root", values.get("flexric_root", "flexric"))),
    )
    return SiteConfig(
        ssh_target=values.get("remote.ssh") or values.get("remote.ssh_target") or values.get("ssh_target"),
        ssh_key=values.get("remote.ssh_key") or values.get("ssh_key"),
        runtime=runtime,
    )


def _parse_config_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if stripped.endswith(":") and "=" not in stripped:
            section = _normalize_key(stripped[:-1])
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            values[_normalize_key(key)] = value.strip()
            continue
        if section and raw_line[:1].isspace():
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                values[f"{section}.{_normalize_key(parts[0])}"] = parts[1].strip()
    return values


def _normalize_key(key: str) -> str:
    return key.strip().replace("-", "_")
