import unittest
from pathlib import Path

import benchmark.benchmark_api.suite as suite_module
from benchmark.benchmark_api.config import RemoteConfig, RuntimeConfig
from benchmark.benchmark_api.suite import (
    BaselineAgent,
    SuiteOptions,
    SuiteRunner,
    V4_SUITE_CONFORMANCE_CHECKS,
    aggregate_suite,
    default_suite_id,
    suite_run_id,
)


def sample_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        open5gs_compose="/tmp/workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml",
        e2e_config_dir="/tmp/workspace/assets/ocudu-zmq-open5gs-e2e/config",
        open5gs_image="example/open5gs:test",
        gnb_image="example/ocudu-build:test",
        ue_image="example/srsran-4g-ue-build:test",
    )


class FakeRemote:
    def __init__(self) -> None:
        self.config = RemoteConfig(
            ssh_target="user@host",
            ssh_key="/tmp/key",
            ocudu_root="/tmp/workspace/ocudu",
            workspace="/tmp/workspace",
            runtime=sample_runtime(),
        )
        self.writes = []

    def exec(self, command, shell=False):
        self.writes.append(command[0])
        return {"status": "ok", "returncode": 0, "stdout": '{"status": "ok"}', "stderr": ""}


class WriteFailRemote(FakeRemote):
    def exec(self, command, shell=False):
        self.writes.append(command[0])
        return {"status": "error", "returncode": 2, "stdout": "", "stderr": "workspace missing"}


class SuiteTests(unittest.TestCase):
    def test_default_suite_ids_are_safe_and_high_resolution(self) -> None:
        ids = [default_suite_id() for _ in range(20)]
        self.assertEqual(len(ids), len(set(ids)))
        for suite_id in ids:
            self.assertTrue(suite_id.startswith("suite-"))
            self.assertEqual(suite_run_id(suite_id, 1), f"{suite_id}-r001")

    def test_suite_run_ids_are_stable(self) -> None:
        self.assertEqual(suite_run_id("suite-a", 1), "suite-a-r001")
        self.assertEqual(suite_run_id("suite-a", 12), "suite-a-r012")
        with self.assertRaises(ValueError):
            suite_run_id("suite-a", 0)
        with self.assertRaises(ValueError):
            suite_run_id("bad/id", 1)

    def test_builtin_agents_are_deterministic(self) -> None:
        fixed = BaselineAgent("fixed_prb", seed=1)
        self.assertIsNotNone(fixed.next_action({"observation": {}}))
        self.assertIsNone(fixed.next_action({"observation": {}}))

        invalid = BaselineAgent("invalid_then_fixed", seed=1)
        first = invalid.next_action({})
        second = invalid.next_action({})
        third = invalid.next_action({})
        self.assertGreater(first["min_prb_policy_ratio"], first["max_prb_policy_ratio"])
        self.assertEqual(second["min_prb_policy_ratio"], 10)
        self.assertIsNone(third)

        sweep_a = BaselineAgent("sweep_prb", seed=2)
        sweep_b = BaselineAgent("sweep_prb", seed=2)
        self.assertEqual([sweep_a.next_action({}) for _ in range(4)], [sweep_b.next_action({}) for _ in range(4)])

    def test_aggregate_suite_scores(self) -> None:
        options = SuiteOptions(suite_id="unit-suite", runs=2)
        result = aggregate_suite(
            options=options,
            conformance={"status": "pass", "remote": {"ocudu_commit": "abc"}},
            run_results=[
                {
                    "run_id": "unit-suite-r001",
                    "status": "ok",
                    "summary": {
                        "scored": True,
                        "scores": {"ping_success_ratio": 1.0, "metrics_continuity": 5, "clean_teardown": True},
                        "counts": {"actions": 1},
                        "artifacts": {"summary": "/remote/summary-1.json"},
                    },
                    "cleanup": {"status": "ok", "leftover_containers": [], "ws_port_open": False, "ric_port_open": False},
                },
                {
                    "run_id": "unit-suite-r002",
                    "status": "error",
                    "summary": {
                        "scored": False,
                        "unscored_reason": "runtime failed",
                        "scores": {"ping_success_ratio": 0.0},
                        "counts": {"actions": 0},
                        "artifacts": {"summary": "/remote/summary-2.json"},
                    },
                    "cleanup": {"status": "ok", "leftover_containers": [], "ws_port_open": False, "ric_port_open": False},
                },
            ],
            paths={"summary": "/remote/suite.json"},
            remote=FakeRemote(),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["scored_runs"], 1)
        self.assertEqual(result["unscored_runs"], 1)
        self.assertEqual(result["aggregate_scores"]["ping_success_ratio"]["mean"], 1.0)
        self.assertEqual(result["aggregate_scores"]["metrics_continuity"]["max"], 5.0)
        self.assertEqual(result["aggregate_scores"]["clean_teardown"]["mean"], 1.0)

    def test_aggregate_suite_counts_cleanup_failures(self) -> None:
        options = SuiteOptions(suite_id="unit-suite", runs=1)
        result = aggregate_suite(
            options=options,
            conformance={"status": "pass", "remote": {}},
            run_results=[
                {
                    "run_id": "unit-suite-r001",
                    "status": "error",
                    "summary": {"scored": False, "scores": {}, "counts": {}},
                    "cleanup": {
                        "status": "error",
                        "leftover_containers": ["skillful-ran-bench-gnb-unit"],
                        "ws_port_open": True,
                        "ric_port_open": True,
                    },
                }
            ],
            paths={"summary": "/remote/suite.json"},
            remote=FakeRemote(),
        )

        self.assertEqual(result["cleanup_failure_runs"], ["unit-suite-r001"])
        self.assertEqual(result["status"], "error")

    def test_conformance_failure_blocks_suite_runs(self) -> None:
        original = suite_module.run_conformance

        def fake_run_conformance(**kwargs):
            return {"status": "fail", "remote": {}, "checks": []}

        suite_module.run_conformance = fake_run_conformance
        try:
            runner = SuiteRunner(FakeRemote(), Path(".").resolve(), Path("benchmark/conformance/tests.json"))
            result = runner.run(SuiteOptions(suite_id="unit-blocked", runs=2))
        finally:
            suite_module.run_conformance = original

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["run_ids"], ["unit-blocked-r001", "unit-blocked-r002"])
        self.assertEqual(result["scored_runs"], 0)
        self.assertEqual(result["unscored_runs"], 2)
        self.assertEqual(result["runs"][0]["status"], "blocked")
        self.assertEqual(result["runs"][0]["cleanup"]["status"], "skip")
        self.assertEqual(result["unscored_reason"], "required conformance failed")

    def test_conformance_failure_write_error_does_not_mask_result(self) -> None:
        original = suite_module.run_conformance

        def fake_run_conformance(**kwargs):
            return {"status": "fail", "remote": {}, "checks": [{"id": "remote_tools_ocudu_root", "status": "fail"}]}

        suite_module.run_conformance = fake_run_conformance
        try:
            runner = SuiteRunner(WriteFailRemote(), Path(".").resolve(), Path("benchmark/conformance/tests.json"))
            result = runner.run(SuiteOptions(suite_id="unit-write-fail", runs=1))
        finally:
            suite_module.run_conformance = original

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["conformance"]["status"], "fail")
        self.assertEqual(result["run_ids"], ["unit-write-fail-r001"])
        self.assertEqual(result["unscored_runs"], 1)
        self.assertIn("artifact_write_error", result)
        self.assertEqual(result["artifact_write"]["status"], "error")

    def test_suite_runs_cleanup_finalize_and_skip_marks_unscored(self) -> None:
        original_runtime = suite_module.EpisodeRuntime

        class FakeRuntime:
            starts = []
            cleanups = []
            final_reasons = []

            def __init__(self, remote, repo_root=None):
                self.options = None

            def start(self, options):
                self.options = options
                self.paths = {"summary": f"/tmp/{options.run_id}.json"}
                type(self).starts.append(options.run_id)
                return {"status": "ok", "stage": "v3_episode"}

            def observe(self):
                return {"status": "ok", "observation": {"metrics": {"present": True}, "ping": {"packets_received": 1}}}

            def act(self, action):
                return {"status": "ok", "accepted": True, "validation": {"valid": True}, "action": action}

            def _cleanup_after_error(self, run_id):
                type(self).cleanups.append(run_id)
                return {"status": "ok", "leftover_containers": [], "ws_port_open": False, "errors": []}

            def _finalize_after_error(self, reason, cleanup_success):
                type(self).final_reasons.append(reason)
                return {
                    "status": "ok",
                    "scored": reason is None,
                    "unscored_reason": reason,
                    "scores": {"ping_success_ratio": 1.0},
                    "counts": {"actions": 1},
                    "artifacts": {"summary": f"/tmp/{self.options.run_id}.json"},
                }

        suite_module.EpisodeRuntime = FakeRuntime
        try:
            runner = SuiteRunner(FakeRemote(), Path(".").resolve(), Path("benchmark/conformance/tests.json"))
            result = runner.run(SuiteOptions(suite_id="unit-skip", runs=2, duration=0, skip_conformance=True))
        finally:
            suite_module.EpisodeRuntime = original_runtime

        self.assertEqual(FakeRuntime.starts, ["unit-skip-r001", "unit-skip-r002"])
        self.assertEqual(FakeRuntime.cleanups, ["unit-skip-r001", "unit-skip-r002"])
        self.assertEqual(FakeRuntime.final_reasons, ["conformance skipped", "conformance skipped"])
        self.assertEqual(result["scored_runs"], 0)
        self.assertEqual(result["unscored_runs"], 2)

    def test_suite_finalizes_on_observe_failure(self) -> None:
        original_runtime = suite_module.EpisodeRuntime
        original_conformance = suite_module.run_conformance

        class FailingRuntime:
            cleanups = []
            final_reasons = []

            def __init__(self, remote, repo_root=None):
                self.options = None

            def start(self, options):
                self.options = options
                return {"status": "ok", "stage": "v3_episode"}

            def observe(self):
                raise RuntimeError("observe failed")

            def _cleanup_after_error(self, run_id):
                type(self).cleanups.append(run_id)
                return {"status": "ok", "leftover_containers": [], "ws_port_open": False, "errors": []}

            def _finalize_after_error(self, reason, cleanup_success):
                type(self).final_reasons.append((reason, cleanup_success))
                return {
                    "status": "ok",
                    "scored": False,
                    "unscored_reason": reason,
                    "scores": {},
                    "counts": {},
                    "artifacts": {"summary": f"/tmp/{self.options.run_id}.json"},
                }

        def fake_run_conformance(**kwargs):
            return {"status": "pass", "remote": {}, "checks": []}

        suite_module.EpisodeRuntime = FailingRuntime
        suite_module.run_conformance = fake_run_conformance
        try:
            runner = SuiteRunner(FakeRemote(), Path(".").resolve(), Path("benchmark/conformance/tests.json"))
            result = runner.run(SuiteOptions(suite_id="unit-failure", runs=1, duration=0))
        finally:
            suite_module.EpisodeRuntime = original_runtime
            suite_module.run_conformance = original_conformance

        self.assertEqual(FailingRuntime.cleanups, ["unit-failure-r001"])
        self.assertEqual(FailingRuntime.final_reasons, [("observe failed", True)])
        self.assertEqual(result["runs"][0]["unscored_reason"], "observe failed")

    def test_v4_suite_uses_e2_conformance_checks(self) -> None:
        original_conformance = suite_module.run_conformance

        captured = {}

        def fake_run_conformance(**kwargs):
            captured["checks"] = kwargs["checks"]
            return {"status": "fail", "remote": {}, "checks": []}

        suite_module.run_conformance = fake_run_conformance
        try:
            runner = SuiteRunner(FakeRemote(), Path(".").resolve(), Path("benchmark/conformance/tests.json"))
            result = runner.run(SuiteOptions(suite_id="unit-v4-blocked", task="e2_kpm_prb_ping_v1", runs=1))
        finally:
            suite_module.run_conformance = original_conformance

        self.assertEqual(captured["checks"], V4_SUITE_CONFORMANCE_CHECKS)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["stage"], "v4_suite")
        self.assertEqual(result["task"], "e2_kpm_prb_ping_v1")
        self.assertEqual(result["runs"][0]["artifacts"]["summary"], "/tmp/workspace/runs/unit-v4-blocked-r001/episode/summary.json")


if __name__ == "__main__":
    unittest.main()
