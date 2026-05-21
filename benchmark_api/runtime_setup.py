"""OCUDU runtime setup handles and cleanup planning."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeHandle:
    run_id: str
    runtime: str
    runtime_adapter: str
    components: tuple[str, ...]
    state: dict[str, Any] = field(default_factory=dict)
    setup_metadata: dict[str, Any] = field(default_factory=dict)
    cleanup_plan: dict[str, Any] = field(default_factory=dict)
    ready: bool = True
    closed: bool = False

    def apply_state(self, updates: dict[str, Any]) -> None:
        self.state.update(updates)


SIMULATED_ADAPTER = "simulated_ocudu"


def default_core_ue_registration() -> dict[str, Any]:
    return {
        "ue_id": "ue1",
        "supi": "001010000000001",
        "plmn": "00101",
        "dnn": "internet",
        "sst": 1,
        "sd": None,
        "auth_profile_id": "ue1_test_profile",
    }


def normalize_core_ue_registration(data: dict[str, Any] | None = None) -> dict[str, Any]:
    registration = default_core_ue_registration()
    for key, value in (data or {}).items():
        if key in registration:
            registration[key] = value
    registration["ue_id"] = str(registration["ue_id"])
    registration["supi"] = str(registration["supi"])
    registration["plmn"] = str(registration["plmn"])
    registration["dnn"] = str(registration["dnn"])
    registration["sst"] = int(registration["sst"])
    registration["sd"] = None if registration.get("sd") is None else int(registration["sd"])
    registration["auth_profile_id"] = str(registration["auth_profile_id"])
    return registration


def core_ue_registration_state(
    desired: dict[str, Any] | None = None,
    current: dict[str, Any] | None = None,
    *,
    last_updated_by: str,
) -> dict[str, Any]:
    desired_registration = normalize_core_ue_registration(desired)
    current_registration = normalize_core_ue_registration(current or desired_registration)
    mismatch_fields = [
        field
        for field in ("ue_id", "supi", "plmn", "dnn", "sst", "sd", "auth_profile_id")
        if desired_registration.get(field) != current_registration.get(field)
    ]
    return {
        "ue_id": desired_registration["ue_id"],
        "desired": desired_registration,
        "current": current_registration,
        "status": "mismatch" if mismatch_fields else "registered",
        "mismatch_fields": mismatch_fields,
        "last_updated_by": last_updated_by,
    }


def instantiate_runtime(setup: dict[str, Any], run_id: str) -> RuntimeHandle:
    components = tuple(str(item) for item in setup.get("components", ()))
    runtime = str(setup.get("runtime", "ocudu_zmq_open5gs"))
    runtime_adapter = str(setup.get("runtime_adapter", SIMULATED_ADAPTER))
    now = time.time()
    ready = runtime_adapter == SIMULATED_ADAPTER
    state = {
        "backend": {
            "websocket": ready and "ocudu_websocket" in components,
            "ocudu_cli": ready and "ocudu_cli" in components,
            "core_control": ready and "open5gs" in components,
            "json_metrics": ready and "ocudu_websocket" in components,
            "e2_kpm": ready and "flexric" in components and "e2_kpm_decoder" in components,
            "e2_control": ready and "flexric" in components and "e2_control_xapp" in components,
        },
        "ping": {"packets_transmitted": 0, "packets_received": 0, "success_ratio": 0.0},
        "metrics": {"present": False, "stale": True, "sample_count": 0, "parse_errors": 0},
        "cell_identity": {"plmn": "00101", "nci": 6733824, "gnb_id": 411, "sector_id": 0},
        "ue_identity": {"du_ue_id": 1, "rnti": "0x4601", "serving_pci": 1, "target_pcis": [2]},
        "ue_runtime": {
            "running": ready and "srsue_zmq" in components,
            "ue_id": "ue1",
            "traffic_active": False,
            "traffic_profile": None,
            "restart_count": 0,
        },
        "radio_runtime": {
            "sector_id": 0,
            "cfo_hz": 0.0,
            "target_cfo_hz": -1250.0,
            "tx_time_offset_us": 0.0,
            "target_tx_time_offset_us": 7.5,
            "current_ssb_block_power_dbm": -20,
            "condition_profile": None,
            "zmq_impairment": None,
        },
        "slice_runtime": {
            "active_slice": {"plmn": "00101", "sst": 1, "sd": None},
            "demand_level": "nominal",
            "active_ues": 1,
            "target_prb_policy": None,
            "current_prb_policy": {
                "plmn": "00101",
                "sst": 1,
                "sd": None,
                "min_prb_policy_ratio": 20,
                "max_prb_policy_ratio": 80,
                "backend": "bootstrap",
                "action_type": None,
            },
        },
        "backhaul_runtime": {
            "delay_ms": 0.0,
            "loss_rate": 0.0,
            "throughput_mbps": None,
        },
        "core_runtime": {
            "running": ready and "open5gs" in components,
            "available_nfs": ["amf", "smf", "upf", "open5gs"],
            "nf_status": {"amf": "running", "smf": "running", "upf": "running", "open5gs": "running"},
            "restart_counts": {},
            "last_restarted_nf": None,
            "ue_registration": core_ue_registration_state(last_updated_by="runtime_setup"),
        },
        "e2": {
            "enabled": ready and "flexric" in components,
            "kpm_indications": 3 if ready and "flexric" in components else 0,
            "has_prb_measurement": ready and "flexric" in components,
            "ric_xapp_running": ready and "flexric" in components and "e2_control_xapp" in components,
            "du_ue_id": 1,
        },
        "control_outcomes": [],
    }
    return RuntimeHandle(
        run_id=run_id,
        runtime=runtime,
        runtime_adapter=runtime_adapter,
        components=components,
        state=state,
        setup_metadata={
            "runtime": runtime,
            "runtime_adapter": runtime_adapter,
            "live_ocudu": False,
            "components": list(components),
            "created_at": now,
            "site_config": setup.get("site_config", "local"),
            "blocking_reason": None if ready else f"runtime adapter is not available: {runtime_adapter}",
        },
        cleanup_plan={
            "run_id": run_id,
            "steps": ["collect_artifacts", "stop_runtime"],
            "destructive": True,
        },
        ready=ready,
    )


def cleanup_runtime(runtime: RuntimeHandle) -> dict[str, Any]:
    runtime.closed = True
    return {"status": "ok", "run_id": runtime.run_id, "cleanup_completed_at": time.time()}
