import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.config import DEFAULT_REMOTE_WORKSPACE, RemoteConfig
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager


class RemoteCommandBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = RemoteConfig(
            ssh_target="zhouyou@10.34.23.184",
            ssh_key="/Users/example/.ssh/key",
        )
        self.manager = RemoteManager(self.cfg)

    def test_ssh_builder_uses_noninteractive_flags(self) -> None:
        argv = self.manager.ssh_argv("true")
        self.assertIn("-i", argv)
        self.assertIn("/Users/example/.ssh/key", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ConnectTimeout=8", argv)
        self.assertIn("zhouyou@10.34.23.184", argv)

    def test_rsync_builder_uses_remote_workspace(self) -> None:
        argv = self.manager.rsync_argv(source=Path("benchmark"), dry_run=True)
        self.assertIn("--dry-run", argv)
        self.assertIn("-e", argv)
        self.assertTrue(any(DEFAULT_REMOTE_WORKSPACE in part for part in argv))

    def test_sync_dry_run_uses_tracked_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            source = repo_root / "benchmark"
            source.mkdir()
            (source / "benchctl.py").write_text("# test\n", encoding="utf-8")
            self.manager._git_tracked_files = lambda repo_root, source: [Path("benchmark/benchctl.py")]  # type: ignore[method-assign]

            result = self.manager.sync(source=source, repo_root=repo_root, dry_run=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source_policy"], "git_tracked")
        self.assertEqual(result["tracked_files"], ["benchmark/benchctl.py"])
        self.assertEqual(result["tracked_file_count"], 1)
        self.assertEqual(result["planned_source"], "<temporary tracked-file staging>/benchmark/")

    def test_init_dry_run_reports_workspace(self) -> None:
        result = self.manager.init_workspace(dry_run=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["workspace"], DEFAULT_REMOTE_WORKSPACE)

    def test_remote_exec_quotes_argv_tokens(self) -> None:
        captured = {}

        def fake_run(argv):
            captured["argv"] = argv

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        self.manager._run = fake_run  # type: ignore[method-assign]
        result = self.manager.exec(["python3", "-c", 'print("hello world")'])
        self.assertEqual(result["status"], "ok")
        self.assertIn("python3 -c 'print(\"hello world\")'", captured["argv"][-1])

    def test_remote_exec_shell_mode_preserves_shell_operators(self) -> None:
        captured = {}

        def fake_run(argv):
            captured["argv"] = argv

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        self.manager._run = fake_run  # type: ignore[method-assign]
        result = self.manager.exec(["printf x | cat"], shell=True)
        self.assertEqual(result["status"], "ok")
        self.assertIn("printf x | cat", captured["argv"][-1])

    def test_remote_exec_empty_command_errors(self) -> None:
        with self.assertRaisesRegex(RemoteCommandError, "requires a command"):
            self.manager.exec([])


if __name__ == "__main__":
    unittest.main()
