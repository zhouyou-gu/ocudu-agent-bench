"""Agent action validation and dispatch lifecycle."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.ran_api import DispatchResult, dispatch_runtime_action
from benchmark.benchmark_api.runtime_setup import RuntimeHandle
from benchmark.benchmark_api.task_definition import PrivateTask
from benchmark.benchmark_api.types import RanActionType, SafeErrorClass


@dataclass(frozen=True)
class ActionRecord:
    step_id: int
    action_id: str
    received_at_s: float
    action: dict[str, Any]
    valid: bool
    safe_error_class: SafeErrorClass | None
    safe_message: str
    dispatch: DispatchResult | None = None
    telemetry: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action_id": self.action_id,
            "received_at_s": self.received_at_s,
            "action": _agent_action_public(self.action),
            "valid": self.valid,
            "safe_error_class": self.safe_error_class.value if self.safe_error_class else None,
            "safe_message": self.safe_message,
            "dispatch": self.dispatch.public_dict() if self.dispatch else None,
            "telemetry": _safe_telemetry(self.telemetry),
        }


def handle_agent_decision(
    task: PrivateTask,
    runtime: RuntimeHandle,
    step_id: int,
    decision: Any,
    telemetry: dict[str, Any] | None = None,
) -> ActionRecord:
    action_id = f"a{step_id:04d}"
    received_at_s = time.time()
    validation = validate_action(task, decision)
    action = validation["action"]
    if not validation["valid"]:
        return ActionRecord(
            step_id=step_id,
            action_id=action_id,
            received_at_s=received_at_s,
            action=action,
            valid=False,
            safe_error_class=validation["safe_error_class"],
            safe_message=validation["safe_message"],
            telemetry=telemetry,
        )
    dispatch = dispatch_runtime_action(runtime, action_id, action)
    return ActionRecord(
        step_id=step_id,
        action_id=action_id,
        received_at_s=received_at_s,
        action=action,
        valid=True,
        safe_error_class=None,
        safe_message="action validated",
        dispatch=dispatch,
        telemetry=telemetry,
    )


def validate_action(task: PrivateTask, decision: Any) -> dict[str, Any]:
    if decision is None:
        if not task.allow_no_action:
            return _invalid({"type": RanActionType.NO_ACTION.value}, SafeErrorClass.PERMISSION_ERROR, "no-action is not allowed")
        return _valid({"type": RanActionType.NO_ACTION.value})
    if not isinstance(decision, dict):
        return _invalid({"type": "MALFORMED"}, SafeErrorClass.SCHEMA_ERROR, "decision must be an object or null")
    action = dict(decision)
    action_type_value = action.get("type")
    if not isinstance(action_type_value, str):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "action.type is required")
    try:
        action_type = RanActionType(action_type_value)
    except ValueError:
        return _invalid(action, SafeErrorClass.PERMISSION_ERROR, "action.type is not selected for this task")
    if action_type == RanActionType.NO_ACTION:
        if len(action) > 1:
            return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "NO_ACTION must not carry payload fields")
        if not task.allow_no_action:
            return _invalid(action, SafeErrorClass.PERMISSION_ERROR, "no-action is not allowed")
        return _valid({"type": RanActionType.NO_ACTION.value})
    if action_type.value not in set(task.allowed_actions):
        return _invalid(action, SafeErrorClass.PERMISSION_ERROR, "action.type is not selected for this task")
    if action_type == RanActionType.SET_SSB_BLOCK_POWER_WS:
        return _validate_ssb(action)
    return _validate_prb(action, action_type)


def _validate_prb(action: dict[str, Any], action_type: RanActionType) -> dict[str, Any]:
    for field in ("min_prb_policy_ratio", "max_prb_policy_ratio"):
        if not _is_int(action.get(field)):
            return _invalid(action, SafeErrorClass.SCHEMA_ERROR, f"{field} must be an integer")
        if not 0 <= int(action[field]) <= 100:
            return _invalid(action, SafeErrorClass.SCHEMA_ERROR, f"{field} must be in [0, 100]")
    if int(action["min_prb_policy_ratio"]) > int(action["max_prb_policy_ratio"]):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "min_prb_policy_ratio must be <= max_prb_policy_ratio")
    plmn = action.get("plmn", "00101")
    if not isinstance(plmn, str) or not plmn:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "plmn must be a non-empty string")
    sst = action.get("sst", 1)
    if not _is_int(sst) or not 0 <= int(sst) <= 255:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "sst must be an integer in [0, 255]")
    sd = action.get("sd")
    if sd is not None and (not _is_int(sd) or not 0 <= int(sd) <= 0xFFFFFF):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "sd must be an integer in [0, 16777215]")
    dedicated_ratio = action.get("dedicated_ratio")
    if dedicated_ratio is not None and (not _is_int(dedicated_ratio) or not 0 <= int(dedicated_ratio) <= 100):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "dedicated_ratio must be an integer in [0, 100]")
    normalized = {
        "type": action_type.value,
        "plmn": plmn,
        "sst": int(sst),
        "sd": None if sd is None else int(sd),
        "dedicated_ratio": None if dedicated_ratio is None else int(dedicated_ratio),
        "min_prb_policy_ratio": int(action["min_prb_policy_ratio"]),
        "max_prb_policy_ratio": int(action["max_prb_policy_ratio"]),
    }
    if action_type == RanActionType.SET_PRB_POLICY_RATIO_RC_DU:
        du_ue_id = action.get("du_ue_id")
        if du_ue_id is not None and (not _is_int(du_ue_id) or int(du_ue_id) < 0):
            return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "du_ue_id must be a non-negative integer")
        normalized["du_ue_id"] = None if du_ue_id is None else int(du_ue_id)
    return _valid(normalized)


def _validate_ssb(action: dict[str, Any]) -> dict[str, Any]:
    if not _is_int(action.get("nci")):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "nci must be an integer")
    if not 0 <= int(action["nci"]) <= 68719476735:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "nci must be in [0, 68719476735]")
    if not _is_int(action.get("ssb_block_power_dbm")):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "ssb_block_power_dbm must be an integer")
    if not -60 <= int(action["ssb_block_power_dbm"]) <= 50:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "ssb_block_power_dbm must be in [-60, 50]")
    return _valid(
        {
            "type": RanActionType.SET_SSB_BLOCK_POWER_WS.value,
            "plmn": str(action.get("plmn", "00101")),
            "nci": int(action["nci"]),
            "ssb_block_power_dbm": int(action["ssb_block_power_dbm"]),
        }
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid(action: dict[str, Any]) -> dict[str, Any]:
    return {"valid": True, "action": action, "safe_error_class": None, "safe_message": "ok"}


def _invalid(action: dict[str, Any], safe_error_class: SafeErrorClass, safe_message: str) -> dict[str, Any]:
    return {"valid": False, "action": action, "safe_error_class": safe_error_class, "safe_message": safe_message}


def _agent_action_public(action: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in action.items() if key != "private_request"}


def _safe_telemetry(telemetry: dict[str, Any] | None) -> dict[str, Any] | None:
    if telemetry is None:
        return None
    allowed = {
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "decision_latency_s",
        "model",
        "provider",
        "timed_out",
        "malformed",
    }
    return {key: telemetry[key] for key in allowed if key in telemetry}
