import unittest

from benchmark.benchmark_api.config import RemoteConfig
from benchmark.benchmark_api.episode import (
    EpisodeOptions,
    EpisodeRuntime,
    build_prb_request,
    episode_exit_code,
    generate_v3_gnb_overlay,
    parse_ping_log,
    score_episode,
    validate_prb_action,
)


class EpisodeTests(unittest.TestCase):
    def test_validate_prb_action_accepts_v3_contract(self) -> None:
        action = {
            "type": "SET_PRB_POLICY_RATIO_WS",
            "min_prb_policy_ratio": 10,
            "max_prb_policy_ratio": 90,
            "sd": 0xFFFFFF,
            "dedicated_ratio": 0,
        }
        validation = validate_prb_action(action)
        self.assertTrue(validation["valid"])
        normalized = validation["normalized"]
        self.assertEqual(normalized["plmn"], "00101")
        self.assertEqual(normalized["sst"], 1)
        request = validation["request"]
        self.assertEqual(request["cmd"], "rrm_policy_ratio_set")
        self.assertEqual(request["policies"]["resourceType"], "PRB")
        self.assertEqual(request["policies"]["rRMPolicyMemberList"][0]["sd"], 0xFFFFFF)
        self.assertEqual(request["policies"]["min_prb_policy_ratio"], 10)
        self.assertEqual(request["policies"]["max_prb_policy_ratio"], 90)

    def test_validate_prb_action_rejects_bad_values(self) -> None:
        cases = [
            ("not a dict", "action must be a dictionary"),
            ({"type": "OTHER", "min_prb_policy_ratio": 1, "max_prb_policy_ratio": 2}, "unsupported action type"),
            ({"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": -1, "max_prb_policy_ratio": 2}, "in [0, 100]"),
            ({"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10}, "<= max"),
            (
                {
                    "type": "SET_PRB_POLICY_RATIO_WS",
                    "min_prb_policy_ratio": 10,
                    "max_prb_policy_ratio": 90,
                    "dedicated_ratio": 101,
                },
                "dedicated_ratio",
            ),
        ]
        for action, reason in cases:
            with self.subTest(action=action):
                validation = validate_prb_action(action)
                self.assertFalse(validation["valid"])
                self.assertIn(reason, validation["reason"])

    def test_build_prb_request_omits_optional_sd_when_absent(self) -> None:
        request = build_prb_request(
            {
                "plmn": "00101",
                "sst": 1,
                "sd": None,
                "min_prb_policy_ratio": 20,
                "max_prb_policy_ratio": 80,
                "dedicated_ratio": None,
            }
        )
        member = request["policies"]["rRMPolicyMemberList"][0]
        self.assertNotIn("sd", member)
        self.assertNotIn("dedicated_ratio", request["policies"])

    def test_parse_ping_log_counts_replies_and_summary(self) -> None:
        ping = parse_ping_log(
            "[171000.1] 64 bytes from 10.45.1.1: icmp_seq=1 ttl=64 time=1.0 ms\n"
            "[171000.3] 64 bytes from 10.45.1.1: icmp_seq=2 ttl=64 time=1.1 ms\n"
            "2 packets transmitted, 2 received, 0% packet loss, time 200ms\n"
        )
        self.assertEqual(ping["packets_transmitted"], 2)
        self.assertEqual(ping["packets_received"], 2)
        self.assertEqual(ping["success_ratio"], 1.0)

    def test_score_episode_marks_healthy_run_scored(self) -> None:
        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[
                {"validation": {"valid": False}, "dispatched": False, "accepted": False},
                {"validation": {"valid": True}, "dispatched": True, "accepted": True},
            ],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
        )
        self.assertTrue(summary["scored"])
        self.assertEqual(summary["scores"]["valid_action_accepted_rate"], 1.0)
        self.assertEqual(summary["scores"]["invalid_local_rejection_correctness"], 1.0)

    def test_score_episode_marks_setup_failure_unscored(self) -> None:
        summary = score_episode(
            ping={"packets_received": 0, "success_ratio": 0.0},
            actions=[],
            observations=[],
            cleanup_success=True,
            unscored_reason="setup failed",
        )
        self.assertFalse(summary["scored"])
        self.assertEqual(summary["unscored_reason"], "setup failed")
        self.assertEqual(episode_exit_code({"status": "ok", "summary": summary}), 1)

    def test_v3_overlay_enables_metrics_and_remote_control(self) -> None:
        overlay = generate_v3_gnb_overlay(9001)
        self.assertIn("enable_json: true", overlay)
        self.assertIn("remote_control:", overlay)
        self.assertIn("bind_addr: 127.0.0.1", overlay)
        self.assertIn("port: 9001", overlay)

    def test_run_cleans_up_and_marks_unscored_on_observe_failure(self) -> None:
        class FailingRuntime(EpisodeRuntime):
            def __init__(self) -> None:
                self.cleaned = False
                self.finalized = None

            def start(self, options):
                self.options = options
                self.paths = {}
                return {"status": "ok", "stage": "v3_episode"}

            def observe(self):
                raise RuntimeError("observe failed")

            def cleanup(self, run_id):
                self.cleaned = True
                return {"status": "ok", "run_id": run_id}

            def finalize(self, unscored_reason=None, cleanup_success=True):
                self.finalized = (unscored_reason, cleanup_success)
                return {"status": "ok", "scored": False, "unscored_reason": unscored_reason}

        runtime = FailingRuntime()
        result = runtime.run(EpisodeOptions(run_id="unit-cleanup", duration=0))

        self.assertEqual(result["status"], "error")
        self.assertTrue(runtime.cleaned)
        self.assertEqual(runtime.finalized, ("observe failed", True))
        self.assertEqual(result["summary"]["unscored_reason"], "observe failed")

    def test_start_uses_configured_ocudu_root_for_docker_mounts(self) -> None:
        class FakeRemote:
            config = RemoteConfig(
                ssh_target="zhouyou@10.34.23.184",
                ssh_key="/tmp/key",
                ocudu_root="/custom/ocudu",
                workspace="/tmp/workspace",
            )

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]
        scripts = []

        def fake_remote_json(body):
            scripts.append(body)
            return {"status": "error", "summary": "stop before launch"}

        runtime._remote_json = fake_remote_json  # type: ignore[method-assign]
        runtime.start(EpisodeOptions(run_id="unit-root"))

        self.assertIn("/custom/ocudu", scripts[0])
        self.assertNotIn("/home/zhouyou/skillful-ran/.local/ocudu/install", scripts[0])

    def test_cleanup_script_reports_postcondition_status(self) -> None:
        class FakeRemote:
            config = RemoteConfig("zhouyou@10.34.23.184", "/tmp/key", workspace="/tmp/workspace")

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]
        scripts = []

        def fake_remote_json(body):
            scripts.append(body)
            return {"status": "ok", "run_id": "unit-cleanup", "leftover_containers": [], "ws_port_open": False}

        runtime._remote_json = fake_remote_json  # type: ignore[method-assign]
        result = runtime.cleanup("unit-cleanup")

        self.assertEqual(result["status"], "ok")
        self.assertIn("leftover_containers", scripts[0])
        self.assertIn("ws_port_open", scripts[0])
        self.assertIn("status = \"error\" if errors else \"ok\"", scripts[0])


if __name__ == "__main__":
    unittest.main()
