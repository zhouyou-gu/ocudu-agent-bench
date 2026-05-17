import json
import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.conformance import load_conformance_specs
from benchmark.benchmark_api.tasks import (
    ACTION_TYPES,
    OBSERVATION_SOURCES,
    READINESS_LEVELS,
    RUNTIME_FAMILIES,
    SCORE_DIMENSIONS,
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_E2_KPM_JSON_CONSISTENCY_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
    TASK_WS_SSB_POWER_GUARD_V1,
    TASK_WS_SSB_POWER_REPAIR_V1,
    TASK_WS_PRB_ACTION_BUDGET_V1,
    TASK_WS_PRB_ERROR_REPAIR_V1,
    TASK_WS_PRB_NOOP_GUARD_V1,
    TASK_WS_PRB_PING_V1,
    conformance_checks_for_task,
    episode_stage_for_task,
    get_task_spec,
    implemented_episode_task_ids,
    is_implemented_episode_task,
    load_task_specs,
    suite_stage_for_task,
    supported_task_ids,
)


def valid_manifest(task_id: str = "unit_task_v1") -> dict[str, object]:
    return {
        "id": task_id,
        "name": "Unit Task",
        "summary": "unit task",
        "stage": "v3_episode",
        "suite_stage": "v3_suite",
        "runtime": "docker_e2e",
        "readiness": "scored",
        "action_types": ["SET_PRB_POLICY_RATIO_WS"],
        "observation_sources": ["ping", "json_metrics", "websocket_control"],
        "required_conformance": ["remote_tools_ocudu_root"],
        "scoring": ["valid_action_accepted_rate", "metrics_continuity", "clean_teardown"],
        "artifact_groups": ["episode/summary.json"],
    }


class TaskRegistryTests(unittest.TestCase):
    def test_registry_loads_current_task_manifests(self) -> None:
        specs = load_task_specs()

        expected = {
            TASK_WS_PRB_PING_V1,
            TASK_E2_KPM_PRB_PING_V1,
            TASK_WS_PRB_NOOP_GUARD_V1,
            TASK_WS_PRB_ERROR_REPAIR_V1,
            TASK_WS_PRB_ACTION_BUDGET_V1,
            TASK_E2_KPM_JSON_CONSISTENCY_V1,
            TASK_METRICS_STALENESS_NOOP_V1,
            TASK_E2_CCC_PRB_POLICY_PING_V1,
            TASK_E2_RC_DU_PRB_POLICY_PING_V1,
            TASK_E2_CONTROL_API_CONSISTENCY_V1,
            TASK_WS_SSB_POWER_GUARD_V1,
            TASK_WS_SSB_POWER_REPAIR_V1,
        }
        self.assertEqual(set(specs), expected)
        self.assertEqual(implemented_episode_task_ids(), expected)
        self.assertTrue(is_implemented_episode_task(TASK_WS_PRB_PING_V1))
        self.assertEqual(episode_stage_for_task(TASK_WS_PRB_PING_V1), "v3_episode")
        self.assertEqual(suite_stage_for_task(TASK_WS_PRB_PING_V1), "v3_1_suite")
        self.assertEqual(episode_stage_for_task(TASK_E2_KPM_PRB_PING_V1), "v4_episode")
        self.assertEqual(suite_stage_for_task(TASK_E2_KPM_PRB_PING_V1), "v4_suite")
        self.assertEqual(episode_stage_for_task(TASK_WS_PRB_NOOP_GUARD_V1), "v3_2_episode")
        self.assertEqual(suite_stage_for_task(TASK_E2_KPM_JSON_CONSISTENCY_V1), "v4_1_suite")
        self.assertEqual(episode_stage_for_task(TASK_E2_CCC_PRB_POLICY_PING_V1), "v4_2_episode")
        self.assertEqual(suite_stage_for_task(TASK_E2_CONTROL_API_CONSISTENCY_V1), "v4_2_suite")
        self.assertEqual(episode_stage_for_task(TASK_WS_SSB_POWER_REPAIR_V1), "v3_3_episode")

    def test_task_conformance_checks_match_manifest_contract(self) -> None:
        v3 = conformance_checks_for_task(TASK_WS_PRB_PING_V1)
        v4 = conformance_checks_for_task(TASK_E2_KPM_PRB_PING_V1)
        v3_2 = conformance_checks_for_task(TASK_METRICS_STALENESS_NOOP_V1)
        v4_1 = conformance_checks_for_task(TASK_E2_KPM_JSON_CONSISTENCY_V1)
        ccc = conformance_checks_for_task(TASK_E2_CCC_PRB_POLICY_PING_V1)
        rc_du = conformance_checks_for_task(TASK_E2_RC_DU_PRB_POLICY_PING_V1)
        consistency = conformance_checks_for_task(TASK_E2_CONTROL_API_CONSISTENCY_V1)
        ssb_guard = conformance_checks_for_task(TASK_WS_SSB_POWER_GUARD_V1)
        ssb_repair = conformance_checks_for_task(TASK_WS_SSB_POWER_REPAIR_V1)

        expected_v3 = {
            "docker_e2e_assets",
            "open5gs_core_health",
            "srsue_zmq_attach",
            "ping_traffic_path",
            "websocket_prb_policy_action",
        }
        expected_v4 = {
            "flexric_docker_assets",
            "near_rt_ric_health",
            "ocudu_e2_config",
            "e2_setup_path",
            "e2_kpm_subscription",
            "e2_pcap_log_oracle",
        }
        self.assertEqual(v3, expected_v3)
        self.assertEqual(v3_2, expected_v3 | {"scenario_metrics_staleness_mask"})
        self.assertEqual(v4, expected_v4)
        self.assertEqual(v4_1, expected_v4)
        self.assertEqual(ccc, expected_v4 | {"e2_ccc_prb_control_path"})
        self.assertEqual(rc_du, expected_v4 | {"e2_rc_du_prb_control_path"})
        self.assertEqual(
            consistency,
            expected_v4 | {"e2_ccc_prb_control_path", "e2_rc_du_prb_control_path"},
        )
        self.assertEqual(ssb_guard, expected_v3 - {"websocket_prb_policy_action"} | {"websocket_ssb_power_action"})
        self.assertEqual(ssb_repair, expected_v3 - {"websocket_prb_policy_action"} | {"websocket_ssb_power_action"})

    def test_every_task_conformance_check_exists(self) -> None:
        conformance_ids = {spec.id for spec in load_conformance_specs(Path("benchmark/conformance/tests.json"))}

        missing = {
            task_id: sorted(set(spec.required_conformance) - conformance_ids)
            for task_id, spec in load_task_specs().items()
            if set(spec.required_conformance) - conformance_ids
        }

        self.assertEqual(missing, {})

    def test_unsupported_task_fails_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported benchmark task"):
            get_task_spec("missing_task_v1")

    def test_malformed_task_manifest_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "bad_task_v1"
            task_dir.mkdir()
            (task_dir / "task.json").write_text(json.dumps({"id": "bad_task_v1"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "name"):
                load_task_specs(task_dir.parent)

    def test_duplicate_task_ids_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for prefix in ("a", "b"):
                task_dir = root / prefix / "dup_task_v1"
                task_dir.mkdir(parents=True)
                manifest = valid_manifest("dup_task_v1")
                manifest["name"] = "Duplicate"
                (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Duplicate task id"):
                load_task_specs(root)

    def test_task_manifest_catalogs_are_exported(self) -> None:
        self.assertIn("SET_PRB_POLICY_RATIO_WS", ACTION_TYPES)
        self.assertIn("NO_ACTION", ACTION_TYPES)
        self.assertIn("docker_e2e", RUNTIME_FAMILIES)
        self.assertIn("scored", READINESS_LEVELS)
        self.assertIn("json_metrics", OBSERVATION_SOURCES)
        self.assertIn("metrics_continuity", SCORE_DIMENSIONS)

    def test_task_manifest_rejects_unknown_catalog_values(self) -> None:
        cases = [
            ("runtime", "bad_runtime", "Unknown 'runtime'"),
            ("readiness", "bad_readiness", "Unknown 'readiness'"),
            ("action_types", ["BAD_ACTION"], "Unknown 'action_types'"),
            ("observation_sources", ["bad_source"], "Unknown 'observation_sources'"),
            ("scoring", ["bad_score"], "Unknown 'scoring'"),
            ("artifact_groups", ["/tmp/summary.json"], "Malformed artifact_groups"),
        ]
        for field, value, expected_error in cases:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as tmpdir:
                    task_dir = Path(tmpdir) / "unit_task_v1"
                    task_dir.mkdir()
                    manifest = valid_manifest()
                    manifest[field] = value
                    (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

                    with self.assertRaisesRegex(ValueError, expected_error):
                        load_task_specs(task_dir.parent)

    def test_task_manifest_rejects_raw_wire_commands_as_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "unit_task_v1"
            task_dir.mkdir()
            manifest = valid_manifest()
            manifest["action_types"] = ["ssb_set"]
            (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Wire command names are not task action types"):
                load_task_specs(task_dir.parent)

    def test_no_action_is_manifest_only_decision_not_runtime_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "unit_task_v1"
            task_dir.mkdir()
            manifest = valid_manifest()
            manifest["action_types"] = ["NO_ACTION"]
            (task_dir / "task.json").write_text(json.dumps(manifest), encoding="utf-8")

            specs = load_task_specs(task_dir.parent)
            self.assertEqual(specs["unit_task_v1"].action_types, ("NO_ACTION",))

    def test_every_task_uses_canonical_scoring_dimensions(self) -> None:
        invalid = {
            task_id: sorted(set(spec.scoring) - SCORE_DIMENSIONS)
            for task_id, spec in load_task_specs().items()
            if set(spec.scoring) - SCORE_DIMENSIONS
        }

        self.assertEqual(invalid, {})

    def test_shared_schemas_and_docs_mention_current_tasks(self) -> None:
        paths = [
            Path("benchmark/schemas/actions.schema.json"),
            Path("benchmark/schemas/observations.schema.json"),
            Path("benchmark/README.md"),
            Path("benchmark/tasks/README.md"),
            Path("benchmark/agents/README.md"),
            Path("benchmark/API_REFERENCE.md"),
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        for task_id in supported_task_ids():
            self.assertIn(task_id, text)

    def test_docs_explain_task_api_boundary_and_future_roadmap(self) -> None:
        api_reference = Path("benchmark/API_REFERENCE.md").read_text(encoding="utf-8")
        task_readme = Path("benchmark/tasks/README.md").read_text(encoding="utf-8")

        self.assertIn("Boundary Model", api_reference)
        self.assertIn("Future API Implementation Roadmap", api_reference)
        self.assertIn("Task manifests consume APIs", api_reference)
        self.assertIn("NO_ACTION", task_readme)
        self.assertIn("not a runtime API command", task_readme)
        self.assertIn("built-in deterministic controller", task_readme)
        self.assertIn("--controller", task_readme)

    def test_task_readmes_have_agent_facing_contract_sections(self) -> None:
        required_headers = [
            "## Goal",
            "## APIs Used",
            "## How To Trigger",
            "## Agent Interaction Loop",
            "## Allowed Actions",
            "## Observation Contract",
            "## Scoring",
            "## Unscored Conditions",
            "## Required Conformance",
            "## Artifacts",
            "## Limitations",
        ]

        missing: dict[str, list[str]] = {}
        for task_id, spec in load_task_specs().items():
            readme = Path("benchmark/tasks") / task_id / "README.md"
            text = readme.read_text(encoding="utf-8")
            absent = [header for header in required_headers if header not in text]
            absent.extend(score for score in spec.scoring if score not in text)
            if absent:
                missing[task_id] = absent

        self.assertEqual(missing, {})


if __name__ == "__main__":
    unittest.main()
