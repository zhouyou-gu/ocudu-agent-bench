import tempfile
import unittest
from pathlib import Path

import benchmark.benchmark_api.env as env_module
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
    ssh user@host
    ssh-key ~/.ssh/ocudu-benchmark
    workspace ~/skillful-ran-benchmark-workspace
    ocudu-root ~/skillful-ran-benchmark-workspace/ocudu
runtime:
    open5gs-compose ~/skillful-ran-benchmark-workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml
    e2e-config-dir ~/skillful-ran-benchmark-workspace/assets/ocudu-zmq-open5gs-e2e/config
    open5gs-image skillful-ran/open5gs:v2.7.0
    gnb-image skillful-ran/ocudu-build:release_26_04
    ue-image skillful-ran/srsran-4g-ue-build:release_23_11
sources:
    ocudu-repo https://gitlab.com/ocudu/ocudu.git
    ocudu-ref release_26_04
    srsran-4g-repo https://github.com/srsran/srsRAN_4G.git
    srsran-4g-ref release_23_11
    open5gs-ref v2.7.0
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

    def test_unknown_task_is_rejected(self) -> None:
        env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)

        with self.assertRaisesRegex(ValueError, "Unsupported benchmark task"):
            env.reset({"run_id": "bad-task", "task": "does_not_exist_v1"})

    def test_act_before_reset_and_malformed_actions_are_rejected(self) -> None:
        env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
        before_reset = env.act({"type": "STUB_NOOP", "stub": True})
        self.assertEqual(before_reset["status"], "rejected")
        self.assertEqual(before_reset["reason"], "reset required before act")

        env.reset({"run_id": "test-run"})
        malformed = env.act("not a dict")
        self.assertEqual(malformed["status"], "rejected")
        self.assertEqual(malformed["reason"], "action must be a dictionary")

    def test_conformance_observe_mode_attaches_results_without_failing_reset(self) -> None:
        original = env_module.run_conformance

        def fake_run_conformance(**kwargs):
            return {
                "status": "fail",
                "backend_enablement": {"ssh": True, "websocket": False, "json_metrics": False},
                "checks": [],
            }

        env_module.run_conformance = fake_run_conformance
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset({"run_id": "test-run", "conformance": "observe"})
        finally:
            env_module.run_conformance = original

        self.assertEqual(reset["status"], "ok")
        self.assertEqual(reset["conformance"]["status"], "fail")
        self.assertEqual(reset["adapters"]["ssh"], "ready")
        self.assertEqual(reset["adapters"]["websocket"], "disabled")

    def test_conformance_required_mode_fails_reset_on_conformance_failure(self) -> None:
        original = env_module.run_conformance

        def fake_run_conformance(**kwargs):
            return {
                "status": "fail",
                "backend_enablement": {"ssh": True, "websocket": False, "json_metrics": False},
                "checks": [],
            }

        env_module.run_conformance = fake_run_conformance
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset({"run_id": "test-run", "conformance": "required"})
        finally:
            env_module.run_conformance = original

        self.assertEqual(reset["status"], "error")
        self.assertEqual(reset["reason"], "required conformance failed")
        self.assertEqual(reset["conformance"]["status"], "fail")

    def test_v3_episode_lifecycle_uses_runtime_adapter(self) -> None:
        original_run_conformance = env_module.run_conformance
        original_episode_runtime = env_module.EpisodeRuntime

        class FakeEpisodeRuntime:
            def __init__(self, remote, repo_root=None) -> None:
                self.remote = remote
                self.repo_root = repo_root
                self.started = None
                self.actions = []

            def start(self, options):
                self.started = options
                return {"status": "ok", "stage": "v3_episode", "run_id": options.run_id}

            def observe(self):
                return {
                    "status": "ok",
                    "stage": "v3_episode",
                    "run_id": self.started.run_id,
                    "state": "running",
                    "observation": {"type": "ws_prb_ping_v1", "ping": {"packets_received": 1}},
                }

            def act(self, action):
                self.actions.append(action)
                return {"status": "ok", "stage": "v3_episode", "accepted": True, "reason": "accepted"}

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                return {"status": "ok", "stage": "v3_episode", "scored": cleanup_success, "unscored_reason": unscored_reason}

        def fake_run_conformance(**kwargs):
            self.assertEqual(kwargs["checks"], env_module.V3_EPISODE_GATE_CHECKS)
            return {
                "status": "pass",
                "backend_enablement": {
                    "ssh": True,
                    "websocket": True,
                    "json_metrics": True,
                    "docker_e2e": True,
                },
                "checks": [],
            }

        env_module.run_conformance = fake_run_conformance
        env_module.EpisodeRuntime = FakeEpisodeRuntime
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset(
                {
                    "run_id": "v3-unit",
                    "task": "ws_prb_ping_v1",
                    "conformance": "required",
                    "duration": 1,
                }
            )
            observation = env.observe()
            action = env.act(
                {
                    "type": "SET_PRB_POLICY_RATIO_WS",
                    "min_prb_policy_ratio": 10,
                    "max_prb_policy_ratio": 90,
                }
            )
            close = env.close()
        finally:
            env_module.run_conformance = original_run_conformance
            env_module.EpisodeRuntime = original_episode_runtime

        self.assertEqual(reset["stage"], "v3_episode")
        self.assertEqual(reset["state"], "running")
        self.assertEqual(observation["stage"], "v3_episode")
        self.assertTrue(action["accepted"])
        self.assertEqual(close["stage"], "v3_episode")
        self.assertTrue(close["summary"]["scored"])

    def test_episode_act_none_is_noop_decision_without_runtime_action(self) -> None:
        original_run_conformance = env_module.run_conformance
        original_episode_runtime = env_module.EpisodeRuntime

        class FakeEpisodeRuntime:
            decisions = []

            def __init__(self, remote, repo_root=None) -> None:
                self.started = None

            def start(self, options):
                self.started = options
                return {"status": "ok", "stage": "v3_2_episode", "run_id": options.run_id}

            def observe(self):
                return {
                    "status": "ok",
                    "stage": "v3_2_episode",
                    "run_id": self.started.run_id,
                    "state": "running",
                    "observation": {"type": "ws_prb_noop_guard_v1", "metrics": {"present": True}},
                }

            def act(self, action):
                raise AssertionError("None no-op decisions must not be dispatched to the runtime")

            def record_decision(self, action, telemetry=None, decision_latency_s=None, observation=None):
                type(self).decisions.append(
                    {
                        "action": action,
                        "telemetry": telemetry,
                        "decision_latency_s": decision_latency_s,
                        "observation": observation,
                    }
                )
                return {"status": "ok", "decision_logged": True}

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                return {"status": "ok", "stage": "v3_2_episode", "scored": cleanup_success}

        def fake_run_conformance(**kwargs):
            return {"status": "pass", "backend_enablement": {"ssh": True, "docker_e2e": True}, "checks": []}

        env_module.run_conformance = fake_run_conformance
        env_module.EpisodeRuntime = FakeEpisodeRuntime
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset(
                {
                    "run_id": "noop-unit",
                    "task": "ws_prb_noop_guard_v1",
                    "conformance": "required",
                    "duration": 0,
                }
            )
            noop = env.act(None)
            malformed = env.act("not a dict", telemetry={"prompt_tokens": 5})
        finally:
            env_module.run_conformance = original_run_conformance
            env_module.EpisodeRuntime = original_episode_runtime

        self.assertEqual(reset["status"], "ok")
        self.assertEqual(noop["status"], "ok")
        self.assertEqual(noop["reason"], "no-op decision")
        self.assertEqual(malformed["status"], "rejected")
        self.assertEqual(malformed["reason"], "action must be a dictionary")
        self.assertFalse(noop["action_logged"])
        self.assertEqual(env.actions, [])
        self.assertEqual(len(FakeEpisodeRuntime.decisions), 2)
        self.assertIsNone(FakeEpisodeRuntime.decisions[0]["action"])
        self.assertEqual(FakeEpisodeRuntime.decisions[1]["action"], "not a dict")
        self.assertEqual(FakeEpisodeRuntime.decisions[1]["telemetry"], {"prompt_tokens": 5})

    def test_v4_episode_lifecycle_uses_v4_conformance_gate(self) -> None:
        original_run_conformance = env_module.run_conformance
        original_episode_runtime = env_module.EpisodeRuntime

        class FakeEpisodeRuntime:
            def __init__(self, remote, repo_root=None) -> None:
                self.started = None

            def start(self, options):
                self.started = options
                return {"status": "ok", "stage": "v4_episode", "run_id": options.run_id}

            def observe(self):
                return {
                    "status": "ok",
                    "stage": "v3_episode",
                    "run_id": self.started.run_id,
                    "state": "running",
                    "observation": {
                        "type": "e2_kpm_prb_ping_v1",
                        "ping": {"packets_received": 1},
                        "e2": {"kpm_indications": 3},
                    },
                }

            def act(self, action):
                return {"status": "ok", "stage": "v3_episode", "accepted": True, "reason": "accepted"}

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id, "ric_port_open": False}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                return {"status": "ok", "stage": "v3_episode", "scored": cleanup_success, "unscored_reason": unscored_reason}

        def fake_run_conformance(**kwargs):
            self.assertEqual(kwargs["checks"], env_module.V4_EPISODE_GATE_CHECKS)
            return {
                "status": "pass",
                "backend_enablement": {
                    "ssh": True,
                    "websocket": True,
                    "json_metrics": True,
                    "e2_kpm": True,
                    "pcap_log": True,
                },
                "checks": [],
            }

        env_module.run_conformance = fake_run_conformance
        env_module.EpisodeRuntime = FakeEpisodeRuntime
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset(
                {
                    "run_id": "v4-unit",
                    "task": "e2_kpm_prb_ping_v1",
                    "conformance": "required",
                    "duration": 1,
                }
            )
            close = env.close()
        finally:
            env_module.run_conformance = original_run_conformance
            env_module.EpisodeRuntime = original_episode_runtime

        self.assertEqual(reset["stage"], "v4_episode")
        self.assertEqual(reset["observation"]["type"], "e2_kpm_prb_ping_v1")
        self.assertEqual(reset["adapters"]["e2_kpm"], "ready")
        self.assertEqual(close["stage"], "v4_episode")
        self.assertTrue(close["summary"]["scored"])

    def test_v3_observe_conformance_failure_marks_close_unscored(self) -> None:
        original_run_conformance = env_module.run_conformance
        original_episode_runtime = env_module.EpisodeRuntime

        class FakeEpisodeRuntime:
            def __init__(self, remote, repo_root=None) -> None:
                self.started = None
                self.finalize_args = None

            def start(self, options):
                self.started = options
                return {"status": "ok", "stage": "v3_episode"}

            def observe(self):
                return {"status": "ok", "stage": "v3_episode", "observation": {"type": "ws_prb_ping_v1"}}

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                self.finalize_args = (unscored_reason, cleanup_success)
                return {"status": "ok", "scored": unscored_reason is None, "unscored_reason": unscored_reason}

        def fake_run_conformance(**kwargs):
            return {
                "status": "fail",
                "backend_enablement": {"ssh": True, "docker_e2e": False},
                "checks": [],
            }

        env_module.run_conformance = fake_run_conformance
        env_module.EpisodeRuntime = FakeEpisodeRuntime
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset({"run_id": "v3-observe", "task": "ws_prb_ping_v1", "conformance": "observe"})
            close = env.close()
        finally:
            env_module.run_conformance = original_run_conformance
            env_module.EpisodeRuntime = original_episode_runtime

        self.assertEqual(reset["status"], "ok")
        self.assertFalse(reset["scored"])
        self.assertEqual(reset["unscored_reason"], "conformance observe failed")
        self.assertFalse(close["summary"]["scored"])
        self.assertEqual(close["summary"]["unscored_reason"], "conformance observe failed")

    def test_v3_reset_cleans_up_when_initial_observe_fails(self) -> None:
        original_run_conformance = env_module.run_conformance
        original_episode_runtime = env_module.EpisodeRuntime

        class FakeEpisodeRuntime:
            cleaned = False
            finalized = None

            def __init__(self, remote, repo_root=None) -> None:
                pass

            def start(self, options):
                return {"status": "ok", "stage": "v3_episode"}

            def observe(self):
                raise RuntimeError("initial observe failed")

            def cleanup(self, run_id):
                type(self).cleaned = True
                return {"status": "ok", "run_id": run_id}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                type(self).finalized = (unscored_reason, cleanup_success)
                return {"status": "ok", "scored": False, "unscored_reason": unscored_reason}

        def fake_run_conformance(**kwargs):
            return {"status": "pass", "backend_enablement": {"ssh": True}, "checks": []}

        env_module.run_conformance = fake_run_conformance
        env_module.EpisodeRuntime = FakeEpisodeRuntime
        try:
            env = BenchmarkEnv(self.config_path, remote_manager_factory=FakeRemoteManager)
            reset = env.reset({"run_id": "v3-observe-fail", "task": "ws_prb_ping_v1", "conformance": "required"})
        finally:
            env_module.run_conformance = original_run_conformance
            env_module.EpisodeRuntime = original_episode_runtime

        self.assertEqual(reset["status"], "error")
        self.assertTrue(FakeEpisodeRuntime.cleaned)
        self.assertEqual(FakeEpisodeRuntime.finalized, ("initial observe failed", True))


if __name__ == "__main__":
    unittest.main()
