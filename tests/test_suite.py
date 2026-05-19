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
        result = run_repeated(ControllerConfig(task_id="ws_prb_ping_v1", controller_id="auto", runs=2, seed=5))

        self.assertEqual(result["suite_summary"]["run_count"], 2)
        self.assertEqual(result["suite_summary"]["scored_count"], 2)
        self.assertEqual(result["run_manifest"]["seed_identifiers"], [5, 6])


if __name__ == "__main__":
    unittest.main()
