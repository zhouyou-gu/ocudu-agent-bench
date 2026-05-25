"""Suite-aware task-set catalog loading."""

from __future__ import annotations

from pathlib import Path

from benchmark_api.task_definition import PrivateTask, load_task_file
from benchmark_api.variant_generator import generate_variant_tasks


TASK_SETS_DIR = Path(__file__).resolve().parents[1] / "task_sets"
CHECKED_IN_SUITES = {"base", "regression", "compound"}
GENERATED_SUITES = {"generated", "standard", "diagnostic", "stress"}
ALL_CHECKED_IN_SUITE = "all_checked_in"


def load_tasks_for_suite(
    *,
    suite: str = "base",
    seed: int = 1,
    count: int | None = None,
    family: str | None = None,
    task_sets_dir: Path | str | None = None,
) -> dict[str, PrivateTask]:
    normalized = _normalize_suite(suite)
    root = Path(task_sets_dir) if task_sets_dir is not None else TASK_SETS_DIR
    if normalized in CHECKED_IN_SUITES:
        return _filter_family(_load_checked_in(root, normalized), family)
    if normalized == ALL_CHECKED_IN_SUITE:
        tasks: dict[str, PrivateTask] = {}
        for checked_in_suite in ("base", "regression", "compound"):
            tasks.update(_load_checked_in(root, checked_in_suite))
        return _filter_family(tasks, family)
    if normalized in GENERATED_SUITES:
        anchors = _load_checked_in(root, "base")
        generated = generate_variant_tasks(
            anchors=anchors,
            seed=seed,
            count=count,
            suite=normalized,
            task_sets_dir=root,
            family=family,
        )
        return generated
    raise ValueError(f"unknown task suite: {suite}")


def load_task_for_suite(
    task_id: str,
    *,
    suite: str = "base",
    seed: int = 1,
    count: int | None = None,
    family: str | None = None,
    task_sets_dir: Path | str | None = None,
) -> PrivateTask:
    tasks = load_tasks_for_suite(suite=suite, seed=seed, count=count, family=family, task_sets_dir=task_sets_dir)
    try:
        return tasks[task_id]
    except KeyError as exc:
        raise FileNotFoundError(f"Task {task_id!r} not found in suite {suite!r}") from exc


def _load_checked_in(root: Path, suite: str) -> dict[str, PrivateTask]:
    tasks: dict[str, PrivateTask] = {}
    suite_dir = root / suite
    for path in sorted(suite_dir.glob("*/*/task.json")):
        task = load_task_file(path)
        if task.task_id in tasks:
            raise ValueError(f"Duplicate task id: {task.task_id}")
        if task.M.get("task_set") != suite:
            raise ValueError(f"Task {task.task_id} is under {suite!r} but declares M.task_set={task.M.get('task_set')!r}")
        if task.M.get("family") != path.parents[1].name:
            raise ValueError(f"Task {task.task_id} family does not match path")
        tasks[task.task_id] = task
    return tasks


def _filter_family(tasks: dict[str, PrivateTask], family: str | None) -> dict[str, PrivateTask]:
    if family is None:
        return tasks
    normalized = family.strip().lower()
    return {task_id: task for task_id, task in tasks.items() if task.M.get("family") == normalized}


def _normalize_suite(suite: str | None) -> str:
    return (suite or "base").strip().lower()
