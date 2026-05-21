import json
import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.task_definition import agent_visible_task, load_all_tasks, load_task


class TaskDefinitionTests(unittest.TestCase):
    def test_current_tasks_load_in_private_contract_shape(self) -> None:
        tasks = load_all_tasks()

        self.assertEqual(len(tasks), 20)
        self.assertIn("slice_congestion_prb_rebalance_v1", tasks)
        self.assertIn("telemetry_gap_fallback_v1", tasks)
        self.assertIn("conditional_handover_planning_v1", tasks)
        self.assertIn("core_ue_registration_repair_multistep_v1", tasks)
        self.assertNotIn("ws_prb_ping_v1", tasks)
        self.assertNotIn("ran_policy_triage_v1", tasks)
        self.assertNotIn("ue_traffic_stimulus_v1", tasks)
        for task in tasks.values():
            self.assertTrue(task.G)
            self.assertIsInstance(task.E, dict)
            self.assertIsInstance(task.U, dict)
            self.assertIsInstance(task.I, dict)
            self.assertIsInstance(task.J, dict)

    def test_agent_visible_task_redacts_private_fields(self) -> None:
        view = agent_visible_task(load_task("slice_congestion_prb_rebalance_v1")).to_dict()
        rendered = repr(view)

        self.assertEqual(view["task_id"], "slice_congestion_prb_rebalance_v1")
        self.assertIn("goal", view)
        self.assertIn("slice_runtime", view["api_projection"]["observation_sources"])
        self.assertNotIn("'E'", rendered)
        self.assertNotIn("'U'", rendered)
        self.assertNotIn("'J'", rendered)
        self.assertNotIn("oracle_requirements", rendered)
        self.assertNotIn("stimulus", rendered.lower())

    def test_no_action_is_not_a_task_allowed_runtime_action(self) -> None:
        tasks = load_all_tasks()
        for task in tasks.values():
            self.assertNotIn("NO_ACTION", task.allowed_actions)

    def test_allowed_actions_must_be_backed_by_selected_api_kinds(self) -> None:
        manifest = {
            "id": "unit_unselected_action_v1",
            "version": 1,
            "G": "test action projection consistency",
            "E": {
                "runtime_adapter": "simulated_ocudu",
                "runtime": "ocudu_zmq_open5gs",
                "components": ["ocudu_websocket"],
            },
            "U": {
                "steps": 1,
                "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 0.01, "decision_deadline_s": 0.01},
                "events": [
                    {"kind": "metrics_staleness_mask", "phase": "pre_observation", "parameters": {"stale_until_step": 0}},
                ],
            },
            "I": {
                "api_kinds": ["ocudu_json_metrics"],
                "allowed_actions": ["SET_PRB_POLICY_RATIO_WS"],
                "observation_sources": ["json_metrics"],
                "allow_no_action": True,
            },
            "J": {
                "raw_metrics": ["temporal_action_sequence_match"],
                "critical_metrics": ["temporal_action_sequence_match"],
                "temporal_expectations": [{"step_id": 1, "action_type": "NO_ACTION"}],
            },
            "allowed_observation_context": ["task_id", "step_id", "backend"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "unit_unselected_action_v1"
            task_dir.mkdir()
            (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "allowed_actions"):
                load_task("unit_unselected_action_v1", Path(tmpdir))


if __name__ == "__main__":
    unittest.main()
