"""Task metadata registry for benchmark episode discovery and gating."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TASK_WS_PRB_PING_V1 = "ws_prb_ping_v1"
TASK_E2_KPM_PRB_PING_V1 = "e2_kpm_prb_ping_v1"
TASK_WS_PRB_NOOP_GUARD_V1 = "ws_prb_noop_guard_v1"
TASK_WS_PRB_ERROR_REPAIR_V1 = "ws_prb_error_repair_v1"
TASK_WS_PRB_ACTION_BUDGET_V1 = "ws_prb_action_budget_v1"
TASK_E2_KPM_JSON_CONSISTENCY_V1 = "e2_kpm_json_consistency_v1"
TASK_METRICS_STALENESS_NOOP_V1 = "metrics_staleness_noop_v1"
TASK_E2_CCC_PRB_POLICY_PING_V1 = "e2_ccc_prb_policy_ping_v1"
TASK_E2_RC_DU_PRB_POLICY_PING_V1 = "e2_rc_du_prb_policy_ping_v1"
TASK_E2_CONTROL_API_CONSISTENCY_V1 = "e2_control_api_consistency_v1"

IMPLEMENTED_EPISODE_TASKS = {
    TASK_WS_PRB_PING_V1,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_WS_PRB_NOOP_GUARD_V1,
    TASK_WS_PRB_ERROR_REPAIR_V1,
    TASK_WS_PRB_ACTION_BUDGET_V1,
    TASK_E2_KPM_JSON_CONSISTENCY_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
}
DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*_v[0-9]+$")


@dataclass(frozen=True)
class TaskSpec:
    id: str
    name: str
    summary: str
    stage: str
    suite_stage: str
    runtime: str
    readiness: str
    action_types: tuple[str, ...]
    observation_sources: tuple[str, ...]
    required_conformance: tuple[str, ...]
    scoring: tuple[str, ...]
    artifact_groups: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any], source: Path) -> "TaskSpec":
        task_id = _required_str(data, "id", source)
        if not _TASK_ID_RE.match(task_id):
            raise ValueError(f"Invalid task id in {source}: {task_id!r}")
        if source.parent.name != task_id:
            raise ValueError(f"Task id {task_id!r} must match directory name {source.parent.name!r}")
        return cls(
            id=task_id,
            name=_required_str(data, "name", source),
            summary=_required_str(data, "summary", source),
            stage=_required_str(data, "stage", source),
            suite_stage=_required_str(data, "suite_stage", source),
            runtime=_required_str(data, "runtime", source),
            readiness=_required_str(data, "readiness", source),
            action_types=_required_str_tuple(data, "action_types", source),
            observation_sources=_required_str_tuple(data, "observation_sources", source),
            required_conformance=_required_str_tuple(data, "required_conformance", source),
            scoring=_required_str_tuple(data, "scoring", source),
            artifact_groups=_required_str_tuple(data, "artifact_groups", source),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "stage": self.stage,
            "suite_stage": self.suite_stage,
            "runtime": self.runtime,
            "readiness": self.readiness,
            "action_types": list(self.action_types),
            "observation_sources": list(self.observation_sources),
            "required_conformance": list(self.required_conformance),
            "scoring": list(self.scoring),
            "artifact_groups": list(self.artifact_groups),
        }


def _required_str(data: dict[str, Any], key: str, source: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid {key!r} in {source}")
    return value


def _required_str_tuple(data: dict[str, Any], key: str, source: Path) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Missing or invalid {key!r} in {source}")
    items = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Invalid item in {key!r} for {source}")
        items.append(item)
    return tuple(items)


def load_task_specs(tasks_dir: Path | str | None = None) -> dict[str, TaskSpec]:
    root = Path(tasks_dir) if tasks_dir is not None else DEFAULT_TASKS_DIR
    if not root.exists():
        raise FileNotFoundError(f"Task directory not found: {root}")
    specs: dict[str, TaskSpec] = {}
    for path in sorted(root.rglob("task.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON task manifest: {path}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Task manifest must be a JSON object: {path}")
        spec = TaskSpec.from_mapping(raw, path)
        if spec.id in specs:
            raise ValueError(f"Duplicate task id: {spec.id}")
        specs[spec.id] = spec
    if not specs:
        raise ValueError(f"No task manifests found under {root}")
    return specs


def supported_task_ids(tasks_dir: Path | str | None = None) -> set[str]:
    return set(load_task_specs(tasks_dir))


def get_task_spec(task_id: str, tasks_dir: Path | str | None = None) -> TaskSpec:
    specs = load_task_specs(tasks_dir)
    try:
        return specs[task_id]
    except KeyError as exc:
        supported = ", ".join(sorted(specs))
        raise ValueError(f"Unsupported benchmark task: {task_id}. Supported tasks: {supported}") from exc


def is_supported_task(task_id: str, tasks_dir: Path | str | None = None) -> bool:
    return task_id in supported_task_ids(tasks_dir)


def implemented_episode_task_ids() -> set[str]:
    registered = supported_task_ids()
    return {task_id for task_id in IMPLEMENTED_EPISODE_TASKS if task_id in registered}


def is_implemented_episode_task(task_id: str) -> bool:
    return task_id in implemented_episode_task_ids()


def conformance_checks_for_task(task_id: str, tasks_dir: Path | str | None = None) -> set[str]:
    return set(get_task_spec(task_id, tasks_dir).required_conformance)


def episode_stage_for_task(task_id: str, tasks_dir: Path | str | None = None) -> str:
    return get_task_spec(task_id, tasks_dir).stage


def suite_stage_for_task(task_id: str, tasks_dir: Path | str | None = None) -> str:
    return get_task_spec(task_id, tasks_dir).suite_stage


V3_EPISODE_GATE_CHECKS = conformance_checks_for_task(TASK_WS_PRB_PING_V1)
V4_EPISODE_GATE_CHECKS = conformance_checks_for_task(TASK_E2_KPM_PRB_PING_V1)
EPISODE_TASKS = implemented_episode_task_ids()
