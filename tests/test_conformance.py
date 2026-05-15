import json
import unittest
from pathlib import Path

import benchmark.benchmark_api.conformance as conformance_module
from benchmark.benchmark_api.config import RemoteConfig
from benchmark.benchmark_api.conformance import (
    ConformanceCheckResult,
    ConformanceOptions,
    ConformanceRunner,
    compute_backend_enablement,
    compute_overall_status,
    conformance_exit_code,
    generate_overlay_config,
    load_conformance_specs,
)


class FakeRemoteManager:
    def __init__(self, missing_libraries=None) -> None:
        self.config = RemoteConfig("zhouyou@10.34.23.184", "/tmp/key")
        self.missing_libraries = missing_libraries or []
        self.commands = []

    def check(self):
        return {
            "status": "ok",
            "ocudu_root": self.config.ocudu_root,
            "remote": {
                "tools": {
                    "python3": "/usr/bin/python3",
                    "git": "/usr/bin/git",
                    "rsync": "/usr/bin/rsync",
                    "ss": "/usr/bin/ss",
                    "ldd": "/usr/bin/ldd",
                },
                "ocudu_exists": True,
                "ocudu_is_git": True,
                "ocudu_commit": "abc123",
                "ocudu_branch": "main",
                "workspace_exists": True,
                "workspace_is_dir": True,
            },
        }

    def init_workspace(self):
        return {"status": "ok"}

    def sync(self, source, repo_root, dry_run=False):
        return {"status": "ok", "source": str(source), "repo_root": str(repo_root), "dry_run": dry_run}

    def exec(self, command, shell=False):
        text = command[0]
        self.commands.append(text)
        if "shutil.which(name)" in text:
            stdout = json.dumps(
                {"tools": {"python3": "/usr/bin/python3", "git": "/usr/bin/git", "rsync": "/usr/bin/rsync", "ss": "/usr/bin/ss", "ldd": "/usr/bin/ldd"}}
            )
        elif '"created": True' in text or '"created": true' in text:
            stdout = json.dumps({"created": True, "paths": {}})
        elif "missing_libraries" in text:
            stdout = json.dumps(
                {
                    "gnb_binary": "/ocudu/gnb",
                    "gnb_binary_exists": True,
                    "gnb_binary_executable": True,
                    "base_config": "/ocudu/gnb_zmq.yaml",
                    "base_config_exists": True,
                    "ldd": "/usr/bin/ldd",
                    "missing_libraries": self.missing_libraries,
                }
            )
        elif "missing_setup" in text and "missing_launch" in text:
            stdout = json.dumps(
                {
                    "exists": {
                        "overlay_config": True,
                        "result_json": True,
                        "gnb_log": False,
                        "stdout": False,
                        "stderr": False,
                        "pid": False,
                        "command_metadata": False,
                    },
                    "missing_setup": [],
                    "missing_launch": ["gnb_log", "stdout", "stderr", "pid", "command_metadata"],
                    "paths": {},
                }
            )
        elif '"written": payload["path"]' in text:
            stdout = json.dumps({"written": "conformance.json"})
        else:
            stdout = json.dumps({})

        class Result(dict):
            def __init__(self):
                super().__init__(status="ok", returncode=0, stdout=stdout, stderr="")

        return Result()


class ConformanceTests(unittest.TestCase):
    def test_loads_v2_executable_and_stub_specs(self) -> None:
        specs = load_conformance_specs(Path("benchmark/conformance/tests.json"))
        by_id = {spec.id: spec for spec in specs}
        for check_id in [
            "remote_tools_ocudu_root",
            "remote_workspace_artifacts",
            "ocudu_runtime_dependencies",
            "ocudu_launch",
            "websocket_command_path",
            "json_metrics_stream",
            "artifact_paths",
            "docker_e2e_assets",
            "open5gs_core_health",
            "srsue_zmq_attach",
            "ping_traffic_path",
            "websocket_prb_policy_action",
        ]:
            self.assertTrue(by_id[check_id].executable)
            self.assertEqual(by_id[check_id].status, "executable")
        self.assertFalse(by_id["e2_kpm"].executable)
        self.assertEqual(by_id["e2_kpm"].status, "stub")

    def test_overlay_config_contains_required_runtime_controls(self) -> None:
        overlay = generate_overlay_config(9001, "/tmp/gnb.log")
        self.assertIn("no_core: true", overlay)
        self.assertIn("enable_json: true", overlay)
        self.assertIn("remote_control:", overlay)
        self.assertIn("bind_addr: 127.0.0.1", overlay)
        self.assertIn("port: 9001", overlay)
        self.assertIn("filename: /tmp/gnb.log", overlay)
        self.assertNotIn("  metrics:\n    enable_json: true", overlay)

    def test_result_status_and_backend_enablement(self) -> None:
        results = [
            ConformanceCheckResult("remote_tools_ocudu_root", "Remote", "ssh", True, "pass", "ok", {}),
            ConformanceCheckResult("remote_workspace_artifacts", "Workspace", "ssh", True, "pass", "ok", {}),
            ConformanceCheckResult("websocket_command_path", "WS", "websocket", True, "blocked", "blocked", {}),
        ]
        result = {"status": compute_overall_status(results)}
        self.assertEqual(result["status"], "fail")
        self.assertEqual(conformance_exit_code(result), 1)
        enablement = compute_backend_enablement(results)
        self.assertTrue(enablement["ssh"])
        self.assertFalse(enablement["websocket"])

    def test_missing_runtime_dependencies_block_launch_checks(self) -> None:
        runner = ConformanceRunner(
            remote=FakeRemoteManager(missing_libraries=["libzmq.so.5"]),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        result = runner.run(
            options=ConformanceOptions(
                run_id="unit-missing-deps",
                checks={"websocket_command_path"},
                ws_port=8001,
                launch_timeout=1,
                probe_timeout=1,
            )
        )
        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "fail")
        self.assertEqual(by_id["ocudu_runtime_dependencies"]["status"], "fail")
        self.assertIn("libzmq.so.5", by_id["ocudu_runtime_dependencies"]["summary"])
        self.assertEqual(by_id["ocudu_launch"]["status"], "blocked")
        self.assertEqual(by_id["ocudu_launch"]["summary"], "One or more setup checks did not pass")
        self.assertEqual(by_id["websocket_command_path"]["status"], "blocked")

    def test_selected_remote_tools_only_skips_runtime_dependencies(self) -> None:
        runner = ConformanceRunner(
            remote=FakeRemoteManager(missing_libraries=["libzmq.so.5"]),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        result = runner.run(
            options=ConformanceOptions(
                run_id="unit-remote-only",
                checks={"remote_tools_ocudu_root"},
                ws_port=8001,
                launch_timeout=1,
                probe_timeout=1,
            )
        )
        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "pass")
        self.assertEqual(by_id["remote_tools_ocudu_root"]["status"], "pass")
        self.assertEqual(by_id["ocudu_runtime_dependencies"]["status"], "skip")

    def test_remote_tools_check_does_not_require_workspace_exec(self) -> None:
        class NoWorkspaceRemote(FakeRemoteManager):
            def exec(self, command, shell=False):
                raise AssertionError("remote tools check must not cd into the benchmark workspace")

        runner = ConformanceRunner(
            remote=NoWorkspaceRemote(),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        result = runner.run(
            options=ConformanceOptions(
                run_id="unit-remote-tools-no-workspace",
                checks={"remote_tools_ocudu_root"},
                ws_port=8001,
                launch_timeout=1,
                probe_timeout=1,
            )
        )
        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "pass")
        self.assertEqual(by_id["remote_tools_ocudu_root"]["status"], "pass")

    def test_websocket_selection_does_not_emit_metrics_when_unselected(self) -> None:
        runner = ConformanceRunner(
            remote=FakeRemoteManager(),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        runner._launch_gnb = lambda options: runner._result("ocudu_launch", "pass", "launched", {"pid": 123})  # type: ignore[method-assign]
        runner._probe_websocket_and_metrics = lambda options: {  # type: ignore[method-assign]
            "websocket": {"status": "pass", "summary": "ws ok", "details": {}},
            "metrics": {"status": "fail", "summary": "metrics fail", "details": {}},
        }
        runner._terminate_gnb = lambda options: None  # type: ignore[method-assign]

        result = runner.run(
            options=ConformanceOptions(
                run_id="unit-ws-only",
                checks={"websocket_command_path"},
                ws_port=8001,
                launch_timeout=1,
                probe_timeout=1,
            )
        )
        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "pass")
        self.assertEqual(by_id["websocket_command_path"]["status"], "pass")
        self.assertEqual(by_id["json_metrics_stream"]["status"], "skip")

    def test_artifact_paths_checks_setup_artifacts_when_launch_blocked(self) -> None:
        runner = ConformanceRunner(
            remote=FakeRemoteManager(missing_libraries=["libzmq.so.5"]),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        result = runner.run(
            options=ConformanceOptions(
                run_id="unit-artifacts-blocked",
                checks={"artifact_paths"},
                ws_port=8001,
                launch_timeout=1,
                probe_timeout=1,
            )
        )
        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "fail")
        self.assertEqual(by_id["ocudu_runtime_dependencies"]["status"], "fail")
        self.assertEqual(by_id["ocudu_launch"]["status"], "blocked")
        self.assertEqual(by_id["artifact_paths"]["status"], "pass")
        self.assertEqual(by_id["artifact_paths"]["details"]["missing_setup"], [])
        self.assertEqual(by_id["artifact_paths"]["details"]["launch_artifacts_status"], "blocked")

    def test_v3_docker_checks_use_episode_runtime_adapter(self) -> None:
        original_runtime = conformance_module.EpisodeRuntime

        class FakeEpisodeRuntime:
            def __init__(self, remote, repo_root=None) -> None:
                self.remote = remote
                self.repo_root = repo_root
                self.options = None

            def check_docker_assets(self):
                return {"status": "pass", "summary": "assets ok", "details": {}}

            def start(self, options):
                self.options = options
                return {"status": "ok", "stage": "v3_episode", "run_id": options.run_id}

            def observe(self):
                return {
                    "status": "ok",
                    "observation": {
                        "ping": {"packets_received": 1},
                        "metrics": {"present": True},
                    },
                }

            def act(self, action):
                if action.get("min_prb_policy_ratio", 0) > action.get("max_prb_policy_ratio", 100):
                    return {"status": "rejected", "accepted": False}
                return {"status": "ok", "accepted": True}

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                return {"status": "ok", "scored": cleanup_success}

        conformance_module.EpisodeRuntime = FakeEpisodeRuntime
        try:
            runner = ConformanceRunner(
                remote=FakeRemoteManager(),
                repo_root=Path(".").resolve(),
                specs_path=Path("benchmark/conformance/tests.json"),
            )
            result = runner.run(
                options=ConformanceOptions(
                    run_id="unit-v3-docker",
                    checks={"websocket_prb_policy_action", "ping_traffic_path"},
                    ws_port=8001,
                    launch_timeout=1,
                    probe_timeout=1,
                )
            )
        finally:
            conformance_module.EpisodeRuntime = original_runtime

        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "pass")
        self.assertEqual(by_id["docker_e2e_assets"]["status"], "pass")
        self.assertEqual(by_id["open5gs_core_health"]["status"], "pass")
        self.assertEqual(by_id["srsue_zmq_attach"]["status"], "pass")
        self.assertEqual(by_id["ping_traffic_path"]["status"], "pass")
        self.assertEqual(by_id["websocket_prb_policy_action"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
