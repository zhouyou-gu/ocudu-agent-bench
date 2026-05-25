import unittest

from benchmark_api.conformance import run_readiness_checks
from benchmark_api.runtime_setup import instantiate_runtime
from benchmark_api.stimulus import expand_stimulus_plan
from tests.task_helpers import load_checked_in_task as load_task


class RuntimeSetupTests(unittest.TestCase):
    def test_unavailable_live_adapter_blocks_readiness(self) -> None:
        task = load_task("base_prb_slice_congestion_rebalance_v1")
        setup = dict(task.E)
        setup["runtime_adapter"] = "ocudu_live"
        runtime = instantiate_runtime(setup, "unit-live")
        plan = expand_stimulus_plan(task.U, seed=1)
        readiness = run_readiness_checks(task, runtime, plan)

        self.assertFalse(runtime.ready)
        self.assertEqual(readiness["status"], "fail")
        self.assertIn("runtime adapter is not available", readiness["checks"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
