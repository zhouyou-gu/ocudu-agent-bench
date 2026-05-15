"""Conformance spec loading for the benchmark v1 skeleton."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConformanceSpec:
    id: str
    name: str
    backend: str
    stage: str
    required_for_scoring: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "backend": self.backend,
            "stage": self.stage,
            "required_for_scoring": self.required_for_scoring,
            "status": self.status,
        }


def load_conformance_specs(path: Path) -> list[ConformanceSpec]:
    data = json.loads(path.read_text(encoding="utf-8"))
    specs = data.get("tests", [])
    result: list[ConformanceSpec] = []
    for item in specs:
        result.append(
            ConformanceSpec(
                id=item["id"],
                name=item["name"],
                backend=item["backend"],
                stage=item.get("stage", "v1_stub"),
                required_for_scoring=bool(item.get("required_for_scoring", False)),
                status=item.get("status", "stub"),
            )
        )
    return result

