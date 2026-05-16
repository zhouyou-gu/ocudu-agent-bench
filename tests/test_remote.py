import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark_api.config import DraxConfig, RemoteConfig, RuntimeConfig, SourcesConfig
from benchmark.benchmark_api.remote import RUNTIME_DEP_PACKAGES, RemoteCommandError, RemoteManager
from benchmark.benchmark_api.ric import FLEXRIC_IMAGE, RIC_PROVIDER_DRAX_EXISTING


SAMPLE_SSH = "user@host"
SAMPLE_WORKSPACE = "/remote/skillful-ran-benchmark-workspace"
SAMPLE_OCUDU_ROOT = "/remote/skillful-ran-benchmark-workspace/ocudu"


def sample_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        open5gs_compose="/remote/skillful-ran-benchmark-workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml",
        e2e_config_dir="/remote/skillful-ran-benchmark-workspace/assets/ocudu-zmq-open5gs-e2e/config",
        open5gs_image="skillful-ran/open5gs:v2.7.0",
        gnb_image="skillful-ran/srsran-project-build:release_25_10",
        ue_image="skillful-ran/srsran-4g-ue-build:release_23_11",
    )


def sample_sources() -> SourcesConfig:
    return SourcesConfig(
        srsran_project_repo="https://github.com/srsran/srsRAN_Project.git",
        srsran_project_ref="release_25_10",
        srsran_4g_repo="https://github.com/srsran/srsRAN_4G.git",
        srsran_4g_ref="release_23_11",
        open5gs_ref="v2.7.0",
    )


class RemoteCommandBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = RemoteConfig(
            ssh_target=SAMPLE_SSH,
            ssh_key="/Users/example/.ssh/key",
            ocudu_root=SAMPLE_OCUDU_ROOT,
            workspace=SAMPLE_WORKSPACE,
            runtime=sample_runtime(),
            sources=sample_sources(),
        )
        self.manager = RemoteManager(self.cfg)

    def test_ssh_builder_uses_noninteractive_flags(self) -> None:
        argv = self.manager.ssh_argv("true")
        self.assertIn("-i", argv)
        self.assertIn("/Users/example/.ssh/key", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ConnectTimeout=8", argv)
        self.assertIn(SAMPLE_SSH, argv)

    def test_rsync_builder_uses_remote_workspace(self) -> None:
        argv = self.manager.rsync_argv(source=Path("benchmark"), dry_run=True)
        self.assertIn("--dry-run", argv)
        self.assertIn("-e", argv)
        self.assertTrue(any(SAMPLE_WORKSPACE in part for part in argv))

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

    def test_sync_dry_run_includes_bootstrap_manifest_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            source = repo_root / "benchmark"
            (source / "benchmark_api").mkdir(parents=True)
            (source / "benchctl.py").write_text("# test\n", encoding="utf-8")
            (source / "benchmark_api" / "websocket_client.py").write_text("# ws\n", encoding="utf-8")
            self.manager._git_tracked_files = lambda repo_root, source: [Path("benchmark/benchctl.py")]  # type: ignore[method-assign]

            result = self.manager.sync(source=source, repo_root=repo_root, dry_run=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source_policy"], "git_tracked_plus_bootstrap_manifest")
        self.assertEqual(
            result["tracked_files"],
            ["benchmark/benchctl.py", "benchmark/benchmark_api/websocket_client.py"],
        )
        self.assertEqual(result["tracked_file_count"], 2)

    def test_prepare_runtime_deps_dry_run_reports_workspace_root(self) -> None:
        result = self.manager.prepare_runtime_deps(dry_run=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["packages"], RUNTIME_DEP_PACKAGES)
        self.assertEqual(result["runtime_root"], f"{SAMPLE_WORKSPACE}/runtime-libs/root")
        self.assertIn("apt-get download", result["planned_remote_command"])
        self.assertIn("libzmq5", result["planned_remote_command"])

    def test_prepare_runtime_deps_initializes_workspace_before_download(self) -> None:
        commands = []

        def fake_run(argv):
            commands.append(argv[-1])

            class Result:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return Result()

        self.manager._run = fake_run  # type: ignore[method-assign]
        result = self.manager.prepare_runtime_deps()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["init"]["status"], "ok")
        self.assertGreaterEqual(len(commands), 2)
        self.assertIn("metadata.json", commands[0])
        self.assertIn("apt-get download", commands[1])

    def test_prepare_ric_dry_run_is_workspace_scoped_docker_build(self) -> None:
        result = self.manager.prepare_ric(dry_run=True)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["image"], FLEXRIC_IMAGE)
        self.assertEqual(result["paths"]["root"], f"{SAMPLE_WORKSPACE}/flexric")
        self.assertIn("docker build -t", result["planned_remote_command"])
        self.assertIn("ubuntu:22.04", result["dockerfile"])
        self.assertIn("-DE2AP_VERSION=E2AP_V3", result["dockerfile"])
        self.assertIn("-DKPM_VERSION=KPM_V3", result["dockerfile"])
        self.assertEqual(result["manifest"]["e2ap_version"], "E2AP_V3")
        self.assertEqual(result["manifest"]["kpm_release"], "KPM_V3_00")
        self.assertIn("-DXAPP_DB=NONE_XAPP", result["dockerfile"])
        self.assertIn("tcpdump", result["dockerfile"])
        self.assertIn("flexric-ric", result["dockerfile"])
        self.assertNotIn("sudo", result["planned_remote_command"])
        self.assertNotIn("sudo apt-get", result["planned_remote_command"])

    def test_prepare_ric_initializes_workspace_before_build(self) -> None:
        commands = []

        def fake_run(argv):
            commands.append(argv[-1])

            class Result:
                returncode = 0
                stdout = "status=ok\nmanifest=/remote/manifest.json\nbuild_log=/remote/build.log\nreused=0\n"
                stderr = ""

            return Result()

        self.manager._run = fake_run  # type: ignore[method-assign]
        result = self.manager.prepare_ric()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["init"]["status"], "ok")
        self.assertGreaterEqual(len(commands), 2)
        self.assertIn("metadata.json", commands[0])
        self.assertIn("docker build -t", commands[1])
        self.assertIn(FLEXRIC_IMAGE, commands[1])

    def test_prepare_ric_drax_existing_writes_manifest_without_build(self) -> None:
        cfg = RemoteConfig(
            ssh_target=SAMPLE_SSH,
            ssh_key="/Users/example/.ssh/key",
            ocudu_root=SAMPLE_OCUDU_ROOT,
            workspace=SAMPLE_WORKSPACE,
            runtime=sample_runtime(),
            sources=sample_sources(),
            ric_provider=RIC_PROVIDER_DRAX_EXISTING,
            drax=DraxConfig(
                kubeconfig="/remote/drax/kubeconfig",
                namespace="ricplt",
                e2_endpoint="10.0.0.10:36421",
                kpm_api_url="http://10.0.0.20:8080",
            ),
        )
        manager = RemoteManager(cfg)
        result = manager.prepare_ric(dry_run=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], RIC_PROVIDER_DRAX_EXISTING)
        self.assertEqual(result["paths"]["root"], f"{SAMPLE_WORKSPACE}/drax")
        self.assertEqual(result["manifest"]["kpm_release"], "E2SM-KPM-R003-v05.00")
        self.assertIn("manifest.json", result["planned_remote_command"])
        self.assertNotIn("docker build", result["planned_remote_command"])

    def test_provision_assets_dry_run_is_workspace_owned(self) -> None:
        result = self.manager.provision(stage="assets", dry_run=True)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["stages"], ["assets"])
        self.assertIn("benchmark/provision/", " ".join(result["asset_sync_argv"]))
        self.assertIn(f"{SAMPLE_WORKSPACE}/tmp/provision-assets/", " ".join(result["asset_sync_argv"]))
        self.assertIn("rewrite_open5gs_compose", result["planned_remote_command"])
        self.assertIn("skillful-ran/open5gs:v2.7.0", result["planned_remote_command"])
        self.assertNotIn("/remote/skills", result["planned_remote_command"])

    def test_provision_dry_run_contains_stage_prerequisite_errors(self) -> None:
        images = self.manager.provision(stage="images", dry_run=True)
        ocudu = self.manager.provision(stage="ocudu", dry_run=True)

        self.assertIn("run remote provision --stage assets first", images["planned_remote_command"])
        self.assertIn("run remote provision --stage images first", ocudu["planned_remote_command"])

    def test_provision_runtime_deps_updates_top_level_manifest(self) -> None:
        calls = []

        def fake_init():
            return {"status": "ok", "returncode": 0}

        def fake_deps():
            return {
                "status": "ok",
                "returncode": 0,
                "packages": ["libzmq5"],
                "runtime_root": f"{SAMPLE_WORKSPACE}/runtime-libs/root",
            }

        def fake_run(argv):
            calls.append(argv[-1])

            class Result:
                returncode = 0
                stdout = "status=ok\nmanifest=/remote/skillful-ran-benchmark-workspace/manifests/provision.json\n"
                stderr = ""

            return Result()

        self.manager.init_workspace = fake_init  # type: ignore[method-assign]
        self.manager.prepare_runtime_deps = fake_deps  # type: ignore[method-assign]
        self.manager._run = fake_run  # type: ignore[method-assign]

        result = self.manager.provision(stage="runtime-deps")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["steps"]["manifest_update"]["status"], "ok")
        self.assertIn("stage_summaries", calls[-1])
        self.assertIn("runtime-deps", calls[-1])

    def test_provision_requires_source_pins(self) -> None:
        cfg = RemoteConfig(
            ssh_target=SAMPLE_SSH,
            ssh_key="/Users/example/.ssh/key",
            ocudu_root=SAMPLE_OCUDU_ROOT,
            workspace=SAMPLE_WORKSPACE,
            runtime=sample_runtime(),
        )
        manager = RemoteManager(cfg)

        with self.assertRaisesRegex(ValueError, "sources.srsran-project-repo"):
            manager.provision(stage="assets", dry_run=True)

    def test_provision_rejects_runtime_outside_workspace(self) -> None:
        cfg = RemoteConfig(
            ssh_target=SAMPLE_SSH,
            ssh_key="/Users/example/.ssh/key",
            ocudu_root="/remote/external/ocudu",
            workspace=SAMPLE_WORKSPACE,
            runtime=sample_runtime(),
            sources=sample_sources(),
        )
        manager = RemoteManager(cfg)

        with self.assertRaisesRegex(ValueError, "remote.ocudu-root"):
            manager.provision(stage="assets", dry_run=True)

    def test_init_dry_run_reports_workspace(self) -> None:
        result = self.manager.init_workspace(dry_run=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["workspace"], SAMPLE_WORKSPACE)

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
        self.assertIn("python3 -c", captured["argv"][-1])
        self.assertIn('print("hello world")', captured["argv"][-1])
        self.assertIn("BENCHMARK_WORKSPACE_RAW=", captured["argv"][-1])
        self.assertIn('cd "$BENCHMARK_WORKSPACE"', captured["argv"][-1])

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

    def test_remote_shell_expands_tilde_workspace(self) -> None:
        cfg = RemoteConfig(
            ssh_target=SAMPLE_SSH,
            ssh_key="/Users/example/.ssh/key",
            ocudu_root="~/ocudu",
            workspace="~/benchmark-workspace",
            runtime=sample_runtime(),
        )
        command = RemoteManager(cfg)._remote_shell('printf "%s" "$BENCHMARK_WORKSPACE"')
        self.assertIn("BENCHMARK_WORKSPACE_RAW='~/benchmark-workspace'", command)
        self.assertIn("expand_remote_path", command)
        self.assertIn("${1#\\~/}", command)

    def test_remote_exec_empty_command_errors(self) -> None:
        with self.assertRaisesRegex(RemoteCommandError, "requires a command"):
            self.manager.exec([])


if __name__ == "__main__":
    unittest.main()
