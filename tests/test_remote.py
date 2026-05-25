import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark_api.config import SiteConfig, RuntimeConfig
from benchmark_api.remote import RemoteManager


class RemoteTests(unittest.TestCase):
    def test_remote_check_without_probe_does_not_open_ssh(self) -> None:
        manager = RemoteManager(SiteConfig(ssh_target="user@example", runtime=RuntimeConfig(workspace="~/bench")))

        with patch("benchmark_api.remote.subprocess.run") as run:
            result = manager.check(probe=False)

        self.assertEqual(result["status"], "configured")
        run.assert_not_called()

    def test_sync_dry_run_uses_rsync_to_synced_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "benchmark"
            local.mkdir()
            (local / "README.md").write_text("# test\n", encoding="utf-8")
            manager = RemoteManager(
                SiteConfig(
                    ssh_target="user@example",
                    ssh_key="~/.ssh/bench",
                    runtime=RuntimeConfig(workspace="~/bench-workspace"),
                )
            )

            completed = subprocess.CompletedProcess(
                args=["rsync"],
                returncode=0,
                stdout="<f+++++++++ README.md\n",
                stderr="",
            )
            with patch("benchmark_api.remote.subprocess.run", return_value=completed) as run:
                result = manager.sync_benchmark(local, dry_run=True)

        command = run.call_args.args[0]
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["itemized_change_count"], 1)
        self.assertIn("--dry-run", command)
        self.assertIn("--delete", command)
        self.assertEqual(command[-1], "user@example:~/bench-workspace/synced/benchmark/")


if __name__ == "__main__":
    unittest.main()
