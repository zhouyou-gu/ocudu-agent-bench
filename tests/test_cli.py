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

    def test_remote_ric_prepare_json_wraps_remote_manager(self) -> None:
        original_remote_manager = benchctl.remote_manager

        class FakeManager:
            def prepare_ric(self, dry_run=False, force=False):
                return {"status": "ok", "dry_run": dry_run, "force": force, "image": "skillful-ran/flexric-bench:test"}

        def fake_remote_manager(args):
            return FakeManager()

        benchctl.remote_manager = fake_remote_manager
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(["remote", "ric-prepare", "--config", "unit.config", "--dry-run", "--force", "--json"])
        finally:
            benchctl.remote_manager = original_remote_manager

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["dry_run"])
        self.assertTrue(output["force"])

    def test_remote_provision_json_wraps_remote_manager(self) -> None:
        original_remote_manager = benchctl.remote_manager

        class FakeManager:
            def provision(self, stage="all", dry_run=False, force=False):
                return {"status": "ok", "stage": stage, "dry_run": dry_run, "force": force}

        def fake_remote_manager(args):
            return FakeManager()

        benchctl.remote_manager = fake_remote_manager
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    ["remote", "provision", "--config", "unit.config", "--stage", "assets", "--dry-run", "--force", "--json"]
                )
        finally:
            benchctl.remote_manager = original_remote_manager

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["stage"], "assets")
        self.assertTrue(output["dry_run"])
        self.assertTrue(output["force"])

    def test_remote_reset_workspace_json_wraps_remote_manager(self) -> None:
        original_remote_manager = benchctl.remote_manager

        class FakeManager:
            def reset_workspace(self, force=False, dry_run=False):
                return {"status": "ok", "force": force, "dry_run": dry_run, "workspace": "/remote/workspace"}

        def fake_remote_manager(args):
            return FakeManager()

        benchctl.remote_manager = fake_remote_manager
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    ["remote", "reset-workspace", "--config", "unit.config", "--dry-run", "--force", "--json"]
                )
        finally:
            benchctl.remote_manager = original_remote_manager

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["force"])
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

    def test_episode_run_invokes_conformance_gate_and_runtime(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_conformance = benchctl.run_conformance
        original_run_episode = benchctl.run_episode

        def fake_remote_manager(args):
            return {"remote": "manager"}

        def fake_run_conformance(**kwargs):
            self.assertEqual(kwargs["checks"], benchctl.V3_EPISODE_GATE_CHECKS)
            return {"status": "pass", "checks": []}

        def fake_run_episode(**kwargs):
            self.assertEqual(kwargs["task"], "ws_prb_ping_v1")
            self.assertEqual(kwargs["duration"], 1)
            return {"status": "ok", "summary": {"scored": True}}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_conformance = fake_run_conformance
        benchctl.run_episode = fake_run_episode
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "episode",
                        "run",
                        "--config",
                        "unit.config",
                        "--task",
                        "ws_prb_ping_v1",
                        "--duration",
                        "1",
                        "--json",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_conformance = original_run_conformance
            benchctl.run_episode = original_run_episode

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["summary"]["scored"])
        self.assertEqual(output["conformance"]["status"], "pass")

    def test_episode_run_uses_v4_gate_for_e2_task(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_conformance = benchctl.run_conformance
        original_run_episode = benchctl.run_episode

        def fake_remote_manager(args):
            return {"remote": "manager"}

        def fake_run_conformance(**kwargs):
            self.assertEqual(kwargs["checks"], benchctl.V4_EPISODE_GATE_CHECKS)
            return {"status": "pass", "checks": []}

        def fake_run_episode(**kwargs):
            self.assertEqual(kwargs["task"], "e2_kpm_prb_ping_v1")
            return {"status": "ok", "summary": {"scored": True}}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_conformance = fake_run_conformance
        benchctl.run_episode = fake_run_episode
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "episode",
                        "run",
                        "--config",
                        "unit.config",
                        "--task",
                        "e2_kpm_prb_ping_v1",
                        "--duration",
                        "1",
                        "--json",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_conformance = original_run_conformance
            benchctl.run_episode = original_run_episode

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["conformance"]["status"], "pass")

    def test_episode_run_skip_conformance_marks_unscored(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_episode = benchctl.run_episode

        def fake_remote_manager(args):
            return object()

        def fake_run_episode(**kwargs):
            self.assertEqual(kwargs["unscored_reason"], "conformance skipped")
            return {"status": "error", "summary": {"scored": False, "unscored_reason": kwargs["unscored_reason"]}}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_episode = fake_run_episode
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(["episode", "run", "--config", "unit.config", "--skip-conformance", "--json"])
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_episode = original_run_episode

        self.assertEqual(code, 1)
        output = json.loads(stdout.getvalue())
        self.assertFalse(output["summary"]["scored"])
        self.assertEqual(output["summary"]["unscored_reason"], "conformance skipped")

    def test_episode_cleanup_wraps_runtime_cleanup(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_cleanup_episode = benchctl.cleanup_episode

        def fake_remote_manager(args):
            return object()

        def fake_cleanup_episode(**kwargs):
            self.assertEqual(kwargs["run_id"], "unit-ep")
            return {"status": "ok", "run_id": kwargs["run_id"]}

        benchctl.remote_manager = fake_remote_manager
        benchctl.cleanup_episode = fake_cleanup_episode
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(["episode", "cleanup", "--config", "unit.config", "--run-id", "unit-ep", "--json"])
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.cleanup_episode = original_cleanup_episode

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["run_id"], "unit-ep")

    def test_episode_suite_parses_options_and_wraps_runner(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_suite = benchctl.run_suite

        def fake_remote_manager(args):
            return {"remote": "manager"}

        def fake_run_suite(**kwargs):
            self.assertEqual(kwargs["task"], "ws_prb_ping_v1")
            self.assertEqual(kwargs["agent"], "invalid_then_fixed")
            self.assertEqual(kwargs["runs"], 4)
            self.assertEqual(kwargs["duration"], 7)
            self.assertEqual(kwargs["seed"], 11)
            self.assertEqual(kwargs["suite_id"], "unit-suite")
            self.assertEqual(kwargs["ws_port"], 9002)
            self.assertTrue(kwargs["skip_conformance"])
            return {"status": "ok", "suite_id": kwargs["suite_id"], "scored_runs": 1}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_suite = fake_run_suite
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "episode",
                        "suite",
                        "--config",
                        "unit.config",
                        "--task",
                        "ws_prb_ping_v1",
                        "--agent",
                        "invalid_then_fixed",
                        "--runs",
                        "4",
                        "--duration",
                        "7",
                        "--seed",
                        "11",
                        "--suite-id",
                        "unit-suite",
                        "--ws-port",
                        "9002",
                        "--skip-conformance",
                        "--json",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_suite = original_run_suite

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["suite_id"], "unit-suite")

    def test_episode_suite_accepts_v4_task(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_suite = benchctl.run_suite

        def fake_remote_manager(args):
            return {"remote": "manager"}

        def fake_run_suite(**kwargs):
            self.assertEqual(kwargs["task"], "e2_kpm_prb_ping_v1")
            return {"status": "ok", "suite_id": "unit-v4-suite", "scored_runs": 1}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_suite = fake_run_suite
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "episode",
                        "suite",
                        "--config",
                        "unit.config",
                        "--task",
                        "e2_kpm_prb_ping_v1",
                        "--suite-id",
                        "unit-v4-suite",
                        "--json",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_suite = original_run_suite

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["suite_id"], "unit-v4-suite")

    def test_episode_suite_accepts_new_task_and_baseline_agents(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_suite = benchctl.run_suite

        def fake_remote_manager(args):
            return {"remote": "manager"}

        def fake_run_suite(**kwargs):
            self.assertEqual(kwargs["task"], "metrics_staleness_noop_v1")
            self.assertEqual(kwargs["agent"], "stale_guard_prb")
            return {"status": "ok", "suite_id": "unit-stale-suite", "scored_runs": 1}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_suite = fake_run_suite
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "episode",
                        "suite",
                        "--config",
                        "unit.config",
                        "--task",
                        "metrics_staleness_noop_v1",
                        "--agent",
                        "stale_guard_prb",
                        "--suite-id",
                        "unit-stale-suite",
                        "--json",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_suite = original_run_suite

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["suite_id"], "unit-stale-suite")

    def test_episode_run_accepts_new_task_id(self) -> None:
        original_remote_manager = benchctl.remote_manager
        original_run_conformance = benchctl.run_conformance
        original_run_episode = benchctl.run_episode

        def fake_remote_manager(args):
            return {"remote": "manager"}

        def fake_run_conformance(**kwargs):
            self.assertIn("websocket_prb_policy_action", kwargs["checks"])
            return {"status": "pass", "checks": []}

        def fake_run_episode(**kwargs):
            self.assertEqual(kwargs["task"], "ws_prb_action_budget_v1")
            return {"status": "ok", "summary": {"scored": True}}

        benchctl.remote_manager = fake_remote_manager
        benchctl.run_conformance = fake_run_conformance
        benchctl.run_episode = fake_run_episode
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(
                    [
                        "episode",
                        "run",
                        "--config",
                        "unit.config",
                        "--task",
                        "ws_prb_action_budget_v1",
                        "--duration",
                        "1",
                        "--json",
                    ]
                )
        finally:
            benchctl.remote_manager = original_remote_manager
            benchctl.run_conformance = original_run_conformance
            benchctl.run_episode = original_run_episode

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
