"""Closed benchmark sum types.

These enums are the executable counterpart of
``docs/design/benchmark_sum_type_list.md``.
"""

from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    """String-valued enum with stable JSON representation."""

    def __str__(self) -> str:
        return self.value


class RanApiKind(StringEnum):
    OCUDU_WEBSOCKET_PRB_POLICY = "ocudu_websocket_prb_policy"
    OCUDU_WEBSOCKET_SSB_POWER = "ocudu_websocket_ssb_power"
    OCUDU_CLI_HANDOVER = "ocudu_cli_handover"
    OCUDU_CLI_CONDITIONAL_HANDOVER = "ocudu_cli_conditional_handover"
    OCUDU_CLI_CFO_CONTROL = "ocudu_cli_cfo_control"
    OCUDU_CLI_TX_TIME_OFFSET_CONTROL = "ocudu_cli_tx_time_offset_control"
    CORE_NF_LIFECYCLE_CONTROL = "core_nf_lifecycle_control"
    CORE_UE_REGISTRATION_CONTROL = "core_ue_registration_control"
    OCUDU_JSON_METRICS = "ocudu_json_metrics"
    E2SM_KPM_V05_OBSERVATION = "e2sm_kpm_v05_observation"
    E2SM_CCC_PRB_POLICY_CONTROL = "e2sm_ccc_prb_policy_control"
    E2SM_RC_DU_PRB_QUOTA_CONTROL = "e2sm_rc_du_prb_quota_control"


class RanApiRole(StringEnum):
    EVIDENCE = "evidence"
    ACTION = "action"
    FEEDBACK = "feedback"
    ORACLE_EVIDENCE = "oracle_evidence"


class RanApiBackend(StringEnum):
    WEBSOCKET = "websocket"
    OCUDU_CLI = "ocudu_cli"
    CORE_CONTROL = "core_control"
    JSON_METRICS = "json_metrics"
    E2_KPM = "e2_kpm"
    E2_CONTROL = "e2_control"


class RanActionType(StringEnum):
    SET_PRB_POLICY_RATIO_WS = "SET_PRB_POLICY_RATIO_WS"
    SET_SSB_BLOCK_POWER_WS = "SET_SSB_BLOCK_POWER_WS"
    TRIGGER_HANDOVER_CLI = "TRIGGER_HANDOVER_CLI"
    TRIGGER_CONDITIONAL_HANDOVER_CLI = "TRIGGER_CONDITIONAL_HANDOVER_CLI"
    SET_CFO_CLI = "SET_CFO_CLI"
    SET_TX_TIME_OFFSET_CLI = "SET_TX_TIME_OFFSET_CLI"
    RESTART_CORE_NF = "RESTART_CORE_NF"
    UPDATE_CORE_UE_REGISTRATION = "UPDATE_CORE_UE_REGISTRATION"
    SET_PRB_POLICY_RATIO_CCC = "SET_PRB_POLICY_RATIO_CCC"
    SET_PRB_POLICY_RATIO_RC_DU = "SET_PRB_POLICY_RATIO_RC_DU"
    NO_ACTION = "NO_ACTION"


class RanObservationSource(StringEnum):
    PING = "ping"
    JSON_METRICS = "json_metrics"
    WEBSOCKET_CONTROL_OUTCOMES = "websocket_control_outcomes"
    CELL_IDENTITY = "cell_identity"
    E2_KPM_V05 = "e2_kpm_v05"
    E2_CONTROL_OUTCOME = "e2_control_outcome"
    UE_IDENTITY = "ue_identity"
    UE_RUNTIME = "ue_runtime"
    CORE_RUNTIME = "core_runtime"
    RADIO_RUNTIME = "radio_runtime"
    SLICE_RUNTIME = "slice_runtime"
    BACKHAUL_RUNTIME = "backhaul_runtime"


class SafeErrorClass(StringEnum):
    SCHEMA_ERROR = "schema_error"
    PERMISSION_ERROR = "permission_error"
    TIMEOUT = "timeout"
    RUNTIME_REJECTED = "runtime_rejected"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    INTERNAL_REDACTED = "internal_redacted"


class ApiCompatibilityStatus(StringEnum):
    COMPATIBLE = "compatible"
    MISSING_RUNTIME_REQUIREMENT = "missing_runtime_requirement"
    SCHEMA_MISMATCH = "schema_mismatch"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    CONFORMANCE_MISSING = "conformance_missing"
    DISABLED_FOR_TASK = "disabled_for_task"


class StimulusDriverKind(StringEnum):
    UE_PING_TRAFFIC = "ue_ping_traffic"
    METRICS_STALENESS_MASK = "metrics_staleness_mask"
    DOCKER_ZMQ_RUNTIME_LAUNCH = "docker_zmq_runtime_launch"
    TRAFFIC_LOAD_PROFILE = "traffic_load_profile"
    UE_ACTIVITY_CHURN = "ue_activity_churn"
    CORE_UE_REGISTRATION_MISCONFIG = "core_ue_registration_misconfig"
    MOBILITY_PATH = "mobility_path"
    RADIO_CONDITION_PROFILE = "radio_condition_profile"
    SLICE_DEMAND_SHIFT = "slice_demand_shift"
    TELEMETRY_GAP = "telemetry_gap"
    E2_KPM_AVAILABILITY_WINDOW = "e2_kpm_availability_window"
    RIC_XAPP_LIFECYCLE = "ric_xapp_lifecycle"
    CORE_LATENCY_PROFILE = "core_latency_profile"
    BACKHAUL_IMPAIRMENT = "backhaul_impairment"
    CELL_IDENTITY_CHANGE = "cell_identity_change"
    FUTURE_ZMQ_IMPAIRMENT = "future_zmq_impairment"


class StimulusImplementationStatus(StringEnum):
    IMPLEMENTED = "implemented"
    FUTURE_IMPLEMENTABLE = "future_implementable"
    REQUIRES_EXTENSION = "requires_extension"


class StimulusPhase(StringEnum):
    PRE_OBSERVATION = "pre_observation"
    IN_STEP = "in_step"


class StimulusEventStatus(StringEnum):
    SCHEDULED = "scheduled"
    APPLIED = "applied"
    SKIPPED = "skipped"
    LATE = "late"
    DRIVER_ERROR = "driver_error"
    CANCELLED_BY_EPISODE_FAILURE = "cancelled_by_episode_failure"


class ClockMode(StringEnum):
    WALL_CLOCK = "wall_clock"
    VIRTUAL_CLOCK = "virtual_clock"
    FIXED_TICK = "fixed_tick"


class RawScoreMetric(StringEnum):
    VALID_ACTION_ACCEPTED_RATE = "valid_action_accepted_rate"
    INVALID_LOCAL_REJECTION_CORRECTNESS = "invalid_local_rejection_correctness"
    PING_SUCCESS_RATIO = "ping_success_ratio"
    METRICS_CONTINUITY = "metrics_continuity"
    E2_KPM_CONTINUITY = "e2_kpm_continuity"
    E2_ORACLE_AVAILABLE = "e2_oracle_available"
    E2_CONTROL_ORACLE_AVAILABLE = "e2_control_oracle_available"
    EXPECTED_ACTION_TYPE_CORRECT = "expected_action_type_correct"
    ACTION_BUDGET_OK = "action_budget_ok"
    NOOP_CORRECTNESS = "noop_correctness"
    EVIDENCE_GATED_ACTION = "evidence_gated_action"
    STALE_ACTION_AVOIDANCE = "stale_action_avoidance"
    TRIAGE_SUCCESS = "triage_success"
    CORRECT_API_SELECTION = "correct_api_selection"
    TEMPORAL_ACTION_SEQUENCE_MATCH = "temporal_action_sequence_match"
    EXPECTED_ACTION_PAYLOAD_MATCH = "expected_action_payload_match"
    POST_ACTION_EVIDENCE_MATCH = "post_action_evidence_match"
    ACTION_TIMING_SIMILARITY = "action_timing_similarity"
    EXPECTED_ACTION_PAYLOAD_SIMILARITY = "expected_action_payload_similarity"
    POST_ACTION_EVIDENCE_SIMILARITY = "post_action_evidence_similarity"
    UNNECESSARY_ACTION_AVOIDANCE = "unnecessary_action_avoidance"
    REPAIR_SUCCESS = "repair_success"
    CORE_UE_REGISTRATION_REPAIRED = "core_ue_registration_repaired"
    CLI_CFO_TARGET_MATCH = "cli_cfo_target_match"
    CLI_TX_TIME_OFFSET_TARGET_MATCH = "cli_tx_time_offset_target_match"
    CLEAN_TEARDOWN = "clean_teardown"


class ComponentScore(StringEnum):
    TASK_CORRECTNESS = "task_correctness"
    ACTION_CORRECTNESS = "action_correctness"
    EVIDENCE_USE = "evidence_use"
    RAN_HEALTH = "ran_health"
    SAFETY = "safety"
    CLEANUP = "cleanup"


class EfficiencyMetric(StringEnum):
    TASK_COMPLETION_TIME_S = "task_completion_time_s"
    DECISION_LATENCY_S_MEAN = "decision_latency_s_mean"
    DECISION_LATENCY_S_P50 = "decision_latency_s_p50"
    DECISION_LATENCY_S_P95 = "decision_latency_s_p95"
    CONTROL_ROUND_TRIP_S_MEAN = "control_round_trip_s_mean"
    CONTROL_ROUND_TRIP_S_P50 = "control_round_trip_s_p50"
    CONTROL_ROUND_TRIP_S_P95 = "control_round_trip_s_p95"
    PROMPT_TOKENS_TOTAL = "prompt_tokens_total"
    COMPLETION_TOKENS_TOTAL = "completion_tokens_total"
    REASONING_TOKENS_TOTAL = "reasoning_tokens_total"
    TOTAL_TOKENS = "total_tokens"
    TOKENS_PER_DECISION_MEAN = "tokens_per_decision_mean"
    TOKENS_TO_TASK_SUCCESS = "tokens_to_task_success"


class ScoreOutcome(StringEnum):
    SUCCESS = "success"
    AGENT_FAILURE = "agent_failure"
    BENCHMARK_FAILURE = "benchmark_failure"
    RUNTIME_FAILURE = "runtime_failure"
    ORACLE_FAILURE = "oracle_failure"
    UNSCORED = "unscored"


class FailureCategory(StringEnum):
    WRONG_ACTION = "wrong_action"
    UNSAFE_ACTION = "unsafe_action"
    MISSING_ACTION = "missing_action"
    UNNECESSARY_ACTION = "unnecessary_action"
    SETUP_FAILURE = "setup_failure"
    STIMULUS_FAILURE = "stimulus_failure"
    RUNTIME_FAILURE = "runtime_failure"
    ORACLE_FAILURE = "oracle_failure"


class MetricAggregation(StringEnum):
    LAST = "last"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    SUM = "sum"
    COUNT = "count"
    RATIO = "ratio"


class MetricDirection(StringEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    BOOLEAN_SUCCESS = "boolean_success"
    CATEGORICAL = "categorical"


IMPLEMENTED_STIMULUS_DRIVERS = {
    StimulusDriverKind.UE_PING_TRAFFIC,
    StimulusDriverKind.UE_ACTIVITY_CHURN,
    StimulusDriverKind.CORE_UE_REGISTRATION_MISCONFIG,
    StimulusDriverKind.TRAFFIC_LOAD_PROFILE,
    StimulusDriverKind.MOBILITY_PATH,
    StimulusDriverKind.RADIO_CONDITION_PROFILE,
    StimulusDriverKind.SLICE_DEMAND_SHIFT,
    StimulusDriverKind.TELEMETRY_GAP,
    StimulusDriverKind.E2_KPM_AVAILABILITY_WINDOW,
    StimulusDriverKind.RIC_XAPP_LIFECYCLE,
    StimulusDriverKind.CORE_LATENCY_PROFILE,
    StimulusDriverKind.BACKHAUL_IMPAIRMENT,
    StimulusDriverKind.CELL_IDENTITY_CHANGE,
    StimulusDriverKind.FUTURE_ZMQ_IMPAIRMENT,
    StimulusDriverKind.METRICS_STALENESS_MASK,
    StimulusDriverKind.DOCKER_ZMQ_RUNTIME_LAUNCH,
}


def enum_values(enum_type: type[StringEnum]) -> tuple[str, ...]:
    return tuple(member.value for member in enum_type)


def parse_enum(enum_type: type[StringEnum], value: str) -> StringEnum:
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(enum_values(enum_type))
        raise ValueError(f"Unknown {enum_type.__name__}: {value!r}; expected one of {allowed}") from exc
