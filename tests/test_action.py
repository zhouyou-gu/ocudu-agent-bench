import unittest
from pathlib import Path

from benchmark_api.action import handle_agent_decision, validate_action
from benchmark_api.runtime_setup import instantiate_runtime
from benchmark_api.task_definition import PrivateTask
from tests.task_helpers import load_checked_in_task as load_task


class ActionTests(unittest.TestCase):
    def test_no_action_is_valid_but_not_dispatched(self) -> None:
        task = load_task("base_prb_slice_congestion_rebalance_v1")
        runtime = instantiate_runtime(task.E, "unit")
        record = handle_agent_decision(task, runtime, step_id=1, decision=None)

        self.assertTrue(record.valid)
        self.assertIsNotNone(record.dispatch)
        self.assertFalse(record.dispatch.dispatched)
        self.assertIsNone(record.dispatch.private_request)

    def test_raw_wire_command_is_not_an_agent_action(self) -> None:
        task = load_task("base_prb_slice_congestion_rebalance_v1")
        validation = validate_action(task, {"type": "rrm_policy_ratio_set"})

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "permission_error")

    def test_prb_payload_bounds_are_checked_locally(self) -> None:
        task = load_task("base_prb_slice_congestion_rebalance_v1")
        validation = validate_action(
            task,
            {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10},
        )

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "schema_error")

    def test_optional_prb_payload_errors_are_safe_schema_errors(self) -> None:
        task = load_task("base_prb_slice_congestion_rebalance_v1")
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

    def test_handover_payload_is_normalized_for_cli_request(self) -> None:
        task = load_task("base_mobility_immediate_handover_v1")
        runtime = instantiate_runtime(task.E, "unit-ho")

        record = handle_agent_decision(
            task,
            runtime,
            step_id=1,
            decision={"type": "TRIGGER_HANDOVER_CLI", "serving_pci": 1, "rnti": "4601", "target_pci": 2},
        )

        self.assertTrue(record.valid)
        self.assertTrue(record.dispatch.accepted)
        self.assertEqual(record.action["rnti"], "0x4601")
        self.assertEqual(record.dispatch.private_request, {"cmd": "ho", "argv": [1, "0x4601", 2]})

    def test_conditional_handover_payload_bounds_are_checked_locally(self) -> None:
        task = load_task("base_mobility_conditional_handover_planning_v1")
        validation = validate_action(
            task,
            {
                "type": "TRIGGER_CONDITIONAL_HANDOVER_CLI",
                "serving_pci": 1,
                "rnti": "0x4601",
                "target_pcis": list(range(9)),
                "timeout_s": 5,
            },
        )

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "schema_error")

    def test_cfo_payload_is_normalized_for_cli_request(self) -> None:
        task = load_task("base_radio_cli_cfo_correction_v1")
        runtime = instantiate_runtime(task.E, "unit-cfo")

        record = handle_agent_decision(
            task,
            runtime,
            step_id=1,
            decision={"type": "SET_CFO_CLI", "sector_id": 0, "cfo_hz": -1250},
        )

        self.assertTrue(record.valid)
        self.assertTrue(record.dispatch.accepted)
        self.assertEqual(record.dispatch.private_request, {"cmd": "cfo", "argv": [0, -1250.0]})
        self.assertEqual(runtime.state["radio_runtime"]["cfo_hz"], -1250.0)

    def test_tx_time_offset_payload_is_normalized_for_cli_request(self) -> None:
        task = load_task("base_radio_cli_tx_time_offset_correction_v1")
        runtime = instantiate_runtime(task.E, "unit-tx-time-offset")

        record = handle_agent_decision(
            task,
            runtime,
            step_id=1,
            decision={"type": "SET_TX_TIME_OFFSET_CLI", "sector_id": 0, "tx_time_offset_us": 7.5},
        )

        self.assertTrue(record.valid)
        self.assertTrue(record.dispatch.accepted)
        self.assertEqual(record.dispatch.private_request, {"cmd": "tx_time_offset", "argv": [0, 7.5]})
        self.assertEqual(runtime.state["radio_runtime"]["tx_time_offset_us"], 7.5)

    def test_cli_radio_payload_bounds_are_checked_locally(self) -> None:
        task = load_task("base_radio_cli_cfo_correction_v1")
        validation = validate_action(task, {"type": "SET_CFO_CLI", "sector_id": 0, "cfo_hz": 100001.0})

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "schema_error")

    def test_ue_runtime_stimulus_is_not_an_agent_action(self) -> None:
        task = load_task("base_restraint_minimal_intervention_budget_v1")
        validation = validate_action(
            task,
            {
                "type": "START_UE_TRAFFIC",
                "traffic_kind": "ping",
                "target": "10.45.1.1",
                "duration_s": 5,
                "packet_count": 5,
            },
        )

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "permission_error")

    def test_action_validation_rejects_unselected_api_action_even_if_manifest_allows_it(self) -> None:
        task = PrivateTask(
            task_id="unit_unselected_action_v1",
            version=1,
            G="test action projection consistency",
            E={"runtime_adapter": "simulated_ocudu", "runtime": "ocudu_zmq_open5gs"},
            U={"steps": 1},
            I={
                "api_kinds": ["ocudu_json_metrics"],
                "allowed_actions": ["SET_PRB_POLICY_RATIO_WS"],
                "observation_sources": ["json_metrics"],
                "allow_no_action": True,
            },
            J={},
            M={"task_set": "base", "family": "prb", "role": "primary"},
            allowed_observation_context=("task_id", "step_id", "backend"),
            public_constraints=(),
            source=Path("unit_unselected_action_v1/task.json"),
        )

        validation = validate_action(
            task,
            {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80},
        )

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "permission_error")

    def test_core_nf_restart_payload_bounds_are_checked_locally(self) -> None:
        task = load_task("base_core_nf_recovery_v1")
        validation = validate_action(task, {"type": "RESTART_CORE_NF", "nf": "hss"})

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "schema_error")

    def test_core_ue_registration_update_uses_redacted_auth_profile(self) -> None:
        task = load_task("base_core_ue_registration_repair_v1")
        runtime = instantiate_runtime(task.E, "unit-core-registration")
        decision = {
            "type": "UPDATE_CORE_UE_REGISTRATION",
            "ue_id": "ue1",
            "supi": "001010000000001",
            "plmn": "00101",
            "dnn": "internet",
            "sst": 1,
            "sd": None,
            "auth_profile_id": "ue1_test_profile",
        }

        record = handle_agent_decision(task, runtime, step_id=1, decision=decision)

        self.assertTrue(record.valid)
        self.assertTrue(record.dispatch.accepted)
        self.assertEqual(record.dispatch.backend, "core_control")
        self.assertEqual(record.dispatch.private_request["cmd"], "benchmark_core_ue_registration_update")
        self.assertNotIn("opc", repr(record.dispatch.private_request).lower())
        self.assertEqual(runtime.state["core_runtime"]["ue_registration"]["status"], "registered")

    def test_core_ue_registration_payload_bounds_are_checked_locally(self) -> None:
        task = load_task("base_core_ue_registration_repair_v1")
        validation = validate_action(
            task,
            {
                "type": "UPDATE_CORE_UE_REGISTRATION",
                "ue_id": "ue1",
                "supi": "not-an-imsi",
                "plmn": "00101",
                "dnn": "internet",
                "sst": 1,
                "auth_profile_id": "ue1_test_profile",
            },
        )

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["safe_error_class"].value, "schema_error")


if __name__ == "__main__":
    unittest.main()
