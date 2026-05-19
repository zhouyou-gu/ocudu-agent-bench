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


def instantiate_runtime(setup: dict[str, Any], run_id: str) -> RuntimeHandle:
    components = tuple(str(item) for item in setup.get("components", ()))
    runtime = str(setup.get("runtime", "ocudu_zmq_open5gs"))
    runtime_adapter = str(setup.get("runtime_adapter", SIMULATED_ADAPTER))
    now = time.time()
    ready = runtime_adapter == SIMULATED_ADAPTER
    state = {
        "backend": {
            "websocket": ready and "ocudu_websocket" in components,
            "json_metrics": ready and "ocudu_websocket" in components,
            "e2_kpm": ready and "flexric" in components,
            "e2_control": ready and "flexric" in components,
        },
        "ping": {"packets_transmitted": 0, "packets_received": 0, "success_ratio": 0.0},
        "metrics": {"present": False, "stale": True, "sample_count": 0, "parse_errors": 0},
        "cell_identity": {"plmn": "00101", "nci": 6733824, "gnb_id": 411, "sector_id": 0},
        "e2": {
            "enabled": ready and "flexric" in components,
            "kpm_indications": 3 if ready and "flexric" in components else 0,
            "has_prb_measurement": ready and "flexric" in components,
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
