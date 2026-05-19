"""Agent-visible observation frame construction."""

from __future__ import annotations

import time
from typing import Any

from benchmark.benchmark_api.ran_api import read_evidence
from benchmark.benchmark_api.runtime_setup import RuntimeHandle
from benchmark.benchmark_api.task_definition import PrivateTask


PRIVATE_FIELD_NAMES = {
    "E",
    "U",
    "J",
    "setup_metadata",
    "stimulus_schedule",
    "future_stimulus",
    "oracle",
    "artifact_path",
    "runtime_handle",
    "seed",
}


def build_observation(
    task: PrivateTask,
    runtime: RuntimeHandle,
    step_id: int,
    previous_feedback: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = read_evidence(runtime, task.observation_sources)
    frame: dict[str, Any] = {
        "step_id": step_id,
        "observation_timestamp_s": time.time(),
        "task_id": task.task_id,
        "evidence": evidence,
        "previous_feedback": previous_feedback,
    }
    context = {
        key: value
        for key, value in {
            "task_id": task.task_id,
            "step_id": step_id,
            "backend": evidence.get("backend", {}),
        }.items()
        if key in task.allowed_observation_context
    }
    frame["context"] = context
    _assert_no_private_fields(frame)
    return frame


def _assert_no_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRIVATE_FIELD_NAMES:
                raise ValueError(f"Private field leaked into observation: {key}")
            _assert_no_private_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_private_fields(item)
