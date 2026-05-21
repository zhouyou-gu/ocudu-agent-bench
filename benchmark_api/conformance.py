"""Non-mutating readiness checks for one task episode."""

from __future__ import annotations

from typing import Any

from benchmark.benchmark_api.api_catalog import validate_api_selection
from benchmark.benchmark_api.runtime_setup import RuntimeHandle
from benchmark.benchmark_api.stimulus import StimulusPlan
from benchmark.benchmark_api.task_definition import PrivateTask
from benchmark.benchmark_api.types import RanActionType


_POST_ACTION_EVIDENCE_SOURCE = {
    "ping": "ping",
    "metrics": "json_metrics",
    "e2_kpm_v05": "e2_kpm_v05",
    "e2_control_outcome": "e2_control_outcome",
    "ue_identity": "ue_identity",
    "ue_runtime": "ue_runtime",
    "core_runtime": "core_runtime",
    "radio_runtime": "radio_runtime",
    "slice_runtime": "slice_runtime",
    "backhaul_runtime": "backhaul_runtime",
    "cell_identity": "cell_identity",
    "websocket_control_outcomes": "websocket_control_outcomes",
}

_ACTION_DERIVATION_SOURCES = {
    RanActionType.SET_PRB_POLICY_RATIO_WS.value: {"slice_runtime"},
    RanActionType.SET_PRB_POLICY_RATIO_CCC.value: {"slice_runtime", "e2_kpm_v05"},
    RanActionType.SET_PRB_POLICY_RATIO_RC_DU.value: {"slice_runtime", "ue_identity", "e2_kpm_v05"},
    RanActionType.SET_SSB_BLOCK_POWER_WS.value: {"cell_identity", "radio_runtime"},
    RanActionType.TRIGGER_HANDOVER_CLI.value: {"ue_identity"},
    RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI.value: {"ue_identity"},
    RanActionType.SET_CFO_CLI.value: {"radio_runtime"},
    RanActionType.SET_TX_TIME_OFFSET_CLI.value: {"radio_runtime"},
    RanActionType.RESTART_CORE_NF.value: {"core_runtime"},
    RanActionType.UPDATE_CORE_UE_REGISTRATION.value: {"core_runtime"},
}


def run_readiness_checks(task: PrivateTask, runtime: RuntimeHandle, stimulus_plan: StimulusPlan) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("runtime_ready", runtime.ready, runtime.setup_metadata.get("blocking_reason") or "runtime handle is ready"))
    checks.append(_check("cleanup_plan", bool(runtime.cleanup_plan), "cleanup plan exists"))
    checks.append(_check("stimulus_seeded", stimulus_plan.seed is not None, "stimulus plan is seed-controlled"))
    checks.append(_check("stimulus_events", bool(stimulus_plan.events), "stimulus plan has events"))
    try:
        descriptors = validate_api_selection(list(task.selected_api_kinds))
        api_ok = all(set(descriptor.runtime_requirements).issubset(set(runtime.components)) for descriptor in descriptors)
    except ValueError:
        api_ok = False
    checks.append(_check("api_projection", api_ok, "task-selected APIs are implemented and runtime requirements are present"))
    checks.append(
        _check(
            "expected_payload_derivable",
            _expected_payload_fields_derivable(task),
            "expected action payload fields are backed by public evidence sources",
        )
    )
    checks.append(
        _check(
            "post_action_evidence_observable",
            _expected_post_action_evidence_observable(task),
            "expected post-action evidence paths are backed by public observation sources",
        )
    )
    oracle_requirements = task.J.get("oracle_requirements", [])
    checks.append(_check("oracle_requirements_declared", isinstance(oracle_requirements, list), "oracle requirements are declarative"))
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "status": "pass" if passed else "fail",
        "task_id": task.task_id,
        "checks": checks,
        "mutated_scored_runtime": False,
    }


def _check(check_id: str, passed: bool, summary: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "summary": summary}


def _expected_payload_fields_derivable(task: PrivateTask) -> bool:
    sources = set(task.observation_sources)
    for expectation in task.J.get("expected_action_fields", []):
        action_type = str(expectation.get("action_type", ""))
        required_sources = _ACTION_DERIVATION_SOURCES.get(action_type, set())
        if not required_sources.issubset(sources):
            return False
    return True


def _expected_post_action_evidence_observable(task: PrivateTask) -> bool:
    sources = set(task.observation_sources)
    for expectation in task.J.get("expected_post_action_evidence", []):
        fields = expectation.get("fields", {})
        if not isinstance(fields, dict) or not fields:
            return False
        for field in fields:
            source = _POST_ACTION_EVIDENCE_SOURCE.get(str(field).split(".", 1)[0])
            if source is None or source not in sources:
                return False
    return True
