import json
import tempfile
import unittest
from pathlib import Path

from benchmark_api.task_catalog import load_tasks_for_suite
from benchmark_api.task_definition import agent_visible_task, clone_task_with_overrides, load_task


class TaskDefinitionTests(unittest.TestCase):
    def test_checked_in_task_sets_load_with_private_manifest_metadata(self) -> None:
        base = load_tasks_for_suite(suite="base")
        regression = load_tasks_for_suite(suite="regression")
        compound = load_tasks_for_suite(suite="compound")
        all_checked_in = load_tasks_for_suite(suite="all_checked_in")

        self.assertEqual(len(base), 25)
        self.assertEqual(len(regression), 1)
        self.assertEqual(len(compound), 8)
        self.assertEqual(len(all_checked_in), 34)
        self.assertIn("base_prb_slice_congestion_rebalance_v1", base)
        self.assertIn("regression_harness_invalid_action_repair_v1", regression)
        self.assertIn("compound_diagnosis_congestion_vs_coverage_v1", compound)
        for task in all_checked_in.values():
            with self.subTest(task_id=task.task_id):
                self.assertEqual(task.source.parent.name, task.task_id)
                self.assertIn(task.M["task_set"], {"base", "regression", "compound"})
                self.assertIn(task.M["family"], {"prb", "ssb", "mobility", "radio_cli", "core", "diagnosis", "isolation", "restraint", "backend", "harness"})
                self.assertIn(task.M["role"], {"primary", "regression", "compound"})
                self.assertNotIn("variant_axes", task.J)

    def test_generated_suites_are_deterministic_private_variants(self) -> None:
        base = load_tasks_for_suite(suite="base")
        generated_a = load_tasks_for_suite(suite="generated", seed=7, count=20)
        generated_b = load_tasks_for_suite(suite="generated", seed=7, count=20)
        generated_c = load_tasks_for_suite(suite="generated", seed=8, count=20)

        self.assertEqual(len(generated_a), 20)
        self.assertEqual(tuple(generated_a), tuple(generated_b))
        self.assertNotEqual(tuple(generated_a), tuple(generated_c))
        for task in generated_a.values():
            with self.subTest(task_id=task.task_id):
                variant = task.M["variant"]
                self.assertRegex(task.task_id, r"^generated_s[0-9]{4}_[0-9a-f]{6}_v1$")
                self.assertNotIn(variant["axis"], task.task_id)
                self.assertNotIn(variant["anchor_task_id"], task.task_id)
                self.assertNotIn(variant["family"], task.task_id)
                self.assertEqual(task.M["task_set"], "generated")
                self.assertEqual(variant["suite"], "generated")
                self.assertIn(variant["anchor_task_id"], base)
                self.assertRegex(variant["axis_registry_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(variant["suite_policies_sha256"], r"^[0-9a-f]{64}$")
                self.assertNotIn("M", agent_visible_task(task).to_dict())
                self.assertNotIn("axis_values", repr(agent_visible_task(task).to_dict()))
                self.assertNotIn("expected_failure_modes", repr(agent_visible_task(task).to_dict()))
                self.assertNotIn("variant_axes", task.J)

        standard = load_tasks_for_suite(suite="standard", seed=7, count=5)
        for task in standard.values():
            self.assertEqual(task.M["variant"]["suite"], "standard")

    def test_generated_suite_policy_counts_and_family_filters(self) -> None:
        self.assertEqual(len(load_tasks_for_suite(suite="generated", seed=1)), 200)
        standard = load_tasks_for_suite(suite="standard", seed=1)
        self.assertEqual(len(standard), 200)
        self.assertEqual(len(load_tasks_for_suite(suite="diagnostic", seed=1)), 1000)
        expected_modes = {
            "eager",
            "noop",
            "repeat",
            "wrong_api",
            "wrong_payload",
        }
        selected_modes = {
            mode
            for task in standard.values()
            for mode in task.M["variant"].get("expected_failure_modes", [])
        }
        self.assertTrue(expected_modes.issubset(selected_modes))

        core = load_tasks_for_suite(suite="standard", seed=1, family="core")
        self.assertEqual(len(core), 10)
        self.assertTrue(all(task.M["family"] == "core" for task in core.values()))
        self.assertEqual(load_tasks_for_suite(suite="standard", seed=1, family="isolation"), {})

    def test_generated_public_goals_do_not_include_variant_axis_values(self) -> None:
        generated = load_tasks_for_suite(suite="standard", seed=1, count=200)
        for task in generated.values():
            with self.subTest(task_id=task.task_id):
                goal = agent_visible_task(task).to_dict()["goal"]
                variant = task.M["variant"]
                self.assertNotIn("Generated deterministic variant", goal)
                self.assertNotIn(f"{variant['axis']}={variant['level']}", goal)
                self.assertNotIn("root_cause", goal)

    def test_old_checked_in_variant_semantics_are_registry_axes(self) -> None:
        registry = json.loads(Path("task_sets/generated/axis_registry.json").read_text(encoding="utf-8"))
        legacy_levels = [
            level
            for axis in registry["axes"]
            for level in axis.get("levels", [])
            if level.get("legacy_task_id")
        ]

        self.assertEqual(len(legacy_levels), 24)

    def test_agent_visible_task_redacts_private_fields(self) -> None:
        task = load_tasks_for_suite(suite="base")["base_prb_slice_congestion_rebalance_v1"]
        view = agent_visible_task(task).to_dict()
        rendered = repr(view)

        self.assertEqual(view["task_id"], "base_prb_slice_congestion_rebalance_v1")
        self.assertIn("goal", view)
        self.assertIn("slice_runtime", view["api_projection"]["observation_sources"])
        for private in ("'E'", "'U'", "'J'", "'M'", "oracle_requirements", "stimulus", "variant"):
            self.assertNotIn(private, rendered)

    def test_no_action_is_not_a_task_allowed_runtime_action(self) -> None:
        for task in load_tasks_for_suite(suite="all_checked_in").values():
            self.assertNotIn("NO_ACTION", task.allowed_actions)

    def test_manifest_role_must_match_task_set(self) -> None:
        task = load_tasks_for_suite(suite="base")["base_prb_slice_congestion_rebalance_v1"]

        with self.assertRaisesRegex(ValueError, "M.role must be 'primary'"):
            clone_task_with_overrides(
                task,
                task_id="base_prb_bad_role_v1",
                M={"task_set": "base", "family": "prb", "role": "compound"},
            )

    def test_allowed_actions_must_be_backed_by_selected_api_kinds(self) -> None:
        manifest = {
            "id": "unit_unselected_action_v1",
            "version": 1,
            "G": "test action projection consistency",
            "E": {
                "runtime_adapter": "simulated_ocudu",
                "runtime": "ocudu_zmq_open5gs",
                "components": ["ocudu_websocket"],
            },
            "U": {
                "steps": 1,
                "timing_policy": {"clock_mode": "fixed_tick", "step_interval_s": 0.01, "decision_deadline_s": 0.01},
                "events": [
                    {"kind": "metrics_staleness_mask", "phase": "pre_observation", "parameters": {"stale_until_step": 0}},
                ],
            },
            "I": {
                "api_kinds": ["ocudu_json_metrics"],
                "allowed_actions": ["SET_PRB_POLICY_RATIO_WS"],
                "observation_sources": ["json_metrics"],
                "allow_no_action": True,
            },
            "J": {
                "raw_metrics": ["temporal_action_sequence_match"],
                "critical_metrics": ["temporal_action_sequence_match"],
                "temporal_expectations": [{"step_id": 1, "action_type": "NO_ACTION"}],
            },
            "M": {"task_set": "base", "family": "prb", "role": "primary"},
            "allowed_observation_context": ["task_id", "step_id", "backend"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "unit_unselected_action_v1"
            task_dir.mkdir()
            (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "allowed_actions"):
                load_task("unit_unselected_action_v1", Path(tmpdir))


if __name__ == "__main__":
    unittest.main()
