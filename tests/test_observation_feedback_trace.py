import unittest
import tempfile
from pathlib import Path

from benchmark.benchmark_api.action import handle_agent_decision
from benchmark.benchmark_api.feedback import build_feedback
from benchmark.benchmark_api.observation import build_observation
from benchmark.benchmark_api.runtime_setup import instantiate_runtime
from benchmark.benchmark_api.task_definition import load_task
from benchmark.benchmark_api.trace import TraceRecorder


class ObservationFeedbackTraceTests(unittest.TestCase):
    def test_observation_excludes_private_runtime_and_stimulus_fields(self) -> None:
        task = load_task("ws_prb_ping_v1")
        runtime = instantiate_runtime(task.E, "unit")
        observation = build_observation(task, runtime, step_id=1, previous_feedback=None)
        rendered = repr(observation)

        self.assertNotIn("setup_metadata", rendered)
        self.assertNotIn("stimulus_schedule", rendered)
        self.assertNotIn("runtime_handle", rendered)
        self.assertIn("evidence", observation)

    def test_feedback_uses_safe_error_classes_only(self) -> None:
        task = load_task("ws_prb_ping_v1")
        runtime = instantiate_runtime(task.E, "unit")
        record = handle_agent_decision(task, runtime, step_id=1, decision={"type": "BAD"})
        feedback = build_feedback(record)

        self.assertEqual(feedback["safe_error_class"], "permission_error")
        self.assertNotIn("/", feedback["safe_message"])

    def test_trace_requires_artifact_finalization_before_scoring_package(self) -> None:
        trace = TraceRecorder("unit")
        with self.assertRaises(RuntimeError):
            trace.finalize_trace()
        trace.finalize_artifacts()
        package = trace.finalize_trace()
        self.assertTrue(package["artifacts_finalized"])
        self.assertTrue(package["trace_finalized"])

    def test_trace_finalization_writes_artifact_file_when_output_dir_is_set(self) -> None:
        trace = TraceRecorder("unit")
        trace.record_observation({"step_id": 1, "evidence": {}})
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = trace.finalize_artifacts(Path(tmpdir))
            path = Path(manifest[0]["private_path"])
            self.assertTrue(path.exists())
            self.assertIn('"interaction"', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
