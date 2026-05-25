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
LIVE_CORE_ADAPTER = "live_core"
LIVE_OCUDU_ADAPTER = "live_ocudu"
LIVE_E2_ADAPTER = "live_e2"
KNOWN_ADAPTERS = (SIMULATED_ADAPTER, LIVE_CORE_ADAPTER, LIVE_OCUDU_ADAPTER, LIVE_E2_ADAPTER)


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


def _live_ocudu_config_from_setup(setup: dict[str, Any]) -> "live_ocudu.LiveOcuduConfig":
    """Build a LiveOcuduConfig from a task `setup` dict's optional `live_ocudu` block.

    Recognized keys under setup["live_ocudu"]:
      ws_url (default "ws://127.0.0.1:8001/")
      connect_timeout_s (default 5.0)
      recv_timeout_s (default 3.0)
    """
    from benchmark.benchmark_api import live_ocudu  # local import to keep module-load cheap
    block = setup.get("live_ocudu") or {}
    return live_ocudu.LiveOcuduConfig(
        ws_url=str(block.get("ws_url", "ws://127.0.0.1:8001/")),
        connect_timeout_s=float(block.get("connect_timeout_s", 5.0)),
        recv_timeout_s=float(block.get("recv_timeout_s", 3.0)),
    )


def _live_core_config_from_setup(setup: dict[str, Any]) -> "live_core.LiveCoreConfig":
    """Build a LiveCoreConfig from a task `setup` dict's optional `live_core` block.

    Recognized keys under setup["live_core"]:
      compose_project (default "open5gs")
      compose_file (default None — uses -p <project> only)
      mongo_uri (default "mongodb://127.0.0.1:27017/")
      mongo_db (default "open5gs")
      timeout_s (default 30.0)
    """
    from benchmark.benchmark_api import live_core  # local import to keep module-load cheap
    block = setup.get("live_core") or {}
    return live_core.LiveCoreConfig(
        compose_project=str(block.get("compose_project", "open5gs")),
        compose_file=block.get("compose_file"),
        mongo_uri=str(block.get("mongo_uri", "mongodb://127.0.0.1:27017/")),
        mongo_db=str(block.get("mongo_db", "open5gs")),
        timeout_s=float(block.get("timeout_s", 30.0)),
    )


def _live_e2_config_from_setup(setup: dict[str, Any]) -> "live_e2.LiveE2Config":
    """Build a LiveE2Config from a task `setup` dict's optional `live_e2` block.

    Recognized keys under setup["live_e2"]:
      container_name (default "flexric-ric")
      jsonl_path (default "/var/log/flexric/kpm.jsonl")
      subprocess_timeout_s (default 10.0)
      min_records_for_ready (default 1)
    """
    from benchmark.benchmark_api import live_e2  # local import to keep module-load cheap
    block = setup.get("live_e2") or {}
    return live_e2.LiveE2Config(
        container_name=str(block.get("container_name", "flexric-ric")),
        jsonl_path=str(block.get("jsonl_path", "/var/log/flexric/kpm.jsonl")),
        subprocess_timeout_s=float(block.get("subprocess_timeout_s", 10.0)),
        min_records_for_ready=int(block.get("min_records_for_ready", 1)),
    )


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

    # --- adapter-specific readiness gate ---
    if runtime_adapter == SIMULATED_ADAPTER:
        ready = True
        blocking_reason = None
        live_core_state = None
        live_ocudu_ready = False
    elif runtime_adapter == LIVE_CORE_ADAPTER:
        # Lazy import — live_core does subprocess + (lazy) pymongo at module load
        from benchmark.benchmark_api import live_core
        cfg = _live_core_config_from_setup(setup)
        readiness_result = live_core.readiness(cfg)
        ready = bool(readiness_result["ready"])
        blocking_reason = (
            None if ready
            else "live_core readiness failed: " + ", ".join(readiness_result.get("failures", []))
        )
        if ready:
            live_core_state = live_core.read_runtime(cfg)
        else:
            live_core_state = None
        live_ocudu_ready = False
    elif runtime_adapter == LIVE_OCUDU_ADAPTER:
        from benchmark.benchmark_api import live_ocudu
        cfg = _live_ocudu_config_from_setup(setup)
        readiness_result = live_ocudu.readiness(cfg)
        ready = bool(readiness_result["ready"])
        blocking_reason = (
            None if ready
            else "live_ocudu readiness failed: " + ", ".join(readiness_result.get("failures", []))
        )
        live_core_state = None       # live_ocudu has no core_runtime contribution
        live_ocudu_ready = ready
    elif runtime_adapter == LIVE_E2_ADAPTER:
        from benchmark.benchmark_api import live_e2
        cfg = _live_e2_config_from_setup(setup)
        readiness_result = live_e2.readiness(cfg)
        ready = bool(readiness_result["ready"])
        blocking_reason = (
            None if ready
            else "live_e2 readiness failed: " + ", ".join(readiness_result.get("failures", []))
        )
        live_core_state = None       # live_e2 has no core_runtime contribution
        live_ocudu_ready = False
    else:
        ready = False
        blocking_reason = f"runtime adapter is not available: {runtime_adapter}"
        live_core_state = None
        live_ocudu_ready = False

    # --- backend capabilities ---
    if runtime_adapter == LIVE_CORE_ADAPTER:
        backend_block = {
            "websocket": False,
            "ocudu_cli": False,
            "core_control": ready,
            "json_metrics": False,
            "e2_kpm": False,
            "e2_control": False,
        }
    elif runtime_adapter == LIVE_OCUDU_ADAPTER:
        backend_block = {
            "websocket": ready,
            "ocudu_cli": ready,
            "core_control": False,
            "json_metrics": ready,
            "e2_kpm": False,
            "e2_control": False,
        }
    elif runtime_adapter == LIVE_E2_ADAPTER:
        backend_block = {
            "websocket": False,
            "ocudu_cli": False,
            "core_control": False,
            "json_metrics": False,
            "e2_kpm": ready,
            "e2_control": False,   # CCC + RC come in a later slice (Phase 2b)
        }
    else:
        backend_block = {
            "websocket": ready and "ocudu_websocket" in components,
            "ocudu_cli": ready and "ocudu_cli" in components,
            "core_control": ready and "open5gs" in components,
            "json_metrics": ready and "ocudu_websocket" in components,
            "e2_kpm": ready and "flexric" in components and "e2_kpm_decoder" in components,
            "e2_control": ready and "flexric" in components and "e2_control_xapp" in components,
        }

    # --- core_runtime state ---
    if live_core_state is not None:
        core_runtime_state = live_core_state
    else:
        core_runtime_state = {
            "running": ready and "open5gs" in components,
            "available_nfs": ["amf", "smf", "upf", "open5gs"],
            "nf_status": {"amf": "running", "smf": "running", "upf": "running", "open5gs": "running"},
            "restart_counts": {},
            "last_restarted_nf": None,
            "ue_registration": core_ue_registration_state(last_updated_by="runtime_setup"),
        }

    state = {
        "backend": backend_block,
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
            "pathloss_db": 80.0,
            "noise_dbm": -96.0,
            "sinr_db": 20.0,
            "cqi": 10,
            "rsrp_dbm": -82.0,
            "rsrq_db": -9.0,
            "zmq_impairment": None,
        },
        "slice_runtime": {
            "active_slice": {"plmn": "00101", "sst": 1, "sd": None},
            "demand_level": "nominal",
            "active_ues": 1,
            "queue_pressure": 0.0,
            "prb_utilization": 0.35,
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
        "core_runtime": core_runtime_state,
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
            "live_ocudu": runtime_adapter == LIVE_OCUDU_ADAPTER and ready,
            "live_core": runtime_adapter == LIVE_CORE_ADAPTER and ready,
            "live_e2": runtime_adapter == LIVE_E2_ADAPTER and ready,
            "components": list(components),
            "created_at": now,
            "site_config": setup.get("site_config", "local"),
            "blocking_reason": blocking_reason,
        },
        cleanup_plan={
            "run_id": run_id,
            "steps": ["collect_artifacts", "stop_runtime"],
            "destructive": True,
        },
        ready=ready,
    )


def cleanup_runtime(runtime: RuntimeHandle) -> dict[str, Any]:
    # NOTE: the live_core adapter intentionally does NOT bring the compose
    # stack down. Operators manage the open5gs lifecycle externally; per-episode
    # teardown would be wasteful (image pulls + UPF TUN setup take ~30s).
    runtime.closed = True
    return {"status": "ok", "run_id": runtime.run_id, "cleanup_completed_at": time.time()}
