from __future__ import annotations

import unittest

from benchmark_api.task_catalog import load_tasks_for_suite
from tests.emulated_agents.profiles import PROFILE_NAMES
from tests.emulated_agents.scenarios import (
    EmulatedRunSpec,
    checked_in_specs,
    generated_legacy_spec,
    generated_specs,
    run_profile,
    spec_for_task,
    summary_correctness,
    trace_private_token_matches,
)


REPRESENTATIVE_SPECS = (
    spec_for_task("base_prb_slice_congestion_rebalance_v1", episode_seed=300),
    spec_for_task("base_prb_stale_metrics_then_rebalance_v1", episode_seed=301),
    spec_for_task("base_radio_cli_diagnose_cfo_vs_timing_v1", episode_seed=302),
    spec_for_task("base_radio_cli_cfo_correction_v1", episode_seed=303),
    spec_for_task("base_ssb_coverage_edge_recovery_v1", episode_seed=304),
    spec_for_task("base_isolation_backhaul_not_ran_v1", episode_seed=305),
    spec_for_task("compound_core_vs_ran_failure_v1", episode_seed=306),
    generated_legacy_spec("slice_congestion_prb_ratio_precision_v1", episode_seed=307),
)


class EmulatedAgentPerformanceTests(unittest.TestCase):
    def test_good_profile_passes_every_checked_in_task(self) -> None:
        specs = checked_in_specs()

        self.assertEqual(len(specs), 34)
        for spec in specs:
            with self.subTest(task_id=spec.task_id):
                result = run_profile(spec, "good")

                self.assert_success(result)
                self.assertEqual(trace_private_token_matches(result["trace"]), [])

    def test_good_profile_passes_generated_sample(self) -> None:
        specs = generated_specs(suite="generated", suite_seed=11, count=40)

        self.assertEqual(len(specs), 40)
        for spec in specs:
            with self.subTest(task_id=spec.task_id):
                result = run_profile(spec, "good")

                self.assert_success(result)
                self.assertEqual(trace_private_token_matches(result["trace"]), [])

    def test_good_profile_passes_compound_suite_without_cause_label_leakage(self) -> None:
        specs = [spec_for_task(task_id, episode_seed=500 + index) for index, task_id in enumerate(sorted(load_tasks_for_suite(suite="compound")), start=1)]

        self.assertEqual(len(specs), 8)
        for spec in specs:
            with self.subTest(task_id=spec.task_id):
                result = run_profile(spec, "good")

                self.assert_success(result)
                self.assertEqual(trace_private_token_matches(result["trace"]), [])

    def test_profiles_are_declared_and_have_distinct_telemetry(self) -> None:
        self.assertEqual(
            set(PROFILE_NAMES),
            {"good", "noop", "early", "late", "wrong_payload", "repeat", "wrong_api", "unsafe_control"},
        )

        good = run_profile(spec_for_task("base_radio_cli_cfo_correction_v1"), "good")
        noop = run_profile(spec_for_task("base_radio_cli_cfo_correction_v1"), "noop")

        self.assertEqual(good["summary"]["outcome"], "success")
        self.assertEqual(noop["summary"]["outcome"], "agent_failure")
        self.assertNotEqual(good["summary"]["efficiency"]["total_tokens"], noop["summary"]["efficiency"]["total_tokens"])

    def test_timing_profiles_are_scored_below_good(self) -> None:
        timing_spec = spec_for_task("base_prb_slice_congestion_rebalance_v1")
        late_spec = spec_for_task("base_prb_telemetry_gap_fallback_v1")

        good = run_profile(timing_spec, "good")
        early = run_profile(timing_spec, "early")
        late = run_profile(late_spec, "late")

        self.assert_success(good)
        self.assert_failure(early)
        self.assert_failure(late)
        self.assertEqual(early["summary"]["raw_metrics"]["temporal_action_sequence_match"], 0.0)
        self.assertEqual(late["summary"]["raw_metrics"]["temporal_action_sequence_match"], 0.0)
        self.assertGreater(summary_correctness(good["summary"]), summary_correctness(early["summary"]))

    def test_evidence_gating_profile_exposes_stale_action_failure(self) -> None:
        spec = spec_for_task("base_prb_stale_metrics_then_rebalance_v1")

        good = run_profile(spec, "good")
        early = run_profile(spec, "early")

        self.assert_success(good)
        self.assert_failure(early)
        self.assertEqual(early["summary"]["raw_metrics"]["stale_action_avoidance"], 0.0)

    def test_payload_precision_profiles_cover_base_and_generated_tasks(self) -> None:
        specs = (
            spec_for_task("base_radio_cli_cfo_correction_v1"),
            generated_legacy_spec("slice_congestion_prb_ratio_precision_v1"),
        )

        for spec in specs:
            with self.subTest(task_id=spec.task_id):
                good = run_profile(spec, "good")
                wrong_payload = run_profile(spec, "wrong_payload")

                self.assert_success(good)
                self.assert_failure(wrong_payload)
                raw = wrong_payload["summary"]["raw_metrics"]
                self.assertEqual(raw["expected_action_payload_match"], 0.0)
                if "expected_action_payload_similarity" in raw:
                    self.assertLess(raw["expected_action_payload_similarity"], 1.0)

    def test_wrong_api_profile_separates_diagnosis_backend_and_compound_tasks(self) -> None:
        specs = (
            spec_for_task("base_radio_cli_diagnose_cfo_vs_timing_v1"),
            spec_for_task("base_prb_backend_e2_vs_ws_v1"),
            spec_for_task("compound_core_vs_ran_failure_v1"),
        )

        for spec in specs:
            with self.subTest(task_id=spec.task_id):
                good = run_profile(spec, "good")
                wrong_api = run_profile(spec, "wrong_api")

                self.assert_success(good)
                self.assert_failure(wrong_api)
                raw = wrong_api["summary"]["raw_metrics"]
                if "correct_api_selection" in raw:
                    self.assertEqual(raw["correct_api_selection"], 0.0)
                else:
                    self.assertEqual(raw["expected_action_payload_match"], 0.0)

    def test_repeat_profile_is_penalized_after_successful_repair(self) -> None:
        spec = spec_for_task("base_ssb_coverage_edge_recovery_v1")

        good = run_profile(spec, "good")
        repeat = run_profile(spec, "repeat")

        self.assert_success(good)
        self.assert_failure(repeat)
        self.assertEqual(repeat["summary"]["raw_metrics"]["action_budget_ok"], 0.0)

    def test_no_action_safety_separates_noop_and_unsafe_control(self) -> None:
        for task_id in ("base_isolation_backhaul_not_ran_v1", "base_restraint_minimal_intervention_budget_v1", "compound_isolation_backhaul_not_ran_v1"):
            with self.subTest(task_id=task_id):
                spec = spec_for_task(task_id)
                noop = run_profile(spec, "noop")
                unsafe = run_profile(spec, "unsafe_control")

                self.assert_success(noop)
                self.assert_failure(unsafe)
                self.assertEqual(unsafe["summary"]["raw_metrics"]["unnecessary_action_avoidance"], 0.0)

    def test_aggregate_profiles_show_distinct_failure_patterns(self) -> None:
        aggregate: dict[str, dict[str, float | int]] = {}
        for profile in PROFILE_NAMES:
            summaries = [run_profile(spec, profile)["summary"] for spec in REPRESENTATIVE_SPECS]
            aggregate[profile] = {
                "score": sum(summary_correctness(summary) for summary in summaries) / len(summaries),
                "failures": sum(1 for summary in summaries if summary["outcome"] == "agent_failure"),
            }

        good_score = float(aggregate["good"]["score"])
        self.assertEqual(aggregate["good"]["failures"], 0)
        for profile in PROFILE_NAMES:
            if profile == "good":
                continue
            with self.subTest(profile=profile):
                self.assertGreater(good_score, float(aggregate[profile]["score"]))
                self.assertGreaterEqual(int(aggregate[profile]["failures"]), 1)

    def test_standard_generated_sample_keeps_good_above_bad_profiles(self) -> None:
        specs = generated_specs(suite="standard", suite_seed=3, count=24, episode_seed=3)
        profiles = ("good", "noop", "early", "wrong_payload", "repeat", "wrong_api", "unsafe_control")
        aggregate: dict[str, float] = {}
        failures: dict[str, int] = {}

        for profile in profiles:
            summaries = [run_profile(spec, profile)["summary"] for spec in specs]
            aggregate[profile] = sum(summary_correctness(summary) for summary in summaries) / len(summaries)
            failures[profile] = sum(1 for summary in summaries if summary["outcome"] == "agent_failure")

        self.assertEqual(failures["good"], 0)
        for profile in profiles:
            if profile == "good":
                continue
            with self.subTest(profile=profile):
                self.assertGreater(aggregate["good"], aggregate[profile])
                self.assertGreaterEqual(failures[profile], 1)

    def assert_success(self, result: dict) -> None:
        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertTrue(result["summary"]["scored"])
        self.assertTrue(result["trace"]["artifacts_finalized"])
        self.assertTrue(result["trace"]["trace_finalized"])

    def assert_failure(self, result: dict) -> None:
        self.assertEqual(result["summary"]["outcome"], "agent_failure")
        self.assertTrue(result["summary"]["scored"])


if __name__ == "__main__":
    unittest.main()
