"""Agent-facing boundary wrapper."""

from __future__ import annotations

import time
import queue
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from benchmark.benchmark_api.task_definition import AgentVisibleTask


class AgentCallable(Protocol):
    def __call__(self, payload: dict[str, Any]) -> Any:
        ...


@dataclass(frozen=True)
class AgentDecision:
    decision: Any
    telemetry: dict[str, Any] | None
    received_at_s: float
    timed_out: bool
    malformed: bool


class AgentApiWrapper:
    def __init__(self, agent: AgentCallable, agent_view: AgentVisibleTask, session_id: str, decision_timeout_s: float) -> None:
        self.agent = agent
        self.agent_view = agent_view
        self.session_id = session_id
        self.decision_timeout_s = decision_timeout_s

    def request_decision(self, observation: dict[str, Any], previous_feedback: dict[str, Any] | None) -> AgentDecision:
        emitted_at = time.time()
        payload = {
            "session_id": self.session_id,
            "task": self.agent_view.to_dict(),
            "observation": observation,
            "previous_feedback": previous_feedback,
            "decision_timeout_s": self.decision_timeout_s,
        }
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def invoke_agent() -> None:
            try:
                result_queue.put(("ok", self.agent(payload)), block=False)
            except Exception as exc:
                result_queue.put(("error", exc), block=False)

        worker = threading.Thread(target=invoke_agent, daemon=True)
        worker.start()
        worker.join(timeout=self.decision_timeout_s)
        if worker.is_alive():
            received_at = emitted_at + self.decision_timeout_s
            return AgentDecision(
                decision=None,
                telemetry={"decision_latency_s": self.decision_timeout_s, "timed_out": True},
                received_at_s=received_at,
                timed_out=True,
                malformed=False,
            )
        try:
            status, response = result_queue.get_nowait()
        except queue.Empty:
            received_at = time.time()
            return AgentDecision(
                decision=None,
                telemetry={"decision_latency_s": received_at - emitted_at, "malformed": True},
                received_at_s=received_at,
                timed_out=False,
                malformed=True,
            )
        if status == "error":
            received_at = time.time()
            return AgentDecision(
                decision=None,
                telemetry={"decision_latency_s": received_at - emitted_at, "malformed": True},
                received_at_s=received_at,
                timed_out=False,
                malformed=True,
            )
        received_at = time.time()
        if isinstance(response, dict) and "decision" in response:
            telemetry = response.get("telemetry")
            if isinstance(telemetry, dict):
                telemetry = dict(telemetry)
            else:
                telemetry = {}
            telemetry.setdefault("decision_latency_s", received_at - emitted_at)
            telemetry.setdefault("timed_out", False)
            telemetry.setdefault("malformed", False)
            return AgentDecision(
                decision=response.get("decision"),
                telemetry=telemetry,
                received_at_s=received_at,
                timed_out=False,
                malformed=False,
            )
        return AgentDecision(
            decision=response,
            telemetry={"decision_latency_s": received_at - emitted_at, "timed_out": False, "malformed": False},
            received_at_s=received_at,
            timed_out=False,
            malformed=False,
        )
