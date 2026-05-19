import unittest

from benchmark.benchmark_api.action import handle_agent_decision, validate_action
from benchmark.benchmark_api.runtime_setup import instantiate_runtime
from benchmark.benchmark_api.task_definition import load_task


class ActionTests(unittest.TestCase):
    def test_no_action_is_valid_but_not_dispatched(self) -> None:
        task = load_task("ws_prb_ping_v1")
        runtime = instantiate_runtime(task.E, "unit")
        record = handle_agent_decision(task, runtime, step_id=1, decision=None)

        self.assertTrue(record.valid)
        self.assertIsNotNone(record.dispatch)
        self.assertFalse(record.dispatch.dispatched)
        self.assertIsNone(record.dispatch.private_request)

    def test_raw_wire_command_is_not_an_agent_action(self) -> None:
        task = load_task("ws_prb_ping_v1")
        validation = validate_action(task, {"type": "rrm_policy_ratio_set"})

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "permission_error")

    def test_prb_payload_bounds_are_checked_locally(self) -> None:
        task = load_task("ws_prb_ping_v1")
        validation = validate_action(
            task,
            {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10},
        )

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "schema_error")

    def test_optional_prb_payload_errors_are_safe_schema_errors(self) -> None:
        task = load_task("ws_prb_ping_v1")
        cases = [
            {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80, "sst": "bad"},
            {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80, "sd": -1},
            {
                "type": "SET_PRB_POLICY_RATIO_WS",
                "min_prb_policy_ratio": 20,
                "max_prb_policy_ratio": 80,
                "dedicated_ratio": 101,
            },
        ]
        for action in cases:
            with self.subTest(action=action):
                validation = validate_action(task, action)
                self.assertFalse(validation["valid"])
                self.assertEqual(validation["safe_error_class"].value, "schema_error")


if __name__ == "__main__":
    unittest.main()
