"""Deterministic test-only timeline agents.

These helpers emulate agent operation choices without invoking external agents.
They intentionally read private task expectations because they are oracle-side
test fixtures, not benchmark runtime components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from benchmark.benchmark_api.controller import BaselineController
from benchmark.benchmark_api.task_definition import PrivateTask, load_task
from benchmark.benchmark_api.types import RanActionType


PROFILE_TELEMETRY: dict[str, dict[str, float | int | str]] = {
    "good": {"prompt_tokens": 16, "completion_tokens": 8, "reasoning_tokens": 6, "decision_latency_s": 0.001},
    "noop": {"prompt_tokens": 2, "completion_tokens": 1, "reasoning_tokens": 0, "decision_latency_s": 0.0002},
    "eager": {"prompt_tokens": 5, "completion_tokens": 3, "reasoning_tokens": 1, "decision_latency_s": 0.0004},
    "wrong_payload": {"prompt_tokens": 14, "completion_tokens": 7, "reasoning_tokens": 5, "decision_latency_s": 0.0015},
    "repeat": {"prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": 3, "decision_latency_s": 0.0008},
    "wrong_api": {"prompt_tokens": 12, "completion_tokens": 6, "reasoning_tokens": 4, "decision_latency_s": 0.0012},
}


class TimelineAgent:
    """A deterministic callable that emulates timeline-level agent operations."""

    def __init__(self, profile: str) -> None:
        if profile not in PROFILE_TELEMETRY:
            raise ValueError(f"unknown timeline-agent profile: {profile}")
        self.profile = profile
        self.auto = BaselineController("auto")
        self.planner = BaselineController("auto")
        self.sent = False
        self.stored_decision: dict[str, Any] | None = None
        self.task_cache: dict[str, PrivateTask] = {}

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.profile == "good":
            response = self.auto(payload)
            return self._with_profile_telemetry(response)
        if self.profile == "noop":
            return self._response(None)
        if self.profile == "eager":
            return self._eager(payload)
        if self.profile == "wrong_payload":
            return self._wrong_payload(payload)
        if self.profile == "repeat":
            return self._repeat(payload)
        if self.profile == "wrong_api":
            return self._wrong_api(payload)
        raise AssertionError(f"unhandled profile: {self.profile}")

    def _eager(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        decision = self._planned_decision(payload)
        if decision is None:
            return self._response(None)
        self.sent = True
        return self._response(decision)

    def _wrong_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        task = self._task(payload)
        target = _target_expectation(task)
        step_id = int(payload["observation"]["step_id"])
        if target is None or step_id != target["step_id"]:
            return self._response(None)
        decision = self._planned_decision(payload, preferred_type=target["action_type"])
        if decision is None:
            return self._response(None)
        self.sent = True
        return self._response(_mutate_payload(decision, task, step_id, target["action_type"]))

    def _repeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.stored_decision is not None:
            return self._response(deepcopy(self.stored_decision))
        response = self.auto(payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        if isinstance(decision, dict) and decision.get("type") != RanActionType.NO_ACTION.value:
            self.stored_decision = deepcopy(decision)
        return self._with_profile_telemetry(response)

    def _wrong_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        task = self._task(payload)
        target = _target_expectation(task)
        step_id = int(payload["observation"]["step_id"])
        if target is None or step_id != target["step_id"]:
            return self._response(None)
        wrong_type = _wrong_action_type(target["action_type"], _actions(payload))
        if wrong_type is None:
            return self._response(None)
        decision = self._planned_decision(payload, preferred_type=wrong_type)
        if decision is None:
            return self._response(None)
        self.sent = True
        return self._response(decision)

    def _planned_decision(self, payload: dict[str, Any], preferred_type: str | None = None) -> dict[str, Any] | None:
        task_id = str(payload["task"]["task_id"])
        evidence = payload["observation"].get("evidence", {})
        actions = _actions(payload)
        action_type = preferred_type if preferred_type in actions else self.planner._select_action_type(task_id, actions, evidence)
        if action_type is None:
            return None
        return self.planner._build_decision(action_type, evidence, payload)

    def _task(self, payload: dict[str, Any]) -> PrivateTask:
        task_id = str(payload["task"]["task_id"])
        if task_id not in self.task_cache:
            self.task_cache[task_id] = load_task(task_id)
        return self.task_cache[task_id]

    def _with_profile_telemetry(self, response: dict[str, Any]) -> dict[str, Any]:
        payload = dict(response)
        payload["telemetry"] = dict(PROFILE_TELEMETRY[self.profile])
        return payload

    def _response(self, decision: dict[str, Any] | None) -> dict[str, Any]:
        return {"decision": decision, "telemetry": dict(PROFILE_TELEMETRY[self.profile])}


def _actions(payload: dict[str, Any]) -> list[str]:
    return list(payload["task"]["api_projection"].get("action_types", []))


def _target_expectation(task: PrivateTask) -> dict[str, Any] | None:
    for expectation in task.J.get("temporal_expectations", []):
        action_type = str(expectation.get("action_type", ""))
        if action_type == RanActionType.NO_ACTION.value:
            continue
        if expectation.get("valid", True) is False or expectation.get("accepted", True) is False:
            continue
        step_id = expectation.get("step_id")
        if isinstance(step_id, int):
            return {"step_id": step_id, "action_type": action_type}
    return None


def _wrong_action_type(expected_type: str, actions: list[str]) -> str | None:
    paired_alternatives = {
        RanActionType.SET_SSB_BLOCK_POWER_WS.value: RanActionType.SET_PRB_POLICY_RATIO_WS.value,
        RanActionType.SET_PRB_POLICY_RATIO_WS.value: RanActionType.SET_SSB_BLOCK_POWER_WS.value,
        RanActionType.SET_PRB_POLICY_RATIO_CCC.value: RanActionType.SET_PRB_POLICY_RATIO_WS.value,
        RanActionType.SET_CFO_CLI.value: RanActionType.SET_TX_TIME_OFFSET_CLI.value,
        RanActionType.SET_TX_TIME_OFFSET_CLI.value: RanActionType.SET_CFO_CLI.value,
        RanActionType.TRIGGER_HANDOVER_CLI.value: RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value,
        RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value: RanActionType.TRIGGER_HANDOVER_CLI.value,
    }
    preferred = paired_alternatives.get(expected_type)
    if preferred in actions:
        return preferred
    for action_type in actions:
        if action_type != expected_type:
            return action_type
    return None


def _mutate_payload(decision: dict[str, Any], task: PrivateTask, step_id: int, action_type: str) -> dict[str, Any]:
    mutated = dict(decision)
    expected_fields = _expected_fields(task, step_id, action_type)
    for field in expected_fields:
        if field in mutated:
            _mutate_field(mutated, field)
            return mutated
    for field in (
        "cfo_hz",
        "tx_time_offset_us",
        "ssb_block_power_dbm",
        "nci",
        "min_prb_policy_ratio",
        "target_pci",
        "target_pcis",
        "nf",
        "supi",
    ):
        if field in mutated:
            _mutate_field(mutated, field)
            return mutated
    return mutated


def _expected_fields(task: PrivateTask, step_id: int, action_type: str) -> tuple[str, ...]:
    for expectation in task.J.get("expected_action_fields", []):
        if expectation.get("step_id") == step_id and expectation.get("action_type") == action_type:
            fields = expectation.get("fields", {})
            if isinstance(fields, dict):
                return tuple(str(field) for field in fields.keys())
    return ()


def _mutate_field(payload: dict[str, Any], field: str) -> None:
    value = payload[field]
    if field == "min_prb_policy_ratio":
        payload[field] = min(100, int(value) + 1)
        if payload[field] > int(payload.get("max_prb_policy_ratio", 100)):
            payload[field] = max(0, int(value) - 1)
    elif field == "max_prb_policy_ratio":
        payload[field] = max(0, int(value) - 1)
        if payload[field] < int(payload.get("min_prb_policy_ratio", 0)):
            payload[field] = min(100, int(value) + 1)
    elif field == "ssb_block_power_dbm":
        payload[field] = max(-60, int(value) - 1)
    elif field == "nci":
        payload[field] = int(value) + 1
    elif field == "cfo_hz":
        payload[field] = float(value) + 100.0
    elif field == "tx_time_offset_us":
        payload[field] = float(value) + 1.0
    elif field == "target_pci":
        payload[field] = (int(value) + 1) % 1008
    elif field == "target_pcis":
        targets = list(value)
        targets[0] = (int(targets[0]) + 1) % 1008
        payload[field] = targets
    elif field == "nf":
        payload[field] = "amf" if value != "amf" else "smf"
    elif field == "supi":
        text = str(value)
        payload[field] = text[:-1] + ("9" if text[-1:] != "9" else "8")
    elif isinstance(value, int):
        payload[field] = value + 1
    elif isinstance(value, float):
        payload[field] = value + 1.0
    elif isinstance(value, str):
        payload[field] = value + "_wrong"
