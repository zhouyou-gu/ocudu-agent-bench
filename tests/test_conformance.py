import unittest
from pathlib import Path

from benchmark.benchmark_api.conformance import load_conformance_specs


class ConformanceTests(unittest.TestCase):
    def test_loads_required_stub_specs(self) -> None:
        specs = load_conformance_specs(Path("benchmark/conformance/tests.json"))
        ids = {spec.id for spec in specs}
        self.assertIn("remote_tools_ocudu_root", ids)
        self.assertIn("websocket_command_path", ids)
        self.assertIn("json_metrics_stream", ids)
        self.assertIn("e2_kpm", ids)
        self.assertIn("e2_rc_ccc", ids)
        self.assertIn("zmq_rf_path", ids)
        self.assertIn("pcap_log_oracle", ids)
        self.assertTrue(all(spec.status == "stub" for spec in specs))


if __name__ == "__main__":
    unittest.main()

