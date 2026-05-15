import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api import BenchmarkEnv


class FakeRemoteManager:
    def __init__(self, config) -> None:
        self.config = config
        self.created_metadata = []

    def check(self):
        return {
            "status": "ok",
            "remote": {
                "workspace_exists": True,
                "workspace_is_dir": True,
            },
        }

    def init_workspace(self):
        raise AssertionError("workspace init should not be called when workspace is ready")

    def create_run_metadata(self, run_id, metadata):
        self.created_metadata.append((run_id, metadata))
        return {
            "status": "ok",
            "returncode": 0,
            "run_id": run_id,
            "stdout": "metadata created",
            "stderr": "",
        }


class BenchmarkEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        tmp.write(
            """
remote:
    ssh zhouyou@10.34.23.184
    ssh-key ~/.ssh/zhouyou5090pc
"""
        )
        tmp.close()
        self.config_path = Path(tmp.name)

    def test_lifecycle_is_deterministic_stub(self) -> None:
        managers = []

        def factory(config):
            manager = FakeRemoteManager(config)
            managers.append(manager)
            return manager

        env = BenchmarkEnv(self.config_path, remote_manager_factory=factory)
        reset = env.reset({"run_id": "test-run"})
        self.assertEqual(reset["run_id"], "test-run")
        self.assertEqual(reset["stage"], "v1_stub")
        self.assertEqual(reset["remote_check"]["status"], "ok")
        self.assertEqual(reset["run_metadata"]["status"], "ok")
        self.assertEqual(managers[0].created_metadata[0][0], "test-run")

        observation = env.observe()
        self.assertEqual(observation["state"], "not_running")

        rejected = env.act({"type": "SET_PRB_POLICY_RATIO_WS"})
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["status"], "rejected")

        accepted = env.act({"type": "STUB_NOOP", "stub": True})
        self.assertTrue(accepted["accepted"])

        summary = env.close()
        self.assertEqual(summary["state"], "closed")
        self.assertFalse(summary["scored"])
        self.assertEqual(summary["actions"], 2)
        self.assertEqual(summary["accepted_actions"], 1)

    def test_observe_before_reset_returns_structured_error(self) -> None:
        env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
        observation = env.observe()
        self.assertEqual(observation["status"], "error")
        self.assertEqual(observation["reason"], "reset required before observe")

    def test_act_before_reset_and_malformed_actions_are_rejected(self) -> None:
        env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
        before_reset = env.act({"type": "STUB_NOOP", "stub": True})
        self.assertEqual(before_reset["status"], "rejected")
        self.assertEqual(before_reset["reason"], "reset required before act")

        env.reset({"run_id": "test-run"})
        malformed = env.act("not a dict")
        self.assertEqual(malformed["status"], "rejected")
        self.assertEqual(malformed["reason"], "action must be a dictionary")


if __name__ == "__main__":
    unittest.main()
