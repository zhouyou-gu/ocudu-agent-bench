import contextlib
import io
import json
import unittest

from benchmark import benchctl


class BenchctlTests(unittest.TestCase):
    def test_remote_deps_json_wraps_remote_manager(self) -> None:
        original_remote_manager = benchctl.remote_manager

        class FakeManager:
            def prepare_runtime_deps(self, dry_run=False):
                return {"status": "ok", "dry_run": dry_run, "runtime_root": "/remote/runtime-libs/root"}

        def fake_remote_manager(args):
            return FakeManager()

        benchctl.remote_manager = fake_remote_manager
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(["remote", "deps", "--config", "unit.config", "--dry-run", "--json"])
        finally:
            benchctl.remote_manager = original_remote_manager

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["dry_run"])

    def test_conformance_run_json_exit_code_uses_result_status(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_conformance = benchctl.run_conformance

        def fake_remote_manager(args):
            return object()

        def fake_run_conformance(**kwargs):
            self.assertEqual(kwargs["run_id"], "unit-cli")
            self.assertEqual(kwargs["checks"], {"websocket_command_path"})
            self.assertEqual(kwargs["ws_port"], 9001)
            return {"status": "fail", "run_id": kwargs["run_id"], "checks": []}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_conformance = fake_run_conformance
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "conformance",
                        "run",
                        "--config",
                        "unit.config",
                        "--json",
                        "--run-id",
                        "unit-cli",
                        "--checks",
                        "websocket_command_path",
                        "--ws-port",
                        "9001",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_conformance = original_run_conformance

        self.assertEqual(code, 1)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "fail")
        self.assertEqual(output["run_id"], "unit-cli")


if __name__ == "__main__":
    unittest.main()
