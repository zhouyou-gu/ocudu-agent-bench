import ast
import inspect
import unittest

from benchmark.benchmark_api import suite
from benchmark.benchmark_api.controller import ControllerConfig, run_repeated


class SuiteTests(unittest.TestCase):
    def test_suite_module_is_aggregation_only(self) -> None:
        tree = ast.parse(inspect.getsource(suite))
        imported_names = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }

        self.assertNotIn("run_episode", imported_names)
        self.assertFalse(hasattr(suite, "run_suite"))

    def test_controller_owns_repeated_runs(self) -> None:
        result = run_repeated(ControllerConfig(task_id="base_prb_slice_congestion_rebalance_v1", controller_id="auto", runs=2, seed=5))

        self.assertEqual(result["suite_summary"]["run_count"], 2)
        self.assertEqual(result["suite_summary"]["scored_count"], 2)
        self.assertEqual(result["suite_summary"]["outcomes"], {"success": 2})
        self.assertEqual(result["run_manifest"]["seed_identifiers"], [5, 6])

    def test_auto_controller_covers_stale_and_repair_tasks(self) -> None:
        for task_id in ("base_prb_stale_metrics_then_rebalance_v1", "regression_harness_invalid_action_repair_v1"):
            with self.subTest(task_id=task_id):
                suite = "regression" if task_id.startswith("regression_") else "base"
                result = run_repeated(ControllerConfig(task_id=task_id, controller_id="auto", runs=1, seed=9, suite=suite))

                self.assertEqual(result["suite_summary"]["run_count"], 1)
                self.assertEqual(result["suite_summary"]["scored_count"], 1)
                self.assertEqual(result["suite_summary"]["outcomes"], {"success": 1})

    def test_generated_suite_repeated_runs_keep_variant_selection_seed_stable(self) -> None:
        result = run_repeated(
            ControllerConfig(
                controller_id="auto",
                runs=2,
                seed=1,
                suite="generated",
                suite_count=5,
            )
        )

        self.assertEqual(result["suite_summary"]["run_count"], 10)
        self.assertEqual(result["suite_summary"]["scored_count"], 10)
        self.assertEqual(result["suite_summary"]["outcomes"], {"success": 10})
        self.assertEqual(result["run_manifest"]["seed_identifiers"], [1, 2] * 5)
        generated_task_ids = {run["task"] for run in result["runs"]}
        self.assertEqual(len(generated_task_ids), 5)


if __name__ == "__main__":
    unittest.main()
