"""Agent action validation and dispatch lifecycle."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchmark_api.api_catalog import validate_api_selection
from benchmark_api.ran_api import DispatchResult, dispatch_runtime_action
from benchmark_api.runtime_setup import RuntimeHandle
from benchmark_api.task_definition import PrivateTask
from benchmark_api.types import RanActionType, SafeErrorClass


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
    selected_action_types = {
        descriptor.action_type.value
        for descriptor in validate_api_selection(list(task.selected_api_kinds))
        if descriptor.action_type is not None
    }
    if action_type.value not in set(task.allowed_actions) or action_type.value not in selected_action_types:
        return _invalid(action, SafeErrorClass.PERMISSION_ERROR, "action.type is not selected for this task")
    if action_type == RanActionType.SET_SSB_BLOCK_POWER_WS:
        return _validate_ssb(action)
    if action_type == RanActionType.TRIGGER_HANDOVER_CLI:
        return _validate_handover(action)
    if action_type == RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI:
        return _validate_conditional_handover(action)
    if action_type == RanActionType.SET_CFO_CLI:
        return _validate_cfo(action)
    if action_type == RanActionType.SET_TX_TIME_OFFSET_CLI:
        return _validate_tx_time_offset(action)
    if action_type == RanActionType.RESTART_CORE_NF:
        return _validate_core_nf_restart(action)
    if action_type == RanActionType.UPDATE_CORE_UE_REGISTRATION:
        return _validate_core_ue_registration_update(action)
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


def _validate_handover(action: dict[str, Any]) -> dict[str, Any]:
    serving_pci = _validate_pci(action, "serving_pci")
    if serving_pci is not None:
        return serving_pci
    target_pci = _validate_pci(action, "target_pci")
    if target_pci is not None:
        return target_pci
    rnti = _normalize_rnti(action.get("rnti"))
    if rnti is None:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "rnti must be a non-zero 16-bit integer or hex string")
    return _valid(
        {
            "type": RanActionType.TRIGGER_HANDOVER_CLI.value,
            "serving_pci": int(action["serving_pci"]),
            "rnti": rnti,
            "target_pci": int(action["target_pci"]),
        }
    )


def _validate_conditional_handover(action: dict[str, Any]) -> dict[str, Any]:
    serving_pci = _validate_pci(action, "serving_pci")
    if serving_pci is not None:
        return serving_pci
    rnti = _normalize_rnti(action.get("rnti"))
    if rnti is None:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "rnti must be a non-zero 16-bit integer or hex string")
    target_pcis = action.get("target_pcis")
    if not isinstance(target_pcis, list) or not 1 <= len(target_pcis) <= 8:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "target_pcis must contain 1 to 8 PCI integers")
    normalized_targets = []
    for target in target_pcis:
        if not _is_int(target) or not 0 <= int(target) <= 1007:
            return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "target_pcis must contain PCI integers in [0, 1007]")
        normalized_targets.append(int(target))
    timeout_s = action.get("timeout_s")
    if timeout_s is not None and (not _is_int(timeout_s) or int(timeout_s) <= 0):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "timeout_s must be a positive integer")
    return _valid(
        {
            "type": RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value,
            "serving_pci": int(action["serving_pci"]),
            "rnti": rnti,
            "target_pcis": normalized_targets,
            "timeout_s": None if timeout_s is None else int(timeout_s),
        }
    )


def _validate_cfo(action: dict[str, Any]) -> dict[str, Any]:
    sector_id = _validate_sector_id(action)
    if sector_id is not None:
        return sector_id
    cfo_hz = action.get("cfo_hz")
    if not _is_number(cfo_hz):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "cfo_hz must be a number")
    if not -100_000.0 <= float(cfo_hz) <= 100_000.0:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "cfo_hz must be in [-100000, 100000]")
    return _valid(
        {
            "type": RanActionType.SET_CFO_CLI.value,
            "sector_id": int(action["sector_id"]),
            "cfo_hz": float(cfo_hz),
        }
    )


def _validate_tx_time_offset(action: dict[str, Any]) -> dict[str, Any]:
    sector_id = _validate_sector_id(action)
    if sector_id is not None:
        return sector_id
    tx_time_offset_us = action.get("tx_time_offset_us")
    if not _is_number(tx_time_offset_us):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "tx_time_offset_us must be a number")
    if not -10_000.0 <= float(tx_time_offset_us) <= 10_000.0:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "tx_time_offset_us must be in [-10000, 10000]")
    return _valid(
        {
            "type": RanActionType.SET_TX_TIME_OFFSET_CLI.value,
            "sector_id": int(action["sector_id"]),
            "tx_time_offset_us": float(tx_time_offset_us),
        }
    )


def _validate_pci(action: dict[str, Any], field: str) -> dict[str, Any] | None:
    if not _is_int(action.get(field)):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, f"{field} must be an integer")
    if not 0 <= int(action[field]) <= 1007:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, f"{field} must be in [0, 1007]")
    return None


def _validate_sector_id(action: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_int(action.get("sector_id")):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "sector_id must be an integer")
    if not 0 <= int(action["sector_id"]) <= 255:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "sector_id must be in [0, 255]")
    return None


def _normalize_rnti(value: Any) -> str | None:
    if _is_int(value):
        rnti = int(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        try:
            rnti = int(text, 16 if text.startswith("0x") else 16)
        except ValueError:
            return None
    else:
        return None
    if not 1 <= rnti <= 0xFFFF:
        return None
    return f"0x{rnti:04x}"


def _validate_core_nf_restart(action: dict[str, Any]) -> dict[str, Any]:
    nf = str(action.get("nf", "")).strip().lower()
    if nf not in {"amf", "smf", "upf", "open5gs"}:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "nf must be one of amf, smf, upf, or open5gs")
    return _valid({"type": RanActionType.RESTART_CORE_NF.value, "nf": nf})


def _validate_core_ue_registration_update(action: dict[str, Any]) -> dict[str, Any]:
    ue_id = _normalize_token(action.get("ue_id"), "ue_id")
    if ue_id is None:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "ue_id must be a non-empty identifier")
    supi = str(action.get("supi", "")).strip()
    if not supi.isdigit() or not 5 <= len(supi) <= 16:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "supi must be a 5 to 16 digit IMSI/SUPI string")
    plmn = str(action.get("plmn", "")).strip()
    if not plmn.isdigit() or len(plmn) not in {5, 6}:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "plmn must be a 5 or 6 digit string")
    dnn = _normalize_dnn(action.get("dnn"))
    if dnn is None:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "dnn must be a non-empty DNN string")
    sst = action.get("sst")
    if not _is_int(sst) or not 0 <= int(sst) <= 255:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "sst must be an integer in [0, 255]")
    sd = action.get("sd")
    if sd is not None and (not _is_int(sd) or not 0 <= int(sd) <= 0xFFFFFF):
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "sd must be an integer in [0, 16777215]")
    auth_profile_id = _normalize_token(action.get("auth_profile_id"), "auth_profile_id")
    if auth_profile_id is None:
        return _invalid(action, SafeErrorClass.SCHEMA_ERROR, "auth_profile_id must be a non-empty identifier")
    return _valid(
        {
            "type": RanActionType.UPDATE_CORE_UE_REGISTRATION.value,
            "ue_id": ue_id,
            "supi": supi,
            "plmn": plmn,
            "dnn": dnn,
            "sst": int(sst),
            "sd": None if sd is None else int(sd),
            "auth_profile_id": auth_profile_id,
        }
    )


def _normalize_token(value: Any, field: str) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 64:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if any(char not in allowed for char in text):
        return None
    return text


def _normalize_dnn(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text or len(text) > 63:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-.")
    if any(char not in allowed for char in text):
        return None
    return text


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
