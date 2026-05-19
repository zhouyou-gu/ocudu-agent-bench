"""RAN API request building, evidence reads, and dispatch metadata."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.api_catalog import descriptor_for_action
from benchmark.benchmark_api.runtime_setup import RuntimeHandle
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


def read_evidence(runtime: RuntimeHandle, sources: tuple[str, ...]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    selected = {RanObservationSource(source) for source in sources}
    state = runtime.state
    if RanObservationSource.PING in selected:
        evidence["ping"] = dict(state.get("ping", {}))
    if RanObservationSource.JSON_METRICS in selected:
        evidence["metrics"] = dict(state.get("metrics", {}))
    if RanObservationSource.WEBSOCKET_CONTROL_OUTCOMES in selected:
        evidence["websocket_control_outcomes"] = list(state.get("control_outcomes", []))
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
        evidence["ue_identity"] = {"du_ue_id": state.get("e2", {}).get("du_ue_id")}
    evidence["backend"] = dict(state.get("backend", {}))
    return evidence


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
    outcome = {
        "action_id": action_id,
        "type": action_type.value,
        "backend": descriptor.backend.value,
        "accepted": True,
    }
    runtime.state.setdefault("control_outcomes", []).append(outcome)
    if action_type in {
        RanActionType.SET_PRB_POLICY_RATIO_WS,
        RanActionType.SET_PRB_POLICY_RATIO_CCC,
        RanActionType.SET_PRB_POLICY_RATIO_RC_DU,
    }:
        runtime.state["last_prb_policy"] = dict(action)
    elif action_type == RanActionType.SET_SSB_BLOCK_POWER_WS:
        runtime.state["last_ssb_power"] = dict(action)
    return DispatchResult(
        action_id=action_id,
        dispatched=True,
        accepted=True,
        backend=descriptor.backend.value,
        safe_error_class=None,
        safe_message="action accepted by benchmark-mediated RAN API",
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
    raise ValueError(f"Unsupported action type: {action_type.value}")
