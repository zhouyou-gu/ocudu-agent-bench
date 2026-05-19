import unittest

from benchmark.benchmark_api.stimulus import apply_in_step, apply_pre_observation, expand_stimulus_plan
from benchmark.benchmark_api.runtime_setup import instantiate_runtime
from benchmark.benchmark_api.task_definition import load_task
from benchmark.benchmark_api.types import StimulusPhase


class StimulusTests(unittest.TestCase):
    def test_same_seed_expands_to_same_private_schedule(self) -> None:
        task = load_task("ws_prb_ping_v1")
        first = expand_stimulus_plan(task.U, seed=7)
        second = expand_stimulus_plan(task.U, seed=7)

        self.assertEqual([event.event_id for event in first.events], [event.event_id for event in second.events])

    def test_pre_observation_and_in_step_windows_are_distinct(self) -> None:
        task = load_task("ws_prb_ping_v1")
        plan = expand_stimulus_plan(task.U, seed=1)
        phases = {event.phase for event in plan.events}

        self.assertIn(StimulusPhase.PRE_OBSERVATION, phases)
        self.assertIn(StimulusPhase.IN_STEP, phases)

    def test_in_step_event_records_reasoning_action_interval(self) -> None:
        task = load_task("ws_prb_ping_v1")
        runtime = instantiate_runtime(task.E, "unit")
        plan = expand_stimulus_plan(task.U, seed=1)
        apply_pre_observation(plan, runtime, step_id=1)
        events = apply_in_step(plan, runtime, step_id=1, observation_emitted_at_s=10.0, action_completed_at_s=10.2)

        self.assertTrue(events)
        for event in events:
            self.assertEqual(event.active_start_time_s, 10.0)
            self.assertGreaterEqual(event.active_end_time_s, 10.2)

    def test_decision_deadline_cannot_exceed_step_interval(self) -> None:
        task = load_task("ws_prb_ping_v1")
        stimulus = dict(task.U)
        stimulus["timing_policy"] = dict(task.U["timing_policy"])
        stimulus["timing_policy"]["decision_deadline_s"] = 2.0
        stimulus["timing_policy"]["step_interval_s"] = 1.0

        with self.assertRaisesRegex(ValueError, "decision_deadline_s"):
            expand_stimulus_plan(stimulus, seed=1)


if __name__ == "__main__":
    unittest.main()
