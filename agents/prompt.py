"""Prompt building and response parsing for the real-LLM agent harness.

The benchmark passes the agent a structured payload (goal, observation, allowed
action set, previous feedback, decision timeout). This module turns that payload
into a short system+user message pair and parses the model reply back into an
action dict that fits ``benchmark/benchmark_api/action.py``.

Design choices kept deliberately small:

* one system message describing role, output format, and the allowed action
  cheatsheet for this task only;
* one user message containing the current observation and the previous
  feedback as JSON;
* response parsing accepts either a bare JSON object, a JSON code block, or
  the literal ``null`` for no-action.
"""

from __future__ import annotations

import json
import re
from typing import Any

from benchmark.agents.action_schemas import cheatsheet_for


SYSTEM_PREAMBLE = """You are a control agent operating an OCUDU 5G radio testbed.
Every step you receive an `observation` describing redacted runtime evidence and the
list of action types your task is allowed to use. You must decide exactly one
control action OR explicit no-action.

Output requirements:
- Respond with a single JSON object only. No prose, no markdown, no code fences.
- The object MUST have a top-level "decision" field.
- "decision" is either `null` (meaning NO_ACTION) or an action object with a
  "type" field whose value is one of the allowed action types.
- The object MAY include "rationale" (short string) and "telemetry" (object).
  Do not include any other fields.
- Numeric fields must be plain JSON numbers. Identifiers (rnti) may be hex strings.

Action policy:
- Use NO_ACTION (`"decision": null`) when the evidence does not yet show a
  repair target, when required evidence is stale or missing, or when you have
  already repaired and any further action would be redundant.
- Read field values out of the observation evidence rather than guessing.
"""


def build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Build the [system, user] message pair for an OpenAI-style chat call."""

    task = payload.get("task", {})
    allowed_actions = list((task.get("api_projection") or {}).get("action_types", []))
    observation = payload.get("observation", {})
    previous_feedback = payload.get("previous_feedback")
    cheatsheet = cheatsheet_for(allowed_actions)

    system = SYSTEM_PREAMBLE + "\n\nAllowed action types for this task:\n"
    system += json.dumps(cheatsheet, indent=2, default=_default)
    system += "\n\nResponse template:\n"
    system += json.dumps(
        {
            "decision": {"type": "<one of the allowed action types>", "...": "..."},
            "rationale": "<short reason>",
        },
        indent=2,
    )

    user_payload = {
        "task_id": task.get("task_id"),
        "goal": task.get("goal"),
        "public_constraints": task.get("public_constraints", []),
        "observation_sources": (task.get("api_projection") or {}).get("observation_sources", []),
        "step_id": observation.get("step_id"),
        "observation": _slim_observation(observation),
        "previous_feedback": previous_feedback,
        "decision_timeout_s": payload.get("decision_timeout_s"),
    }
    user = "Current step input:\n" + json.dumps(user_payload, indent=2, default=_default)
    user += "\n\nReturn only the JSON object."

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_decision(model_text: str) -> tuple[Any, dict[str, Any]]:
    """Parse a model response into (decision, rationale_telemetry).

    The agent_api_wrapper expects either a bare action dict, ``None``, or
    ``{"decision": ..., "telemetry": {...}}``. This parser returns the decision
    object plus a small dict suitable for merging into telemetry (rationale,
    parse status).
    """

    text = (model_text or "").strip()
    parse_status = "ok"
    if not text:
        return None, {"rationale": None, "parse_status": "empty"}

    candidate = _extract_json_block(text)
    if candidate is None:
        return None, {"rationale": None, "parse_status": "no_json"}

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None, {"rationale": None, "parse_status": "invalid_json"}

    if parsed is None:
        return None, {"rationale": None, "parse_status": "ok"}

    if isinstance(parsed, dict) and "decision" in parsed:
        decision = parsed.get("decision")
        rationale = parsed.get("rationale") if isinstance(parsed.get("rationale"), str) else None
        return _normalize_decision(decision), {"rationale": rationale, "parse_status": parse_status}

    if isinstance(parsed, dict) and "type" in parsed:
        return _normalize_decision(parsed), {"rationale": None, "parse_status": parse_status}

    return None, {"rationale": None, "parse_status": "unrecognized_shape"}


def _normalize_decision(decision: Any) -> Any:
    if decision is None:
        return None
    if isinstance(decision, dict):
        action_type = decision.get("type")
        if isinstance(action_type, str) and action_type.strip().upper() == "NO_ACTION":
            return None
        return decision
    return decision


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|null)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_block(text: str) -> str | None:
    match = _JSON_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if stripped.lower() == "null":
        return "null"
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return None


def _slim_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Drop high-volume fields the model rarely needs.

    The benchmark observation is already redacted; this helper just keeps the
    prompt budget tight by removing the float-second timestamp and any future
    high-cardinality buffers. Evidence and previous-feedback remain intact.
    """

    skip = {"observation_timestamp_s"}
    return {key: value for key, value in observation.items() if key not in skip}


def _default(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
