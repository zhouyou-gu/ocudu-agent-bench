"""Deterministic benchmark stimulus scheduling."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

from benchmark.benchmark_api.runtime_setup import RuntimeHandle, core_ue_registration_state
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
            if step_id not in targeted_steps_for_event(template, steps):
                continue
            kind = StimulusDriverKind(template["kind"])
            if kind not in IMPLEMENTED_STIMULUS_DRIVERS:
                raise ValueError(f"Stimulus driver is not implemented: {kind.value}")
            validate_stimulus_parameters(kind, template.get("parameters", {}))
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


def targeted_steps_for_event(template: dict[str, Any], steps: int) -> tuple[int, ...]:
    has_apply = "apply_steps" in template
    has_range = "start_step" in template or "end_step" in template
    if has_apply and has_range:
        raise ValueError("stimulus event cannot specify both apply_steps and start_step/end_step")
    if steps <= 0:
        raise ValueError("U.steps must be positive")
    if has_apply:
        raw_steps = template["apply_steps"]
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("apply_steps must be a non-empty list")
        selected: list[int] = []
        for raw_step in raw_steps:
            if isinstance(raw_step, bool) or not isinstance(raw_step, int):
                raise ValueError("apply_steps must contain integer step ids")
            if raw_step < 1 or raw_step > steps:
                raise ValueError("apply_steps contains a step outside U.steps")
            selected.append(int(raw_step))
        if len(set(selected)) != len(selected):
            raise ValueError("apply_steps must not contain duplicate step ids")
        return tuple(selected)
    if has_range:
        if "start_step" not in template or "end_step" not in template:
            raise ValueError("start_step and end_step must be specified together")
        start = template["start_step"]
        end = template["end_step"]
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("start_step and end_step must be integer step ids")
        if start < 1 or end < 1 or start > steps or end > steps:
            raise ValueError("start_step/end_step must be within U.steps")
        if start > end:
            raise ValueError("start_step must be <= end_step")
        return tuple(range(int(start), int(end) + 1))
    return tuple(range(1, steps + 1))


def validate_stimulus_parameters(kind: StimulusDriverKind, parameters: dict[str, Any]) -> None:
    if not isinstance(parameters, dict):
        raise ValueError(f"Stimulus parameters must be an object: {kind.value}")
    if kind == StimulusDriverKind.UE_PING_TRAFFIC:
        packets = _stimulus_int(parameters.get("packets", 3), "packets")
        if packets < 0:
            raise ValueError("packets must be non-negative")
        if "received" in parameters:
            received = _stimulus_int(parameters["received"], "received")
            if received < 0 or received > packets:
                raise ValueError("received must be in [0, packets]")
    elif kind == StimulusDriverKind.METRICS_STALENESS_MASK:
        if _stimulus_int(parameters.get("stale_until_step", 0), "stale_until_step") < 0:
            raise ValueError("stale_until_step must be non-negative")
    elif kind == StimulusDriverKind.UE_ACTIVITY_CHURN:
        action = str(parameters.get("action", "restart")).strip().lower()
        if action not in {"attach", "detach", "reconnect", "restart"}:
            raise ValueError(f"Unsupported UE activity churn action: {action}")
    elif kind == StimulusDriverKind.CORE_UE_REGISTRATION_MISCONFIG:
        if "mismatch_field" in parameters:
            mismatch_field = str(parameters["mismatch_field"]).strip()
            if mismatch_field not in {"ue_id", "supi", "plmn", "dnn", "sst", "sd", "auth_profile_id"}:
                raise ValueError(f"Unsupported core UE registration mismatch field: {mismatch_field}")
    elif kind == StimulusDriverKind.TRAFFIC_LOAD_PROFILE:
        if "packet_rate_pps" in parameters and _stimulus_float(parameters["packet_rate_pps"], "packet_rate_pps") < 0:
            raise ValueError("packet_rate_pps must be non-negative")
        if "active_ues" in parameters and _stimulus_int(parameters["active_ues"], "active_ues") < 0:
            raise ValueError("active_ues must be non-negative")
        if "target" in parameters and not str(parameters["target"]).strip():
            raise ValueError("target must be non-empty")
    elif kind == StimulusDriverKind.MOBILITY_PATH:
        if "serving_pci" in parameters:
            _validate_pci(parameters["serving_pci"], "serving_pci")
        if "du_ue_id" in parameters and _stimulus_int(parameters["du_ue_id"], "du_ue_id") < 0:
            raise ValueError("du_ue_id must be non-negative")
        target_pcis = parameters.get("target_pcis", [])
        if target_pcis is not None and not isinstance(target_pcis, list):
            raise ValueError("target_pcis must be a list")
        for value in target_pcis or []:
            _validate_pci(value, "target_pcis")
    elif kind == StimulusDriverKind.RADIO_CONDITION_PROFILE:
        if "cqi" in parameters and not 0 <= _stimulus_int(parameters["cqi"], "cqi") <= 15:
            raise ValueError("cqi must be in [0, 15]")
        if "pathloss_db" in parameters and _stimulus_float(parameters["pathloss_db"], "pathloss_db") < 0:
            raise ValueError("pathloss_db must be non-negative")
        if "target_cfo_hz" in parameters and not -100_000.0 <= _stimulus_float(parameters["target_cfo_hz"], "target_cfo_hz") <= 100_000.0:
            raise ValueError("target_cfo_hz must be in [-100000, 100000]")
        if "target_tx_time_offset_us" in parameters and not -10_000.0 <= _stimulus_float(parameters["target_tx_time_offset_us"], "target_tx_time_offset_us") <= 10_000.0:
            raise ValueError("target_tx_time_offset_us must be in [-10000, 10000]")
        if "target_ssb_block_power_dbm" in parameters and not -60 <= _stimulus_int(parameters["target_ssb_block_power_dbm"], "target_ssb_block_power_dbm") <= 50:
            raise ValueError("target_ssb_block_power_dbm must be in [-60, 50]")
    elif kind == StimulusDriverKind.SLICE_DEMAND_SHIFT:
        if "sst" in parameters and not 0 <= _stimulus_int(parameters["sst"], "sst") <= 255:
            raise ValueError("sst must be in [0, 255]")
        for field in ("min_prb_policy_ratio", "max_prb_policy_ratio"):
            if field in parameters and not 0 <= _stimulus_int(parameters[field], field) <= 100:
                raise ValueError(f"{field} must be in [0, 100]")
        min_ratio = _stimulus_int(parameters.get("min_prb_policy_ratio", 20), "min_prb_policy_ratio")
        max_ratio = _stimulus_int(parameters.get("max_prb_policy_ratio", 80), "max_prb_policy_ratio")
        if min_ratio > max_ratio:
            raise ValueError("min_prb_policy_ratio must be <= max_prb_policy_ratio")
        if "sd" in parameters and parameters["sd"] is not None:
            sd = _stimulus_int(parameters["sd"], "sd")
            if not 0 <= sd <= 0xFFFFFF:
                raise ValueError("sd must be in [0, 16777215]")
        if "active_ues" in parameters and _stimulus_int(parameters["active_ues"], "active_ues") < 0:
            raise ValueError("active_ues must be non-negative")
    elif kind == StimulusDriverKind.TELEMETRY_GAP:
        sources = parameters.get("sources", ["json_metrics"])
        if not isinstance(sources, list) or not all(isinstance(source, str) and source for source in sources):
            raise ValueError("sources must be a list of non-empty strings")
    elif kind == StimulusDriverKind.E2_KPM_AVAILABILITY_WINDOW:
        for field in ("available_from_step", "available_until_step", "kpm_indications"):
            if field in parameters and _stimulus_int(parameters[field], field) < 0:
                raise ValueError(f"{field} must be non-negative")
        if (
            "available_from_step" in parameters
            and "available_until_step" in parameters
            and _stimulus_int(parameters["available_until_step"], "available_until_step")
            < _stimulus_int(parameters["available_from_step"], "available_from_step")
        ):
            raise ValueError("available_until_step must be >= available_from_step")
    elif kind == StimulusDriverKind.RIC_XAPP_LIFECYCLE:
        action = str(parameters.get("action", "restart")).strip().lower()
        if action not in {"start", "stop", "restart", "delay"}:
            raise ValueError(f"Unsupported RIC xApp lifecycle action: {action}")
    elif kind == StimulusDriverKind.CORE_LATENCY_PROFILE:
        for field in ("latency_ms", "jitter_ms"):
            if field in parameters and _stimulus_float(parameters[field], field) < 0:
                raise ValueError(f"{field} must be non-negative")
        if "loss_rate" in parameters:
            loss_rate = _stimulus_float(parameters["loss_rate"], "loss_rate")
            if not 0.0 <= loss_rate <= 1.0:
                raise ValueError("loss_rate must be in [0, 1]")
        if "degraded_nf" in parameters:
            degraded_nf = str(parameters["degraded_nf"]).strip().lower()
            if degraded_nf not in {"amf", "smf", "upf", "open5gs"}:
                raise ValueError("degraded_nf must be one of amf, smf, upf, or open5gs")
    elif kind == StimulusDriverKind.BACKHAUL_IMPAIRMENT:
        if "loss_rate" in parameters:
            loss_rate = _stimulus_float(parameters["loss_rate"], "loss_rate")
            if not 0.0 <= loss_rate <= 1.0:
                raise ValueError("loss_rate must be in [0, 1]")
        for field in ("delay_ms", "throughput_mbps"):
            if field in parameters and _stimulus_float(parameters[field], field) < 0:
                raise ValueError(f"{field} must be non-negative")
    elif kind == StimulusDriverKind.CELL_IDENTITY_CHANGE:
        for field in ("nci", "gnb_id", "sector_id"):
            if field in parameters and _stimulus_int(parameters[field], field) < 0:
                raise ValueError(f"{field} must be non-negative")
        if "pci" in parameters:
            _validate_pci(parameters["pci"], "pci")
        if "serving_pci" in parameters:
            _validate_pci(parameters["serving_pci"], "serving_pci")
    elif kind == StimulusDriverKind.FUTURE_ZMQ_IMPAIRMENT:
        if "sample_loss_rate" in parameters:
            loss_rate = _stimulus_float(parameters["sample_loss_rate"], "sample_loss_rate")
            if not 0.0 <= loss_rate <= 1.0:
                raise ValueError("sample_loss_rate must be in [0, 1]")
        if "delay_us" in parameters and _stimulus_float(parameters["delay_us"], "delay_us") < 0:
            raise ValueError("delay_us must be non-negative")


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
        target = str(event.parameters.get("target", "10.45.1.1"))
        runtime.state["ping"] = {
            "packets_transmitted": transmitted,
            "packets_received": received,
            "success_ratio": success_ratio,
        }
        runtime.state.setdefault("ue_runtime", {}).update(
            {
                "running": True,
                "traffic_active": True,
                "traffic_profile": {
                    "traffic_kind": "ping",
                    "target": target,
                    "packet_count": transmitted,
                    "source": "stimulus",
                },
            }
        )
        event.evidence = {"packets_transmitted": transmitted, "packets_received": received, "target": target}
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
    elif event.kind == StimulusDriverKind.UE_ACTIVITY_CHURN:
        ue_runtime = runtime.state.setdefault("ue_runtime", {})
        action = str(event.parameters.get("action", "restart")).strip().lower()
        ue_id = str(event.parameters.get("ue_id", ue_runtime.get("ue_id", "ue1"))).strip() or "ue1"
        if action == "detach":
            ue_runtime["running"] = False
            ue_runtime["traffic_active"] = False
        elif action in {"attach", "reconnect"}:
            ue_runtime["running"] = True
            ue_runtime["traffic_active"] = False
        elif action == "restart":
            ue_runtime["running"] = True
            ue_runtime["traffic_active"] = False
            ue_runtime["restart_count"] = int(ue_runtime.get("restart_count", 0) or 0) + 1
        else:
            raise ValueError(f"Unsupported UE activity churn action: {action}")
        ue_runtime["ue_id"] = ue_id
        event.evidence = {
            "ue_id": ue_id,
            "action": action,
            "running": bool(ue_runtime.get("running")),
            "restart_count": int(ue_runtime.get("restart_count", 0) or 0),
        }
    elif event.kind == StimulusDriverKind.CORE_UE_REGISTRATION_MISCONFIG:
        core_runtime = runtime.state.setdefault("core_runtime", {})
        previous_registration = core_runtime.get("ue_registration", {})
        desired = (
            event.parameters.get("desired")
            or event.parameters.get("desired_registration")
            or previous_registration.get("desired")
            or {}
        )
        current = event.parameters.get("current") or event.parameters.get("current_registration")
        if current is None:
            current = dict(desired)
            mismatch_field = str(event.parameters.get("mismatch_field", "supi")).strip()
            if mismatch_field not in {"ue_id", "supi", "plmn", "dnn", "sst", "sd", "auth_profile_id"}:
                raise ValueError(f"Unsupported core UE registration mismatch field: {mismatch_field}")
            current[mismatch_field] = event.parameters.get("mismatch_value", "001010000000099")
        core_runtime["running"] = True
        core_runtime["ue_registration"] = core_ue_registration_state(
            desired=desired,
            current=current,
            last_updated_by="stimulus",
        )
        registration = core_runtime["ue_registration"]
        event.evidence = {
            "ue_id": registration["ue_id"],
            "status": registration["status"],
            "mismatch_fields": list(registration["mismatch_fields"]),
        }
    elif event.kind == StimulusDriverKind.TRAFFIC_LOAD_PROFILE:
        profile = str(event.parameters.get("profile", event.parameters.get("load_level", "medium"))).strip().lower() or "medium"
        default_rate = {"low": 10.0, "medium": 100.0, "high": 1000.0, "bursty": 500.0}.get(profile, 100.0)
        packet_rate_pps = _stimulus_float(event.parameters.get("packet_rate_pps", default_rate), "packet_rate_pps")
        active_ues = _stimulus_int(event.parameters.get("active_ues", 1), "active_ues")
        target = str(event.parameters.get("target", "10.45.1.1"))
        ue_runtime = runtime.state.setdefault("ue_runtime", {})
        traffic_profile = {
            "traffic_kind": str(event.parameters.get("traffic_kind", "ping")),
            "profile": profile,
            "packet_rate_pps": packet_rate_pps,
            "active_ues": active_ues,
            "target": target,
            "source": "stimulus",
        }
        ue_runtime.update({"traffic_active": True, "traffic_profile": traffic_profile})
        runtime.state["traffic_load"] = dict(traffic_profile)
        event.evidence = {"profile": profile, "packet_rate_pps": packet_rate_pps, "active_ues": active_ues}
    elif event.kind == StimulusDriverKind.MOBILITY_PATH:
        ue_identity = runtime.state.setdefault("ue_identity", {})
        serving_pci = _stimulus_int(event.parameters.get("serving_pci", ue_identity.get("serving_pci", 1)), "serving_pci")
        target_pcis = event.parameters.get("target_pcis", ue_identity.get("target_pcis", [2]))
        target_pcis = [_stimulus_int(value, "target_pcis") for value in list(target_pcis)]
        ue_identity.update(
            {
                "serving_pci": serving_pci,
                "target_pcis": target_pcis,
                "rnti": str(event.parameters.get("rnti", ue_identity.get("rnti", "0x4601"))),
                "du_ue_id": _stimulus_int(event.parameters.get("du_ue_id", ue_identity.get("du_ue_id", 1)), "du_ue_id"),
                "mobility_path_id": str(event.parameters.get("path_id", "path-1")),
                "mobility_step": event.step_id,
            }
        )
        event.evidence = {"serving_pci": serving_pci, "target_pcis": target_pcis, "path_id": ue_identity["mobility_path_id"]}
    elif event.kind == StimulusDriverKind.RADIO_CONDITION_PROFILE:
        radio_runtime = runtime.state.setdefault("radio_runtime", {})
        profile = str(event.parameters.get("profile", "nominal")).strip().lower() or "nominal"
        radio_runtime.update(
            {
                "condition_profile": profile,
                "pathloss_db": _stimulus_float(event.parameters.get("pathloss_db", 80.0), "pathloss_db"),
                "noise_dbm": _stimulus_float(event.parameters.get("noise_dbm", -96.0), "noise_dbm"),
                "sinr_db": _stimulus_float(event.parameters.get("sinr_db", 20.0), "sinr_db"),
                "cqi": _stimulus_int(event.parameters.get("cqi", 10), "cqi"),
            }
        )
        for field in ("target_cfo_hz", "target_tx_time_offset_us", "target_ssb_block_power_dbm"):
            if field in event.parameters:
                radio_runtime[field] = event.parameters[field]
        event.evidence = {"condition_profile": profile, "sinr_db": radio_runtime["sinr_db"], "cqi": radio_runtime["cqi"]}
    elif event.kind == StimulusDriverKind.SLICE_DEMAND_SHIFT:
        plmn = str(event.parameters.get("plmn", "00101"))
        sst = _stimulus_int(event.parameters.get("sst", 1), "sst")
        sd = event.parameters.get("sd")
        demand_level = str(event.parameters.get("demand_level", "high")).strip().lower() or "high"
        min_ratio = _stimulus_int(event.parameters.get("min_prb_policy_ratio", 20), "min_prb_policy_ratio")
        max_ratio = _stimulus_int(event.parameters.get("max_prb_policy_ratio", 80), "max_prb_policy_ratio")
        slice_runtime = runtime.state.setdefault("slice_runtime", {})
        slice_runtime.update(
            {
                "active_slice": {"plmn": plmn, "sst": sst, "sd": None if sd is None else _stimulus_int(sd, "sd")},
                "demand_level": demand_level,
                "active_ues": _stimulus_int(event.parameters.get("active_ues", 1), "active_ues"),
                "target_prb_policy": {"min_prb_policy_ratio": min_ratio, "max_prb_policy_ratio": max_ratio},
            }
        )
        event.evidence = {"slice": dict(slice_runtime["active_slice"]), "demand_level": demand_level}
    elif event.kind == StimulusDriverKind.TELEMETRY_GAP:
        sources = [str(source) for source in event.parameters.get("sources", ["json_metrics"])]
        runtime.state["telemetry_gap"] = {"sources": sources, "step_id": event.step_id}
        if "json_metrics" in sources:
            runtime.state["metrics"] = {"present": False, "stale": True, "sample_count": 0, "parse_errors": 0}
        if "e2_kpm_v05" in sources:
            runtime.state.setdefault("e2", {}).update({"kpm_indications": 0, "has_prb_measurement": False})
        event.evidence = {"sources": sources, "gap_active": True}
    elif event.kind == StimulusDriverKind.E2_KPM_AVAILABILITY_WINDOW:
        available_from = _stimulus_int(event.parameters.get("available_from_step", 1), "available_from_step")
        available_until = event.parameters.get("available_until_step")
        in_window = event.step_id >= available_from and (available_until is None or event.step_id <= _stimulus_int(available_until, "available_until_step"))
        e2 = runtime.state.setdefault("e2", {})
        e2.update(
            {
                "enabled": bool(in_window),
                "kpm_indications": _stimulus_int(event.parameters.get("kpm_indications", 3), "kpm_indications") if in_window else 0,
                "has_prb_measurement": bool(event.parameters.get("has_prb_measurement", True)) if in_window else False,
                "availability_window": {"from_step": available_from, "until_step": available_until},
            }
        )
        event.evidence = {"available": in_window, "from_step": available_from, "until_step": available_until}
    elif event.kind == StimulusDriverKind.RIC_XAPP_LIFECYCLE:
        action = str(event.parameters.get("action", "restart")).strip().lower()
        xapp = str(event.parameters.get("xapp", "control")).strip().lower() or "control"
        e2 = runtime.state.setdefault("e2", {})
        restart_count = int(e2.get("xapp_restart_count", 0) or 0)
        running = action in {"start", "restart"}
        if action == "restart":
            restart_count += 1
        if action in {"stop", "delay"}:
            running = False
        e2.update({"ric_xapp": xapp, "ric_xapp_running": running, "xapp_restart_count": restart_count})
        runtime.state.setdefault("backend", {})["e2_control"] = bool(running and "flexric" in runtime.components)
        event.evidence = {"xapp": xapp, "action": action, "running": running}
    elif event.kind == StimulusDriverKind.CORE_LATENCY_PROFILE:
        latency_profile = {
            "latency_ms": _stimulus_float(event.parameters.get("latency_ms", 25.0), "latency_ms"),
            "jitter_ms": _stimulus_float(event.parameters.get("jitter_ms", 5.0), "jitter_ms"),
            "loss_rate": _stimulus_float(event.parameters.get("loss_rate", 0.0), "loss_rate"),
        }
        core_runtime = runtime.state.setdefault("core_runtime", {})
        core_runtime["latency_profile"] = latency_profile
        if "degraded_nf" in event.parameters:
            degraded_nf = str(event.parameters["degraded_nf"]).strip().lower()
            core_runtime["degraded_nf"] = degraded_nf if latency_profile["loss_rate"] > 0.0 or latency_profile["latency_ms"] > 50.0 else None
            nf_status = dict(core_runtime.get("nf_status", {}))
            nf_status[degraded_nf] = "degraded" if core_runtime["degraded_nf"] else "running"
            core_runtime["nf_status"] = nf_status
        event.evidence = dict(latency_profile)
    elif event.kind == StimulusDriverKind.BACKHAUL_IMPAIRMENT:
        impairment = {
            "delay_ms": _stimulus_float(event.parameters.get("delay_ms", 10.0), "delay_ms"),
            "loss_rate": _stimulus_float(event.parameters.get("loss_rate", 0.0), "loss_rate"),
            "throughput_mbps": _optional_float(event.parameters.get("throughput_mbps")),
        }
        runtime.state["backhaul_runtime"] = impairment
        if "ping" in runtime.state and runtime.state["ping"].get("packets_transmitted"):
            transmitted = int(runtime.state["ping"]["packets_transmitted"])
            received = max(0, min(transmitted, round(transmitted * (1.0 - impairment["loss_rate"]))))
            runtime.state["ping"]["packets_received"] = received
            runtime.state["ping"]["success_ratio"] = received / transmitted if transmitted else 0.0
        event.evidence = dict(impairment)
    elif event.kind == StimulusDriverKind.CELL_IDENTITY_CHANGE:
        cell_identity = runtime.state.setdefault("cell_identity", {})
        for field in ("plmn", "nci", "gnb_id", "sector_id", "pci"):
            if field in event.parameters:
                cell_identity[field] = event.parameters[field]
        if "serving_pci" in event.parameters:
            runtime.state.setdefault("ue_identity", {})["serving_pci"] = _stimulus_int(event.parameters["serving_pci"], "serving_pci")
        event.evidence = dict(cell_identity)
    elif event.kind == StimulusDriverKind.FUTURE_ZMQ_IMPAIRMENT:
        impairment = {
            "enabled": bool(event.parameters.get("enabled", True)),
            "impairment_kind": str(event.parameters.get("impairment_kind", "sample_path")),
            "sample_loss_rate": _stimulus_float(event.parameters.get("sample_loss_rate", 0.0), "sample_loss_rate"),
            "delay_us": _stimulus_float(event.parameters.get("delay_us", 0.0), "delay_us"),
            "simulated_only": True,
        }
        runtime.state.setdefault("radio_runtime", {})["zmq_impairment"] = impairment
        event.evidence = {"enabled": impairment["enabled"], "impairment_kind": impairment["impairment_kind"], "simulated_only": True}


def _stimulus_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return int(value)


def _validate_pci(value: Any, field: str) -> int:
    pci = _stimulus_int(value, field)
    if not 0 <= pci <= 1007:
        raise ValueError(f"{field} must be in [0, 1007]")
    return pci


def _stimulus_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else _stimulus_float(value, "throughput_mbps")
