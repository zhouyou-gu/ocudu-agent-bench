"""Deterministic benchmark stimulus scheduling."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.runtime_setup import RuntimeHandle
from benchmark.benchmark_api.types import (
    ClockMode,
    IMPLEMENTED_STIMULUS_DRIVERS,
    StimulusDriverKind,
    StimulusEventStatus,
    StimulusPhase,
)


@dataclass(frozen=True)
class TimingPolicy:
    clock_mode: ClockMode = ClockMode.FIXED_TICK
    step_interval_s: float = 1.0
    decision_deadline_s: float = 1.0
    late_action_policy: str = "timeout_to_no_action"
    action_apply_policy: str = "apply_until_step_boundary"

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "TimingPolicy":
        data = data or {}
        policy = cls(
            clock_mode=ClockMode(data.get("clock_mode", ClockMode.FIXED_TICK.value)),
            step_interval_s=float(data.get("step_interval_s", 1.0)),
            decision_deadline_s=float(data.get("decision_deadline_s", 1.0)),
            late_action_policy=str(data.get("late_action_policy", "timeout_to_no_action")),
            action_apply_policy=str(data.get("action_apply_policy", "apply_until_step_boundary")),
        )
        if policy.step_interval_s <= 0:
            raise ValueError("step_interval_s must be positive")
        if policy.decision_deadline_s <= 0:
            raise ValueError("decision_deadline_s must be positive")
        if policy.decision_deadline_s > policy.step_interval_s:
            raise ValueError("decision_deadline_s must be <= step_interval_s")
        return policy


@dataclass
class StimulusEvent:
    event_id: str
    step_id: int
    phase: StimulusPhase
    kind: StimulusDriverKind
    scheduled_time_s: float
    parameters: dict[str, Any]
    status: StimulusEventStatus = StimulusEventStatus.SCHEDULED
    applied_time_s: float | None = None
    active_start_time_s: float | None = None
    active_end_time_s: float | None = None
    evidence: dict[str, Any] | None = None

    def to_private_record(self, run_id: str, seed: int) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "event_id": self.event_id,
            "step_id": self.step_id,
            "phase_label": self.phase.value,
            "kind": self.kind.value,
            "scheduled_time_s": self.scheduled_time_s,
            "applied_time_s": self.applied_time_s,
            "active_start_time_s": self.active_start_time_s,
            "active_end_time_s": self.active_end_time_s,
            "status": self.status.value,
            "seed": seed,
            "driver_parameters": dict(self.parameters),
            "stimulus_evidence": dict(self.evidence or {}),
        }


@dataclass
class StimulusPlan:
    seed: int
    timing_policy: TimingPolicy
    events: list[StimulusEvent]

    def events_for(self, step_id: int, phase: StimulusPhase) -> list[StimulusEvent]:
        return [event for event in self.events if event.step_id == step_id and event.phase == phase]


def expand_stimulus_plan(stimulus: dict[str, Any], seed: int) -> StimulusPlan:
    timing_policy = TimingPolicy.from_mapping(stimulus.get("timing_policy"))
    steps = int(stimulus.get("steps", 1))
    rng = random.Random(seed)
    templates = list(stimulus.get("events", []))
    events: list[StimulusEvent] = []
    for step_id in range(1, steps + 1):
        for template in templates:
            kind = StimulusDriverKind(template["kind"])
            if kind not in IMPLEMENTED_STIMULUS_DRIVERS:
                raise ValueError(f"Stimulus driver is not implemented: {kind.value}")
            phase = StimulusPhase(template["phase"])
            when = float((step_id - 1) * timing_policy.step_interval_s)
            if phase == StimulusPhase.IN_STEP:
                when += min(0.001, timing_policy.step_interval_s / 1000.0)
            event_id = f"s{step_id}-{phase.value}-{kind.value}-{rng.randrange(1_000_000):06d}"
            events.append(
                StimulusEvent(
                    event_id=event_id,
                    step_id=step_id,
                    phase=phase,
                    kind=kind,
                    scheduled_time_s=round(when, 6),
                    parameters=dict(template.get("parameters", {})),
                )
            )
    return StimulusPlan(seed=seed, timing_policy=timing_policy, events=events)


def apply_pre_observation(plan: StimulusPlan, runtime: RuntimeHandle, step_id: int) -> list[StimulusEvent]:
    applied = []
    for event in plan.events_for(step_id, StimulusPhase.PRE_OBSERVATION):
        _apply_event_to_runtime(event, runtime)
        event.status = StimulusEventStatus.APPLIED
        event.applied_time_s = event.scheduled_time_s
        applied.append(event)
    return applied


def apply_in_step(
    plan: StimulusPlan,
    runtime: RuntimeHandle,
    step_id: int,
    observation_emitted_at_s: float,
    action_completed_at_s: float,
) -> list[StimulusEvent]:
    events = start_in_step(plan, runtime, step_id, observation_emitted_at_s)
    return finish_in_step(plan, events, action_completed_at_s)


def start_in_step(
    plan: StimulusPlan,
    runtime: RuntimeHandle,
    step_id: int,
    observation_emitted_at_s: float,
) -> list[StimulusEvent]:
    started = []
    for event in plan.events_for(step_id, StimulusPhase.IN_STEP):
        event.active_start_time_s = observation_emitted_at_s
        _apply_event_to_runtime(event, runtime)
        started.append(event)
    return started


def finish_in_step(
    plan: StimulusPlan,
    events: list[StimulusEvent],
    action_completed_at_s: float,
) -> list[StimulusEvent]:
    applied = []
    for event in events:
        if event.active_start_time_s is None:
            raise RuntimeError(f"in-step stimulus was not started: {event.event_id}")
        boundary = event.active_start_time_s + plan.timing_policy.step_interval_s
        if action_completed_at_s < boundary:
            time.sleep(boundary - action_completed_at_s)
        event.active_end_time_s = max(action_completed_at_s, boundary)
        event.status = StimulusEventStatus.APPLIED
        event.applied_time_s = event.active_end_time_s
        applied.append(event)
    return applied


def _apply_event_to_runtime(event: StimulusEvent, runtime: RuntimeHandle) -> None:
    if event.kind == StimulusDriverKind.DOCKER_ZMQ_RUNTIME_LAUNCH:
        runtime.apply_state({"runtime_condition": "launched"})
        event.evidence = {"runtime_condition": "launched"}
    elif event.kind == StimulusDriverKind.UE_PING_TRAFFIC:
        transmitted = int(event.parameters.get("packets", 3))
        received = int(event.parameters.get("received", transmitted))
        success_ratio = received / transmitted if transmitted else 0.0
        runtime.state["ping"] = {
            "packets_transmitted": transmitted,
            "packets_received": received,
            "success_ratio": success_ratio,
        }
        event.evidence = {"packets_transmitted": transmitted, "packets_received": received}
    elif event.kind == StimulusDriverKind.METRICS_STALENESS_MASK:
        stale_until_step = int(event.parameters.get("stale_until_step", 0))
        stale = event.step_id <= stale_until_step
        runtime.state["metrics"] = {
            "present": True,
            "stale": stale,
            "sample_count": max(1, event.step_id),
            "parse_errors": 0,
        }
        event.evidence = {"metrics_stale": stale}
