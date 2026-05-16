import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.config import parse_config


BASE_CONFIG = """
remote:
    ssh user@host
    ssh-key ~/.ssh/ocudu-benchmark
    workspace ~/skillful-ran-benchmark-workspace
    ocudu-root ~/skillful-ran-benchmark-workspace/ocudu
runtime:
    open5gs-compose ~/skillful-ran-benchmark-workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml
    e2e-config-dir ~/skillful-ran-benchmark-workspace/assets/ocudu-zmq-open5gs-e2e/config
    open5gs-image skillful-ran/open5gs:v2.7.0
    gnb-image skillful-ran/srsran-project-build:release_25_10
    ue-image skillful-ran/srsran-4g-ue-build:release_23_11
sources:
    srsran-project-repo https://github.com/srsran/srsRAN_Project.git
    srsran-project-ref release_25_10
    srsran-4g-repo https://github.com/srsran/srsRAN_4G.git
    srsran-4g-ref release_23_11
    open5gs-ref v2.7.0
provision:
    mode workspace-owned
"""


class ConfigTests(unittest.TestCase):
    def write_config(self, text: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        return Path(tmp.name)

    def test_parse_current_shape(self) -> None:
        path = self.write_config(BASE_CONFIG)
        cfg = parse_config(path)
        self.assertEqual(cfg.ssh_target, "user@host")
        self.assertTrue(cfg.ssh_key.endswith(".ssh/ocudu-benchmark"))
        self.assertEqual(cfg.ocudu_root, "~/skillful-ran-benchmark-workspace/ocudu")
        self.assertEqual(cfg.workspace, "~/skillful-ran-benchmark-workspace")
        self.assertEqual(
            cfg.runtime.open5gs_compose,
            "~/skillful-ran-benchmark-workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml",
        )
        self.assertEqual(cfg.runtime.open5gs_image, "skillful-ran/open5gs:v2.7.0")
        self.assertEqual(cfg.runtime.gnb_image, "skillful-ran/srsran-project-build:release_25_10")
        self.assertEqual(cfg.sources.srsran_project_ref, "release_25_10")
        self.assertEqual(cfg.provision.mode, "workspace-owned")
        self.assertEqual(cfg.ric_provider, "flexric")

    def test_parse_tracked_example_config(self) -> None:
        cfg = parse_config(Path(__file__).resolve().parents[2] / ".config.example")

        self.assertEqual(cfg.ssh_target, "user@host")
        self.assertEqual(cfg.ocudu_root, "~/skillful-ran-benchmark-workspace/ocudu")
        self.assertEqual(cfg.runtime.open5gs_image, "skillful-ran/open5gs:v2.7.0")
        self.assertEqual(cfg.sources.open5gs_ref, "v2.7.0")

    def test_parse_drax_existing_provider(self) -> None:
        path = self.write_config(
            BASE_CONFIG
            + """
ric:
    provider drax-existing
drax:
    kubeconfig ~/skillful-ran-benchmark-workspace/drax/kubeconfig
    namespace ricplt
    kubectl-image bitnami/kubectl:1.30.8
    e2-endpoint 10.0.0.10:36421
    e2-bind-addr 0.0.0.0
    kpm-api-url http://10.0.0.20:8080
"""
        )
        cfg = parse_config(path)
        self.assertEqual(cfg.ric_provider, "drax-existing")
        self.assertEqual(cfg.drax.namespace, "ricplt")
        self.assertEqual(cfg.drax.kubeconfig, "~/skillful-ran-benchmark-workspace/drax/kubeconfig")
        self.assertEqual(cfg.drax.e2_endpoint, "10.0.0.10:36421")
        self.assertEqual(cfg.drax.kpm_api_url, "http://10.0.0.20:8080")

    def test_missing_ssh_target_errors(self) -> None:
        path = self.write_config(
            """
remote:
    ssh-key ~/.ssh/ocudu-benchmark
"""
        )
        with self.assertRaisesRegex(ValueError, "remote.ssh"):
            parse_config(path)

    def test_missing_ssh_key_errors(self) -> None:
        path = self.write_config(
            """
remote:
    ssh user@host
"""
        )
        with self.assertRaisesRegex(ValueError, "remote.ssh-key"):
            parse_config(path)

    def test_missing_runtime_values_error(self) -> None:
        path = self.write_config(
            """
remote:
    ssh user@host
    ssh-key ~/.ssh/ocudu-benchmark
    workspace ~/skillful-ran-benchmark-workspace
    ocudu-root ~/skillful-ran-benchmark-workspace/ocudu
"""
        )
        with self.assertRaisesRegex(ValueError, "runtime.open5gs-compose"):
            parse_config(path)

    def test_drax_provider_requires_api_values(self) -> None:
        path = self.write_config(
            BASE_CONFIG
            + """
ric:
    provider drax-existing
drax:
    namespace ricplt
"""
        )
        with self.assertRaisesRegex(ValueError, "drax.kubeconfig"):
            parse_config(path)


if __name__ == "__main__":
    unittest.main()
