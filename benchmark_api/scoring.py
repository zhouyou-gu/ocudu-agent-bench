"""Post-episode oracle scoring."""

from __future__ import annotations

import statistics
from typing import Any

from benchmark.benchmark_api.task_definition import PrivateTask
from benchmark.benchmark_api.types import ComponentScore, FailureCategory, RanActionType, RawScoreMetric, ScoreOutcome


def score_episode(task: PrivateTask, trace_package: dict[str, Any]) -> dict[str, Any]:
    if not trace_package.get("artifacts_finalized") or not trace_package.get("trace_finalized"):
        raise RuntimeError("scoring requires finalized artifacts and trace")
    observations = _records(trace_package, "observation")
    actions = _records(trace_package, "action")
    feedback = _records(trace_package, "feedback")
    raw_metrics = _raw_metrics(task, observations, actions, feedback)
    components = _component_scores(task, raw_metrics)
    outcome, failure_category = _outcome(task, raw_metrics, components)
    efficiency = _efficiency(trace_package, actions)
    return {
        "run_id": trace_package["run_id"],
        "task_id": task.task_id,
        "outcome": outcome.value,
        "failure_category": failure_category.value if failure_category else None,
        "raw_metrics": raw_metrics,
        "component_scores": components,
        "efficiency": efficiency,
        "scored": outcome in {ScoreOutcome.SUCCESS, ScoreOutcome.AGENT_FAILURE},
        "artifact_manifest": trace_package.get("artifact_manifest", []),
    }


def _raw_metrics(
    task: PrivateTask,
    observations: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
) -> dict[str, float]:
    requested = set(task.J.get("raw_metrics", []))
    metrics: dict[str, float] = {}
    action_records = [record for record in actions if record.get("action", {}).get("type") != RanActionType.NO_ACTION.value]
    valid_actions = [record for record in action_records if record.get("valid")]
    accepted = [record for record in action_records if _dispatch(record).get("accepted")]
    no_actions = [record for record in actions if record.get("action", {}).get("type") == RanActionType.NO_ACTION.value]
    observations_by_step = {record.get("step_id"): record for record in observations}
    actions_on_stale_evidence = [
        record
        for record in action_records
        if observations_by_step.get(record.get("step_id"), {}).get("evidence", {}).get("metrics", {}).get("stale")
    ]
    accepted_after_required_evidence = [
        record
        for record in accepted
        if _required_evidence_available(task, observations_by_step.get(record.get("step_id"), {}))
    ]
    last_observation = observations[-1] if observations else {}
    evidence = last_observation.get("evidence", {})
    ping = evidence.get("ping", {})
    metrics_evidence = evidence.get("metrics", {})
    e2 = evidence.get("e2_kpm_v05", {})
    expected_action = task.J.get("expected_action_type")
    max_actions = task.J.get("max_actions")
    require_no_action = bool(task.J.get("require_no_action", False))

    all_values = {
        RawScoreMetric.VALID_ACTION_ACCEPTED_RATE.value: (
            len(accepted) / len(valid_actions) if valid_actions else (1.0 if not action_records else 0.0)
        ),
        RawScoreMetric.INVALID_LOCAL_REJECTION_CORRECTNESS.value: (
            1.0 if any(not record.get("valid") for record in actions) else 0.0
        ),
        RawScoreMetric.PING_SUCCESS_RATIO.value: float(ping.get("success_ratio", 0.0) or 0.0),
        RawScoreMetric.METRICS_CONTINUITY.value: 1.0 if metrics_evidence.get("present") and not metrics_evidence.get("stale") else 0.0,
        RawScoreMetric.E2_KPM_CONTINUITY.value: 1.0 if e2.get("enabled") and int(e2.get("kpm_indications", 0) or 0) > 0 else 0.0,
        RawScoreMetric.E2_ORACLE_AVAILABLE.value: 1.0 if e2.get("enabled") else 0.0,
        RawScoreMetric.E2_CONTROL_ORACLE_AVAILABLE.value: (
            1.0 if any(_dispatch(record).get("backend") == "e2_control" for record in action_records) else 0.0
        ),
        RawScoreMetric.EXPECTED_ACTION_TYPE_CORRECT.value: (
            1.0
            if expected_action is None
            or any(record.get("action", {}).get("type") == expected_action and _dispatch(record).get("accepted") for record in action_records)
            else 0.0
        ),
        RawScoreMetric.ACTION_BUDGET_OK.value: 1.0 if max_actions is None or len(action_records) <= int(max_actions) else 0.0,
        RawScoreMetric.NOOP_CORRECTNESS.value: 1.0 if (require_no_action and no_actions and not action_records) or not require_no_action else 0.0,
        RawScoreMetric.EVIDENCE_GATED_ACTION.value: (
            1.0
            if not task.J.get("require_evidence_before_action")
            else (1.0 if bool(accepted) and len(accepted_after_required_evidence) == len(accepted) else 0.0)
        ),
        RawScoreMetric.STALE_ACTION_AVOIDANCE.value: 1.0 if not actions_on_stale_evidence else 0.0,
        RawScoreMetric.TRIAGE_SUCCESS.value: 1.0 if bool(accepted) or (require_no_action and no_actions) else 0.0,
        RawScoreMetric.CORRECT_API_SELECTION.value: (
            1.0
            if expected_action is None
            or any(record.get("action", {}).get("type") == expected_action for record in action_records)
            else 0.0
        ),
        RawScoreMetric.TEMPORAL_ACTION_SEQUENCE_MATCH.value: _temporal_action_sequence_match(task, actions),
        RawScoreMetric.EXPECTED_ACTION_PAYLOAD_MATCH.value: _expected_action_payload_match(task, accepted),
        RawScoreMetric.UNNECESSARY_ACTION_AVOIDANCE.value: 1.0 if not require_no_action or not action_records else 0.0,
        RawScoreMetric.REPAIR_SUCCESS.value: (
            1.0 if any(not record.get("valid") for record in actions) and bool(accepted) else 0.0
        ),
        RawScoreMetric.CORE_UE_REGISTRATION_REPAIRED.value: _core_ue_registration_repaired(accepted, observations_by_step),
        RawScoreMetric.CLI_CFO_TARGET_MATCH.value: _accepted_numeric_action_matches(
            accepted,
            RanActionType.SET_CFO_CLI.value,
            "cfo_hz",
            task.J.get("expected_cfo_hz"),
            task.J.get("expected_sector_id"),
            float(task.J.get("numeric_tolerance", 1e-6)),
        ),
        RawScoreMetric.CLI_TX_TIME_OFFSET_TARGET_MATCH.value: _accepted_numeric_action_matches(
            accepted,
            RanActionType.SET_TX_TIME_OFFSET_CLI.value,
            "tx_time_offset_us",
            task.J.get("expected_tx_time_offset_us"),
            task.J.get("expected_sector_id"),
            float(task.J.get("numeric_tolerance", 1e-6)),
        ),
        RawScoreMetric.CLEAN_TEARDOWN.value: 1.0,
    }
    for metric in requested:
        metrics[metric] = float(all_values.get(metric, 0.0))
    return metrics


def _component_scores(task: PrivateTask, raw_metrics: dict[str, float]) -> dict[str, float]:
    def avg(names: tuple[str, ...], default: float = 1.0) -> float:
        values = [raw_metrics[name] for name in names if name in raw_metrics]
        return sum(values) / len(values) if values else default

    return {
        ComponentScore.TASK_CORRECTNESS.value: avg(
            (
                RawScoreMetric.EXPECTED_ACTION_TYPE_CORRECT.value,
                RawScoreMetric.NOOP_CORRECTNESS.value,
                RawScoreMetric.TRIAGE_SUCCESS.value,
                RawScoreMetric.TEMPORAL_ACTION_SEQUENCE_MATCH.value,
                RawScoreMetric.EXPECTED_ACTION_PAYLOAD_MATCH.value,
            ),
            default=0.0,
        ),
        ComponentScore.ACTION_CORRECTNESS.value: avg(
            (
                RawScoreMetric.VALID_ACTION_ACCEPTED_RATE.value,
                RawScoreMetric.CORRECT_API_SELECTION.value,
                RawScoreMetric.REPAIR_SUCCESS.value,
                RawScoreMetric.CORE_UE_REGISTRATION_REPAIRED.value,
                RawScoreMetric.CLI_CFO_TARGET_MATCH.value,
                RawScoreMetric.CLI_TX_TIME_OFFSET_TARGET_MATCH.value,
                RawScoreMetric.EXPECTED_ACTION_PAYLOAD_MATCH.value,
            ),
            default=1.0,
        ),
        ComponentScore.EVIDENCE_USE.value: avg(
            (
                RawScoreMetric.EVIDENCE_GATED_ACTION.value,
                RawScoreMetric.STALE_ACTION_AVOIDANCE.value,
                RawScoreMetric.TEMPORAL_ACTION_SEQUENCE_MATCH.value,
            )
        ),
        ComponentScore.RAN_HEALTH.value: avg(
            (
                RawScoreMetric.PING_SUCCESS_RATIO.value,
                RawScoreMetric.METRICS_CONTINUITY.value,
                RawScoreMetric.E2_KPM_CONTINUITY.value,
            )
        ),
        ComponentScore.SAFETY.value: avg(
            (
                RawScoreMetric.ACTION_BUDGET_OK.value,
                RawScoreMetric.UNNECESSARY_ACTION_AVOIDANCE.value,
                RawScoreMetric.INVALID_LOCAL_REJECTION_CORRECTNESS.value,
            )
        ),
        ComponentScore.CLEANUP.value: raw_metrics.get(RawScoreMetric.CLEAN_TEARDOWN.value, 1.0),
    }


def _outcome(
    task: PrivateTask,
    raw_metrics: dict[str, float],
    components: dict[str, float],
) -> tuple[ScoreOutcome, FailureCategory | None]:
    success_threshold = float(task.J.get("success_threshold", 1.0))
    critical = tuple(task.J.get("critical_metrics", raw_metrics.keys()))
    critical_values = [raw_metrics.get(metric, 0.0) for metric in critical]
    if critical_values and all(value >= success_threshold for value in critical_values):
        return ScoreOutcome.SUCCESS, None
    if raw_metrics.get(RawScoreMetric.UNNECESSARY_ACTION_AVOIDANCE.value, 1.0) < success_threshold:
        return ScoreOutcome.AGENT_FAILURE, FailureCategory.UNNECESSARY_ACTION
    if raw_metrics.get(RawScoreMetric.VALID_ACTION_ACCEPTED_RATE.value, 1.0) < success_threshold:
        return ScoreOutcome.AGENT_FAILURE, FailureCategory.WRONG_ACTION
    if raw_metrics.get(RawScoreMetric.EXPECTED_ACTION_TYPE_CORRECT.value, 1.0) < success_threshold:
        return ScoreOutcome.AGENT_FAILURE, FailureCategory.WRONG_ACTION
    if raw_metrics.get(RawScoreMetric.TEMPORAL_ACTION_SEQUENCE_MATCH.value, 1.0) < success_threshold:
        return ScoreOutcome.AGENT_FAILURE, FailureCategory.WRONG_ACTION
    if raw_metrics.get(RawScoreMetric.EXPECTED_ACTION_PAYLOAD_MATCH.value, 1.0) < success_threshold:
        return ScoreOutcome.AGENT_FAILURE, FailureCategory.WRONG_ACTION
    return ScoreOutcome.AGENT_FAILURE, FailureCategory.MISSING_ACTION


def _required_evidence_available(task: PrivateTask, observation: dict[str, Any]) -> bool:
    evidence = observation.get("evidence", {})
    sources = set(task.observation_sources)
    if "json_metrics" in sources:
        metrics = evidence.get("metrics", {})
        if not metrics.get("present") or metrics.get("stale"):
            return False
    if "e2_kpm_v05" in sources:
        e2 = evidence.get("e2_kpm_v05", {})
        if not e2.get("enabled") or int(e2.get("kpm_indications", 0) or 0) <= 0:
            return False
    if task.J.get("expected_action_type") == RanActionType.SET_PRB_POLICY_RATIO_RC_DU.value:
        identity = evidence.get("ue_identity", {})
        if identity.get("du_ue_id") is None:
            return False
    if task.J.get("expected_action_type") in {
        RanActionType.TRIGGER_HANDOVER_CLI.value,
        RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value,
    }:
        identity = evidence.get("ue_identity", {})
        if identity.get("rnti") is None or identity.get("serving_pci") is None or not identity.get("target_pcis"):
            return False
    if task.J.get("expected_action_type") == RanActionType.RESTART_CORE_NF.value:
        core_runtime = evidence.get("core_runtime", {})
        if not core_runtime.get("running"):
            return False
    if task.J.get("expected_action_type") == RanActionType.UPDATE_CORE_UE_REGISTRATION.value:
        core_runtime = evidence.get("core_runtime", {})
        registration = core_runtime.get("ue_registration", {})
        if not core_runtime.get("running"):
            return False
        if registration.get("status") != "mismatch" or not registration.get("mismatch_fields"):
            return False
        if not registration.get("desired") or not registration.get("current"):
            return False
    if task.J.get("expected_action_type") == RanActionType.SET_CFO_CLI.value:
        radio_runtime = evidence.get("radio_runtime", {})
        if radio_runtime.get("sector_id") is None or radio_runtime.get("target_cfo_hz") is None:
            return False
    if task.J.get("expected_action_type") == RanActionType.SET_TX_TIME_OFFSET_CLI.value:
        radio_runtime = evidence.get("radio_runtime", {})
        if radio_runtime.get("sector_id") is None or radio_runtime.get("target_tx_time_offset_us") is None:
            return False
    return True


def _temporal_action_sequence_match(task: PrivateTask, actions: list[dict[str, Any]]) -> float:
    expectations = task.J.get("temporal_expectations", [])
    if not expectations:
        return 1.0
    by_step = {record.get("step_id"): record for record in actions}
    for expectation in expectations:
        step_id = expectation.get("step_id")
        expected_type = str(expectation.get("action_type", "")).strip()
        if not isinstance(step_id, int) or not expected_type:
            return 0.0
        record = by_step.get(step_id)
        if record is None:
            return 0.0
        actual_type = record.get("action", {}).get("type")
        if expected_type == RanActionType.NO_ACTION.value:
            if actual_type != RanActionType.NO_ACTION.value:
                return 0.0
            continue
        if actual_type != expected_type:
            return 0.0
        if "valid" in expectation:
            if bool(record.get("valid")) != bool(expectation["valid"]):
                return 0.0
        elif not record.get("valid"):
            return 0.0
        if "accepted" in expectation:
            if bool(_dispatch(record).get("accepted")) != bool(expectation["accepted"]):
                return 0.0
        elif not _dispatch(record).get("accepted"):
            return 0.0
    return 1.0


def _expected_action_payload_match(task: PrivateTask, accepted_actions: list[dict[str, Any]]) -> float:
    expectations = task.J.get("expected_action_fields", [])
    if not expectations:
        return 1.0
    for expectation in expectations:
        step_id = expectation.get("step_id")
        expected_type = str(expectation.get("action_type", "")).strip()
        fields = expectation.get("fields", {})
        tolerance = float(expectation.get("numeric_tolerance", task.J.get("numeric_tolerance", 1e-6)))
        if not isinstance(step_id, int) or not expected_type or not isinstance(fields, dict):
            return 0.0
        matching_records = [
            record
            for record in accepted_actions
            if record.get("step_id") == step_id and record.get("action", {}).get("type") == expected_type
        ]
        if not matching_records:
            return 0.0
        if not any(_fields_match(record.get("action", {}), fields, tolerance) for record in matching_records):
            return 0.0
    return 1.0


def _fields_match(action: dict[str, Any], fields: dict[str, Any], tolerance: float) -> bool:
    for field, expected in fields.items():
        actual = _path_get(action, str(field))
        actual_num = _num(actual)
        expected_num = _num(expected)
        if actual_num is not None and expected_num is not None:
            if abs(actual_num - expected_num) > tolerance:
                return False
            continue
        if actual != expected:
            return False
    return True


def _path_get(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _accepted_numeric_action_matches(
    accepted_actions: list[dict[str, Any]],
    action_type: str,
    field: str,
    expected_value: Any,
    expected_sector_id: Any,
    tolerance: float,
) -> float:
    for record in accepted_actions:
        action = record.get("action", {})
        if action.get("type") != action_type:
            continue
        if expected_sector_id is not None and action.get("sector_id") != int(expected_sector_id):
            continue
        if expected_value is None:
            return 1.0
        actual = _num(action.get(field))
        expected = _num(expected_value)
        if actual is None or expected is None:
            continue
        if abs(actual - expected) <= tolerance:
            return 1.0
    return 0.0


def _core_ue_registration_repaired(
    accepted_actions: list[dict[str, Any]],
    observations_by_step: dict[Any, dict[str, Any]],
) -> float:
    fields = ("ue_id", "supi", "plmn", "dnn", "sst", "sd", "auth_profile_id")
    for record in accepted_actions:
        action = record.get("action", {})
        if action.get("type") != RanActionType.UPDATE_CORE_UE_REGISTRATION.value:
            continue
        observation = observations_by_step.get(record.get("step_id"), {})
        registration = observation.get("evidence", {}).get("core_runtime", {}).get("ue_registration", {})
        desired = registration.get("desired", {})
        if registration.get("status") != "mismatch" or not registration.get("mismatch_fields"):
            continue
        if all(action.get(field) == desired.get(field) for field in fields):
            return 1.0
    return 0.0


def _efficiency(trace_package: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = trace_package.get("run_metadata", {})
    started = metadata.get("started_at_s")
    closed = metadata.get("closed_at_s")
    latencies = [_num(record.get("telemetry", {}).get("decision_latency_s")) for record in actions]
    latencies = [value for value in latencies if value is not None]
    dispatch_times = [
        _num(_dispatch(record).get("completed_at_s")) - _num(record.get("received_at_s"))
        for record in actions
        if _num(_dispatch(record).get("completed_at_s")) is not None and _num(record.get("received_at_s")) is not None
    ]
    token_totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0}
    for record in actions:
        telemetry = record.get("telemetry") or {}
        prompt = int(telemetry.get("prompt_tokens", 0) or 0)
        completion = int(telemetry.get("completion_tokens", 0) or 0)
        reasoning = int(telemetry.get("reasoning_tokens", 0) or 0)
        total = int(telemetry.get("total_tokens", prompt + completion + reasoning) or 0)
        token_totals["prompt_tokens"] += prompt
        token_totals["completion_tokens"] += completion
        token_totals["reasoning_tokens"] += reasoning
        token_totals["total_tokens"] += total
    return {
        "task_completion_time_s": max(0.0, float(closed - started)) if started is not None and closed is not None else None,
        "decision_latency_s_mean": _mean(latencies),
        "decision_latency_s_p50": _quantile(latencies, 0.5),
        "decision_latency_s_p95": _quantile(latencies, 0.95),
        "control_round_trip_s_mean": _mean(dispatch_times),
        "control_round_trip_s_p50": _quantile(dispatch_times, 0.5),
        "control_round_trip_s_p95": _quantile(dispatch_times, 0.95),
        "prompt_tokens_total": token_totals["prompt_tokens"],
        "completion_tokens_total": token_totals["completion_tokens"],
        "reasoning_tokens_total": token_totals["reasoning_tokens"],
        "total_tokens": token_totals["total_tokens"],
        "tokens_per_decision_mean": token_totals["total_tokens"] / len(actions) if actions else 0.0,
        "tokens_to_task_success": token_totals["total_tokens"],
    }


def _records(trace_package: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [entry["record"] for entry in trace_package.get("interaction", []) if entry.get("kind") == kind]


def _dispatch(record: dict[str, Any]) -> dict[str, Any]:
    dispatch = record.get("dispatch")
    return dispatch if isinstance(dispatch, dict) else {}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _quantile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = int(round((len(values) - 1) * quantile))
    return values[index]
