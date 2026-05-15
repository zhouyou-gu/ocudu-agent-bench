import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.config import DEFAULT_REMOTE_WORKSPACE, parse_config


class ConfigTests(unittest.TestCase):
    def write_config(self, text: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        return Path(tmp.name)

    def test_parse_current_shape(self) -> None:
        path = self.write_config(
            """
remote:
    ssh zhouyou@10.34.23.184
    ssh-key ~/.ssh/zhouyou5090pc
"""
        )
        cfg = parse_config(path)
        self.assertEqual(cfg.ssh_target, "zhouyou@10.34.23.184")
        self.assertTrue(cfg.ssh_key.endswith(".ssh/zhouyou5090pc"))
        self.assertEqual(cfg.workspace, DEFAULT_REMOTE_WORKSPACE)

    def test_missing_ssh_target_errors(self) -> None:
        path = self.write_config(
            """
remote:
    ssh-key ~/.ssh/zhouyou5090pc
"""
        )
        with self.assertRaisesRegex(ValueError, "remote.ssh"):
            parse_config(path)

    def test_missing_ssh_key_errors(self) -> None:
        path = self.write_config(
            """
remote:
    ssh zhouyou@10.34.23.184
"""
        )
        with self.assertRaisesRegex(ValueError, "remote.ssh-key"):
            parse_config(path)


if __name__ == "__main__":
    unittest.main()

