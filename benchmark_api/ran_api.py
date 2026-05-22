"""RAN API request building, evidence reads, and dispatch metadata."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.api_catalog import descriptor_for_action, validate_api_selection
from benchmark.benchmark_api.runtime_setup import RuntimeHandle
from benchmark.benchmark_api.simulated_ocudu import apply_simulated_action
from benchmark.benchmark_api.types import RanActionType, RanObservationSource, SafeErrorClass


@dataclass(frozen=True)
class DispatchResult:
    action_id: str
    dispatched: bool
    accepted: bool
    backend: str | None
    safe_error_class: SafeErrorClass | None
    safe_message: str
    private_request: dict[str, Any] | None
    completed_at_s: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "dispatched": self.dispatched,
            "accepted": self.accepted,
            "backend": self.backend,
            "safe_error_class": self.safe_error_class.value if self.safe_error_class else None,
            "safe_message": self.safe_message,
            "completed_at_s": self.completed_at_s,
        }


def read_evidence(
    runtime: RuntimeHandle,
    sources: tuple[str, ...],
    selected_api_kinds: tuple[str, ...] = (),
    observation_detail: str = "repair_targets",
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    selected = {RanObservationSource(source) for source in sources}
    selected_backend_names = {
        descriptor.backend.value
        for descriptor in validate_api_selection(list(selected_api_kinds))
    }
    state = runtime.state
    if RanObservationSource.PING in selected:
        evidence["ping"] = dict(state.get("ping", {}))
    if RanObservationSource.JSON_METRICS in selected:
        evidence["metrics"] = dict(state.get("metrics", {}))
    if RanObservationSource.WEBSOCKET_CONTROL_OUTCOMES in selected:
        evidence["websocket_control_outcomes"] = [
            item for item in state.get("control_outcomes", []) if item.get("backend") == "websocket"
        ]
    if RanObservationSource.CELL_IDENTITY in selected:
        evidence["cell_identity"] = dict(state.get("cell_identity", {}))
    if RanObservationSource.E2_KPM_V05 in selected:
        evidence["e2_kpm_v05"] = {
            "enabled": bool(state.get("e2", {}).get("enabled")),
            "kpm_indications": int(state.get("e2", {}).get("kpm_indications", 0) or 0),
            "has_prb_measurement": bool(state.get("e2", {}).get("has_prb_measurement")),
        }
    if RanObservationSource.E2_CONTROL_OUTCOME in selected:
        evidence["e2_control_outcome"] = {
            "accepted_records": [
                item for item in state.get("control_outcomes", []) if item.get("backend") == "e2_control" and item.get("accepted")
            ]
        }
    if RanObservationSource.UE_IDENTITY in selected:
        identity = dict(state.get("ue_identity", {}))
        identity.setdefault("du_ue_id", state.get("e2", {}).get("du_ue_id"))
        evidence["ue_identity"] = identity
    if RanObservationSource.UE_RUNTIME in selected:
        evidence["ue_runtime"] = dict(state.get("ue_runtime", {}))
    if RanObservationSource.CORE_RUNTIME in selected:
        evidence["core_runtime"] = _agent_core_runtime(state.get("core_runtime", {}))
    if RanObservationSource.RADIO_RUNTIME in selected:
        evidence["radio_runtime"] = _agent_radio_runtime(state.get("radio_runtime", {}), observation_detail)
    if RanObservationSource.SLICE_RUNTIME in selected:
        evidence["slice_runtime"] = _agent_slice_runtime(state.get("slice_runtime", {}), observation_detail)
    if RanObservationSource.BACKHAUL_RUNTIME in selected:
        evidence["backhaul_runtime"] = _agent_backhaul_runtime(state.get("backhaul_runtime", {}))
    evidence["backend"] = {
        backend: state.get("backend", {}).get(backend)
        for backend in sorted(selected_backend_names)
    }
    return evidence


def _agent_radio_runtime(radio_runtime: dict[str, Any], observation_detail: str = "repair_targets") -> dict[str, Any]:
    allowed = {
        "sector_id",
        "cfo_hz",
        "target_cfo_hz",
        "tx_time_offset_us",
        "target_tx_time_offset_us",
        "condition_profile",
        "pathloss_db",
        "noise_dbm",
        "sinr_db",
        "cqi",
        "rsrp_dbm",
        "rsrq_db",
        "target_ssb_block_power_dbm",
        "current_ssb_block_power_dbm",
        "ssb_power_cell",
        "cfo_corrected",
        "tx_time_offset_corrected",
    }
    if observation_detail == "diagnosis_symptoms":
        allowed -= {"target_cfo_hz", "target_tx_time_offset_us", "target_ssb_block_power_dbm"}
    public = {key: radio_runtime[key] for key in allowed if key in radio_runtime}
    impairment = radio_runtime.get("zmq_impairment")
    if isinstance(impairment, dict):
        public["zmq_impairment"] = {
            "enabled": bool(impairment.get("enabled")),
            "impairment_kind": str(impairment.get("impairment_kind", "sample_path")),
        }
    return public


def _agent_slice_runtime(slice_runtime: dict[str, Any], observation_detail: str = "repair_targets") -> dict[str, Any]:
    public = {
        "active_slice": dict(slice_runtime.get("active_slice", {})),
        "demand_level": slice_runtime.get("demand_level"),
        "active_ues": slice_runtime.get("active_ues"),
    }
    for field in ("queue_pressure", "prb_utilization"):
        if field in slice_runtime:
            public[field] = slice_runtime[field]
    target_prb_policy = slice_runtime.get("target_prb_policy")
    if observation_detail != "diagnosis_symptoms" and isinstance(target_prb_policy, dict):
        public["target_prb_policy"] = {
            "min_prb_policy_ratio": target_prb_policy.get("min_prb_policy_ratio"),
            "max_prb_policy_ratio": target_prb_policy.get("max_prb_policy_ratio"),
        }
    current_prb_policy = slice_runtime.get("current_prb_policy")
    if isinstance(current_prb_policy, dict):
        public["current_prb_policy"] = {
            "plmn": current_prb_policy.get("plmn"),
            "sst": current_prb_policy.get("sst"),
            "sd": current_prb_policy.get("sd"),
            "min_prb_policy_ratio": current_prb_policy.get("min_prb_policy_ratio"),
            "max_prb_policy_ratio": current_prb_policy.get("max_prb_policy_ratio"),
            "backend": current_prb_policy.get("backend"),
            "action_type": current_prb_policy.get("action_type"),
        }
    return public


def _agent_backhaul_runtime(backhaul_runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        key: backhaul_runtime[key]
        for key in ("delay_ms", "loss_rate", "throughput_mbps")
        if key in backhaul_runtime
    }


def _agent_core_runtime(core_runtime: dict[str, Any]) -> dict[str, Any]:
    public = dict(core_runtime)
    registration = dict(public.get("ue_registration", {}))
    registration.pop("last_updated_by", None)
    if "desired" in registration:
        registration["desired"] = dict(registration["desired"])
    if "current" in registration:
        registration["current"] = dict(registration["current"])
    if registration:
        public["ue_registration"] = registration
    return public


def dispatch_runtime_action(runtime: RuntimeHandle, action_id: str, action: dict[str, Any]) -> DispatchResult:
    action_type = RanActionType(action["type"])
    if action_type == RanActionType.NO_ACTION:
        return DispatchResult(
            action_id=action_id,
            dispatched=False,
            accepted=True,
            backend=None,
            safe_error_class=None,
            safe_message="no action selected",
            private_request=None,
            completed_at_s=time.time(),
        )
    descriptor = descriptor_for_action(action_type)
    if descriptor is None:
        return DispatchResult(
            action_id=action_id,
            dispatched=False,
            accepted=False,
            backend=None,
            safe_error_class=SafeErrorClass.PERMISSION_ERROR,
            safe_message="action type is not bound to a task-selected RAN API",
            private_request=None,
            completed_at_s=time.time(),
        )
    backend_ready = bool(runtime.state.get("backend", {}).get(descriptor.backend.value))
    if not runtime.ready or not backend_ready:
        return DispatchResult(
            action_id=action_id,
            dispatched=False,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message="selected runtime backend is unavailable",
            private_request=None,
            completed_at_s=time.time(),
        )
    request = build_request(action_type, action)
    simulated = apply_simulated_action(
        runtime,
        action_id=action_id,
        action_type=action_type,
        action=action,
        backend=descriptor.backend.value,
    )
    return DispatchResult(
        action_id=action_id,
        dispatched=True,
        accepted=simulated.accepted,
        backend=descriptor.backend.value,
        safe_error_class=simulated.safe_error_class,
        safe_message=simulated.safe_message,
        private_request=request,
        completed_at_s=time.time(),
    )


def build_request(action_type: RanActionType, action: dict[str, Any]) -> dict[str, Any]:
    if action_type in {
        RanActionType.SET_PRB_POLICY_RATIO_WS,
        RanActionType.SET_PRB_POLICY_RATIO_CCC,
        RanActionType.SET_PRB_POLICY_RATIO_RC_DU,
    }:
        request = {
            "cmd": (
                "rrm_policy_ratio_set"
                if action_type == RanActionType.SET_PRB_POLICY_RATIO_WS
                else (
                    "E2SM-CCC O-RRMPolicyRatio"
                    if action_type == RanActionType.SET_PRB_POLICY_RATIO_CCC
                    else "E2SM-RC DU style 2 action 6"
                )
            ),
            "policies": {
                "resourceType": "PRB",
                "rRMPolicyMemberList": [
                    {
                        "plmn": action.get("plmn", "00101"),
                        "sst": action.get("sst", 1),
                    }
                ],
                "min_prb_policy_ratio": action["min_prb_policy_ratio"],
                "max_prb_policy_ratio": action["max_prb_policy_ratio"],
            },
        }
        if action.get("sd") is not None:
            request["policies"]["rRMPolicyMemberList"][0]["sd"] = action["sd"]
        if action.get("dedicated_ratio") is not None:
            request["policies"]["dedicated_ratio"] = action["dedicated_ratio"]
        if action_type == RanActionType.SET_PRB_POLICY_RATIO_RC_DU and action.get("du_ue_id") is not None:
            request["du_ue_id"] = action["du_ue_id"]
        return request
    if action_type == RanActionType.SET_SSB_BLOCK_POWER_WS:
        return {
            "cmd": "ssb_set",
            "cells": [
                {
                    "plmn": action.get("plmn", "00101"),
                    "nci": action["nci"],
                    "ssb_block_power_dbm": action["ssb_block_power_dbm"],
                }
            ],
        }
    if action_type == RanActionType.TRIGGER_HANDOVER_CLI:
        return {
            "cmd": "ho",
            "argv": [action["serving_pci"], action["rnti"], action["target_pci"]],
        }
    if action_type == RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI:
        argv: list[Any] = [action["serving_pci"], action["rnti"], *action["target_pcis"]]
        if action.get("timeout_s") is not None:
            argv.extend(["timeout", action["timeout_s"]])
        return {
            "cmd": "cho",
            "argv": argv,
        }
    if action_type == RanActionType.SET_CFO_CLI:
        return {
            "cmd": "cfo",
            "argv": [action["sector_id"], action["cfo_hz"]],
        }
    if action_type == RanActionType.SET_TX_TIME_OFFSET_CLI:
        return {
            "cmd": "tx_time_offset",
            "argv": [action["sector_id"], action["tx_time_offset_us"]],
        }
    if action_type == RanActionType.RESTART_CORE_NF:
        return {
            "cmd": "benchmark_core_nf_restart",
            "nf": action["nf"],
        }
    if action_type == RanActionType.UPDATE_CORE_UE_REGISTRATION:
        return {
            "cmd": "benchmark_core_ue_registration_update",
            "ue_id": action["ue_id"],
            "registration": {
                "supi": action["supi"],
                "plmn": action["plmn"],
                "dnn": action["dnn"],
                "sst": action["sst"],
                "sd": action["sd"],
                "auth_profile_id": action["auth_profile_id"],
            },
        }
    raise ValueError(f"Unsupported action type: {action_type.value}")
