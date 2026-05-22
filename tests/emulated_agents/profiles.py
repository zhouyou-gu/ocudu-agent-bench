"""Deterministic emulated profile agents used only by tests.

These profiles are not production agents. Some profiles read private task
expectations to create controlled bad decisions, which lets the tests verify
that scoring separates timing, evidence, API, payload, repeat, and safety
failures.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from benchmark.benchmark_api.controller import BaselineController
from benchmark.benchmark_api.task_definition import PrivateTask
from benchmark.benchmark_api.types import RanActionType


PROFILE_NAMES = (
    "good",
    "noop",
    "early",
    "late",
    "wrong_payload",
    "repeat",
    "wrong_api",
    "unsafe_control",
)

PROFILE_TELEMETRY: dict[str, dict[str, float | int]] = {
    "good": {"prompt_tokens": 16, "completion_tokens": 8, "reasoning_tokens": 6, "decision_latency_s": 0.0010},
    "noop": {"prompt_tokens": 2, "completion_tokens": 1, "reasoning_tokens": 0, "decision_latency_s": 0.0002},
    "early": {"prompt_tokens": 7, "completion_tokens": 4, "reasoning_tokens": 1, "decision_latency_s": 0.0004},
    "late": {"prompt_tokens": 9, "completion_tokens": 4, "reasoning_tokens": 2, "decision_latency_s": 0.0007},
    "wrong_payload": {"prompt_tokens": 14, "completion_tokens": 7, "reasoning_tokens": 5, "decision_latency_s": 0.0015},
    "repeat": {"prompt_tokens": 10, "completion_tokens": 5, "reasoning_tokens": 3, "decision_latency_s": 0.0008},
    "wrong_api": {"prompt_tokens": 12, "completion_tokens": 6, "reasoning_tokens": 4, "decision_latency_s": 0.0012},
    "unsafe_control": {"prompt_tokens": 5, "completion_tokens": 3, "reasoning_tokens": 1, "decision_latency_s": 0.0003},
}


class EmulatedProfileAgent:
    """A callable test fixture that emits deterministic timeline decisions."""

    def __init__(self, profile: str, tasks: dict[str, PrivateTask] | None = None) -> None:
        if profile not in PROFILE_NAMES:
            raise ValueError(f"unknown emulated profile: {profile}")
        self.profile = profile
        self.auto = BaselineController("auto")
        self.planner = BaselineController("auto")
        self.task_cache = dict(tasks or {})
        self.sent = False
        self.stored_decision: dict[str, Any] | None = None

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.profile == "good":
            return self._with_profile_telemetry(self.auto(payload))
        if self.profile == "noop":
            return self._response(None)
        if self.profile == "early":
            return self._early(payload)
        if self.profile == "late":
            return self._late(payload)
        if self.profile == "wrong_payload":
            return self._wrong_payload(payload)
        if self.profile == "repeat":
            return self._repeat(payload)
        if self.profile == "wrong_api":
            return self._wrong_api(payload)
        if self.profile == "unsafe_control":
            return self._unsafe_control(payload)
        raise AssertionError(f"unhandled profile: {self.profile}")

    def _early(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        target = self._target_expectation(payload)
        step_id = _step_id(payload)
        if target is not None:
            target_step = int(target["step_id"])
            if step_id >= target_step:
                return self._response(None)
            if step_id != max(1, target_step - 1):
                return self._response(None)
            decision = self._planned_decision(payload, preferred_type=str(target["action_type"]))
        else:
            decision = self._first_available_decision(payload)
        return self._send_once(decision)

    def _late(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        target = self._target_expectation(payload)
        if target is None:
            return self._response(None)
        if _step_id(payload) != int(target["step_id"]) + 1:
            return self._response(None)
        return self._send_once(self._planned_decision(payload, preferred_type=str(target["action_type"])))

    def _wrong_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        target = self._target_expectation(payload)
        if target is None or _step_id(payload) != int(target["step_id"]):
            return self._response(None)
        action_type = str(target["action_type"])
        decision = self._planned_decision(payload, preferred_type=action_type)
        if decision is None:
            return self._response(None)
        task = self._task(payload)
        mutated = _mutate_expected_field(decision, task, int(target["step_id"]), action_type)
        return self._send_once(mutated)

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
        target = self._target_expectation(payload)
        if target is None or _step_id(payload) != int(target["step_id"]):
            return self._response(None)
        wrong_type = _wrong_action_type(str(target["action_type"]), _actions(payload))
        if wrong_type is None:
            return self._response(None)
        return self._send_once(self._planned_decision(payload, preferred_type=wrong_type))

    def _unsafe_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sent:
            return self._response(None)
        return self._send_once(self._first_available_decision(payload))

    def _send_once(self, decision: dict[str, Any] | None) -> dict[str, Any]:
        if decision is None:
            return self._response(None)
        self.sent = True
        return self._response(decision)

    def _target_expectation(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return _target_expectation(self._task(payload))

    def _planned_decision(self, payload: dict[str, Any], preferred_type: str | None = None) -> dict[str, Any] | None:
        task_id = str(payload["task"]["task_id"])
        evidence = payload["observation"].get("evidence", {})
        actions = _actions(payload)
        action_type = preferred_type if preferred_type in actions else self.planner._select_action_type(task_id, actions, evidence, payload)
        if action_type is None or action_type == RanActionType.NO_ACTION.value:
            return None
        return self.planner._build_decision(action_type, evidence, payload)

    def _first_available_decision(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        for action_type in _actions(payload):
            if action_type != RanActionType.NO_ACTION.value:
                return self._planned_decision(payload, preferred_type=action_type)
        return None

    def _task(self, payload: dict[str, Any]) -> PrivateTask:
        task_id = str(payload["task"]["task_id"])
        try:
            return self.task_cache[task_id]
        except KeyError as exc:
            raise KeyError(f"task {task_id!r} missing from emulated profile task cache") from exc

    def _with_profile_telemetry(self, response: dict[str, Any]) -> dict[str, Any]:
        payload = dict(response)
        payload["telemetry"] = dict(PROFILE_TELEMETRY[self.profile])
        return payload

    def _response(self, decision: dict[str, Any] | None) -> dict[str, Any]:
        return {"decision": decision, "telemetry": dict(PROFILE_TELEMETRY[self.profile])}


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
        RanActionType.SET_PRB_POLICY_RATIO_WS.value: RanActionType.SET_SSB_BLOCK_POWER_WS.value,
        RanActionType.SET_PRB_POLICY_RATIO_CCC.value: RanActionType.SET_PRB_POLICY_RATIO_WS.value,
        RanActionType.SET_SSB_BLOCK_POWER_WS.value: RanActionType.SET_PRB_POLICY_RATIO_WS.value,
        RanActionType.SET_CFO_CLI.value: RanActionType.SET_TX_TIME_OFFSET_CLI.value,
        RanActionType.SET_TX_TIME_OFFSET_CLI.value: RanActionType.SET_CFO_CLI.value,
        RanActionType.TRIGGER_HANDOVER_CLI.value: RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value,
        RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value: RanActionType.TRIGGER_HANDOVER_CLI.value,
        RanActionType.RESTART_CORE_NF.value: RanActionType.SET_PRB_POLICY_RATIO_WS.value,
    }
    preferred = paired_alternatives.get(expected_type)
    if preferred in actions:
        return preferred
    for action_type in actions:
        if action_type not in {expected_type, RanActionType.NO_ACTION.value}:
            return action_type
    return None


def _mutate_expected_field(decision: dict[str, Any], task: PrivateTask, step_id: int, action_type: str) -> dict[str, Any]:
    mutated = dict(decision)
    fields = _expected_fields(task, step_id, action_type)
    for field in fields:
        if field in mutated:
            _mutate_field(mutated, field)
            return mutated
    for field in ("min_prb_policy_ratio", "max_prb_policy_ratio", "ssb_block_power_dbm", "nci", "cfo_hz", "tx_time_offset_us", "target_pci", "target_pcis", "nf", "supi", "plmn", "dnn", "auth_profile_id"):
        if field in mutated:
            _mutate_field(mutated, field)
            return mutated
    return mutated


def _expected_fields(task: PrivateTask, step_id: int, action_type: str) -> tuple[str, ...]:
    for expectation in task.J.get("expected_action_fields", []):
        if expectation.get("step_id") == step_id and expectation.get("action_type") == action_type:
            fields = expectation.get("fields", {})
            if isinstance(fields, dict):
                return tuple(str(field) for field in fields)
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
    elif field in {"ssb_block_power_dbm", "nci", "target_pci"}:
        payload[field] = int(value) + 1
    elif field == "target_pcis":
        target_pcis = list(value)
        target_pcis[0] = int(target_pcis[0]) + 1
        payload[field] = target_pcis
    elif field in {"cfo_hz", "tx_time_offset_us"}:
        payload[field] = float(value) + 1.0
    elif field == "nf":
        payload[field] = "amf" if value != "amf" else "smf"
    elif isinstance(value, int) and not isinstance(value, bool):
        payload[field] = value + 1
    elif isinstance(value, float):
        payload[field] = value + 1.0
    else:
        payload[field] = f"{value}_wrong"


def _actions(payload: dict[str, Any]) -> list[str]:
    return list(payload["task"]["api_projection"].get("action_types", []))


def _step_id(payload: dict[str, Any]) -> int:
    return int(payload["observation"]["step_id"])
