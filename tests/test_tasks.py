import json
import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.conformance import load_conformance_specs
from benchmark.benchmark_api.tasks import (
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_E2_KPM_JSON_CONSISTENCY_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
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

    def test_task_conformance_checks_match_manifest_contract(self) -> None:
        v3 = conformance_checks_for_task(TASK_WS_PRB_PING_V1)
        v4 = conformance_checks_for_task(TASK_E2_KPM_PRB_PING_V1)
        v3_2 = conformance_checks_for_task(TASK_METRICS_STALENESS_NOOP_V1)
        v4_1 = conformance_checks_for_task(TASK_E2_KPM_JSON_CONSISTENCY_V1)
        ccc = conformance_checks_for_task(TASK_E2_CCC_PRB_POLICY_PING_V1)
        rc_du = conformance_checks_for_task(TASK_E2_RC_DU_PRB_POLICY_PING_V1)
        consistency = conformance_checks_for_task(TASK_E2_CONTROL_API_CONSISTENCY_V1)

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
                (task_dir / "task.json").write_text(
                    json.dumps(
                        {
                            "id": "dup_task_v1",
                            "name": "Duplicate",
                            "summary": "duplicate",
                            "stage": "v1_episode",
                            "suite_stage": "v1_suite",
                            "runtime": "unit",
                            "readiness": "test",
                            "action_types": ["STUB_NOOP"],
                            "observation_sources": ["stub"],
                            "required_conformance": ["remote_tools_ocudu_root"],
                            "scoring": ["stub"],
                            "artifact_groups": ["episode/summary.json"],
                        }
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(ValueError, "Duplicate task id"):
                load_task_specs(root)

    def test_shared_schemas_and_docs_mention_current_tasks(self) -> None:
        paths = [
            Path("benchmark/schemas/actions.schema.json"),
            Path("benchmark/schemas/observations.schema.json"),
            Path("benchmark/README.md"),
            Path("benchmark/tasks/README.md"),
            Path("benchmark/agents/README.md"),
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        for task_id in supported_task_ids():
            self.assertIn(task_id, text)


if __name__ == "__main__":
    unittest.main()
