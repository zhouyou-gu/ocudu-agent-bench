import json
import tempfile
import time
import unittest
from pathlib import Path

from benchmark.benchmark_api.episode import EpisodeConfig, run_episode


class EpisodeTests(unittest.TestCase):
    def test_episode_runs_design_order_and_scores_after_trace_finalization(self) -> None:
        sent = {"value": False}

        def agent(payload):
            if sent["value"]:
                return {"decision": None, "telemetry": {"prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}}
            sent["value"] = True
            return {
                "decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80},
                "telemetry": {"prompt_tokens": 10, "completion_tokens": 3, "reasoning_tokens": 2},
            }

        result = run_episode(EpisodeConfig(task_id="ws_prb_ping_v1", run_id="unit-episode", seed=3), agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertTrue(result["trace"]["artifacts_finalized"])
        self.assertTrue(result["trace"]["trace_finalized"])
        private_kinds = [entry["kind"] for entry in result["trace"]["private_benchmark"]]
        self.assertIn("readiness", private_kinds)
        self.assertIn("stimulus", private_kinds)
        self.assertGreater(result["summary"]["efficiency"]["total_tokens"], 0)

    def test_timeout_becomes_no_action_and_d_interval_stays_active_to_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            task_dir = tasks_dir / "unit_timeout_v1"
            task_dir.mkdir()
            manifest = json.loads(Path("benchmark/tasks/ws_prb_ping_v1/task.json").read_text(encoding="utf-8"))
            manifest["id"] = "unit_timeout_v1"
            manifest["U"]["steps"] = 1
            manifest["U"]["timing_policy"]["decision_deadline_s"] = 0.01
            manifest["U"]["timing_policy"]["step_interval_s"] = 0.02
            (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

            def slow_agent(payload):
                time.sleep(0.25)
                return {"decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}}

            started = time.time()
            result = run_episode(EpisodeConfig(task_id="unit_timeout_v1", run_id="unit-timeout", seed=1, tasks_dir=tasks_dir), slow_agent)

        self.assertLess(time.time() - started, 0.2)
        action = [entry["record"] for entry in result["trace"]["interaction"] if entry["kind"] == "action"][0]
        self.assertEqual(action["action"]["type"], "NO_ACTION")
        self.assertTrue(action["telemetry"]["timed_out"])
        in_step = [
            entry["record"]
            for entry in result["trace"]["private_benchmark"]
            if entry["kind"] == "stimulus" and entry["record"]["phase_label"] == "in_step"
        ][0]
        self.assertGreaterEqual(in_step["active_end_time_s"], in_step["active_start_time_s"] + 0.02)

    def test_action_on_stale_metrics_fails_staleness_task(self) -> None:
        sent = {"value": False}

        def acts_on_stale_once(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            return {"decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}}

        result = run_episode(EpisodeConfig(task_id="metrics_staleness_noop_v1", run_id="unit-stale", seed=1), acts_on_stale_once)

        self.assertEqual(result["summary"]["outcome"], "agent_failure")
        self.assertEqual(result["summary"]["raw_metrics"]["stale_action_avoidance"], 0.0)


if __name__ == "__main__":
    unittest.main()
