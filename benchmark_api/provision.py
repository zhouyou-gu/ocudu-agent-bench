"""Provisioning plan helpers for runtime infrastructure."""

from __future__ import annotations

from typing import Any


PROVISION_STAGES = ("assets", "images", "ocudu", "runtime_deps", "ric")


def build_provision_plan(stage: str = "all") -> dict[str, Any]:
    if stage != "all" and stage not in PROVISION_STAGES:
        raise ValueError(f"Unknown provision stage: {stage}")
    stages = list(PROVISION_STAGES if stage == "all" else (stage,))
    return {
        "stage": stage,
        "steps": [{"stage": item, "owner": "runtime_setup.py"} for item in stages],
    }
