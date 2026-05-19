import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.config import parse_config


class ConfigTests(unittest.TestCase):
    def test_section_style_config_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            path.write_text(
                "\n".join(
                    [
                        "remote:",
                        "    ssh user@example",
                        "    ssh-key ~/.ssh/bench-key",
                        "    workspace ~/bench-workspace",
                        "    ocudu-root ~/bench-workspace/ocudu",
                        "",
                        "runtime:",
                        "    open5gs-image bench/open5gs:v1",
                    ]
                ),
                encoding="utf-8",
            )

            config = parse_config(path)

        self.assertEqual(config.ssh_target, "user@example")
        self.assertEqual(config.ssh_key, "~/.ssh/bench-key")
        self.assertEqual(config.runtime.workspace, "~/bench-workspace")
        self.assertEqual(config.runtime.ocudu_root, "~/bench-workspace/ocudu")

    def test_key_value_config_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            path.write_text(
                "\n".join(
                    [
                        "remote.ssh=runner@example",
                        "remote.ssh_key=~/.ssh/runner",
                        "runtime.workspace=~/runner-workspace",
                    ]
                ),
                encoding="utf-8",
            )

            config = parse_config(path)

        self.assertEqual(config.ssh_target, "runner@example")
        self.assertEqual(config.ssh_key, "~/.ssh/runner")
        self.assertEqual(config.runtime.workspace, "~/runner-workspace")


if __name__ == "__main__":
    unittest.main()
