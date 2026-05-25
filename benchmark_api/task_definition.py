"""Task contract loading, digesting, and agent-view redaction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark_api.api_catalog import build_api_projection, validate_api_selection
from benchmark_api.stimulus import targeted_steps_for_event, validate_stimulus_parameters
from benchmark_api.types import (
    IMPLEMENTED_STIMULUS_DRIVERS,
    RanActionType,
    RanObservationSource,
    RawScoreMetric,
    StimulusDriverKind,
)


_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*_v[0-9]+$")
_ANCHOR_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*_v[0-9]+$")
_TASK_SETS = {"base", "regression", "compound", "generated"}
_TASK_FAMILIES = {"prb", "ssb", "mobility", "radio_cli", "core", "diagnosis", "isolation", "restraint", "backend", "harness"}
_TASK_ROLES = {"primary", "regression", "compound"}


@dataclass(frozen=True)
class PrivateTask:
    task_id: str
    version: int
    G: str
    E: dict[str, Any]
    U: dict[str, Any]
    I: dict[str, Any]
    J: dict[str, Any]
    M: dict[str, Any]
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


def load_task(task_id: str, tasks_dir: Path | str) -> PrivateTask:
    root = Path(tasks_dir)
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


def load_all_tasks(tasks_dir: Path | str) -> dict[str, PrivateTask]:
    root = Path(tasks_dir)
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
    projection["observation_sources"] = sorted(set(task.observation_sources))
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
    selected_descriptors = validate_api_selection(list(task.selected_api_kinds))
    selected_action_types = {
        descriptor.action_type.value
        for descriptor in selected_descriptors
        if descriptor.action_type is not None
    }
    allowed_actions = set(task.allowed_actions)
    for action in allowed_actions:
        RanActionType(action)
    if RanActionType.NO_ACTION.value in allowed_actions:
        raise ValueError("NO_ACTION belongs to allow_no_action, not allowed_actions")
    unselected_actions = allowed_actions - selected_action_types
    if unselected_actions:
        names = ", ".join(sorted(unselected_actions))
        raise ValueError(f"allowed_actions must be backed by selected api_kinds: {names}")
    for source in task.observation_sources:
        RanObservationSource(source)
    observation_detail = str(task.I.get("observation_detail", "repair_targets"))
    if observation_detail not in {"repair_targets", "diagnosis_symptoms"}:
        raise ValueError("I.observation_detail must be repair_targets or diagnosis_symptoms")
    stimulus_events = _stimulus_events(task.U)
    steps = task.step_count
    if steps <= 0:
        raise ValueError(f"Task {task.task_id} must declare positive U.steps")
    selected_driver_kinds = {StimulusDriverKind(event["kind"]) for event in stimulus_events}
    future = selected_driver_kinds - IMPLEMENTED_STIMULUS_DRIVERS
    if future:
        names = ", ".join(sorted(driver.value for driver in future))
        raise ValueError(f"Task {task.task_id} references non-implemented stimulus driver(s): {names}")
    for event in stimulus_events:
        targeted_steps_for_event(event, steps)
        validate_stimulus_parameters(StimulusDriverKind(event["kind"]), event.get("parameters", {}))
    for metric in task.J.get("raw_metrics", []):
        RawScoreMetric(metric)
    expected_action = task.J.get("expected_action_type")
    if expected_action is not None:
        RanActionType(str(expected_action))
    _validate_temporal_expectations(task)
    _validate_expected_action_fields(task)
    _validate_expected_post_action_evidence(task)
    _validate_manifest_metadata(task)


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
        "task_set": task.M.get("task_set"),
        "family": task.M.get("family"),
        "role": task.M.get("role"),
    }


def task_definition_digest(task: PrivateTask) -> str:
    """Stable SHA-256 over the private task contract, excluding local source paths."""

    return hashlib.sha256(
        json.dumps(_task_digest_payload(task), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def task_provenance(
    task: PrivateTask,
    *,
    suite: str,
    episode_seed: int,
    suite_seed: int,
    suite_count: int | None,
    family: str | None,
) -> dict[str, Any]:
    provenance = {
        "task_id": task.task_id,
        "task_definition_sha256": task_definition_digest(task),
        "task_set": task.M.get("task_set"),
        "family": task.M.get("family"),
        "role": task.M.get("role"),
        "suite": suite,
        "episode_seed": episode_seed,
        "suite_seed": suite_seed,
        "suite_count": suite_count,
        "family_filter": family,
        "source": str(task.source),
    }
    variant = task.M.get("variant")
    if isinstance(variant, dict):
        provenance["generated_variant"] = {
            "variant_id": variant.get("variant_id"),
            "anchor_task_id": variant.get("anchor_task_id"),
            "axis_registry_sha256": variant.get("axis_registry_sha256"),
            "suite_policies_sha256": variant.get("suite_policies_sha256"),
            "expected_failure_modes": list(variant.get("expected_failure_modes", [])),
        }
    return provenance


def clone_task_with_overrides(
    task: PrivateTask,
    *,
    task_id: str,
    goal: str | None = None,
    E: dict[str, Any] | None = None,
    U: dict[str, Any] | None = None,
    I: dict[str, Any] | None = None,
    J: dict[str, Any] | None = None,
    M: dict[str, Any] | None = None,
    allowed_observation_context: tuple[str, ...] | None = None,
    public_constraints: tuple[str, ...] | None = None,
    source: Path | None = None,
) -> PrivateTask:
    cloned = PrivateTask(
        task_id=task_id,
        version=task.version,
        G=goal if goal is not None else task.G,
        E=E if E is not None else json.loads(json.dumps(task.E)),
        U=U if U is not None else json.loads(json.dumps(task.U)),
        I=I if I is not None else json.loads(json.dumps(task.I)),
        J=J if J is not None else json.loads(json.dumps(task.J)),
        M=M if M is not None else json.loads(json.dumps(task.M)),
        allowed_observation_context=(
            allowed_observation_context
            if allowed_observation_context is not None
            else tuple(task.allowed_observation_context)
        ),
        public_constraints=public_constraints if public_constraints is not None else tuple(task.public_constraints),
        source=source if source is not None else Path("<generated>") / task_id / "task.json",
    )
    validate_private_task(cloned)
    return cloned


def _task_digest_payload(task: PrivateTask) -> dict[str, Any]:
    return {
        "id": task.task_id,
        "version": task.version,
        "G": task.G,
        "E": task.E,
        "U": task.U,
        "I": task.I,
        "J": task.J,
        "M": task.M,
        "allowed_observation_context": list(task.allowed_observation_context),
        "public_constraints": list(task.public_constraints),
    }


def _task_from_mapping(data: dict[str, Any], source: Path) -> PrivateTask:
    required = ("id", "version", "G", "E", "U", "I", "J", "M")
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
        M=_required_dict(data, "M", source),
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


def _validate_temporal_expectations(task: PrivateTask) -> None:
    expectations = task.J.get("temporal_expectations", [])
    if not isinstance(expectations, list):
        raise ValueError("J.temporal_expectations must be a list")
    for expectation in expectations:
        if not isinstance(expectation, dict):
            raise ValueError("Each temporal expectation must be an object")
        step_id = expectation.get("step_id")
        if isinstance(step_id, bool) or not isinstance(step_id, int) or not 1 <= step_id <= task.step_count:
            raise ValueError("temporal expectation step_id must be within U.steps")
        RanActionType(str(expectation.get("action_type", "")))
        for field in ("valid", "accepted"):
            if field in expectation and not isinstance(expectation[field], bool):
                raise ValueError(f"temporal expectation {field} must be a boolean")


def _validate_expected_action_fields(task: PrivateTask) -> None:
    expectations = task.J.get("expected_action_fields", [])
    if not isinstance(expectations, list):
        raise ValueError("J.expected_action_fields must be a list")
    for expectation in expectations:
        if not isinstance(expectation, dict):
            raise ValueError("Each expected action field spec must be an object")
        step_id = expectation.get("step_id")
        if isinstance(step_id, bool) or not isinstance(step_id, int) or not 1 <= step_id <= task.step_count:
            raise ValueError("expected action field step_id must be within U.steps")
        action_type = RanActionType(str(expectation.get("action_type", "")))
        if action_type == RanActionType.NO_ACTION:
            raise ValueError("expected action fields cannot target NO_ACTION")
        fields = expectation.get("fields", {})
        if not isinstance(fields, dict) or not fields:
            raise ValueError("expected action fields must be a non-empty object")
        if "numeric_tolerance" in expectation:
            tolerance = expectation["numeric_tolerance"]
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or float(tolerance) < 0:
                raise ValueError("numeric_tolerance must be a non-negative number")


def _validate_expected_post_action_evidence(task: PrivateTask) -> None:
    expectations = task.J.get("expected_post_action_evidence", [])
    if not isinstance(expectations, list):
        raise ValueError("J.expected_post_action_evidence must be a list")
    for expectation in expectations:
        if not isinstance(expectation, dict):
            raise ValueError("Each expected post-action evidence spec must be an object")
        step_id = expectation.get("step_id")
        if isinstance(step_id, bool) or not isinstance(step_id, int) or not 1 <= step_id <= task.step_count:
            raise ValueError("expected post-action evidence step_id must be within U.steps")
        after_step_id = expectation.get("after_step_id")
        if after_step_id is not None:
            if isinstance(after_step_id, bool) or not isinstance(after_step_id, int) or not 1 <= after_step_id < step_id:
                raise ValueError("expected post-action evidence after_step_id must be before step_id")
        fields = expectation.get("fields", {})
        if not isinstance(fields, dict) or not fields:
            raise ValueError("expected post-action evidence fields must be a non-empty object")
        for field in fields:
            if not isinstance(field, str) or not field.strip():
                raise ValueError("expected post-action evidence field paths must be non-empty strings")
        if "numeric_tolerance" in expectation:
            tolerance = expectation["numeric_tolerance"]
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or float(tolerance) < 0:
                raise ValueError("numeric_tolerance must be a non-negative number")


def _validate_manifest_metadata(task: PrivateTask) -> None:
    if "variant_axes" in task.J:
        raise ValueError("J.variant_axes is retired; use private M.variant")
    task_set = task.M.get("task_set")
    family = task.M.get("family")
    role = task.M.get("role")
    if task_set not in _TASK_SETS:
        raise ValueError("M.task_set must be base, regression, compound, or generated")
    if family not in _TASK_FAMILIES:
        raise ValueError("M.family has an unknown task family")
    if role not in _TASK_ROLES:
        raise ValueError("M.role must be primary, regression, or compound")
    expected_role = {
        "base": "primary",
        "regression": "regression",
        "compound": "compound",
        "generated": "primary",
    }[str(task_set)]
    if role != expected_role:
        raise ValueError(f"M.role must be {expected_role!r} when M.task_set is {task_set!r}")
    variant = task.M.get("variant")
    if task_set == "generated":
        if not isinstance(variant, dict):
            raise ValueError("generated tasks must declare M.variant")
        required = ("family", "axis", "level", "anchor_task_id", "suite", "variant_id", "seed", "axis_values", "expected_failure_modes")
        missing = [field for field in required if field not in variant]
        if missing:
            raise ValueError(f"M.variant missing required fields: {', '.join(missing)}")
        for field in ("family", "axis", "level", "anchor_task_id", "suite", "variant_id"):
            value = variant[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"M.variant.{field} must be a non-empty string")
        if not _ANCHOR_TASK_ID_RE.match(variant["anchor_task_id"]):
            raise ValueError("M.variant.anchor_task_id must be a valid task id")
        if isinstance(variant["seed"], bool) or not isinstance(variant["seed"], int):
            raise ValueError("M.variant.seed must be an integer")
        if not isinstance(variant["axis_values"], dict):
            raise ValueError("M.variant.axis_values must be an object")
        modes = variant["expected_failure_modes"]
        if not isinstance(modes, list) or not modes or not all(isinstance(item, str) and item for item in modes):
            raise ValueError("M.variant.expected_failure_modes must be a non-empty string list")
    elif variant is not None and not isinstance(variant, dict):
        raise ValueError("M.variant must be an object when present")
