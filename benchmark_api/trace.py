"""Partitioned trace recording for one episode."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceRecorder:
    run_id: str
    interaction: list[dict[str, Any]] = field(default_factory=list)
    private_benchmark: list[dict[str, Any]] = field(default_factory=list)
    oracle: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    run_metadata: dict[str, Any] = field(default_factory=dict)
    artifacts_finalized: bool = False
    trace_finalized: bool = False

    def record_observation(self, observation: dict[str, Any]) -> None:
        self.interaction.append({"kind": "observation", "record": dict(observation)})

    def record_action(self, action: dict[str, Any]) -> None:
        self.interaction.append({"kind": "action", "record": dict(action)})

    def record_feedback(self, feedback: dict[str, Any]) -> None:
        self.interaction.append({"kind": "feedback", "record": dict(feedback)})

    def record_stimulus(self, stimulus_record: dict[str, Any]) -> None:
        self.private_benchmark.append({"kind": "stimulus", "record": dict(stimulus_record)})

    def record_readiness(self, readiness: dict[str, Any]) -> None:
        self.private_benchmark.append({"kind": "readiness", "record": dict(readiness)})

    def record_oracle(self, oracle_record: dict[str, Any]) -> None:
        self.oracle.append({"kind": "oracle", "record": dict(oracle_record)})

    def finalize_artifacts(self, output_dir: Path | None = None) -> list[dict[str, Any]]:
        private_path = f"memory://{self.run_id}.trace"
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{self.run_id}.trace.json"
            private_path = str(path)
        manifest = {
            "run_id": self.run_id,
            "artifact_id": f"{self.run_id}-trace-package",
            "artifact_type": "trace_package",
            "producer_module": "trace.py",
            "private_path": private_path,
            "checksum": "",
            "collection_time_s": time.time(),
            "visibility_class": "benchmark_private",
            "retention_rule": "operator_policy",
        }
        self.artifacts = [manifest]
        self.artifacts_finalized = True
        return list(self.artifacts)

    def finalize_trace(self) -> dict[str, Any]:
        if not self.artifacts_finalized:
            raise RuntimeError("artifact manifest must be finalized before trace finalization")
        self.trace_finalized = True
        package = self.package()
        checksum = _stable_checksum(_package_without_artifact_checksums(package))
        self.artifacts = [{**artifact, "checksum": checksum} for artifact in self.artifacts]
        package = self.package()
        _write_package_to_artifact_path(package)
        return package

    def package(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "interaction": list(self.interaction),
            "private_benchmark": list(self.private_benchmark),
            "oracle": list(self.oracle),
            "artifact_manifest": list(self.artifacts),
            "run_metadata": dict(self.run_metadata),
            "artifacts_finalized": self.artifacts_finalized,
            "trace_finalized": self.trace_finalized,
        }


def _stable_checksum(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _package_without_artifact_checksums(package: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(package)
    normalized["artifact_manifest"] = [
        {**artifact, "checksum": ""}
        for artifact in package.get("artifact_manifest", [])
    ]
    return normalized


def _write_package_to_artifact_path(package: dict[str, Any]) -> None:
    for artifact in package.get("artifact_manifest", []):
        private_path = str(artifact.get("private_path", ""))
        if not private_path or private_path.startswith("memory://"):
            continue
        path = Path(private_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(package, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
