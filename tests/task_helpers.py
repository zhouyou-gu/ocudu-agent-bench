"""Test helpers for resolving checked-in task-set manifests."""

from __future__ import annotations

from benchmark.benchmark_api.task_catalog import load_task_for_suite
from benchmark.benchmark_api.task_definition import PrivateTask


def load_checked_in_task(task_id: str) -> PrivateTask:
    if task_id.startswith("regression_"):
        suite = "regression"
    elif task_id.startswith("compound_"):
        suite = "compound"
    else:
        suite = "base"
    return load_task_for_suite(task_id, suite=suite)
