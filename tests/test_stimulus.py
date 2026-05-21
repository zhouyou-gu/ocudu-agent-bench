import unittest

from benchmark.benchmark_api.ran_api import read_evidence
from benchmark.benchmark_api.stimulus import apply_in_step, apply_pre_observation, expand_stimulus_plan
from benchmark.benchmark_api.runtime_setup import instantiate_runtime
from benchmark.benchmark_api.task_definition import load_task
from benchmark.benchmark_api.types import IMPLEMENTED_STIMULUS_DRIVERS, StimulusDriverKind, StimulusPhase


class StimulusTests(unittest.TestCase):
    def test_same_seed_expands_to_same_private_schedule(self) -> None:
        task = load_task("slice_congestion_prb_rebalance_v1")
        first = expand_stimulus_plan(task.U, seed=7)
        second = expand_stimulus_plan(task.U, seed=7)

        self.assertEqual([event.event_id for event in first.events], [event.event_id for event in second.events])

    def test_pre_observation_and_in_step_windows_are_distinct(self) -> None:
        task = load_task("slice_congestion_prb_rebalance_v1")
        plan = expand_stimulus_plan(task.U, seed=1)
        phases = {event.phase for event in plan.events}

        self.assertIn(StimulusPhase.PRE_OBSERVATION, phases)
        self.assertIn(StimulusPhase.IN_STEP, phases)

    def test_in_step_event_records_reasoning_action_interval(self) -> None:
        task = load_task("slice_congestion_prb_rebalance_v1")
        runtime = instantiate_runtime(task.E, "unit")
        plan = expand_stimulus_plan(task.U, seed=1)
        apply_pre_observation(plan, runtime, step_id=1)
        events = apply_in_step(plan, runtime, step_id=1, observation_emitted_at_s=10.0, action_completed_at_s=10.2)

        self.assertTrue(events)
        for event in events:
            self.assertEqual(event.active_start_time_s, 10.0)
            self.assertGreaterEqual(event.active_end_time_s, 10.2)

    def test_decision_deadline_cannot_exceed_step_interval(self) -> None:
        task = load_task("slice_congestion_prb_rebalance_v1")
        stimulus = dict(task.U)
        stimulus["timing_policy"] = dict(task.U["timing_policy"])
        stimulus["timing_policy"]["decision_deadline_s"] = 2.0
        stimulus["timing_policy"]["step_interval_s"] = 1.0

        with self.assertRaisesRegex(ValueError, "decision_deadline_s"):
            expand_stimulus_plan(stimulus, seed=1)

    def test_ue_ping_traffic_updates_ue_runtime_as_stimulus(self) -> None:
        task = load_task("slice_congestion_prb_rebalance_v1")
        runtime = instantiate_runtime(task.E, "unit-ue-traffic-stimulus")
        plan = expand_stimulus_plan(task.U, seed=1)
        apply_pre_observation(plan, runtime, step_id=1)

        ue_runtime = runtime.state["ue_runtime"]
        self.assertTrue(ue_runtime["traffic_active"])
        self.assertEqual(ue_runtime["traffic_profile"]["source"], "stimulus")
        self.assertEqual(runtime.state["ping"]["packets_transmitted"], 3)

    def test_ue_activity_churn_restarts_ue_as_stimulus(self) -> None:
        runtime = instantiate_runtime(
            {
                "runtime_adapter": "simulated_ocudu",
                "runtime": "ocudu_zmq_open5gs",
                "components": ["ocudu_websocket", "open5gs", "srsue_zmq"],
            },
            "unit-ue-churn-stimulus",
        )
        plan = expand_stimulus_plan(
            {
                "steps": 1,
                "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 0.01, "decision_deadline_s": 0.01},
                "events": [
                    {"kind": "ue_activity_churn", "phase": "pre_observation", "parameters": {"action": "restart"}},
                ],
            },
            seed=1,
        )
        events = apply_pre_observation(plan, runtime, step_id=1)

        ue_runtime = runtime.state["ue_runtime"]
        self.assertTrue(ue_runtime["running"])
        self.assertEqual(ue_runtime["restart_count"], 1)
        self.assertIn("ue_activity_churn", [event.kind.value for event in events])

    def test_core_ue_registration_misconfig_is_private_stimulus(self) -> None:
        task = load_task("core_ue_registration_repair_multistep_v1")
        runtime = instantiate_runtime(task.E, "unit-core-registration-stimulus")
        plan = expand_stimulus_plan(task.U, seed=1)
        events = apply_pre_observation(plan, runtime, step_id=1)

        registration = runtime.state["core_runtime"]["ue_registration"]
        self.assertEqual(registration["status"], "mismatch")
        self.assertEqual(registration["mismatch_fields"], ["supi"])
        private_event = [event for event in events if event.kind.value == "core_ue_registration_misconfig"][0]
        self.assertEqual(private_event.evidence["status"], "mismatch")
        self.assertNotIn("desired", private_event.evidence)

    def test_step_targeting_limits_event_expansion(self) -> None:
        stimulus = {
            "steps": 4,
            "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 0.01, "decision_deadline_s": 0.01},
            "events": [
                {"kind": "ue_ping_traffic", "phase": "pre_observation", "parameters": {"packets": 1}, "apply_steps": [1, 3]},
                {"kind": "metrics_staleness_mask", "phase": "pre_observation", "parameters": {"stale_until_step": 0}, "start_step": 2, "end_step": 4},
            ],
        }
        plan = expand_stimulus_plan(stimulus, seed=1)

        ping_steps = [event.step_id for event in plan.events if event.kind.value == "ue_ping_traffic"]
        metrics_steps = [event.step_id for event in plan.events if event.kind.value == "metrics_staleness_mask"]
        self.assertEqual(ping_steps, [1, 3])
        self.assertEqual(metrics_steps, [2, 3, 4])

    def test_untargeted_events_still_expand_to_all_steps(self) -> None:
        stimulus = {
            "steps": 3,
            "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 0.01, "decision_deadline_s": 0.01},
            "events": [
                {"kind": "ue_ping_traffic", "phase": "pre_observation", "parameters": {"packets": 1}},
            ],
        }
        plan = expand_stimulus_plan(stimulus, seed=1)

        self.assertEqual([event.step_id for event in plan.events], [1, 2, 3])

    def test_invalid_step_targeting_is_rejected(self) -> None:
        invalid = {
            "steps": 3,
            "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 0.01, "decision_deadline_s": 0.01},
            "events": [
                {
                    "kind": "ue_ping_traffic",
                    "phase": "pre_observation",
                    "parameters": {"packets": 1},
                    "apply_steps": [1],
                    "start_step": 1,
                    "end_step": 2,
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, "apply_steps"):
            expand_stimulus_plan(invalid, seed=1)

    def test_candidate_stimulus_drivers_apply_simulated_runtime_effects(self) -> None:
        runtime = instantiate_runtime(
            {
                "runtime_adapter": "simulated_ocudu",
                "runtime": "ocudu_zmq_open5gs_flexric",
                "components": ["ocudu_websocket", "open5gs", "srsue_zmq", "flexric"],
            },
            "unit-stimulus-candidates",
        )
        stimulus = {
            "steps": 1,
            "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 1.0, "decision_deadline_s": 1.0},
            "events": [
                {
                    "kind": "traffic_load_profile",
                    "phase": "pre_observation",
                    "parameters": {"profile": "high", "packet_rate_pps": 250.0, "active_ues": 3},
                },
                {"kind": "mobility_path", "phase": "pre_observation", "parameters": {"serving_pci": 7, "target_pcis": [8, 9]}},
                {"kind": "radio_condition_profile", "phase": "pre_observation", "parameters": {"profile": "edge", "sinr_db": 6.5, "cqi": 4}},
                {"kind": "slice_demand_shift", "phase": "pre_observation", "parameters": {"sst": 1, "demand_level": "high", "active_ues": 4}},
                {"kind": "telemetry_gap", "phase": "pre_observation", "parameters": {"sources": ["json_metrics"]}},
                {"kind": "e2_kpm_availability_window", "phase": "pre_observation", "parameters": {"available_from_step": 1, "kpm_indications": 5}},
                {"kind": "ric_xapp_lifecycle", "phase": "pre_observation", "parameters": {"action": "restart", "xapp": "control"}},
                {"kind": "core_latency_profile", "phase": "pre_observation", "parameters": {"latency_ms": 45.0, "jitter_ms": 7.0}},
                {"kind": "backhaul_impairment", "phase": "pre_observation", "parameters": {"delay_ms": 12.0, "loss_rate": 0.25, "throughput_mbps": 100.0}},
                {"kind": "cell_identity_change", "phase": "pre_observation", "parameters": {"nci": 6733825, "sector_id": 1, "serving_pci": 11}},
                {"kind": "future_zmq_impairment", "phase": "pre_observation", "parameters": {"sample_loss_rate": 0.1, "delay_us": 50.0}},
            ],
        }
        plan = expand_stimulus_plan(stimulus, seed=3)
        events = apply_pre_observation(plan, runtime, step_id=1)

        self.assertEqual(len(events), 11)
        self.assertEqual(runtime.state["traffic_load"]["profile"], "high")
        self.assertEqual(runtime.state["ue_identity"]["serving_pci"], 11)
        self.assertEqual(runtime.state["radio_runtime"]["condition_profile"], "edge")
        self.assertEqual(runtime.state["slice_runtime"]["demand_level"], "high")
        self.assertFalse(runtime.state["metrics"]["present"])
        self.assertEqual(runtime.state["e2"]["kpm_indications"], 5)
        self.assertTrue(runtime.state["e2"]["ric_xapp_running"])
        self.assertEqual(runtime.state["core_runtime"]["latency_profile"]["latency_ms"], 45.0)
        self.assertEqual(runtime.state["backhaul_runtime"]["loss_rate"], 0.25)
        self.assertEqual(runtime.state["cell_identity"]["nci"], 6733825)
        self.assertTrue(runtime.state["radio_runtime"]["zmq_impairment"]["simulated_only"])
        radio_evidence = read_evidence(runtime, ("radio_runtime",))["radio_runtime"]
        self.assertEqual(radio_evidence["zmq_impairment"], {"enabled": True, "impairment_kind": "sample_path"})
        self.assertNotIn("sample_loss_rate", repr(radio_evidence))
        self.assertNotIn("delay_us", repr(radio_evidence))
        self.assertNotIn("simulated_only", repr(radio_evidence))

    def test_all_stimulus_driver_kinds_are_currently_implemented(self) -> None:
        self.assertEqual(set(StimulusDriverKind), IMPLEMENTED_STIMULUS_DRIVERS)

    def test_candidate_stimulus_payloads_are_validated(self) -> None:
        invalid_events = [
            {"kind": "traffic_load_profile", "parameters": {"packet_rate_pps": -1.0}, "message": "packet_rate_pps"},
            {"kind": "mobility_path", "parameters": {"target_pcis": [1008]}, "message": "target_pcis"},
            {"kind": "radio_condition_profile", "parameters": {"cqi": 16}, "message": "cqi"},
            {
                "kind": "slice_demand_shift",
                "parameters": {"min_prb_policy_ratio": 80, "max_prb_policy_ratio": 20},
                "message": "min_prb_policy_ratio",
            },
            {
                "kind": "e2_kpm_availability_window",
                "parameters": {"available_from_step": 3, "available_until_step": 2},
                "message": "available_until_step",
            },
            {"kind": "core_latency_profile", "parameters": {"loss_rate": 1.5}, "message": "loss_rate"},
            {"kind": "backhaul_impairment", "parameters": {"loss_rate": 1.5}, "message": "loss_rate"},
            {"kind": "cell_identity_change", "parameters": {"serving_pci": -1}, "message": "serving_pci"},
            {"kind": "future_zmq_impairment", "parameters": {"delay_us": -1.0}, "message": "delay_us"},
        ]

        for invalid in invalid_events:
            with self.subTest(kind=invalid["kind"]):
                stimulus = {
                    "steps": 1,
                    "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 1.0, "decision_deadline_s": 1.0},
                    "events": [
                        {
                            "kind": invalid["kind"],
                            "phase": "pre_observation",
                            "parameters": invalid["parameters"],
                        },
                    ],
                }
                with self.assertRaisesRegex(ValueError, invalid["message"]):
                    expand_stimulus_plan(stimulus, seed=1)


if __name__ == "__main__":
    unittest.main()
