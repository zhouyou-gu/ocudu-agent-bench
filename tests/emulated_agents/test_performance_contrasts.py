from __future__ import annotations

import unittest
from pathlib import Path

from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.task_definition import load_all_tasks
from benchmark.tests.emulated_agents.timeline_agent import TimelineAgent


TASKS_DIR = Path("benchmark/tasks")
REPRESENTATIVE_TASKS = (
    "slice_congestion_prb_rebalance_v1",
    "stale_metrics_then_prb_v1",
    "diagnose_coverage_ssb_v1",
    "cfo_correction_v1",
    "coverage_edge_ssb_recovery_v1",
    "backhaul_ran_isolation_v1",
)


class EmulatedAgentPerformanceTests(unittest.TestCase):
    def test_good_timeline_agent_succeeds_on_every_task(self) -> None:
        tasks = load_all_tasks()

        self.assertEqual(len(tasks), 20)
        for index, task_id in enumerate(sorted(tasks), start=1):
            with self.subTest(task_id=task_id):
                result = _run(task_id, "good", seed=100 + index)

                self.assertEqual(result["summary"]["outcome"], "success")
                self.assertTrue(result["summary"]["scored"])
                self.assertTrue(result["trace"]["artifacts_finalized"])
                self.assertTrue(result["trace"]["trace_finalized"])
                raw_metrics = result["summary"]["raw_metrics"]
                if "temporal_action_sequence_match" in raw_metrics:
                    self.assertEqual(raw_metrics["temporal_action_sequence_match"], 1.0)

    def test_direct_control_scores_eager_timing_below_good(self) -> None:
        good = _run("slice_congestion_prb_rebalance_v1", "good")
        eager = _run("slice_congestion_prb_rebalance_v1", "eager")

        self.assertEqual(good["summary"]["outcome"], "success")
        self.assertEqual(eager["summary"]["outcome"], "agent_failure")
        self.assertEqual(eager["summary"]["raw_metrics"]["temporal_action_sequence_match"], 0.0)
        self.assertGreater(_correctness(good), _correctness(eager))

    def test_evidence_gated_task_scores_stale_action_below_good(self) -> None:
        good = _run("stale_metrics_then_prb_v1", "good")
        eager = _run("stale_metrics_then_prb_v1", "eager")

        self.assertEqual(good["summary"]["outcome"], "success")
        self.assertEqual(eager["summary"]["outcome"], "agent_failure")
        self.assertEqual(eager["summary"]["raw_metrics"]["stale_action_avoidance"], 0.0)
        self.assertGreater(_correctness(good), _correctness(eager))

    def test_diagnosis_task_scores_wrong_api_below_good(self) -> None:
        good = _run("diagnose_coverage_ssb_v1", "good")
        wrong_api = _run("diagnose_coverage_ssb_v1", "wrong_api")

        self.assertEqual(good["summary"]["outcome"], "success")
        self.assertEqual(wrong_api["summary"]["outcome"], "agent_failure")
        self.assertEqual(wrong_api["summary"]["raw_metrics"]["correct_api_selection"], 0.0)
        self.assertGreater(_correctness(good), _correctness(wrong_api))

    def test_payload_precision_tasks_score_wrong_payload_below_good(self) -> None:
        for task_id in ("cfo_correction_v1", "slice_congestion_prb_rebalance_v1"):
            with self.subTest(task_id=task_id):
                good = _run(task_id, "good")
                wrong_payload = _run(task_id, "wrong_payload")

                self.assertEqual(good["summary"]["outcome"], "success")
                self.assertEqual(wrong_payload["summary"]["outcome"], "agent_failure")
                self.assertEqual(wrong_payload["summary"]["raw_metrics"]["expected_action_payload_match"], 0.0)
                self.assertGreater(_correctness(good), _correctness(wrong_payload))

    def test_no_repeat_task_scores_repeated_control_below_good(self) -> None:
        good = _run("coverage_edge_ssb_recovery_v1", "good")
        repeat = _run("coverage_edge_ssb_recovery_v1", "repeat")

        self.assertEqual(good["summary"]["outcome"], "success")
        self.assertEqual(repeat["summary"]["outcome"], "agent_failure")
        self.assertEqual(repeat["summary"]["raw_metrics"]["temporal_action_sequence_match"], 0.0)
        self.assertEqual(repeat["summary"]["raw_metrics"]["action_budget_ok"], 0.0)
        self.assertGreater(_correctness(good), _correctness(repeat))

    def test_no_action_safety_scores_noop_above_eager_control(self) -> None:
        for task_id in ("backhaul_ran_isolation_v1", "minimal_intervention_budget_v1"):
            with self.subTest(task_id=task_id):
                noop = _run(task_id, "noop")
                eager = _run(task_id, "eager")

                self.assertEqual(noop["summary"]["outcome"], "success")
                self.assertEqual(eager["summary"]["outcome"], "agent_failure")
                self.assertEqual(eager["summary"]["raw_metrics"]["unnecessary_action_avoidance"], 0.0)
                self.assertGreater(_correctness(noop), _correctness(eager))

    def test_aggregate_scores_separate_good_from_bad_profiles(self) -> None:
        profiles = ("good", "noop", "eager", "wrong_payload", "repeat", "wrong_api")
        aggregate: dict[str, dict[str, float | int]] = {}
        for profile in profiles:
            summaries = [_run(task_id, profile, seed=300 + index)["summary"] for index, task_id in enumerate(REPRESENTATIVE_TASKS)]
            aggregate[profile] = {
                "score": sum(_summary_correctness(summary) for summary in summaries) / len(summaries),
                "failures": sum(1 for summary in summaries if summary["outcome"] == "agent_failure"),
            }

        good_score = float(aggregate["good"]["score"])
        for profile in profiles:
            if profile == "good":
                self.assertEqual(aggregate[profile]["failures"], 0)
                continue
            with self.subTest(profile=profile):
                self.assertGreater(good_score, float(aggregate[profile]["score"]))
                self.assertGreaterEqual(int(aggregate[profile]["failures"]), 1)

    def test_emulated_operation_telemetry_changes_efficiency_not_just_correctness(self) -> None:
        good = _run("cfo_correction_v1", "good")
        noop = _run("cfo_correction_v1", "noop")

        self.assertEqual(good["summary"]["outcome"], "success")
        self.assertEqual(noop["summary"]["outcome"], "agent_failure")
        self.assertNotEqual(good["summary"]["efficiency"]["total_tokens"], noop["summary"]["efficiency"]["total_tokens"])
        self.assertGreater(_correctness(good), _correctness(noop))


def _run(task_id: str, profile: str, seed: int = 200) -> dict:
    return run_episode(
        EpisodeConfig(task_id=task_id, run_id=f"emulated-{profile}-{task_id}", seed=seed),
        TimelineAgent(profile),
    )


def _correctness(result: dict) -> float:
    return _summary_correctness(result["summary"])


def _summary_correctness(summary: dict) -> float:
    components = summary["component_scores"]
    return (components["task_correctness"] + components["action_correctness"]) / 2.0


if __name__ == "__main__":
    unittest.main()
