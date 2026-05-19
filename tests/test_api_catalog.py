import unittest

from benchmark.benchmark_api.api_catalog import build_api_projection, load_api_catalog
from benchmark.benchmark_api.types import RanActionType, RanApiKind


class ApiCatalogTests(unittest.TestCase):
    def test_catalog_contains_grounded_ocudu_and_flexric_bindings(self) -> None:
        catalog = load_api_catalog()

        self.assertEqual(catalog[RanApiKind.OCUDU_WEBSOCKET_PRB_POLICY].wire_command, "rrm_policy_ratio_set")
        self.assertEqual(catalog[RanApiKind.OCUDU_WEBSOCKET_SSB_POWER].wire_command, "ssb_set")
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


if __name__ == "__main__":
    unittest.main()
