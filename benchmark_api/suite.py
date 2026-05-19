"""Aggregation-only suite module."""

from __future__ import annotations

from typing import Any


def aggregate_summaries(summaries: list[dict[str, Any]], run_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    run_manifest = run_manifest or {}
    scored = [summary for summary in summaries if summary.get("scored")]
    unscored = [summary for summary in summaries if not summary.get("scored")]
    component_totals: dict[str, list[float]] = {}
    for summary in scored:
        for name, value in summary.get("component_scores", {}).items():
            if isinstance(value, (int, float)):
                component_totals.setdefault(name, []).append(float(value))
    aggregate_components = {
        name: sum(values) / len(values)
        for name, values in sorted(component_totals.items())
        if values
    }
    outcomes: dict[str, int] = {}
    for summary in summaries:
        outcome = str(summary.get("outcome", "unknown"))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {
        "task_id": run_manifest.get("task_id"),
        "agent_id": run_manifest.get("agent_id") or run_manifest.get("controller_id"),
        "run_count": len(summaries),
        "scored_count": len(scored),
        "unscored_count": len(unscored),
        "outcomes": outcomes,
        "aggregate_component_scores": aggregate_components,
        "seed_identifiers": list(run_manifest.get("seed_identifiers", [])),
    }
