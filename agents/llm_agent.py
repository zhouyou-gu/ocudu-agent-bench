"""``AgentCallable`` implementation backed by an :class:`LLMBackend`.

The benchmark agent contract is a plain ``Callable[[dict], dict]``. This module
wires one LLM round-trip per step:

1. ``benchmark/benchmark_api/agent_api_wrapper.py`` invokes the agent with a
   redacted payload (goal, observation, allowed actions, previous feedback,
   decision_timeout).
2. :func:`LLMAgent.__call__` builds the chat messages via
   ``benchmark/agents/prompt.py``, asks the backend for a completion, parses the
   model text into either an action dict or ``None`` (NO_ACTION), and returns a
   ``{"decision": ..., "telemetry": {...}}`` envelope.
3. The wrapper enforces the decision deadline. If the backend takes too long
   the wrapper auto-converts the step into NO_ACTION; the failure is captured
   as ``telemetry.timed_out``.

The agent itself never raises on backend errors — it returns NO_ACTION with a
``parse_status`` / ``error`` telemetry field so the run continues to completion
and scoring sees the failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchmark.agents.backends import LLMBackend, LLMTransportError
from benchmark.agents.prompt import build_messages, parse_decision


@dataclass
class LLMAgent:
    backend: LLMBackend

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        messages = build_messages(payload)
        try:
            response = self.backend.complete(messages)
        except LLMTransportError as exc:
            elapsed = time.time() - started
            return {
                "decision": None,
                "telemetry": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                    "decision_latency_s": elapsed,
                    "model": getattr(self.backend, "model", "unknown"),
                    "provider": getattr(self.backend, "name", "unknown"),
                    "backend_error": str(exc)[:400],
                    "parse_status": "transport_error",
                },
            }
        decision, parse_info = parse_decision(response.text)
        elapsed = time.time() - started
        telemetry: dict[str, Any] = {
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "reasoning_tokens": response.reasoning_tokens,
            "total_tokens": response.total_tokens,
            "decision_latency_s": elapsed,
            "model": response.model,
            "provider": response.provider,
            "parse_status": parse_info.get("parse_status"),
        }
        rationale = parse_info.get("rationale")
        if rationale:
            telemetry["rationale"] = rationale[:400]
        return {"decision": decision, "telemetry": telemetry}
