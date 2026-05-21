import json
import tempfile
import time
import unittest
from pathlib import Path

import benchmark.benchmark_api.episode as episode_module
from benchmark.benchmark_api.controller import BaselineController
from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.trace import TraceRecorder


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

        result = run_episode(
            EpisodeConfig(task_id="slice_congestion_prb_rebalance_v1", run_id="unit-episode", seed=3),
            BaselineController("auto"),
        )

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertTrue(result["trace"]["artifacts_finalized"])
        self.assertTrue(result["trace"]["trace_finalized"])
        private_kinds = [entry["kind"] for entry in result["trace"]["private_benchmark"]]
        self.assertIn("readiness", private_kinds)
        self.assertIn("stimulus", private_kinds)
        self.assertGreater(result["summary"]["efficiency"]["total_tokens"], 0)

    def test_episode_writes_scored_summary_when_output_dir_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_episode(
                EpisodeConfig(
                    task_id="slice_congestion_prb_rebalance_v1",
                    run_id="unit-summary-output",
                    seed=3,
                    output_dir=Path(tmpdir),
                ),
                BaselineController("auto"),
            )

            summary_path = Path(result["scored_summary_path"])
            trace_path = Path(result["summary"]["artifact_manifest"][0]["private_path"])

            self.assertTrue(summary_path.exists())
            self.assertTrue(trace_path.exists())
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["outcome"], "success")
            self.assertEqual(payload["scored_summary_path"], str(summary_path))
            self.assertTrue(trace_payload["trace_finalized"])
            self.assertTrue(trace_payload["artifacts_finalized"])
            self.assertTrue(trace_payload["oracle"])

    def test_episode_finalizes_artifacts_before_cleanup(self) -> None:
        order: list[str] = []
        original_finalize_artifacts = TraceRecorder.finalize_artifacts
        original_cleanup_runtime = episode_module.cleanup_runtime

        def wrapped_finalize_artifacts(self, output_dir=None):
            order.append("finalize_artifacts")
            return original_finalize_artifacts(self, output_dir)

        def wrapped_cleanup_runtime(runtime):
            order.append("cleanup")
            self.assertIn("finalize_artifacts", order)
            return original_cleanup_runtime(runtime)

        try:
            TraceRecorder.finalize_artifacts = wrapped_finalize_artifacts
            episode_module.cleanup_runtime = wrapped_cleanup_runtime
            result = episode_module.run_episode(
                EpisodeConfig(task_id="minimal_intervention_budget_v1", run_id="unit-artifact-before-cleanup", seed=1),
                lambda payload: {"decision": None},
            )
        finally:
            TraceRecorder.finalize_artifacts = original_finalize_artifacts
            episode_module.cleanup_runtime = original_cleanup_runtime

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertLess(order.index("finalize_artifacts"), order.index("cleanup"))

    def test_timeout_becomes_no_action_and_d_interval_stays_active_to_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            task_dir = tasks_dir / "unit_timeout_v1"
            task_dir.mkdir()
            manifest = json.loads(Path("benchmark/tasks/minimal_intervention_budget_v1/task.json").read_text(encoding="utf-8"))
            manifest["id"] = "unit_timeout_v1"
            manifest["U"]["steps"] = 1
            manifest["U"]["timing_policy"]["decision_deadline_s"] = 0.01
            manifest["U"]["timing_policy"]["step_interval_s"] = 0.02
            manifest["U"]["events"] = [
                event
                for event in manifest["U"]["events"]
                if event.get("apply_steps") in ([1], None) or event.get("start_step") == 1
            ]
            for event in manifest["U"]["events"]:
                event.pop("start_step", None)
                event.pop("end_step", None)
                event.pop("apply_steps", None)
            manifest["J"]["temporal_expectations"] = [{"step_id": 1, "action_type": "NO_ACTION"}]
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

        result = run_episode(EpisodeConfig(task_id="stale_metrics_then_prb_v1", run_id="unit-stale", seed=1), acts_on_stale_once)

        self.assertEqual(result["summary"]["outcome"], "agent_failure")
        self.assertEqual(result["summary"]["raw_metrics"]["stale_action_avoidance"], 0.0)

    def test_temporal_action_sequence_metric_fails_wrong_step_action(self) -> None:
        sent = {"value": False}

        def acts_too_early(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            return {"decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 30, "max_prb_policy_ratio": 90}}

        result = run_episode(
            EpisodeConfig(task_id="slice_congestion_prb_rebalance_v1", run_id="unit-temporal-fail", seed=1),
            acts_too_early,
        )

        self.assertEqual(result["summary"]["outcome"], "agent_failure")
        self.assertEqual(result["summary"]["raw_metrics"]["temporal_action_sequence_match"], 0.0)

    def test_expected_action_payload_metric_fails_wrong_payload(self) -> None:
        sent = {"value": False}

        def wrong_cfo(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            radio = payload["observation"]["evidence"]["radio_runtime"]
            return {"decision": {"type": "SET_CFO_CLI", "sector_id": radio["sector_id"], "cfo_hz": -1.0}}

        result = run_episode(EpisodeConfig(task_id="cfo_correction_v1", run_id="unit-payload-fail", seed=1), wrong_cfo)

        self.assertEqual(result["summary"]["outcome"], "agent_failure")
        self.assertEqual(result["summary"]["raw_metrics"]["expected_action_payload_match"], 0.0)

    def test_cli_handover_experiment_scores_with_visible_ue_identity(self) -> None:
        sent = {"value": False}

        def handover_agent(payload):
            if payload["observation"]["step_id"] == 1 or sent["value"]:
                return {"decision": None}
            sent["value"] = True
            identity = payload["observation"]["evidence"]["ue_identity"]
            return {
                "decision": {
                    "type": "TRIGGER_HANDOVER_CLI",
                    "serving_pci": identity["serving_pci"],
                    "rnti": identity["rnti"],
                    "target_pci": identity["target_pcis"][0],
                }
            }

        result = run_episode(EpisodeConfig(task_id="immediate_handover_v1", run_id="unit-ho", seed=1), handover_agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        action = [
            entry["record"]
            for entry in result["trace"]["interaction"]
            if entry["kind"] == "action" and entry["record"]["dispatch"]["dispatched"]
        ][0]
        self.assertEqual(action["dispatch"]["backend"], "ocudu_cli")

    def test_cli_conditional_handover_experiment_scores_with_visible_ue_identity(self) -> None:
        sent = {"value": False}

        def cho_agent(payload):
            if payload["observation"]["step_id"] == 1 or sent["value"]:
                return {"decision": None}
            sent["value"] = True
            identity = payload["observation"]["evidence"]["ue_identity"]
            return {
                "decision": {
                    "type": "TRIGGER_CONDITIONAL_HANDOVER_CLI",
                    "serving_pci": identity["serving_pci"],
                    "rnti": identity["rnti"],
                    "target_pcis": identity["target_pcis"],
                    "timeout_s": 5,
                }
            }

        result = run_episode(EpisodeConfig(task_id="conditional_handover_planning_v1", run_id="unit-cho", seed=1), cho_agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        action = [
            entry["record"]
            for entry in result["trace"]["interaction"]
            if entry["kind"] == "action" and entry["record"]["dispatch"]["dispatched"]
        ][0]
        self.assertEqual(action["dispatch"]["backend"], "ocudu_cli")

    def test_cli_cfo_experiment_scores_with_visible_radio_runtime(self) -> None:
        sent = {"value": False}

        def cfo_agent(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            radio = payload["observation"]["evidence"]["radio_runtime"]
            return {
                "decision": {
                    "type": "SET_CFO_CLI",
                    "sector_id": radio["sector_id"],
                    "cfo_hz": radio["target_cfo_hz"],
                }
            }

        result = run_episode(EpisodeConfig(task_id="cfo_correction_v1", run_id="unit-cfo", seed=1), cfo_agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertEqual(result["summary"]["raw_metrics"]["expected_action_payload_match"], 1.0)
        action = [
            entry["record"]
            for entry in result["trace"]["interaction"]
            if entry["kind"] == "action" and entry["record"]["dispatch"]["dispatched"]
        ][0]
        self.assertEqual(action["dispatch"]["backend"], "ocudu_cli")
        self.assertNotIn("last_cfo_action_id", repr(result["trace"]["interaction"]))

    def test_cli_tx_time_offset_experiment_scores_with_visible_radio_runtime(self) -> None:
        sent = {"value": False}

        def tx_time_offset_agent(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            radio = payload["observation"]["evidence"]["radio_runtime"]
            return {
                "decision": {
                    "type": "SET_TX_TIME_OFFSET_CLI",
                    "sector_id": radio["sector_id"],
                    "tx_time_offset_us": radio["target_tx_time_offset_us"],
                }
            }

        result = run_episode(EpisodeConfig(task_id="tx_time_offset_correction_v1", run_id="unit-tx-time-offset", seed=1), tx_time_offset_agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertEqual(result["summary"]["raw_metrics"]["expected_action_payload_match"], 1.0)
        action = [entry["record"] for entry in result["trace"]["interaction"] if entry["kind"] == "action"][0]
        self.assertEqual(action["dispatch"]["backend"], "ocudu_cli")
        self.assertNotIn("last_tx_time_offset_action_id", repr(result["trace"]["interaction"]))

    def test_core_runtime_support_control_experiment_scores_with_visible_evidence(self) -> None:
        sent = {"value": False}

        def agent(payload):
            if payload["observation"]["step_id"] == 1 or sent["value"]:
                return {"decision": None}
            sent["value"] = True
            return {"decision": {"type": "RESTART_CORE_NF", "nf": "open5gs"}}

        result = run_episode(EpisodeConfig(task_id="core_nf_recovery_multistep_v1", run_id="unit-core-restart", seed=1), agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        action = [
            entry["record"]
            for entry in result["trace"]["interaction"]
            if entry["kind"] == "action" and entry["record"]["dispatch"]["dispatched"]
        ][0]
        self.assertEqual(action["dispatch"]["backend"], "core_control")

    def test_core_ue_registration_repair_scores_with_visible_core_evidence(self) -> None:
        sent = {"value": False}

        def agent(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            desired = payload["observation"]["evidence"]["core_runtime"]["ue_registration"]["desired"]
            return {
                "decision": {
                    "type": "UPDATE_CORE_UE_REGISTRATION",
                    "ue_id": desired["ue_id"],
                    "supi": desired["supi"],
                    "plmn": desired["plmn"],
                    "dnn": desired["dnn"],
                    "sst": desired["sst"],
                    "sd": desired["sd"],
                    "auth_profile_id": desired["auth_profile_id"],
                }
            }

        result = run_episode(EpisodeConfig(task_id="core_ue_registration_repair_multistep_v1", run_id="unit-core-ue-reg", seed=1), agent)

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertEqual(result["summary"]["raw_metrics"]["core_ue_registration_repaired"], 1.0)
        observation = [entry["record"] for entry in result["trace"]["interaction"] if entry["kind"] == "observation"][0]
        self.assertNotIn("last_updated_by", repr(observation["evidence"]["core_runtime"]))
        action = [entry["record"] for entry in result["trace"]["interaction"] if entry["kind"] == "action"][0]
        self.assertEqual(action["dispatch"]["backend"], "core_control")

    def test_minimal_intervention_scores_as_no_action_task(self) -> None:
        result = run_episode(
            EpisodeConfig(task_id="minimal_intervention_budget_v1", run_id="unit-minimal-intervention", seed=1),
            lambda payload: {"decision": None},
        )

        self.assertEqual(result["summary"]["outcome"], "success")
        actions = [entry["record"] for entry in result["trace"]["interaction"] if entry["kind"] == "action"]
        self.assertTrue(actions)
        self.assertTrue(all(action["action"]["type"] == "NO_ACTION" for action in actions))
        self.assertTrue(all(not action["dispatch"]["dispatched"] for action in actions))


if __name__ == "__main__":
    unittest.main()
