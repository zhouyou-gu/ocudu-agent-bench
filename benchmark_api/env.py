"""Agent-facing benchmark environment API for the v1 skeleton."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.conformance import parse_checks, run_conformance
from benchmark.benchmark_api.config import RemoteConfig, parse_config
from benchmark.benchmark_api.episode import (
    DEFAULT_ATTACH_TIMEOUT,
    DEFAULT_EPISODE_DURATION,
    DEFAULT_LAUNCH_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT,
    DEFAULT_WS_PORT,
    EpisodeOptions,
    EpisodeRuntime,
)
from benchmark.benchmark_api.remote import RemoteManager
from benchmark.benchmark_api.tasks import (
    V3_EPISODE_GATE_CHECKS,
    V4_EPISODE_GATE_CHECKS,
    conformance_checks_for_task,
    episode_stage_for_task,
    implemented_episode_task_ids,
)


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
        self.task: str | None = None
        self.episode_runtime: EpisodeRuntime | None = None
        self.last_observation: dict[str, Any] | None = None
        self.unscored_reason: str | None = None

    def reset(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config or {}
        if not isinstance(config, dict):
            raise ValueError("reset config must be a dictionary")
        self.remote_config = parse_config(self.config_path)
        self.task = str(config.get("task", "v1_stub"))
        episode_tasks = implemented_episode_task_ids()
        if self.task != "v1_stub" and self.task not in episode_tasks:
            supported = ", ".join(sorted(episode_tasks | {"v1_stub"}))
            raise ValueError(f"Unsupported benchmark task: {self.task}. Supported tasks: {supported}")
        self.run_id = str(config.get("run_id") or (f"ep-{int(time.time())}" if self._is_episode_task() else f"v1-{int(time.time())}"))
        self.remote = self.remote_manager_factory(self.remote_config)
        self.actions = []
        self.started_at = time.time()
        self.closed_at = None
        self.episode_runtime = None
        self.last_observation = None
        self.unscored_reason = None
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
        stage = self._episode_stage() if self._is_episode_task() else "v1_stub"

        if check_remote:
            remote_check = self.remote.check()
            if remote_check.get("status") != "ok":
                self.state = "error"
                return {
                    "status": "error",
                    "stage": stage,
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
                        "stage": stage,
                        "run_id": self.run_id,
                        "state": self.state,
                        "reason": "remote workspace init failed",
                        "remote_check": remote_check,
                        "workspace_init": workspace_init,
                    }

            if create_remote_metadata:
                metadata = {
                    "run_id": self.run_id,
                    "stage": stage,
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
                        "stage": stage,
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
            if self._is_episode_task() and check_value is None:
                checks = conformance_checks_for_task(self.task)
            elif isinstance(check_value, str) or check_value is None:
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
            if conformance_mode == "observe" and conformance_result.get("status") != "pass":
                self.unscored_reason = "conformance observe failed"
            if conformance_mode == "required" and conformance_result.get("status") != "pass":
                self.state = "error"
                return {
                    "status": "error",
                    "stage": stage,
                    "run_id": self.run_id,
                    "state": self.state,
                    "reason": "required conformance failed",
                    "remote_check": remote_check,
                    "workspace_init": workspace_init,
                    "run_metadata": run_metadata,
                    "conformance": conformance_result,
                }

        if self._is_episode_task():
            if conformance_mode == "skip":
                self.state = "error"
                return {
                    "status": "error",
                    "stage": stage,
                    "run_id": self.run_id,
                    "state": self.state,
                    "reason": f"{self.task} reset requires conformance='required' or conformance='observe'",
                    "remote_check": remote_check,
                    "workspace_init": workspace_init,
                    "run_metadata": run_metadata,
                    "conformance": conformance_result,
                }
            self.episode_runtime = EpisodeRuntime(self.remote, repo_root=Path(__file__).resolve().parents[2])
            options = EpisodeOptions(
                run_id=self.run_id,
                task=self.task,
                duration=int(config.get("duration", DEFAULT_EPISODE_DURATION)),
                ws_port=int(config.get("ws_port", DEFAULT_WS_PORT)),
                launch_timeout=int(config.get("launch_timeout", DEFAULT_LAUNCH_TIMEOUT)),
                attach_timeout=int(config.get("attach_timeout", DEFAULT_ATTACH_TIMEOUT)),
                probe_timeout=int(config.get("probe_timeout", DEFAULT_PROBE_TIMEOUT)),
            )
            try:
                start = self.episode_runtime.start(options)
                if start.get("status") != "ok":
                    self.state = "error"
                    cleanup = self._cleanup_episode_runtime()
                    summary = self._finalize_episode_runtime(
                        reason=start.get("summary", "episode start failed"),
                        cleanup_success=cleanup.get("status") == "ok",
                    )
                    return {
                        "status": "error",
                        "stage": stage,
                        "run_id": self.run_id,
                        "state": self.state,
                        "reason": start.get("summary", "episode start failed"),
                        "remote_check": remote_check,
                        "workspace_init": workspace_init,
                        "run_metadata": run_metadata,
                        "conformance": conformance_result,
                        "start": start,
                        "cleanup": cleanup,
                        "summary": summary,
                    }
                self.state = "running"
                self.last_observation = self.episode_runtime.observe()
            except Exception as exc:
                self.state = "error"
                cleanup = self._cleanup_episode_runtime()
                summary = self._finalize_episode_runtime(
                    reason=str(exc),
                    cleanup_success=cleanup.get("status") == "ok",
                )
                return {
                    "status": "error",
                    "stage": stage,
                    "run_id": self.run_id,
                    "state": self.state,
                    "reason": str(exc),
                    "remote_check": remote_check,
                    "workspace_init": workspace_init,
                    "run_metadata": run_metadata,
                    "conformance": conformance_result,
                    "cleanup": cleanup,
                    "summary": summary,
                }
            return {
                "status": "ok",
                "stage": stage,
                "task": self.task,
                "run_id": self.run_id,
                "state": self.state,
                "scored": self.unscored_reason is None,
                "unscored_reason": self.unscored_reason,
                "remote_check": remote_check,
                "workspace_init": workspace_init,
                "run_metadata": run_metadata,
                "conformance": conformance_result,
                "start": start,
                "observation": self.last_observation.get("observation"),
                "adapters": self.adapters,
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
        if self.state == "closed":
            return {
                "status": "error",
                "stage": self._episode_stage() if self._is_episode_task() else "v1_stub",
                "run_id": self.run_id,
                "state": self.state,
                "reason": "episode is closed",
            }
        if self._is_episode_task() and self.episode_runtime is not None:
            self.last_observation = self.episode_runtime.observe()
            return self.last_observation
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
        if self.state == "closed":
            return {
                "status": "rejected",
                "stage": self._episode_stage() if self._is_episode_task() else "v1_stub",
                "run_id": self.run_id,
                "accepted": False,
                "reason": "episode is closed",
            }
        if action is None and self._is_episode_task() and self.episode_runtime is not None:
            return {
                "status": "ok",
                "stage": self._episode_stage(),
                "run_id": self.run_id,
                "accepted": True,
                "action_logged": False,
                "reason": "no-op decision",
            }
        if not isinstance(action, dict):
            return {
                "status": "rejected",
                "stage": self._episode_stage() if self._is_episode_task() else "v1_stub",
                "run_id": self.run_id,
                "accepted": False,
                "reason": "action must be a dictionary",
            }
        if self._is_episode_task() and self.episode_runtime is not None:
            result = self.episode_runtime.act(action)
            self.actions.append(
                {
                    "action": action,
                    "accepted": bool(result.get("accepted")),
                    "reason": result.get("reason", ""),
                    "timestamp": time.time(),
                }
            )
            return result
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
        if self._is_episode_task() and self.episode_runtime is not None and self.run_id is not None:
            cleanup = self._cleanup_episode_runtime()
            summary = self._finalize_episode_runtime(
                reason=self.unscored_reason,
                cleanup_success=cleanup.get("status") == "ok",
            )
            self.state = "closed"
            return {
                "status": "ok" if cleanup.get("status") == "ok" else "error",
                "stage": self._episode_stage(),
                "run_id": self.run_id,
                "state": self.state,
                "cleanup": cleanup,
                "summary": summary,
            }
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

    def _cleanup_episode_runtime(self) -> dict[str, Any]:
        if self.episode_runtime is None or self.run_id is None:
            return {"status": "ok", "run_id": self.run_id, "commands": []}
        try:
            return self.episode_runtime.cleanup(self.run_id)
        except Exception as exc:
            return {"status": "error", "run_id": self.run_id, "errors": [str(exc)], "commands": []}

    def _finalize_episode_runtime(self, reason: str | None, cleanup_success: bool) -> dict[str, Any]:
        if self.episode_runtime is None:
            return {
                "status": "error",
                "stage": self._episode_stage(),
                "run_id": self.run_id,
                "scored": False,
                "unscored_reason": reason or "episode runtime unavailable",
            }
        try:
            return self.episode_runtime.finalize(unscored_reason=reason, cleanup_success=cleanup_success)
        except Exception as exc:
            return {
                "status": "error",
                "stage": self._episode_stage(),
                "run_id": self.run_id,
                "scored": False,
                "unscored_reason": reason or str(exc),
                "finalize_error": str(exc),
            }

    def _episode_stage(self) -> str:
        return episode_stage_for_task(self.task) if self._is_episode_task() else "v1_stub"

    def _is_episode_task(self) -> bool:
        return self.task in implemented_episode_task_ids() if self.task is not None else False
