import unittest

from benchmark.benchmark_api.config import RemoteConfig, RuntimeConfig
from benchmark.benchmark_api.episode import (
    EpisodeOptions,
    EpisodeRuntime,
    ACTION_SET_SSB_BLOCK_POWER_WS,
    ACTION_SET_PRB_POLICY_RATIO_CCC,
    ACTION_SET_PRB_POLICY_RATIO_RC_DU,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
    TASK_E2_KPM_JSON_CONSISTENCY_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
    TASK_WS_SSB_POWER_GUARD_V1,
    TASK_WS_SSB_POWER_REPAIR_V1,
    TASK_WS_PRB_PING_V1,
    TASK_WS_PRB_ACTION_BUDGET_V1,
    TASK_WS_PRB_ERROR_REPAIR_V1,
    TASK_WS_PRB_NOOP_GUARD_V1,
    build_prb_request,
    build_ssb_request,
    episode_paths,
    episode_exit_code,
    generate_kpm_xapp_config,
    generate_v3_gnb_overlay,
    generate_v4_e2_gnb_overlay,
    generate_v4_e2_gnb_overlay_for_policy,
    kpm_record_has_prb_measurement,
    parse_kpm_indication_records,
    parse_ping_log,
    normalize_decision_telemetry,
    scenario_metadata,
    score_episode,
    task_policy,
    validate_prb_action,
    validate_ssb_action,
    validate_episode_action,
)
from benchmark.benchmark_api.ric import RIC_PORT


SAMPLE_SSH = "user@host"
SAMPLE_WORKSPACE = "/tmp/workspace"
SAMPLE_OCUDU_ROOT = "/tmp/workspace/ocudu"
SAMPLE_OPEN5GS_COMPOSE = "/tmp/workspace/assets/open5gs-core/compose/docker-compose.open5gs.yml"
SAMPLE_E2E_CONFIG_DIR = "/tmp/workspace/assets/ocudu-zmq-open5gs-e2e/config"
SAMPLE_OPEN5GS_IMAGE = "example/open5gs:test"
SAMPLE_GNB_IMAGE = "example/ocudu-build:test"
SAMPLE_UE_IMAGE = "example/srsran-4g-ue-build:test"


def sample_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        open5gs_compose=SAMPLE_OPEN5GS_COMPOSE,
        e2e_config_dir=SAMPLE_E2E_CONFIG_DIR,
        open5gs_image=SAMPLE_OPEN5GS_IMAGE,
        gnb_image=SAMPLE_GNB_IMAGE,
        ue_image=SAMPLE_UE_IMAGE,
    )


def sample_remote_config(**kwargs) -> RemoteConfig:
    values = {
        "ssh_target": SAMPLE_SSH,
        "ssh_key": "/tmp/key",
        "ocudu_root": SAMPLE_OCUDU_ROOT,
        "workspace": SAMPLE_WORKSPACE,
        "runtime": sample_runtime(),
    }
    values.update(kwargs)
    return RemoteConfig(**values)


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

    def test_validate_ssb_action_accepts_native_websocket_contract(self) -> None:
        action = {
            "type": ACTION_SET_SSB_BLOCK_POWER_WS,
            "plmn": "00101",
            "nci": 6733824,
            "ssb_block_power_dbm": -16,
        }
        validation = validate_ssb_action(action)

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["dispatch"], "websocket")
        self.assertEqual(validation["request"]["cmd"], "ssb_set")
        self.assertEqual(validation["request"]["cells"][0]["nci"], 6733824)
        self.assertEqual(validation["request"]["cells"][0]["ssb_block_power_dbm"], -16)

    def test_validate_ssb_action_rejects_bad_values(self) -> None:
        cases = [
            ({"type": ACTION_SET_SSB_BLOCK_POWER_WS, "ssb_block_power_dbm": -16}, "nci"),
            ({"type": ACTION_SET_SSB_BLOCK_POWER_WS, "nci": True, "ssb_block_power_dbm": -16}, "nci"),
            ({"type": ACTION_SET_SSB_BLOCK_POWER_WS, "nci": 1, "ssb_block_power_dbm": 51}, "[-60, 50]"),
            ({"type": ACTION_SET_SSB_BLOCK_POWER_WS, "nci": 1, "ssb_block_power_dbm": False}, "integer"),
        ]
        for action, reason in cases:
            with self.subTest(action=action):
                validation = validate_ssb_action(action)
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

    def test_build_ssb_request_uses_ocudu_ssb_set_shape(self) -> None:
        request = build_ssb_request({"plmn": "00101", "nci": 6733824, "ssb_block_power_dbm": -20})

        self.assertEqual(request["cmd"], "ssb_set")
        self.assertEqual(request["cells"], [{"plmn": "00101", "nci": 6733824, "ssb_block_power_dbm": -20}])

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
                {"timestamp": 101.0, "completed_at": 101.1, "validation": {"valid": False}, "dispatched": False, "accepted": False},
                {"timestamp": 102.0, "completed_at": 102.2, "validation": {"valid": True}, "dispatched": True, "accepted": True},
            ],
            observations=[{"observation": {"timestamp": 100.0, "metrics": {"present": True}}}],
            cleanup_success=True,
            decisions=[
                {
                    "timestamp": 101.0,
                    "decision_latency_s": 0.5,
                    "token_usage": {
                        "provider": "generic",
                        "model": "unit-model",
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "reasoning_tokens": 2,
                        "estimated_cost_usd": 0.01,
                    },
                }
            ],
            episode_started_at=99.0,
        )
        self.assertTrue(summary["scored"])
        self.assertEqual(summary["scoring_version"], "v2")
        self.assertEqual(summary["episode_success"], 1.0)
        self.assertIsNone(summary["failure_category"])
        self.assertEqual(summary["score_components"]["task_correctness"], 1.0)
        self.assertEqual(summary["score_components"]["cleanup"], 1.0)
        self.assertEqual(summary["counts"]["decisions"], 1)
        self.assertEqual(summary["efficiency"]["tokens"]["total_tokens"], 16)
        self.assertEqual(summary["efficiency"]["tokens"]["tokens_per_decision_mean"], 16)
        self.assertEqual(summary["efficiency"]["timing"]["time_to_first_action_s"], 2.0)
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
        self.assertEqual(summary["episode_success"], 0.0)
        self.assertEqual(summary["failure_category"], "setup")
        self.assertEqual(episode_exit_code({"status": "ok", "summary": summary}), 1)

    def test_score_episode_classifies_agent_and_oracle_failures(self) -> None:
        agent_summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_PING_V1,
        )
        oracle_summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[{"validation": {"valid": True}, "dispatched": True, "accepted": True}],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_E2_KPM_PRB_PING_V1,
            require_e2=True,
            e2_oracle={"kpm_indications": 0, "oracle_available": False},
        )

        self.assertTrue(agent_summary["scored"])
        self.assertEqual(agent_summary["episode_success"], 0.0)
        self.assertEqual(agent_summary["failure_category"], "agent")
        self.assertEqual(agent_summary["failure_reason"], "no accepted valid expected action")
        self.assertFalse(oracle_summary["scored"])
        self.assertEqual(oracle_summary["failure_category"], "oracle")

    def test_normalize_decision_telemetry_computes_token_total(self) -> None:
        telemetry = normalize_decision_telemetry(
            {
                "provider": "generic",
                "model": "unit-model",
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "reasoning_tokens": 2,
            },
            decision_latency_s=1.5,
        )

        self.assertEqual(telemetry["decision_latency_s"], 1.5)
        self.assertEqual(telemetry["token_usage"]["total_tokens"], 15)
        self.assertEqual(telemetry["token_usage"]["provider"], "generic")

    def test_normalize_decision_telemetry_rejects_negative_or_fractional_tokens(self) -> None:
        telemetry = normalize_decision_telemetry(
            {
                "prompt_tokens": -1,
                "completion_tokens": 2.5,
                "reasoning_tokens": 3.0,
                "estimated_cost_usd": -0.1,
            }
        )

        self.assertEqual(telemetry["token_usage"], {"reasoning_tokens": 3, "total_tokens": 3})

    def test_v3_overlay_enables_metrics_and_remote_control(self) -> None:
        overlay = generate_v3_gnb_overlay(9001)
        self.assertIn("enable_json: true", overlay)
        self.assertIn("enable_app_usage: true", overlay)
        self.assertIn("app_usage_report_period: 1000", overlay)
        self.assertIn("remote_control:", overlay)
        self.assertIn("bind_addr: 127.0.0.1", overlay)
        self.assertIn("port: 9001", overlay)

    def test_v4_overlay_enables_e2_kpm_pcap_and_remote_control(self) -> None:
        overlay = generate_v4_e2_gnb_overlay(9001)
        self.assertIn("remote_control:", overlay)
        self.assertIn("enable_json: true", overlay)
        self.assertIn("enable_du_e2: true", overlay)
        self.assertIn("enable_cu_cp_e2: true", overlay)
        self.assertIn(f"port: {RIC_PORT}", overlay)
        self.assertIn("bind_addr: 127.0.0.1", overlay)
        self.assertIn("e2sm_kpm_enabled: true", overlay)
        self.assertIn("e2sm_rc_enabled: false", overlay)
        self.assertIn("e2sm_ccc_enabled: false", overlay)
        self.assertIn("e2ap_enable: true", overlay)
        self.assertIn("/stage/logs/e2ap_du.pcap", overlay)

    def test_v4_overlay_enables_control_service_models_only_when_requested(self) -> None:
        overlay = generate_v4_e2_gnb_overlay(9001, enable_rc=True, enable_ccc=True)
        self.assertIn("e2sm_kpm_enabled: true", overlay)
        self.assertIn("e2sm_rc_enabled: true", overlay)
        self.assertIn("e2sm_ccc_enabled: true", overlay)

    def test_v4_overlay_for_kpm_task_disables_control_service_models(self) -> None:
        overlay = generate_v4_e2_gnb_overlay_for_policy(9001, task_policy(TASK_E2_KPM_PRB_PING_V1))
        self.assertIn("e2sm_kpm_enabled: true", overlay)
        self.assertIn("e2sm_rc_enabled: false", overlay)
        self.assertIn("e2sm_ccc_enabled: false", overlay)

    def test_v4_overlay_for_control_tasks_enables_required_service_models(self) -> None:
        ccc_overlay = generate_v4_e2_gnb_overlay_for_policy(9001, task_policy(TASK_E2_CCC_PRB_POLICY_PING_V1))
        rc_overlay = generate_v4_e2_gnb_overlay_for_policy(9001, task_policy(TASK_E2_RC_DU_PRB_POLICY_PING_V1))
        consistency_overlay = generate_v4_e2_gnb_overlay_for_policy(9001, task_policy(TASK_E2_CONTROL_API_CONSISTENCY_V1))
        self.assertIn("e2sm_rc_enabled: false", ccc_overlay)
        self.assertIn("e2sm_ccc_enabled: true", ccc_overlay)
        self.assertIn("e2sm_rc_enabled: true", rc_overlay)
        self.assertIn("e2sm_ccc_enabled: false", rc_overlay)
        self.assertIn("e2sm_rc_enabled: true", consistency_overlay)
        self.assertIn("e2sm_ccc_enabled: true", consistency_overlay)

    def test_v4_kpm_xapp_config_matches_supported_du_style(self) -> None:
        config = generate_kpm_xapp_config()
        self.assertIn('Name = "xApp"', config)
        self.assertIn("format = 1", config)
        self.assertIn('ran_type = "ngran_gNB_DU"', config)
        self.assertIn('name = "RRU.PrbUsedDl"', config)
        self.assertIn('name = "RRU.PrbUsedUl"', config)
        self.assertIn('name = "RRU.PrbTotDl"', config)
        self.assertIn('name = "RRU.PrbTotUl"', config)
        self.assertIn('enable = "OFF"', config)
        self.assertNotIn("format = 4", config)

    def test_parse_kpm_indication_records_ignores_subscription_setup(self) -> None:
        records = parse_kpm_indication_records(
            "[xApp]: RIC SUBSCRIPTION REQUEST sent\n"
            "[xApp]: SUBSCRIPTION RESPONSE received\n"
            "[xApp]: Successfully SUBSCRIBED to ran function = 2\n"
            "      1, KPM v2 ind_msg latency > 10 s from E2-node type 2 ID 411\n"
            "meas record INTEGER_MEAS_VALUE value 28\n"
            "meas record INTEGER_MEAS_VALUE value 8312\n"
            "      2, KPM v2 ind_msg latency > 11 s from E2-node type 2 ID 411\n"
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(len(records[0]["measurements"]), 2)
        self.assertIn("KPM v2 ind_msg", records[0]["text"])

    def test_kpm_prb_measurement_detection_requires_prb_name_or_text(self) -> None:
        self.assertTrue(
            kpm_record_has_prb_measurement(
                {"measurements": [{"name": "RRU.PrbUsedDl", "type": "integer", "value": 1}]}
            )
        )
        self.assertTrue(kpm_record_has_prb_measurement({"measurements": [{"text": "meas record RRU.PrbTotUl"}]}))
        self.assertFalse(kpm_record_has_prb_measurement({"measurements": [{"name": "DRB.UEThpDl"}]}))
        self.assertFalse(kpm_record_has_prb_measurement({"measurements": []}))

    def test_score_episode_requires_e2_when_requested(self) -> None:
        base = {
            "ping": {"packets_received": 3, "success_ratio": 1.0},
            "actions": [{"validation": {"valid": True}, "dispatched": True, "accepted": True}],
            "observations": [{"observation": {"metrics": {"present": True}}}],
            "cleanup_success": True,
            "require_e2": True,
        }
        failed = score_episode(**base, e2_oracle={"kpm_indications": 2, "oracle_available": True})
        passed = score_episode(**base, e2_oracle={"kpm_indications": 3, "oracle_available": True})

        self.assertFalse(failed["scored"])
        self.assertEqual(failed["unscored_reason"], "insufficient E2 KPM indications")
        self.assertTrue(passed["scored"])
        self.assertEqual(passed["scores"]["e2_kpm_continuity"], 3)

    def test_score_episode_supports_noop_guard_task(self) -> None:
        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_NOOP_GUARD_V1,
        )
        failed = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[{"validation": {"valid": True}, "dispatched": True, "accepted": True}],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_NOOP_GUARD_V1,
        )

        self.assertTrue(summary["scored"])
        self.assertEqual(summary["scores"]["noop_correctness"], 1.0)
        self.assertTrue(failed["scored"])
        self.assertEqual(failed["episode_success"], 0.0)
        self.assertEqual(failed["failure_category"], "agent")
        self.assertEqual(failed["failure_reason"], "agent acted during no-op task")
        self.assertEqual(failed["score_components"]["task_correctness"], 0.0)

    def test_score_episode_supports_error_repair_task(self) -> None:
        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[
                {"validation": {"valid": False}, "dispatched": False, "accepted": False},
                {"validation": {"valid": True}, "dispatched": True, "accepted": True},
            ],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_ERROR_REPAIR_V1,
        )
        failed = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[{"validation": {"valid": True}, "dispatched": True, "accepted": True}],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_ERROR_REPAIR_V1,
        )

        self.assertTrue(summary["scored"])
        self.assertTrue(failed["scored"])
        self.assertEqual(failed["episode_success"], 0.0)
        self.assertIn("invalid action followed by valid repair", failed["failure_reason"])

    def test_score_episode_supports_ssb_repair_task(self) -> None:
        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[
                {"validation": {"valid": False, "normalized": {"type": ACTION_SET_SSB_BLOCK_POWER_WS}}, "dispatched": False, "accepted": False},
                {
                    "validation": {"valid": True, "normalized": {"type": ACTION_SET_SSB_BLOCK_POWER_WS}},
                    "dispatched": True,
                    "accepted": True,
                    "action": {"type": ACTION_SET_SSB_BLOCK_POWER_WS},
                },
            ],
            observations=[{"observation": {"metrics": {"present": True}, "cell": {"nci": 6733824}}}],
            cleanup_success=True,
            task=TASK_WS_SSB_POWER_REPAIR_V1,
        )

        self.assertTrue(summary["scored"])
        self.assertEqual(summary["counts"]["accepted_expected_actions"], 1)

    def test_score_episode_supports_action_budget_task(self) -> None:
        action = {"validation": {"valid": True}, "dispatched": True, "accepted": True}
        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[action],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_ACTION_BUDGET_V1,
        )
        failed = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[action, action],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_WS_PRB_ACTION_BUDGET_V1,
        )

        self.assertTrue(summary["scored"])
        self.assertTrue(summary["scores"]["action_budget_ok"])
        self.assertTrue(failed["scored"])
        self.assertEqual(failed["episode_success"], 0.0)
        self.assertEqual(failed["failure_category"], "agent")
        self.assertEqual(failed["failure_reason"], "action budget exceeded")

    def test_score_episode_supports_evidence_and_stale_guards(self) -> None:
        evidence_action = {
            "validation": {"valid": True},
            "dispatched": True,
            "accepted": True,
            "decision_context": {
                "metrics": {"present": True, "stale": False},
                "e2": {"kpm_indications": 3, "has_prb_measurement": True},
            },
        }
        e2_summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[evidence_action],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_E2_KPM_JSON_CONSISTENCY_V1,
            e2_oracle={"kpm_indications": 3, "oracle_available": True},
        )
        stale_summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[{"validation": {"valid": True}, "dispatched": True, "accepted": True, "decision_context": {"metrics": {"present": True, "stale": False}}}],
            observations=[
                {"observation": {"metrics": {"present": False, "stale": True}, "scenario": {"metrics_stale": True}}},
                {"observation": {"metrics": {"present": False, "stale": True}, "scenario": {"metrics_stale": True}}},
                {"observation": {"metrics": {"present": True, "stale": False}, "scenario": {"metrics_stale": False}}},
            ],
            cleanup_success=True,
            task=TASK_METRICS_STALENESS_NOOP_V1,
        )

        self.assertTrue(e2_summary["scored"])
        self.assertTrue(e2_summary["scores"]["evidence_gated_action"])
        self.assertTrue(stale_summary["scored"])
        self.assertEqual(stale_summary["counts"]["stale_metric_observations"], 2)

    def test_score_episode_marks_context_capture_failure_unscored_for_context_tasks(self) -> None:
        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[
                {
                    "validation": {"valid": True},
                    "dispatched": True,
                    "accepted": True,
                    "decision_context": {},
                    "decision_context_error": "observation read failed",
                }
            ],
            observations=[
                {"observation": {"metrics": {"present": False, "stale": True}, "scenario": {"metrics_stale": True}}},
                {"observation": {"metrics": {"present": False, "stale": True}, "scenario": {"metrics_stale": True}}},
                {"observation": {"metrics": {"present": True, "stale": False}, "scenario": {"metrics_stale": False}}},
            ],
            cleanup_success=True,
            task=TASK_METRICS_STALENESS_NOOP_V1,
        )

        self.assertFalse(summary["scored"])
        self.assertEqual(summary["unscored_reason"], "decision context capture failed")
        self.assertEqual(summary["counts"]["decision_context_errors"], 1)

    def test_task_policy_and_scenario_metadata_describe_new_tasks(self) -> None:
        self.assertTrue(task_policy(TASK_E2_KPM_JSON_CONSISTENCY_V1).requires_e2)
        self.assertEqual(task_policy(TASK_WS_PRB_NOOP_GUARD_V1).default_smoke_agent, "noop")
        self.assertEqual(task_policy(TASK_E2_CCC_PRB_POLICY_PING_V1).expected_action_type, ACTION_SET_PRB_POLICY_RATIO_CCC)
        self.assertTrue(task_policy(TASK_E2_RC_DU_PRB_POLICY_PING_V1).requires_ue_identity)
        self.assertIn(ACTION_SET_PRB_POLICY_RATIO_RC_DU, task_policy(TASK_E2_CONTROL_API_CONSISTENCY_V1).allowed_action_types)
        self.assertEqual(task_policy(TASK_WS_SSB_POWER_REPAIR_V1).expected_action_type, ACTION_SET_SSB_BLOCK_POWER_WS)
        self.assertTrue(task_policy(TASK_WS_SSB_POWER_GUARD_V1).require_no_actions)
        scenario = scenario_metadata(TASK_METRICS_STALENESS_NOOP_V1, duration=5)
        self.assertEqual(scenario["labels"]["stale_metrics_observations"], 2)
        self.assertEqual(scenario["traffic"]["target"], "10.45.1.1")

    def test_validate_e2_prb_actions_use_expected_wire_shapes(self) -> None:
        ccc = validate_episode_action(
            {
                "type": ACTION_SET_PRB_POLICY_RATIO_CCC,
                "min_prb_policy_ratio": 10,
                "max_prb_policy_ratio": 90,
            },
            TASK_E2_CCC_PRB_POLICY_PING_V1,
        )
        rc = validate_episode_action(
            {
                "type": ACTION_SET_PRB_POLICY_RATIO_RC_DU,
                "min_prb_policy_ratio": 10,
                "max_prb_policy_ratio": 90,
                "du_ue_id": 7,
            },
            TASK_E2_RC_DU_PRB_POLICY_PING_V1,
        )

        self.assertTrue(ccc["valid"])
        self.assertEqual(ccc["dispatch"], "e2_ccc")
        self.assertEqual(ccc["request"]["control"], "O-RRMPolicyRatio")
        self.assertTrue(rc["valid"])
        self.assertEqual(rc["dispatch"], "e2_rc_du")
        self.assertEqual(rc["request"]["control_style"], 2)
        self.assertEqual(rc["request"]["control_action"], 6)

    def test_validate_episode_action_routes_ssb_tasks(self) -> None:
        validation = validate_episode_action(
            {"type": ACTION_SET_SSB_BLOCK_POWER_WS, "nci": 6733824, "ssb_block_power_dbm": -16},
            TASK_WS_SSB_POWER_REPAIR_V1,
        )
        wrong_task = validate_episode_action(
            {"type": ACTION_SET_SSB_BLOCK_POWER_WS, "nci": 6733824, "ssb_block_power_dbm": -16},
            TASK_WS_PRB_PING_V1,
        )

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["request"]["cmd"], "ssb_set")
        self.assertFalse(wrong_task["valid"])

    def test_score_episode_requires_expected_e2_control_action_and_oracle(self) -> None:
        action = {
            "validation": {
                "valid": True,
                "dispatch": "e2_ccc",
                "normalized": {"type": ACTION_SET_PRB_POLICY_RATIO_CCC},
            },
            "dispatched": True,
            "accepted": True,
            "decision_context": {
                "metrics": {"present": True, "stale": False},
                "e2": {"kpm_indications": 3, "has_prb_measurement": True},
            },
            "action": {"type": ACTION_SET_PRB_POLICY_RATIO_CCC},
        }
        passed = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[action],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_E2_CCC_PRB_POLICY_PING_V1,
            e2_oracle={
                "kpm_indications": 3,
                "oracle_available": True,
                "control_oracle_available": True,
                "control_types": [ACTION_SET_PRB_POLICY_RATIO_CCC],
                "control_records": [{"accepted": True}],
            },
        )
        wrong_api = dict(action)
        wrong_api["validation"] = {
            "valid": True,
            "dispatch": "e2_rc_du",
            "normalized": {"type": ACTION_SET_PRB_POLICY_RATIO_RC_DU},
        }
        wrong_api["action"] = {"type": ACTION_SET_PRB_POLICY_RATIO_RC_DU}
        failed = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[wrong_api],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_E2_CONTROL_API_CONSISTENCY_V1,
            e2_oracle={
                "kpm_indications": 3,
                "oracle_available": True,
                "control_oracle_available": True,
                "control_types": [ACTION_SET_PRB_POLICY_RATIO_RC_DU],
                "control_records": [{"accepted": True}],
            },
        )

        self.assertTrue(passed["scored"])
        self.assertEqual(passed["scores"]["accepted_e2_control_actions"], 1)
        self.assertTrue(failed["scored"])
        self.assertEqual(failed["episode_success"], 0.0)
        self.assertEqual(failed["failure_category"], "agent")
        self.assertEqual(failed["failure_reason"], "no accepted valid expected action")

    def test_score_episode_requires_expected_e2_control_oracle_type(self) -> None:
        action = {
            "validation": {
                "valid": True,
                "dispatch": "e2_ccc",
                "normalized": {"type": ACTION_SET_PRB_POLICY_RATIO_CCC},
            },
            "dispatched": True,
            "accepted": True,
            "decision_context": {
                "metrics": {"present": True, "stale": False},
                "e2": {"kpm_indications": 3, "has_prb_measurement": True},
            },
            "action": {"type": ACTION_SET_PRB_POLICY_RATIO_CCC},
        }

        summary = score_episode(
            ping={"packets_received": 3, "success_ratio": 1.0},
            actions=[action],
            observations=[{"observation": {"metrics": {"present": True}}}],
            cleanup_success=True,
            task=TASK_E2_CCC_PRB_POLICY_PING_V1,
            e2_oracle={
                "kpm_indications": 3,
                "oracle_available": True,
                "control_oracle_available": True,
                "control_types": [ACTION_SET_PRB_POLICY_RATIO_RC_DU],
                "control_records": [{"accepted": True}],
            },
        )

        self.assertFalse(summary["scored"])
        self.assertEqual(summary["unscored_reason"], "E2 control oracle missing expected action type")

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
            config = sample_remote_config()

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]
        scripts = []

        def fake_remote_json(body):
            scripts.append(body)
            return {"status": "error", "summary": "stop before launch"}

        runtime._remote_json = fake_remote_json  # type: ignore[method-assign]
        runtime.start(EpisodeOptions(run_id="unit-root", task=TASK_E2_KPM_JSON_CONSISTENCY_V1))

        self.assertIn(SAMPLE_OCUDU_ROOT, scripts[0])
        self.assertIn(SAMPLE_OPEN5GS_COMPOSE, scripts[0])
        self.assertIn(SAMPLE_E2E_CONFIG_DIR, scripts[0])
        self.assertIn(SAMPLE_GNB_IMAGE, scripts[0])
        self.assertIn(SAMPLE_UE_IMAGE, scripts[0])
        self.assertIn("skillful-ran/flexric-bench", scripts[0])
        self.assertIn("kpm_xapp_container", scripts[0])
        self.assertIn("e2_pcap_container", scripts[0])
        self.assertIn("e2ap_sctp.pcap", scripts[0])
        self.assertIn("scenario.json", scripts[0])
        self.assertNotIn("/home/", scripts[0])

    def test_latest_decision_context_surfaces_snapshot_errors(self) -> None:
        class FakeRemote:
            config = sample_remote_config()

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]
        runtime.paths = {"observations": "/tmp/workspace/runs/unit/episode/observations.jsonl"}

        def fail_remote_json(body):
            raise RuntimeError("remote read failed")

        runtime._remote_json = fail_remote_json  # type: ignore[method-assign]
        context = runtime._latest_decision_context()

        self.assertIn("_decision_context_error", context)
        self.assertIn("remote read failed", context["_decision_context_error"])

    def test_finalize_remote_script_uses_shared_score_episode_source(self) -> None:
        class FakeRemote:
            config = sample_remote_config()

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]
        runtime.options = EpisodeOptions(run_id="unit-finalize", task=TASK_METRICS_STALENESS_NOOP_V1)
        runtime.paths = episode_paths(SAMPLE_WORKSPACE, "unit-finalize")
        scripts = []

        def fake_remote_json(body):
            scripts.append(body)
            return {"status": "ok", "scored": False}

        runtime._remote_json = fake_remote_json  # type: ignore[method-assign]
        runtime.finalize()

        self.assertIn("def score_episode(", scripts[0])
        self.assertIn("scoring = score_episode(", scripts[0])
        self.assertIn("decision_context_errors", scripts[0])

    def test_start_rejects_unimplemented_task_even_if_manifest_exists_later(self) -> None:
        class FakeRemote:
            config = sample_remote_config()

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "Unsupported episode task"):
            runtime.start(EpisodeOptions(run_id="unit-unsupported", task="future_task_v1"))

    def test_cleanup_script_reports_postcondition_status(self) -> None:
        class FakeRemote:
            config = sample_remote_config()

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
        self.assertIn("ric_port_open", scripts[0])
        self.assertIn("e2_control_container_prefix", scripts[0])
        self.assertIn("e2_control_containers_removed", scripts[0])
        self.assertIn("startswith(payload[\"e2_control_container_prefix\"])", scripts[0])
        self.assertIn("status = \"error\" if errors else \"ok\"", scripts[0])

    def test_docker_asset_check_uses_configured_images(self) -> None:
        class FakeRemote:
            config = sample_remote_config()

        runtime = EpisodeRuntime(FakeRemote())  # type: ignore[arg-type]
        scripts = []

        def fake_remote_json(body):
            scripts.append(body)
            return {"docker": "/usr/bin/docker", "docker_compose": True, "images": {}, "files": {}}

        runtime._remote_json = fake_remote_json  # type: ignore[method-assign]
        runtime.check_docker_assets()

        self.assertIn(SAMPLE_GNB_IMAGE, scripts[0])
        self.assertIn(SAMPLE_UE_IMAGE, scripts[0])


if __name__ == "__main__":
    unittest.main()
