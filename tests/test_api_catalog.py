import unittest

from benchmark.benchmark_api.api_catalog import build_api_projection, load_api_catalog
from benchmark.benchmark_api.types import RanActionType, RanApiKind


class ApiCatalogTests(unittest.TestCase):
    def test_catalog_contains_grounded_ocudu_and_flexric_bindings(self) -> None:
        catalog = load_api_catalog()

        self.assertEqual(catalog[RanApiKind.OCUDU_WEBSOCKET_PRB_POLICY].wire_command, "rrm_policy_ratio_set")
        self.assertEqual(catalog[RanApiKind.OCUDU_WEBSOCKET_SSB_POWER].wire_command, "ssb_set")
        self.assertEqual(catalog[RanApiKind.OCUDU_CLI_HANDOVER].wire_command, "ho")
        self.assertEqual(catalog[RanApiKind.OCUDU_CLI_CONDITIONAL_HANDOVER].wire_command, "cho")
        self.assertEqual(catalog[RanApiKind.OCUDU_CLI_CFO_CONTROL].wire_command, "cfo")
        self.assertEqual(catalog[RanApiKind.OCUDU_CLI_TX_TIME_OFFSET_CONTROL].wire_command, "tx_time_offset")
        self.assertEqual(catalog[RanApiKind.CORE_NF_LIFECYCLE_CONTROL].wire_command, "benchmark_core_nf_restart")
        self.assertEqual(
            catalog[RanApiKind.CORE_UE_REGISTRATION_CONTROL].wire_command,
            "benchmark_core_ue_registration_update",
        )
        self.assertEqual(catalog[RanApiKind.OCUDU_JSON_METRICS].wire_command, "metrics_subscribe")
        self.assertEqual(
            catalog[RanApiKind.E2SM_CCC_PRB_POLICY_CONTROL].action_type,
            RanActionType.SET_PRB_POLICY_RATIO_CCC,
        )

    def test_agent_projection_excludes_private_wire_details(self) -> None:
        projection = build_api_projection(["ocudu_websocket_prb_policy", "ocudu_json_metrics"])
        rendered = repr(projection)

        self.assertIn("SET_PRB_POLICY_RATIO_WS", projection["action_types"])
        self.assertIn("json_metrics", projection["observation_sources"])
        self.assertNotIn("rrm_policy_ratio_set", rendered)
        self.assertNotIn("metrics_subscribe", rendered)
        self.assertNotIn("runtime_requirements", rendered)

    def test_cli_mobility_projection_excludes_raw_cli_command_names(self) -> None:
        projection = build_api_projection(
            [
                "ocudu_cli_handover",
                "ocudu_cli_conditional_handover",
                "ocudu_cli_cfo_control",
                "ocudu_cli_tx_time_offset_control",
            ]
        )
        rendered = repr(projection)

        self.assertIn("TRIGGER_HANDOVER_CLI", projection["action_types"])
        self.assertIn("TRIGGER_CONDITIONAL_HANDOVER_CLI", projection["action_types"])
        self.assertIn("SET_CFO_CLI", projection["action_types"])
        self.assertIn("SET_TX_TIME_OFFSET_CLI", projection["action_types"])
        self.assertNotIn("'ho'", rendered)
        self.assertNotIn("'cho'", rendered)
        self.assertNotIn("'cfo'", rendered)
        self.assertNotIn("'tx_time_offset'", rendered)

    def test_core_runtime_support_projection_excludes_private_benchmark_command(self) -> None:
        projection = build_api_projection(["core_nf_lifecycle_control", "core_ue_registration_control"])
        rendered = repr(projection)

        self.assertIn("RESTART_CORE_NF", projection["action_types"])
        self.assertIn("UPDATE_CORE_UE_REGISTRATION", projection["action_types"])
        self.assertNotIn("benchmark_core_nf_restart", rendered)
        self.assertNotIn("benchmark_core_ue_registration_update", rendered)

    def test_ue_runtime_stimulus_is_not_a_task_selectable_api(self) -> None:
        with self.assertRaises(ValueError):
            build_api_projection(["ue_traffic_control"])


if __name__ == "__main__":
    unittest.main()
