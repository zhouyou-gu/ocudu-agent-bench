import json
import unittest
from pathlib import Path

import benchmark.benchmark_api.conformance as conformance_module
from benchmark.benchmark_api.config import DraxConfig, RemoteConfig, RuntimeConfig
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
from benchmark.benchmark_api.episode import TASK_E2_KPM_PRB_PING_V1
from benchmark.benchmark_api.ric import RIC_PROVIDER_DRAX_EXISTING


def sample_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        open5gs_compose="/remote/workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml",
        e2e_config_dir="/remote/workspace/assets/ocudu-zmq-open5gs-e2e/config",
        open5gs_image="example/open5gs:test",
        gnb_image="example/srsran-project-build:test",
        ue_image="example/srsran-4g-ue-build:test",
    )


def sample_remote_config(**kwargs) -> RemoteConfig:
    values = {
        "ssh_target": "user@host",
        "ssh_key": "/tmp/key",
        "ocudu_root": "/remote/ocudu",
        "workspace": "/remote/workspace",
        "runtime": sample_runtime(),
    }
    values.update(kwargs)
    return RemoteConfig(**values)


class FakeRemoteManager:
    def __init__(self, missing_libraries=None, config=None) -> None:
        self.config = config or sample_remote_config()
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
            "flexric_docker_assets",
            "near_rt_ric_health",
            "drax_cluster_access",
            "drax_e2_endpoint_config",
            "drax_kpm_xapp_api",
            "ocudu_e2_config",
            "e2_setup_path",
            "e2_kpm_subscription",
            "e2_pcap_log_oracle",
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

        e2_enablement = compute_backend_enablement(
            [
                ConformanceCheckResult("e2_kpm_subscription", "KPM", "e2_kpm", True, "pass", "ok", {}),
                ConformanceCheckResult("e2_pcap_log_oracle", "Oracle", "pcap_log", True, "pass", "ok", {}),
            ]
        )
        self.assertTrue(e2_enablement["e2_kpm"])
        self.assertTrue(e2_enablement["v4_e2_kpm"])
        self.assertTrue(e2_enablement["pcap_log"])

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

    def test_v4_e2_checks_use_flexric_and_episode_runtime_adapters(self) -> None:
        original_runtime = conformance_module.EpisodeRuntime
        original_flexric_assets = conformance_module.ConformanceRunner._check_flexric_assets
        original_ric_health = conformance_module.ConformanceRunner._check_ric_health
        original_kpm_compat = conformance_module.ConformanceRunner._detect_ocudu_kpm_compatibility

        class FakeEpisodeRuntime:
            started_tasks = []

            def __init__(self, remote, repo_root=None) -> None:
                self.remote = remote
                self.repo_root = repo_root
                self.options = None

            def check_docker_assets(self):
                return {"status": "pass", "summary": "docker assets ok", "details": {}}

            def start(self, options):
                self.options = options
                type(self).started_tasks.append(options.task)
                return {"status": "ok", "stage": "v4_episode", "run_id": options.run_id}

            def observe(self):
                return {
                    "status": "ok",
                    "observation": {
                        "e2": {"kpm_indications": 3, "oracle_available": True},
                        "metrics": {"present": True},
                    },
                }

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id, "ric_port_open": False}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                return {
                    "status": "ok",
                    "scored": cleanup_success,
                    "e2_oracle": {"kpm_indications": 3, "oracle_available": True},
                }

        conformance_module.EpisodeRuntime = FakeEpisodeRuntime
        conformance_module.ConformanceRunner._check_flexric_assets = lambda self: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "flexric ok",
            "details": {},
        }
        conformance_module.ConformanceRunner._check_ric_health = lambda self, options: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "ric ok",
            "details": {},
        }
        conformance_module.ConformanceRunner._detect_ocudu_kpm_compatibility = lambda self: {  # type: ignore[method-assign]
            "compatible": True,
            "summary": "compatible",
            "ocudu_e2sm_kpm_version": "03.00",
            "flexric_kpm_version": "03.00",
        }
        try:
            runner = ConformanceRunner(
                remote=FakeRemoteManager(),
                repo_root=Path(".").resolve(),
                specs_path=Path("benchmark/conformance/tests.json"),
            )
            result = runner.run(
                options=ConformanceOptions(
                    run_id="unit-v4-e2",
                    checks={"e2_pcap_log_oracle"},
                    ws_port=8001,
                    launch_timeout=1,
                    probe_timeout=1,
                )
            )
        finally:
            conformance_module.EpisodeRuntime = original_runtime
            conformance_module.ConformanceRunner._check_flexric_assets = original_flexric_assets
            conformance_module.ConformanceRunner._check_ric_health = original_ric_health
            conformance_module.ConformanceRunner._detect_ocudu_kpm_compatibility = original_kpm_compat

        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["stage"], "v4_conformance")
        self.assertEqual(FakeEpisodeRuntime.started_tasks, [TASK_E2_KPM_PRB_PING_V1])
        self.assertEqual(by_id["flexric_docker_assets"]["status"], "pass")
        self.assertEqual(by_id["near_rt_ric_health"]["status"], "pass")
        self.assertEqual(by_id["ocudu_e2_config"]["status"], "pass")
        self.assertEqual(by_id["e2_setup_path"]["status"], "pass")
        self.assertEqual(by_id["e2_kpm_subscription"]["status"], "pass")
        self.assertEqual(by_id["e2_pcap_log_oracle"]["status"], "pass")
        self.assertTrue(result["backend_enablement"]["e2_kpm"])
        self.assertTrue(result["backend_enablement"]["pcap_log"])

    def test_v4_kpm_version_mismatch_blocks_episode_checks(self) -> None:
        original_runtime = conformance_module.EpisodeRuntime
        original_flexric_assets = conformance_module.ConformanceRunner._check_flexric_assets
        original_ric_health = conformance_module.ConformanceRunner._check_ric_health
        original_kpm_compat = conformance_module.ConformanceRunner._detect_ocudu_kpm_compatibility

        class FakeEpisodeRuntime:
            def __init__(self, remote, repo_root=None) -> None:
                pass

            def check_docker_assets(self):
                return {"status": "pass", "summary": "docker assets ok", "details": {}}

            def start(self, options):
                raise AssertionError("KPM version mismatch should block episode launch")

        conformance_module.EpisodeRuntime = FakeEpisodeRuntime
        conformance_module.ConformanceRunner._check_flexric_assets = lambda self: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "flexric ok",
            "details": {},
        }
        conformance_module.ConformanceRunner._check_ric_health = lambda self, options: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "ric ok",
            "details": {},
        }
        conformance_module.ConformanceRunner._detect_ocudu_kpm_compatibility = lambda self: {  # type: ignore[method-assign]
            "compatible": False,
            "summary": "OCUDU E2SM KPM v05.00 is incompatible with FlexRIC KPM v03.00",
            "ocudu_e2sm_kpm_version": "05.00",
            "flexric_kpm_version": "03.00",
        }
        try:
            runner = ConformanceRunner(
                remote=FakeRemoteManager(),
                repo_root=Path(".").resolve(),
                specs_path=Path("benchmark/conformance/tests.json"),
            )
            result = runner.run(
                options=ConformanceOptions(
                    run_id="unit-v4-kpm-mismatch",
                    checks={"e2_kpm_subscription"},
                    ws_port=8001,
                    launch_timeout=1,
                    probe_timeout=1,
                )
            )
        finally:
            conformance_module.EpisodeRuntime = original_runtime
            conformance_module.ConformanceRunner._check_flexric_assets = original_flexric_assets
            conformance_module.ConformanceRunner._check_ric_health = original_ric_health
            conformance_module.ConformanceRunner._detect_ocudu_kpm_compatibility = original_kpm_compat

        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(by_id["ocudu_e2_config"]["status"], "fail")
        self.assertIn("incompatible", by_id["ocudu_e2_config"]["summary"])
        self.assertEqual(by_id["e2_setup_path"]["status"], "blocked")
        self.assertEqual(by_id["e2_kpm_subscription"]["status"], "blocked")

    def test_drax_kpm_xapp_api_probes_runtime_methods(self) -> None:
        cfg = sample_remote_config(
            ric_provider=RIC_PROVIDER_DRAX_EXISTING,
            drax=DraxConfig(kpm_api_url="http://10.0.0.20:8080"),
        )
        runner = ConformanceRunner(
            remote=FakeRemoteManager(config=cfg),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        scripts = []

        def fake_remote_json(script):
            scripts.append(script)
            return {
                "url": cfg.drax.kpm_api_url,
                "probe_run_id": "unit-drax-api-drax-api-probe",
                "required_release": "E2SM-KPM-R003-v05.00",
                "health_status": 200,
                "health": {"status": "healthy", "release": "E2SM-KPM-R003-v05.00"},
                "reset_status": 204,
                "reset_body": "",
                "records_status": 200,
                "records": {"records": [], "count": 0},
                "errors": [],
            }

        runner._remote_json = fake_remote_json  # type: ignore[method-assign]
        result = runner._check_drax_kpm_xapp_api(ConformanceOptions(run_id="unit-drax-api"))

        self.assertEqual(result["status"], "pass")
        self.assertIn("/health", scripts[0])
        self.assertIn("/reset", scripts[0])
        self.assertIn("/records?", scripts[0])
        self.assertEqual(result["details"]["probe_run_id"], "unit-drax-api-drax-api-probe")

    def test_drax_kpm_xapp_api_fails_when_reset_or_records_missing(self) -> None:
        cfg = sample_remote_config(
            ric_provider=RIC_PROVIDER_DRAX_EXISTING,
            drax=DraxConfig(kpm_api_url="http://10.0.0.20:8080"),
        )
        runner = ConformanceRunner(
            remote=FakeRemoteManager(config=cfg),
            repo_root=Path(".").resolve(),
            specs_path=Path("benchmark/conformance/tests.json"),
        )
        runner._remote_json = lambda script: {  # type: ignore[method-assign]
            "health_status": 200,
            "health": {"status": "ok", "release": "E2SM-KPM-R003-v05.00"},
            "reset_status": 404,
            "records_status": 200,
            "records": {"items": []},
            "errors": [],
        }

        result = runner._check_drax_kpm_xapp_api(ConformanceOptions(run_id="unit-drax-api-fail"))

        self.assertEqual(result["status"], "fail")
        self.assertIn("/reset", result["summary"])
        self.assertIn("/records", result["summary"])

    def test_v4b_drax_checks_use_drax_provider_gate(self) -> None:
        original_runtime = conformance_module.EpisodeRuntime
        original_cluster = conformance_module.ConformanceRunner._check_drax_cluster_access
        original_endpoint = conformance_module.ConformanceRunner._check_drax_e2_endpoint_config
        original_api = conformance_module.ConformanceRunner._check_drax_kpm_xapp_api

        class FakeEpisodeRuntime:
            started_tasks = []

            def __init__(self, remote, repo_root=None) -> None:
                self.options = None

            def check_docker_assets(self):
                return {"status": "pass", "summary": "docker assets ok", "details": {}}

            def start(self, options):
                self.options = options
                type(self).started_tasks.append(options.task)
                return {"status": "ok", "stage": "v4_episode", "run_id": options.run_id}

            def observe(self):
                return {"status": "ok", "observation": {"e2": {"kpm_indications": 3, "oracle_available": True}}}

            def cleanup(self, run_id):
                return {"status": "ok", "run_id": run_id, "ric_port_open": False}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                return {"status": "ok", "scored": cleanup_success, "e2_oracle": {"kpm_indications": 3, "oracle_available": True}}

        cfg = sample_remote_config(
            ric_provider=RIC_PROVIDER_DRAX_EXISTING,
            drax=DraxConfig(
                kubeconfig="/remote/kubeconfig",
                namespace="ricplt",
                e2_endpoint="10.0.0.10:36421",
                kpm_api_url="http://10.0.0.20:8080",
            ),
        )
        conformance_module.EpisodeRuntime = FakeEpisodeRuntime
        conformance_module.ConformanceRunner._check_drax_cluster_access = lambda self: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "cluster ok",
            "details": {},
        }
        conformance_module.ConformanceRunner._check_drax_e2_endpoint_config = lambda self, options: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "endpoint ok",
            "details": {},
        }
        conformance_module.ConformanceRunner._check_drax_kpm_xapp_api = lambda self, options: {  # type: ignore[method-assign]
            "status": "pass",
            "summary": "api ok",
            "details": {},
        }
        try:
            runner = ConformanceRunner(
                remote=FakeRemoteManager(config=cfg),
                repo_root=Path(".").resolve(),
                specs_path=Path("benchmark/conformance/tests.json"),
            )
            result = runner.run(
                options=ConformanceOptions(
                    run_id="unit-v4b-drax",
                    checks={"e2_pcap_log_oracle"},
                    ws_port=8001,
                    launch_timeout=1,
                    probe_timeout=1,
                )
            )
        finally:
            conformance_module.EpisodeRuntime = original_runtime
            conformance_module.ConformanceRunner._check_drax_cluster_access = original_cluster
            conformance_module.ConformanceRunner._check_drax_e2_endpoint_config = original_endpoint
            conformance_module.ConformanceRunner._check_drax_kpm_xapp_api = original_api

        by_id = {check["id"]: check for check in result["checks"]}
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["stage"], "v4b_conformance")
        self.assertEqual(by_id["drax_cluster_access"]["status"], "pass")
        self.assertEqual(by_id["drax_e2_endpoint_config"]["status"], "pass")
        self.assertEqual(by_id["drax_kpm_xapp_api"]["status"], "pass")
        self.assertEqual(by_id["flexric_docker_assets"]["status"], "skip")
        self.assertEqual(FakeEpisodeRuntime.started_tasks, [TASK_E2_KPM_PRB_PING_V1])


if __name__ == "__main__":
    unittest.main()
