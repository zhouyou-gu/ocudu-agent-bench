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
TASK_WS_SSB_POWER_GUARD_V1 = "ws_ssb_power_guard_v1"
TASK_WS_SSB_POWER_REPAIR_V1 = "ws_ssb_power_repair_v1"
TASK_RAN_POLICY_TRIAGE_V1 = "ran_policy_triage_v1"

TRIAGE_HIDDEN_SCENARIOS = (
    TASK_WS_PRB_PING_V1,
    TASK_WS_PRB_NOOP_GUARD_V1,
    TASK_WS_PRB_ERROR_REPAIR_V1,
    TASK_WS_PRB_ACTION_BUDGET_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
    TASK_WS_SSB_POWER_GUARD_V1,
    TASK_WS_SSB_POWER_REPAIR_V1,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_E2_KPM_JSON_CONSISTENCY_V1,
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
)

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
    TASK_WS_SSB_POWER_GUARD_V1,
    TASK_WS_SSB_POWER_REPAIR_V1,
    TASK_RAN_POLICY_TRIAGE_V1,
}
DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*_v[0-9]+$")

ACTION_TYPES = {
    "NO_ACTION",
    "SET_PRB_POLICY_RATIO_WS",
    "SET_SSB_BLOCK_POWER_WS",
    "SET_PRB_POLICY_RATIO_CCC",
    "SET_PRB_POLICY_RATIO_RC_DU",
}
RUNTIME_FAMILIES = {
    "docker_e2e",
    "docker_e2e_flexric",
    "hidden_triage",
}
READINESS_LEVELS = {
    "idea",
    "designed",
    "conformance_needed",
    "implemented_unscored",
    "scored",
}
OBSERVATION_SOURCES = {
    "ping",
    "json_metrics",
    "websocket_control",
    "websocket_control_outcomes",
    "cell_identity",
    "scenario_metrics_staleness",
    "e2_kpm_v05",
    "e2_control_outcome",
    "ue_identity",
    "pcap_log_oracle",
    "management_context",
    "stable_action_catalog",
}
SCORE_DIMENSIONS = {
    "valid_action_accepted_rate",
    "invalid_local_rejection_correctness",
    "ping_success_ratio",
    "metrics_continuity",
    "e2_kpm_continuity",
    "e2_oracle_available",
    "e2_control_oracle_available",
    "expected_e2_control_oracle_available",
    "expected_action_type_correct",
    "accepted_e2_control_actions",
    "clean_teardown",
    "task_success",
    "action_budget_ok",
    "noop_correctness",
    "evidence_gated_action",
    "stale_action_avoidance",
    "triage_success",
    "rationale_complete",
    "correct_api_selection",
    "unnecessary_action_avoidance",
    "repair_success",
    "stale_wait_success",
}
WIRE_COMMAND_NAMES = {
    "rrm_policy_ratio_set",
    "ssb_set",
    "metrics_subscribe",
}


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
        name = _required_str(data, "name", source)
        summary = _required_str(data, "summary", source)
        stage = _required_str(data, "stage", source)
        suite_stage = _required_str(data, "suite_stage", source)
        runtime = _required_str(data, "runtime", source)
        readiness = _required_str(data, "readiness", source)
        action_types = _required_str_tuple(data, "action_types", source)
        observation_sources = _required_str_tuple(data, "observation_sources", source)
        scoring = _required_str_tuple(data, "scoring", source)
        artifact_groups = _required_str_tuple(data, "artifact_groups", source)
        _validate_catalog_value("runtime", runtime, RUNTIME_FAMILIES, source)
        _validate_catalog_value("readiness", readiness, READINESS_LEVELS, source)
        _validate_catalog_items("action_types", action_types, ACTION_TYPES, source, reject_wire_commands=True)
        _validate_catalog_items("observation_sources", observation_sources, OBSERVATION_SOURCES, source)
        _validate_catalog_items("scoring", scoring, SCORE_DIMENSIONS, source)
        _validate_artifact_groups(artifact_groups, source)
        return cls(
            id=task_id,
            name=name,
            summary=summary,
            stage=stage,
            suite_stage=suite_stage,
            runtime=runtime,
            readiness=readiness,
            action_types=action_types,
            observation_sources=observation_sources,
            required_conformance=_required_str_tuple(data, "required_conformance", source),
            scoring=scoring,
            artifact_groups=artifact_groups,
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


def _validate_catalog_value(key: str, value: str, allowed: set[str], source: Path) -> None:
    if value not in allowed:
        raise ValueError(f"Unknown {key!r} value in {source}: {value!r}")


def _validate_catalog_items(
    key: str,
    items: tuple[str, ...],
    allowed: set[str],
    source: Path,
    reject_wire_commands: bool = False,
) -> None:
    unknown = sorted(set(items) - allowed)
    if reject_wire_commands:
        wire_commands = sorted(set(items) & WIRE_COMMAND_NAMES)
        if wire_commands:
            joined = ", ".join(wire_commands)
            raise ValueError(f"Wire command names are not task action types in {source}: {joined}")
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"Unknown {key!r} item(s) in {source}: {joined}")


def _validate_artifact_groups(items: tuple[str, ...], source: Path) -> None:
    malformed = [
        item
        for item in items
        if item.startswith("/")
        or item.startswith("~")
        or ".." in Path(item).parts
        or not item.startswith("episode/")
    ]
    if malformed:
        joined = ", ".join(sorted(malformed))
        raise ValueError(f"Malformed artifact_groups item(s) in {source}: {joined}")


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
