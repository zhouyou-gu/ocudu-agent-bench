"""Shared emulated-agent scenario helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.task_catalog import GENERATED_SUITES, load_tasks_for_suite
from benchmark.benchmark_api.task_definition import PrivateTask
from benchmark.tests.emulated_agents.profiles import EmulatedProfileAgent


PRIVATE_TRACE_TOKENS = (
    '"M"',
    "variant_axes",
    "axis_values",
    "expected_failure_modes",
    "root_cause",
    "underlying_task",
    "oracle_task",
    "stimulus_schedule",
    "runtime_handle",
    "future_stimulus",
    "Generated deterministic variant",
)


@dataclass(frozen=True)
class EmulatedRunSpec:
    task_id: str
    suite: str
    episode_seed: int = 200
    suite_seed: int = 1
    count: int | None = None

    @property
    def run_id_stem(self) -> str:
        return f"{self.suite}-{self.task_id}-seed{self.episode_seed}"


def checked_in_specs(seed_base: int = 100) -> list[EmulatedRunSpec]:
    tasks = load_tasks_for_suite(suite="all_checked_in")
    return [
        EmulatedRunSpec(task_id=task_id, suite=_suite_for_task_id(task_id), episode_seed=seed_base + index)
        for index, task_id in enumerate(sorted(tasks), start=1)
    ]


def generated_specs(*, suite: str = "generated", suite_seed: int = 11, count: int = 40, episode_seed: int = 11) -> list[EmulatedRunSpec]:
    tasks = load_tasks_for_suite(suite=suite, seed=suite_seed, count=count)
    return [
        EmulatedRunSpec(
            task_id=task_id,
            suite=suite,
            episode_seed=episode_seed,
            suite_seed=suite_seed,
            count=count,
        )
        for task_id in sorted(tasks)
    ]


def generated_legacy_spec(legacy_task_id: str, *, suite_seed: int = 11, count: int = 50, episode_seed: int = 11) -> EmulatedRunSpec:
    generated = load_tasks_for_suite(suite="generated", seed=suite_seed, count=count)
    for task in generated.values():
        if task.M.get("variant", {}).get("legacy_task_id") == legacy_task_id:
            return EmulatedRunSpec(
                task_id=task.task_id,
                suite="generated",
                episode_seed=episode_seed,
                suite_seed=suite_seed,
                count=count,
            )
    raise AssertionError(f"missing generated legacy task: {legacy_task_id}")


def spec_for_task(task_id: str, *, episode_seed: int = 200) -> EmulatedRunSpec:
    return EmulatedRunSpec(task_id=task_id, suite=_suite_for_task_id(task_id), episode_seed=episode_seed)


def run_profile(spec: EmulatedRunSpec, profile: str) -> dict[str, Any]:
    task_cache = _task_cache(spec)
    return run_episode(
        EpisodeConfig(
            task_id=spec.task_id,
            run_id=f"emulated-{profile}-{spec.run_id_stem}",
            seed=spec.episode_seed,
            suite=spec.suite,
            suite_count=spec.count,
            suite_seed=spec.suite_seed,
        ),
        EmulatedProfileAgent(profile, tasks=task_cache),
    )


def summary_correctness(summary: dict[str, Any]) -> float:
    components = summary["component_scores"]
    return (float(components["task_correctness"]) + float(components["action_correctness"])) / 2.0


def trace_private_token_matches(trace: dict[str, Any]) -> list[str]:
    interaction_blob = json.dumps(trace.get("interaction", []), sort_keys=True)
    return [token for token in PRIVATE_TRACE_TOKENS if token in interaction_blob]


def task_cache_for_specs(specs: list[EmulatedRunSpec]) -> dict[str, PrivateTask]:
    cache: dict[str, PrivateTask] = {}
    for spec in specs:
        cache.update(_task_cache(spec))
    return cache


def _task_cache(spec: EmulatedRunSpec) -> dict[str, PrivateTask]:
    if spec.suite in GENERATED_SUITES:
        return load_tasks_for_suite(suite=spec.suite, seed=spec.suite_seed, count=spec.count)
    if spec.suite == "all_checked_in":
        return load_tasks_for_suite(suite="all_checked_in")
    return load_tasks_for_suite(suite=spec.suite)


def _suite_for_task_id(task_id: str) -> str:
    if task_id.startswith("compound_"):
        return "compound"
    if task_id.startswith("regression_"):
        return "regression"
    if task_id.startswith("generated_"):
        return "generated"
    return "base"
