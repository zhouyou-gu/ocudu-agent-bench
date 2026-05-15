"""Agent-facing benchmark environment API for the v1 skeleton."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.conformance import parse_checks, run_conformance
from benchmark.benchmark_api.config import RemoteConfig, parse_config
from benchmark.benchmark_api.remote import RemoteManager


class BenchmarkEnv:
    """Minimal local orchestrator facade for future scored benchmark episodes."""

    def __init__(self, config_path: str | Path = ".config", remote_manager_factory: Any = RemoteManager) -> None:
        self.config_path = Path(config_path)
        self.remote_manager_factory = remote_manager_factory
        self.remote_config: RemoteConfig | None = None
        self.remote: RemoteManager | None = None
        self.run_id: str | None = None
        self.state = "new"
        self.actions: list[dict[str, Any]] = []
        self.started_at: float | None = None
        self.closed_at: float | None = None
        self.adapters: dict[str, str] = {}

    def reset(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config or {}
        if not isinstance(config, dict):
            raise ValueError("reset config must be a dictionary")
        self.remote_config = parse_config(self.config_path)
        self.run_id = str(config.get("run_id") or f"v1-{int(time.time())}")
        self.remote = self.remote_manager_factory(self.remote_config)
        self.actions = []
        self.started_at = time.time()
        self.closed_at = None
        self.adapters = {
            "ssh": "ready",
            "websocket": "stub",
            "json_metrics": "stub",
            "e2_kpm": "stub",
            "e2_control": "stub",
            "zmq": "stub",
            "pcap_log": "stub",
        }

        check_remote = bool(config.get("check_remote", True))
        init_remote_workspace = bool(config.get("init_remote_workspace", True))
        create_remote_metadata = bool(config.get("create_remote_metadata", True))
        remote_check: dict[str, Any] | None = None
        workspace_init: dict[str, Any] | None = None
        run_metadata: dict[str, Any] | None = None
        conformance_result: dict[str, Any] | None = None

        if check_remote:
            remote_check = self.remote.check()
            if remote_check.get("status") != "ok":
                self.state = "error"
                return {
                    "status": "error",
                    "stage": "v1_stub",
                    "run_id": self.run_id,
                    "state": self.state,
                    "reason": "remote check failed",
                    "remote_check": remote_check,
                }

            remote_state = remote_check.get("remote", {})
            workspace_ready = remote_state.get("workspace_exists") and remote_state.get("workspace_is_dir")
            if init_remote_workspace and not workspace_ready:
                workspace_init = self.remote.init_workspace()
                if workspace_init.get("status") != "ok":
                    self.state = "error"
                    return {
                        "status": "error",
                        "stage": "v1_stub",
                        "run_id": self.run_id,
                        "state": self.state,
                        "reason": "remote workspace init failed",
                        "remote_check": remote_check,
                        "workspace_init": workspace_init,
                    }

            if create_remote_metadata:
                metadata = {
                    "run_id": self.run_id,
                    "stage": "v1_stub",
                    "scored": False,
                    "created_at": self.started_at,
                    "remote": {
                        "ssh": self.remote_config.ssh_target,
                        "ocudu_root": self.remote_config.ocudu_root,
                        "workspace": self.remote_config.workspace,
                    },
                    "config": config,
                }
                run_metadata = self.remote.create_run_metadata(self.run_id, metadata)
                if run_metadata.get("status") != "ok":
                    self.state = "error"
                    return {
                        "status": "error",
                        "stage": "v1_stub",
                        "run_id": self.run_id,
                        "state": self.state,
                        "reason": "remote run metadata creation failed",
                        "remote_check": remote_check,
                        "workspace_init": workspace_init,
                        "run_metadata": run_metadata,
                    }

        conformance_mode = str(config.get("conformance", "skip"))
        if conformance_mode not in {"skip", "observe", "required"}:
            raise ValueError("conformance must be one of: skip, observe, required")
        if conformance_mode in {"observe", "required"}:
            check_value = config.get("conformance_checks")
            if isinstance(check_value, str) or check_value is None:
                checks = parse_checks(check_value)
            elif isinstance(check_value, list):
                checks = {str(item) for item in check_value}
            else:
                raise ValueError("conformance_checks must be a comma-separated string or a list")
            conformance_result = run_conformance(
                remote=self.remote,
                repo_root=Path(__file__).resolve().parents[2],
                specs_path=Path(__file__).resolve().parents[1] / "conformance" / "tests.json",
                run_id=self.run_id,
                checks=checks,
                ws_port=int(config.get("ws_port", 8001)),
                launch_timeout=int(config.get("launch_timeout", 20)),
                probe_timeout=int(config.get("probe_timeout", 10)),
            )
            backend_enablement = conformance_result.get("backend_enablement", {})
            for backend, enabled in backend_enablement.items():
                self.adapters[backend] = "ready" if enabled else "disabled"
            if conformance_mode == "required" and conformance_result.get("status") != "pass":
                self.state = "error"
                return {
                    "status": "error",
                    "stage": "v1_stub",
                    "run_id": self.run_id,
                    "state": self.state,
                    "reason": "required conformance failed",
                    "remote_check": remote_check,
                    "workspace_init": workspace_init,
                    "run_metadata": run_metadata,
                    "conformance": conformance_result,
                }

        self.state = "ready"
        return {
            "status": "ok",
            "stage": "v1_stub",
            "run_id": self.run_id,
            "state": self.state,
            "scored": False,
            "remote_check": remote_check,
            "workspace_init": workspace_init,
            "run_metadata": run_metadata,
            "conformance": conformance_result,
            "adapters": self.adapters,
            "remote": {
                "ssh": self.remote_config.ssh_target,
                "ocudu_root": self.remote_config.ocudu_root,
                "workspace": self.remote_config.workspace,
            },
        }

    def observe(self) -> dict[str, Any]:
        if self.run_id is None or self.state == "new":
            return {
                "status": "error",
                "stage": "v1_stub",
                "run_id": self.run_id,
                "state": self.state,
                "reason": "reset required before observe",
            }
        return {
            "status": "ok",
            "stage": "v1_stub",
            "run_id": self.run_id,
            "state": "not_running" if self.state in {"new", "ready", "closed"} else self.state,
            "observation": {
                "type": "stub",
                "message": "v1 skeleton does not launch OCUDU or collect live metrics",
            },
        }

    def act(self, action: Any) -> dict[str, Any]:
        if self.run_id is None or self.state == "new":
            return {
                "status": "rejected",
                "stage": "v1_stub",
                "run_id": self.run_id,
                "accepted": False,
                "reason": "reset required before act",
            }
        if not isinstance(action, dict):
            return {
                "status": "rejected",
                "stage": "v1_stub",
                "run_id": self.run_id,
                "accepted": False,
                "reason": "action must be a dictionary",
            }
        accepted = action.get("type") == "STUB_NOOP" and action.get("stub") is True
        record = {
            "action": action,
            "accepted": accepted,
            "reason": "accepted stub action" if accepted else "v1 skeleton rejects runtime actions",
            "timestamp": time.time(),
        }
        self.actions.append(record)
        return {
            "status": "ok" if accepted else "rejected",
            "stage": "v1_stub",
            "run_id": self.run_id,
            "accepted": accepted,
            "reason": record["reason"],
        }

    def close(self) -> dict[str, Any]:
        self.closed_at = time.time()
        self.state = "closed"
        return {
            "status": "ok",
            "stage": "v1_stub",
            "run_id": self.run_id,
            "state": self.state,
            "scored": False,
            "actions": len(self.actions),
            "accepted_actions": sum(1 for action in self.actions if action["accepted"]),
        }
