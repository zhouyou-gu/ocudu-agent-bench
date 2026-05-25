import unittest

from benchmark_api.action import handle_agent_decision
from benchmark_api.controller import BaselineController
from benchmark_api.episode import EpisodeConfig, run_episode
from benchmark_api.runtime_setup import instantiate_runtime
from benchmark_api.stimulus import apply_pre_observation, expand_stimulus_plan
from tests.task_helpers import load_checked_in_task as load_task


class SimulatedOcuduTransitionTests(unittest.TestCase):
    def test_accepted_prb_action_changes_next_step_slice_evidence(self) -> None:
        result = run_episode(EpisodeConfig(task_id="base_prb_slice_congestion_rebalance_v1", run_id="unit-prb-effect", seed=1), BaselineController("auto"))

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertEqual(result["summary"]["raw_metrics"]["post_action_evidence_match"], 1.0)
        step3 = _observation(result, 3)
        current = step3["evidence"]["slice_runtime"]["current_prb_policy"]
        self.assertEqual(current["min_prb_policy_ratio"], 30)
        self.assertEqual(current["max_prb_policy_ratio"], 90)
        self.assertEqual(current["backend"], "websocket")

    def test_accepted_ssb_action_changes_next_step_radio_evidence(self) -> None:
        result = run_episode(EpisodeConfig(task_id="base_ssb_coverage_edge_recovery_v1", run_id="unit-ssb-effect", seed=1), BaselineController("auto"))

        self.assertEqual(result["summary"]["outcome"], "success")
        self.assertEqual(result["summary"]["raw_metrics"]["post_action_evidence_match"], 1.0)
        step3 = _observation(result, 3)
        radio = step3["evidence"]["radio_runtime"]
        self.assertEqual(radio["current_ssb_block_power_dbm"], -13)
        self.assertEqual(radio["ssb_power_cell"]["nci"], 6733825)

    def test_accepted_cli_radio_actions_change_next_step_radio_evidence(self) -> None:
        cases = (
            ("base_radio_cli_cfo_correction_v1", "radio_runtime.cfo_hz", -1200.0),
            ("base_radio_cli_tx_time_offset_correction_v1", "radio_runtime.tx_time_offset_us", 7.5),
        )
        for task_id, path, expected in cases:
            with self.subTest(task_id=task_id):
                result = run_episode(EpisodeConfig(task_id=task_id, run_id=f"unit-{task_id}-effect", seed=1), BaselineController("auto"))

                self.assertEqual(result["summary"]["outcome"], "success")
                self.assertEqual(result["summary"]["raw_metrics"]["post_action_evidence_match"], 1.0)
                self.assertEqual(_path_get(_observation(result, 2)["evidence"], path), expected)

    def test_handover_and_cho_actions_change_ue_identity_evidence(self) -> None:
        handover = run_episode(EpisodeConfig(task_id="base_mobility_immediate_handover_v1", run_id="unit-ho-effect", seed=1), BaselineController("auto"))
        cho = run_episode(EpisodeConfig(task_id="base_mobility_conditional_handover_planning_v1", run_id="unit-cho-effect", seed=1), BaselineController("auto"))

        self.assertEqual(handover["summary"]["outcome"], "success")
        self.assertEqual(_observation(handover, 3)["evidence"]["ue_identity"]["serving_pci"], 7)
        self.assertEqual(cho["summary"]["outcome"], "success")
        self.assertEqual(_observation(cho, 3)["evidence"]["ue_identity"]["conditional_handover_plan"]["target_pcis"], [7, 8])

    def test_core_actions_change_next_step_core_evidence(self) -> None:
        nf = run_episode(EpisodeConfig(task_id="base_core_nf_recovery_v1", run_id="unit-core-nf-effect", seed=1), BaselineController("auto"))
        ue = run_episode(
            EpisodeConfig(task_id="base_core_ue_registration_repair_v1", run_id="unit-core-ue-effect", seed=1),
            BaselineController("auto"),
        )

        self.assertEqual(nf["summary"]["outcome"], "success")
        self.assertEqual(_observation(nf, 3)["evidence"]["core_runtime"]["restart_counts"]["open5gs"], 1)
        self.assertEqual(ue["summary"]["outcome"], "success")
        self.assertEqual(_observation(ue, 2)["evidence"]["core_runtime"]["ue_registration"]["status"], "registered")

    def test_runtime_rejects_stale_cell_wrong_slice_wrong_mobility_and_repeats(self) -> None:
        ssb_task = load_task("base_ssb_coverage_edge_recovery_v1")
        ssb_runtime = _runtime_at_step(ssb_task, step_id=2)
        stale_cell = handle_agent_decision(
            ssb_task,
            ssb_runtime,
            step_id=2,
            decision={"type": "SET_SSB_BLOCK_POWER_WS", "plmn": "00101", "nci": 6733824, "ssb_block_power_dbm": -13},
        )
        self.assertTrue(stale_cell.valid)
        self.assertFalse(stale_cell.dispatch.accepted)
        self.assertEqual(stale_cell.dispatch.safe_error_class.value, "runtime_rejected")

        prb_task = load_task("base_prb_slice_congestion_rebalance_v1")
        prb_runtime = _runtime_at_step(prb_task, step_id=2)
        wrong_slice = handle_agent_decision(
            prb_task,
            prb_runtime,
            step_id=2,
            decision={"type": "SET_PRB_POLICY_RATIO_WS", "plmn": "00101", "sst": 2, "min_prb_policy_ratio": 30, "max_prb_policy_ratio": 90},
        )
        self.assertTrue(wrong_slice.valid)
        self.assertFalse(wrong_slice.dispatch.accepted)
        self.assertEqual(wrong_slice.dispatch.safe_error_class.value, "runtime_rejected")

        ho_task = load_task("base_mobility_immediate_handover_v1")
        ho_runtime = _runtime_at_step(ho_task, step_id=2)
        wrong_pci = handle_agent_decision(
            ho_task,
            ho_runtime,
            step_id=2,
            decision={"type": "TRIGGER_HANDOVER_CLI", "serving_pci": 99, "rnti": "0x4601", "target_pci": 7},
        )
        self.assertTrue(wrong_pci.valid)
        self.assertFalse(wrong_pci.dispatch.accepted)
        self.assertEqual(wrong_pci.dispatch.safe_error_class.value, "runtime_rejected")

        accepted = handle_agent_decision(
            ssb_task,
            ssb_runtime,
            step_id=2,
            decision={"type": "SET_SSB_BLOCK_POWER_WS", "plmn": "00101", "nci": 6733825, "ssb_block_power_dbm": -13},
        )
        repeat = handle_agent_decision(
            ssb_task,
            ssb_runtime,
            step_id=3,
            decision={"type": "SET_SSB_BLOCK_POWER_WS", "plmn": "00101", "nci": 6733825, "ssb_block_power_dbm": -13},
        )
        self.assertTrue(accepted.dispatch.accepted)
        self.assertFalse(repeat.dispatch.accepted)
        self.assertEqual(repeat.dispatch.safe_error_class.value, "runtime_rejected")

    def test_unavailable_e2_backend_rejects_before_domain_transition(self) -> None:
        task = load_task("base_prb_ric_xapp_ws_fallback_v1")
        runtime = _runtime_at_step(task, step_id=2)

        record = handle_agent_decision(
            task,
            runtime,
            step_id=2,
            decision={"type": "SET_PRB_POLICY_RATIO_CCC", "min_prb_policy_ratio": 40, "max_prb_policy_ratio": 90},
        )

        self.assertTrue(record.valid)
        self.assertFalse(record.dispatch.accepted)
        self.assertEqual(record.dispatch.safe_error_class.value, "runtime_unavailable")

    def test_post_action_evidence_metric_fails_payload_correct_but_effect_wrong(self) -> None:
        sent = {"value": False}

        def wrong_cfo(payload):
            if sent["value"]:
                return {"decision": None}
            sent["value"] = True
            radio = payload["observation"]["evidence"]["radio_runtime"]
            return {"decision": {"type": "SET_CFO_CLI", "sector_id": radio["sector_id"], "cfo_hz": -1150.0}}

        result = run_episode(EpisodeConfig(task_id="base_radio_cli_cfo_correction_v1", run_id="unit-post-action-fail", seed=1), wrong_cfo)

        self.assertEqual(result["summary"]["outcome"], "agent_failure")
        self.assertEqual(result["summary"]["raw_metrics"]["expected_action_payload_match"], 0.0)
        self.assertEqual(result["summary"]["raw_metrics"]["post_action_evidence_match"], 0.0)


def _runtime_at_step(task, step_id: int):
    runtime = instantiate_runtime(task.E, f"unit-runtime-step-{step_id}")
    plan = expand_stimulus_plan(task.U, seed=1)
    for current_step in range(1, step_id + 1):
        apply_pre_observation(plan, runtime, current_step)
    return runtime


def _observation(result: dict, step_id: int) -> dict:
    for entry in result["trace"]["interaction"]:
        if entry["kind"] == "observation" and entry["record"]["step_id"] == step_id:
            return entry["record"]
    raise AssertionError(f"missing observation step {step_id}")


def _path_get(data: dict, path: str):
    value = data
    for part in path.split("."):
        value = value[part]
    return value


if __name__ == "__main__":
    unittest.main()
