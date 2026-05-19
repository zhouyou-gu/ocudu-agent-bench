"""Task contract loading and agent-view redaction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.api_catalog import build_api_projection, validate_api_selection
from benchmark.benchmark_api.types import (
    IMPLEMENTED_STIMULUS_DRIVERS,
    RanActionType,
    RanObservationSource,
    RawScoreMetric,
    StimulusDriverKind,
)


DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"
_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*_v[0-9]+$")


@dataclass(frozen=True)
class PrivateTask:
    task_id: str
    version: int
    G: str
    E: dict[str, Any]
    U: dict[str, Any]
    I: dict[str, Any]
    J: dict[str, Any]
    allowed_observation_context: tuple[str, ...]
    public_constraints: tuple[str, ...]
    source: Path

    @property
    def step_count(self) -> int:
        return int(self.U.get("steps", 1))

    @property
    def selected_api_kinds(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.I.get("api_kinds", ()))

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.I.get("allowed_actions", ()))

    @property
    def observation_sources(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.I.get("observation_sources", ()))

    @property
    def allow_no_action(self) -> bool:
        return bool(self.I.get("allow_no_action", True))


@dataclass(frozen=True)
class AgentVisibleTask:
    task_id: str
    goal: str
    api_projection: dict[str, Any]
    observation_schema: str
    action_schema: str
    feedback_schema: str
    allow_no_action: bool
    public_constraints: tuple[str, ...]
    observation_context_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "api_projection": self.api_projection,
            "observation_schema": self.observation_schema,
            "action_schema": self.action_schema,
            "feedback_schema": self.feedback_schema,
            "allow_no_action": self.allow_no_action,
            "public_constraints": list(self.public_constraints),
            "observation_context_keys": list(self.observation_context_keys),
        }


def load_task(task_id: str, tasks_dir: Path | str | None = None) -> PrivateTask:
    root = Path(tasks_dir) if tasks_dir is not None else DEFAULT_TASKS_DIR
    path = root / task_id / "task.json"
    if not path.exists():
        raise FileNotFoundError(f"Task manifest not found: {path}")
    return load_task_file(path)


def load_task_file(path: Path | str) -> PrivateTask:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON task manifest: {source}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Task manifest must be an object: {source}")
    task = _task_from_mapping(raw, source)
    validate_private_task(task)
    return task


def load_all_tasks(tasks_dir: Path | str | None = None) -> dict[str, PrivateTask]:
    root = Path(tasks_dir) if tasks_dir is not None else DEFAULT_TASKS_DIR
    tasks: dict[str, PrivateTask] = {}
    for path in sorted(root.glob("*/task.json")):
        task = load_task_file(path)
        if task.task_id in tasks:
            raise ValueError(f"Duplicate task id: {task.task_id}")
        tasks[task.task_id] = task
    return tasks


def agent_visible_task(task: PrivateTask) -> AgentVisibleTask:
    projection = build_api_projection(list(task.selected_api_kinds))
    selected_actions = sorted(set(projection["action_types"]) & set(task.allowed_actions))
    projection["action_types"] = selected_actions
    projection["observation_sources"] = sorted(set(projection["observation_sources"]) & set(task.observation_sources))
    if task.allow_no_action:
        projection["action_types"].append(RanActionType.NO_ACTION.value)
    return AgentVisibleTask(
        task_id=task.task_id,
        goal=task.G,
        api_projection=projection,
        observation_schema="schemas/observation.schema.json",
        action_schema="schemas/action.schema.json",
        feedback_schema="schemas/feedback.schema.json",
        allow_no_action=task.allow_no_action,
        public_constraints=task.public_constraints,
        observation_context_keys=task.allowed_observation_context,
    )


def validate_private_task(task: PrivateTask) -> None:
    if not _TASK_ID_RE.match(task.task_id):
        raise ValueError(f"Invalid task id: {task.task_id!r}")
    if task.source.parent.name != task.task_id:
        raise ValueError(f"Task id {task.task_id!r} must match directory name {task.source.parent.name!r}")
    if not task.G.strip():
        raise ValueError(f"Task {task.task_id} has empty G")
    if not isinstance(task.E, dict) or not task.E.get("runtime"):
        raise ValueError(f"Task {task.task_id} has invalid E runtime setup")
    if not task.E.get("runtime_adapter"):
        raise ValueError(f"Task {task.task_id} must declare E.runtime_adapter")
    validate_api_selection(list(task.selected_api_kinds))
    allowed_actions = set(task.allowed_actions)
    for action in allowed_actions:
        RanActionType(action)
    if RanActionType.NO_ACTION.value in allowed_actions:
        raise ValueError("NO_ACTION belongs to allow_no_action, not allowed_actions")
    for source in task.observation_sources:
        RanObservationSource(source)
    selected_driver_kinds = {
        StimulusDriverKind(event["kind"])
        for event in _stimulus_events(task.U)
    }
    future = selected_driver_kinds - IMPLEMENTED_STIMULUS_DRIVERS
    if future:
        names = ", ".join(sorted(driver.value for driver in future))
        raise ValueError(f"Task {task.task_id} references non-implemented stimulus driver(s): {names}")
    for metric in task.J.get("raw_metrics", []):
        RawScoreMetric(metric)
    expected_action = task.J.get("expected_action_type")
    if expected_action is not None:
        RanActionType(str(expected_action))


def task_summary(task: PrivateTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "version": task.version,
        "goal": task.G,
        "runtime": task.E.get("runtime"),
        "api_kinds": list(task.selected_api_kinds),
        "observation_sources": list(task.observation_sources),
        "allowed_actions": list(task.allowed_actions),
        "allow_no_action": task.allow_no_action,
        "steps": task.step_count,
        "scoring_rule": task.J.get("scoring_rule"),
    }


def _task_from_mapping(data: dict[str, Any], source: Path) -> PrivateTask:
    required = ("id", "version", "G", "E", "U", "I", "J")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"Task manifest {source} missing required keys: {', '.join(missing)}")
    return PrivateTask(
        task_id=_required_str(data, "id", source),
        version=int(data["version"]),
        G=_required_str(data, "G", source),
        E=_required_dict(data, "E", source),
        U=_required_dict(data, "U", source),
        I=_required_dict(data, "I", source),
        J=_required_dict(data, "J", source),
        allowed_observation_context=tuple(str(item) for item in data.get("allowed_observation_context", [])),
        public_constraints=tuple(str(item) for item in data.get("public_constraints", [])),
        source=source,
    )


def _required_str(data: dict[str, Any], key: str, source: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid {key!r} in {source}")
    return value


def _required_dict(data: dict[str, Any], key: str, source: Path) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Missing or invalid {key!r} in {source}")
    return value


def _stimulus_events(stimulus: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    events = stimulus.get("events", [])
    if not isinstance(events, list):
        raise ValueError("U.events must be a list")
    for event in events:
        if not isinstance(event, dict) or "kind" not in event or "phase" not in event:
            raise ValueError("Each stimulus event must contain kind and phase")
    return tuple(events)
