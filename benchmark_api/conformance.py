"""Non-mutating readiness checks for one task episode."""

from __future__ import annotations

from typing import Any

from benchmark.benchmark_api.api_catalog import validate_api_selection
from benchmark.benchmark_api.runtime_setup import RuntimeHandle
from benchmark.benchmark_api.stimulus import StimulusPlan
from benchmark.benchmark_api.task_definition import PrivateTask


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
