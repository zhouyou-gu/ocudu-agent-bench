"""Offline smoke tests for the real-LLM agent harness.

These tests use the in-process ``EchoBackend`` so they never touch the network.
They cover:

* :class:`LLMAgent` payload → backend message construction
* response parser handling of bare JSON, fenced JSON, the literal ``null``,
  and the ``NO_ACTION`` shorthand
* :func:`run_agent_episode` against a real checked-in task with a scripted
  good action — scored summary should report ``outcome == "success"``
"""

from __future__ import annotations

import json
import unittest

from agents.backends import EchoBackend
from agents.llm_agent import LLMAgent
from agents.prompt import build_messages, parse_decision
from agents.runner import RealAgentConfig, relax_task_clock, run_agent_episode
from benchmark_api.task_catalog import load_task_for_suite


SAMPLE_PAYLOAD = {
    "session_id": "smoke-session",
    "task": {
        "task_id": "base_prb_slice_congestion_rebalance_v1",
        "goal": "Rebalance PRB policy when congestion is visible.",
        "api_projection": {
            "action_types": ["SET_PRB_POLICY_RATIO_WS", "NO_ACTION"],
            "observation_sources": ["slice_runtime", "json_metrics"],
        },
        "public_constraints": ["Use only the selected APIs."],
    },
    "observation": {
        "step_id": 2,
        "observation_timestamp_s": 1700000000.0,
        "evidence": {
            "slice_runtime": {
                "active_slice": {"plmn": "00101", "sst": 1, "sd": None},
                "demand_level": "high",
                "target_prb_policy": {"min_prb_policy_ratio": 30, "max_prb_policy_ratio": 90},
            }
        },
    },
    "previous_feedback": None,
    "decision_timeout_s": 5.0,
}


GOOD_PRB_ACTION = {
    "type": "SET_PRB_POLICY_RATIO_WS",
    "plmn": "00101",
    "sst": 1,
    "sd": None,
    "min_prb_policy_ratio": 30,
    "max_prb_policy_ratio": 90,
}


class PromptTests(unittest.TestCase):
    def test_build_messages_includes_allowed_action_cheatsheet(self) -> None:
        messages = build_messages(SAMPLE_PAYLOAD)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("SET_PRB_POLICY_RATIO_WS", messages[0]["content"])
        self.assertIn("NO_ACTION", messages[0]["content"])
        self.assertIn("Rebalance PRB policy", messages[1]["content"])
        # Private fields must never appear in the prompt the LLM sees.
        for forbidden in ("oracle", "stimulus_schedule", "future_stimulus"):
            self.assertNotIn(forbidden, messages[1]["content"])

    def test_parse_decision_accepts_bare_json(self) -> None:
        decision, info = parse_decision(json.dumps({"decision": GOOD_PRB_ACTION, "rationale": "high demand"}))

        self.assertEqual(decision, GOOD_PRB_ACTION)
        self.assertEqual(info["parse_status"], "ok")
        self.assertEqual(info["rationale"], "high demand")

    def test_parse_decision_accepts_fenced_json(self) -> None:
        text = "Here is my decision:\n```json\n" + json.dumps({"decision": None}) + "\n```\n"
        decision, info = parse_decision(text)

        self.assertIsNone(decision)
        self.assertEqual(info["parse_status"], "ok")

    def test_parse_decision_accepts_no_action_shorthand(self) -> None:
        decision, info = parse_decision(json.dumps({"decision": {"type": "NO_ACTION"}}))

        self.assertIsNone(decision)
        self.assertEqual(info["parse_status"], "ok")

    def test_parse_decision_accepts_bare_action_object(self) -> None:
        decision, _ = parse_decision(json.dumps(GOOD_PRB_ACTION))

        self.assertEqual(decision, GOOD_PRB_ACTION)

    def test_parse_decision_handles_empty_and_garbage(self) -> None:
        for text, expected_status in [("", "empty"), ("not json", "no_json"), ("{not: valid}", "invalid_json")]:
            decision, info = parse_decision(text)
            self.assertIsNone(decision)
            self.assertEqual(info["parse_status"], expected_status)


class LLMAgentTests(unittest.TestCase):
    def test_agent_returns_decision_and_telemetry(self) -> None:
        backend = EchoBackend(
            scripted_text=json.dumps({"decision": GOOD_PRB_ACTION, "rationale": "test"}),
            prompt_tokens=12,
            completion_tokens=8,
        )
        agent = LLMAgent(backend=backend)

        result = agent(SAMPLE_PAYLOAD)

        self.assertEqual(result["decision"], GOOD_PRB_ACTION)
        telemetry = result["telemetry"]
        self.assertEqual(telemetry["prompt_tokens"], 12)
        self.assertEqual(telemetry["completion_tokens"], 8)
        self.assertEqual(telemetry["total_tokens"], 20)
        self.assertEqual(telemetry["provider"], "echo")
        self.assertEqual(telemetry["parse_status"], "ok")
        self.assertEqual(telemetry["rationale"], "test")
        self.assertIn("decision_latency_s", telemetry)
        self.assertEqual(len(backend.call_log), 1)

    def test_agent_returns_no_action_on_unparseable_response(self) -> None:
        backend = EchoBackend(scripted_text="the answer is not json")
        agent = LLMAgent(backend=backend)

        result = agent(SAMPLE_PAYLOAD)

        self.assertIsNone(result["decision"])
        self.assertEqual(result["telemetry"]["parse_status"], "no_json")


class RunnerTests(unittest.TestCase):
    def test_relax_task_clock_lifts_decision_deadline(self) -> None:
        task = load_task_for_suite("base_prb_slice_congestion_rebalance_v1", suite="base")

        relaxed = relax_task_clock(task, decision_deadline_s=2.0, step_interval_padding_s=0.5)

        timing = relaxed.U["timing_policy"]
        self.assertEqual(timing["decision_deadline_s"], 2.0)
        self.assertEqual(timing["step_interval_s"], 2.5)
        # Original task is untouched.
        self.assertEqual(task.U["timing_policy"]["decision_deadline_s"], 0.01)

    def test_end_to_end_scripted_run_scores_success(self) -> None:
        task = load_task_for_suite("base_prb_slice_congestion_rebalance_v1", suite="base")
        relaxed = relax_task_clock(task, decision_deadline_s=2.0, step_interval_padding_s=0.05)
        backend = ScriptedBackend(
            responses=[
                json.dumps({"decision": None, "rationale": "no demand shift yet"}),
                json.dumps({"decision": GOOD_PRB_ACTION, "rationale": "high demand"}),
                json.dumps({"decision": None, "rationale": "already repaired"}),
            ]
        )
        agent = LLMAgent(backend=backend)
        config = RealAgentConfig(
            task_id=relaxed.task_id,
            run_id="smoke-1",
            seed=1,
            suite="base",
            decision_deadline_s=2.0,
            step_interval_padding_s=0.05,
        )

        result = run_agent_episode(relaxed, agent, config)

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertEqual(len(backend.responses), 0, "all scripted responses consumed")


class ScriptedBackend:
    """EchoBackend variant that returns a different scripted text per step."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def complete(self, messages):  # type: ignore[override]
        text = self.responses.pop(0) if self.responses else "null"
        backend = EchoBackend(scripted_text=text, model="scripted", name="scripted")
        return backend.complete(messages)


if __name__ == "__main__":
    unittest.main()
