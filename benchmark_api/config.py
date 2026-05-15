"""Configuration parsing for the benchmark remote harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_OCUDU_ROOT = "/home/zhouyou/skillful-ran/.local/ocudu"
DEFAULT_REMOTE_WORKSPACE = "/home/zhouyou/skillful-ran-benchmark-workspace"


@dataclass(frozen=True)
class RemoteConfig:
    ssh_target: str
    ssh_key: str
    ocudu_root: str = DEFAULT_OCUDU_ROOT
    workspace: str = DEFAULT_REMOTE_WORKSPACE
    connect_timeout: int = 8


def _strip_value(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
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
    ssh_target = remote.get("ssh")
    ssh_key = remote.get("ssh-key")
    if not ssh_target:
        raise ValueError("Missing required config value: remote.ssh")
    if not ssh_key:
        raise ValueError("Missing required config value: remote.ssh-key")

    return RemoteConfig(
        ssh_target=ssh_target,
        ssh_key=str(Path(ssh_key).expanduser()),
        ocudu_root=remote.get("ocudu-root", DEFAULT_OCUDU_ROOT),
        workspace=remote.get("workspace", DEFAULT_REMOTE_WORKSPACE),
    )

