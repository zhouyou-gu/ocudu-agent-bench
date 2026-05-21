"""Deterministic closed-loop state transitions for the simulated OCUDU adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.runtime_setup import RuntimeHandle, SIMULATED_ADAPTER, core_ue_registration_state
from benchmark.benchmark_api.types import RanActionType, SafeErrorClass


@dataclass(frozen=True)
class SimulatedActionResult:
    accepted: bool
    safe_error_class: SafeErrorClass | None
    safe_message: str


def apply_simulated_action(
    runtime: RuntimeHandle,
    *,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    if runtime.runtime_adapter != SIMULATED_ADAPTER:
        result = _reject("simulated transition layer is only available for simulated_ocudu")
    elif action_type in {
        RanActionType.SET_PRB_POLICY_RATIO_WS,
        RanActionType.SET_PRB_POLICY_RATIO_CCC,
        RanActionType.SET_PRB_POLICY_RATIO_RC_DU,
    }:
        result = _apply_prb_policy(runtime, action_id, action_type, action, backend)
    elif action_type == RanActionType.SET_SSB_BLOCK_POWER_WS:
        result = _apply_ssb_power(runtime, action_id, action, backend)
    elif action_type == RanActionType.TRIGGER_HANDOVER_CLI:
        result = _apply_handover(runtime, action_id, action, backend)
    elif action_type == RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI:
        result = _apply_conditional_handover(runtime, action_id, action, backend)
    elif action_type == RanActionType.SET_CFO_CLI:
        result = _apply_cfo(runtime, action_id, action, backend)
    elif action_type == RanActionType.SET_TX_TIME_OFFSET_CLI:
        result = _apply_tx_time_offset(runtime, action_id, action, backend)
    elif action_type == RanActionType.RESTART_CORE_NF:
        result = _apply_core_nf_restart(runtime, action_id, action, backend)
    elif action_type == RanActionType.UPDATE_CORE_UE_REGISTRATION:
        result = _apply_core_ue_registration(runtime, action_id, action, backend)
    else:
        result = _reject(f"unsupported simulated action type: {action_type.value}")

    outcome = {
        "action_id": action_id,
        "type": action_type.value,
        "backend": backend,
        "accepted": result.accepted,
    }
    if not result.accepted:
        outcome["safe_error_class"] = result.safe_error_class.value if result.safe_error_class else None
        outcome["safe_message"] = result.safe_message
    runtime.state.setdefault("control_outcomes", []).append(outcome)
    return result


def _apply_prb_policy(
    runtime: RuntimeHandle,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    if backend == "e2_control":
        e2 = runtime.state.get("e2", {})
        if not e2.get("enabled") or int(e2.get("kpm_indications", 0) or 0) <= 0:
            return _reject("E2 PRB control requires available KPM evidence")
        if e2.get("ric_xapp_running") is False:
            return _reject("E2 PRB control xApp is unavailable")

    slice_runtime = runtime.state.setdefault("slice_runtime", {})
    active_slice = dict(slice_runtime.get("active_slice", {}))
    expected_slice = {
        "plmn": str(active_slice.get("plmn", "00101")),
        "sst": int(active_slice.get("sst", 1) or 1),
        "sd": active_slice.get("sd"),
    }
    requested_slice = {
        "plmn": str(action.get("plmn", "00101")),
        "sst": int(action.get("sst", 1)),
        "sd": action.get("sd"),
    }
    if requested_slice != expected_slice:
        return _reject("PRB policy slice identifiers do not match the active slice")

    requested_policy = _prb_policy(action, backend, action_type.value, action_id)
    current_policy = slice_runtime.get("current_prb_policy")
    if runtime.state.get("last_prb_policy") and _same_policy(current_policy, requested_policy):
        return _reject("PRB policy already matches the requested simulated state")

    slice_runtime["current_prb_policy"] = requested_policy
    runtime.state["last_prb_policy"] = dict(action)
    return _accept("simulated PRB policy applied")


def _apply_ssb_power(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    cell = runtime.state.get("cell_identity", {})
    if action.get("plmn", "00101") != cell.get("plmn", "00101") or action.get("nci") != cell.get("nci"):
        return _reject("SSB power control targets a stale or wrong cell identity")

    radio = runtime.state.setdefault("radio_runtime", {})
    if runtime.state.get("last_ssb_power") and radio.get("current_ssb_block_power_dbm") == action["ssb_block_power_dbm"]:
        return _reject("SSB block power already matches the requested simulated state")

    radio["current_ssb_block_power_dbm"] = action["ssb_block_power_dbm"]
    radio["ssb_power_cell"] = {"plmn": action.get("plmn", "00101"), "nci": action["nci"]}
    radio["last_ssb_action_id"] = action_id
    runtime.state["last_ssb_power"] = dict(action)
    return _accept("simulated SSB block power applied")


def _apply_handover(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    identity = runtime.state.setdefault("ue_identity", {})
    if action.get("serving_pci") != identity.get("serving_pci"):
        return _reject("handover serving PCI does not match the current UE serving PCI")
    if action.get("rnti") != identity.get("rnti"):
        return _reject("handover RNTI does not match the current UE RNTI")
    if action.get("target_pci") not in set(identity.get("target_pcis") or []):
        return _reject("handover target PCI is not in the current candidate set")

    identity["previous_serving_pci"] = identity.get("serving_pci")
    identity["serving_pci"] = action["target_pci"]
    identity["last_handover_action_id"] = action_id
    runtime.state["last_handover"] = dict(action)
    return _accept("simulated handover applied")


def _apply_conditional_handover(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    identity = runtime.state.setdefault("ue_identity", {})
    if action.get("serving_pci") != identity.get("serving_pci"):
        return _reject("conditional handover serving PCI does not match the current UE serving PCI")
    if action.get("rnti") != identity.get("rnti"):
        return _reject("conditional handover RNTI does not match the current UE RNTI")
    current_targets = list(identity.get("target_pcis") or [])
    requested_targets = list(action.get("target_pcis") or [])
    if not requested_targets or not set(requested_targets).issubset(set(current_targets)):
        return _reject("conditional handover targets are not in the current candidate set")
    last = runtime.state.get("last_conditional_handover")
    if last and list(last.get("target_pcis", [])) == requested_targets and last.get("serving_pci") == action.get("serving_pci"):
        return _reject("conditional handover plan already matches the requested simulated state")

    identity["conditional_handover_plan"] = {
        "serving_pci": action["serving_pci"],
        "target_pcis": requested_targets,
        "timeout_s": action.get("timeout_s"),
        "action_id": action_id,
    }
    runtime.state["last_conditional_handover"] = dict(action)
    return _accept("simulated conditional handover plan applied")


def _apply_cfo(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    radio = runtime.state.setdefault("radio_runtime", {})
    if action.get("sector_id") != radio.get("sector_id", 0):
        return _reject("CFO control sector does not match the current radio sector")
    if radio.get("last_cfo_action_id") and radio.get("cfo_hz") == action["cfo_hz"]:
        return _reject("CFO compensation already matches the requested simulated state")

    radio["sector_id"] = action["sector_id"]
    radio["cfo_hz"] = action["cfo_hz"]
    radio["cfo_corrected"] = action["cfo_hz"] == radio.get("target_cfo_hz")
    radio["last_cfo_action_id"] = action_id
    return _accept("simulated CFO compensation applied")


def _apply_tx_time_offset(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    radio = runtime.state.setdefault("radio_runtime", {})
    if action.get("sector_id") != radio.get("sector_id", 0):
        return _reject("TX time-offset control sector does not match the current radio sector")
    if radio.get("last_tx_time_offset_action_id") and radio.get("tx_time_offset_us") == action["tx_time_offset_us"]:
        return _reject("TX time offset already matches the requested simulated state")

    radio["sector_id"] = action["sector_id"]
    radio["tx_time_offset_us"] = action["tx_time_offset_us"]
    radio["tx_time_offset_corrected"] = action["tx_time_offset_us"] == radio.get("target_tx_time_offset_us")
    radio["last_tx_time_offset_action_id"] = action_id
    return _accept("simulated TX time offset applied")


def _apply_core_nf_restart(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    core = runtime.state.setdefault("core_runtime", {})
    nf = action["nf"]
    if nf not in set(core.get("available_nfs", [])):
        return _reject("core NF is not available in the simulated runtime")
    if core.get("last_restarted_nf") == nf and core.get("degraded_nf") in {None, ""}:
        return _reject("core NF already restarted and has no active simulated degradation")

    restart_counts = dict(core.get("restart_counts", {}))
    restart_counts[nf] = int(restart_counts.get(nf, 0) or 0) + 1
    nf_status = dict(core.get("nf_status", {}))
    nf_status[nf] = "restarted"
    core["running"] = True
    core["last_restarted_nf"] = nf
    core["last_restart_action_id"] = action_id
    core["restart_counts"] = restart_counts
    core["nf_status"] = nf_status
    if core.get("degraded_nf") == nf:
        core["degraded_nf"] = None
    return _accept("simulated core NF restart applied")


def _apply_core_ue_registration(
    runtime: RuntimeHandle,
    action_id: str,
    action: dict[str, Any],
    backend: str,
) -> SimulatedActionResult:
    core = runtime.state.setdefault("core_runtime", {})
    previous = core.get("ue_registration", {})
    desired = previous.get("desired") or {}
    fields = ("ue_id", "supi", "plmn", "dnn", "sst", "sd", "auth_profile_id")
    if desired and any(action.get(field) != desired.get(field) for field in fields):
        return _reject("UE registration update does not match the desired benchmark profile")
    if core.get("last_ue_registration_update") == action.get("ue_id") and previous.get("status") == "registered":
        current = previous.get("current") or {}
        if all(action.get(field) == current.get(field) for field in fields):
            return _reject("UE registration already matches the requested simulated state")

    core["ue_registration"] = core_ue_registration_state(
        desired=previous.get("desired") or action,
        current=action,
        last_updated_by=action_id,
    )
    core["last_ue_registration_update"] = action["ue_id"]
    return _accept("simulated core UE registration updated")


def _prb_policy(action: dict[str, Any], backend: str, action_type: str, action_id: str) -> dict[str, Any]:
    return {
        "plmn": action.get("plmn", "00101"),
        "sst": action.get("sst", 1),
        "sd": action.get("sd"),
        "min_prb_policy_ratio": action["min_prb_policy_ratio"],
        "max_prb_policy_ratio": action["max_prb_policy_ratio"],
        "backend": backend,
        "action_type": action_type,
        "action_id": action_id,
    }


def _same_policy(current: Any, requested: dict[str, Any]) -> bool:
    if not isinstance(current, dict):
        return False
    fields = ("plmn", "sst", "sd", "min_prb_policy_ratio", "max_prb_policy_ratio", "backend", "action_type")
    return all(current.get(field) == requested.get(field) for field in fields)


def _accept(message: str) -> SimulatedActionResult:
    return SimulatedActionResult(accepted=True, safe_error_class=None, safe_message=message)


def _reject(message: str) -> SimulatedActionResult:
    return SimulatedActionResult(accepted=False, safe_error_class=SafeErrorClass.RUNTIME_REJECTED, safe_message=message)
