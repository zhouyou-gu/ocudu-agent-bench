"""Repeated-run controller and deterministic baseline agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.suite import aggregate_summaries
from benchmark.benchmark_api.task_catalog import load_tasks_for_suite


NO_ACTION_TASKS = {
    "base_isolation_backhaul_not_ran_v1",
    "base_restraint_minimal_intervention_budget_v1",
}

FIRST_STEP_WAIT_TASKS = {
    "base_prb_backend_e2_vs_ws_v1",
    "base_mobility_conditional_handover_planning_v1",
    "base_core_nf_recovery_v1",
    "base_core_nf_partial_recovery_no_repeat_v1",
    "base_ssb_coverage_edge_recovery_v1",
    "base_ssb_power_boundary_precision_v1",
    "base_diagnosis_congestion_prb_v1",
    "base_diagnosis_coverage_ssb_v1",
    "base_mobility_immediate_handover_v1",
    "base_prb_ric_xapp_ws_fallback_v1",
    "base_prb_slice_congestion_rebalance_v1",
    "base_prb_overcorrection_restraint_v1",
    "base_ssb_wrong_cell_identity_trap_v1",
}

WAIT_UNTIL_STEP3_TASKS = {
    "base_radio_cli_diagnose_cfo_vs_timing_v1",
    "base_prb_telemetry_gap_fallback_v1",
}

E2_EVIDENCE_GATED_TASKS = {
    "base_prb_backend_e2_vs_ws_v1",
    "base_prb_e2_kpm_gated_v1",
}

SSB_CONTROL_TASKS = {
    "base_ssb_coverage_edge_recovery_v1",
    "base_ssb_power_boundary_precision_v1",
    "base_diagnosis_coverage_ssb_v1",
    "base_ssb_wrong_cell_identity_trap_v1",
}

PRB_CONTROL_TASKS = {
    "base_diagnosis_congestion_prb_v1",
    "base_prb_slice_congestion_rebalance_v1",
    "base_prb_stale_metrics_then_rebalance_v1",
    "base_prb_telemetry_gap_fallback_v1",
    "base_prb_overcorrection_restraint_v1",
}


@dataclass(frozen=True)
class ControllerConfig:
    task_id: str | None = None
    controller_id: str = "fixed_prb"
    runs: int = 1
    seed: int = 1
    output_dir: Path | None = None
    tasks_dir: Path | None = None
    suite: str = "base"
    suite_count: int | None = None
    family: str | None = None
    agent_session_policy: str = "isolated_per_run"


class BaselineController:
    def __init__(self, controller_id: str) -> None:
        self.controller_id = controller_id
        self.sent = False
        self.step = 0

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.step += 1
        task_id = str(payload["task"].get("task_id", ""))
        actions = payload["task"]["api_projection"]["action_types"]
        evidence = payload["observation"].get("evidence", {})
        if self.controller_id == "auto":
            return self._auto_decision(task_id, actions, evidence, payload)
        if self.controller_id == "noop":
            return {"decision": None, "telemetry": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}}
        if self.step == 1 and self.controller_id == "invalid_then_fixed":
            return {
                "decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10},
                "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2},
            }
        if self.sent:
            return {"decision": None, "telemetry": {"prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}}
        self.sent = True
        ue_identity = evidence.get("ue_identity", {})
        core_registration = evidence.get("core_runtime", {}).get("ue_registration", {})
        radio_runtime = evidence.get("radio_runtime", {})
        if "RESTART_CORE_NF" in actions and self.controller_id in {"core_restart", "auto"}:
            decision = {"type": "RESTART_CORE_NF", "nf": "open5gs"}
        elif "UPDATE_CORE_UE_REGISTRATION" in actions and self.controller_id in {"core_ue_registration", "auto"}:
            desired = dict(core_registration.get("desired") or {})
            decision = {
                "type": "UPDATE_CORE_UE_REGISTRATION",
                "ue_id": desired.get("ue_id", "ue1"),
                "supi": desired.get("supi", "001010000000001"),
                "plmn": desired.get("plmn", "00101"),
                "dnn": desired.get("dnn", "internet"),
                "sst": desired.get("sst", 1),
                "sd": desired.get("sd"),
                "auth_profile_id": desired.get("auth_profile_id", "ue1_test_profile"),
            }
        elif "TRIGGER_HANDOVER_CLI" in actions and self.controller_id in {"handover", "auto"}:
            target_pcis = ue_identity.get("target_pcis") or [2]
            decision = {
                "type": "TRIGGER_HANDOVER_CLI",
                "serving_pci": ue_identity.get("serving_pci", 1),
                "rnti": ue_identity.get("rnti", "0x4601"),
                "target_pci": target_pcis[0],
            }
        elif "TRIGGER_CONDITIONAL_HANDOVER_CLI" in actions and self.controller_id in {"conditional_handover", "cho", "auto"}:
            decision = {
                "type": "TRIGGER_CONDITIONAL_HANDOVER_CLI",
                "serving_pci": ue_identity.get("serving_pci", 1),
                "rnti": ue_identity.get("rnti", "0x4601"),
                "target_pcis": ue_identity.get("target_pcis", [2]),
                "timeout_s": 5,
            }
        elif "SET_CFO_CLI" in actions and self.controller_id in {"cfo", "auto"}:
            decision = {
                "type": "SET_CFO_CLI",
                "sector_id": radio_runtime.get("sector_id", 0),
                "cfo_hz": radio_runtime.get("target_cfo_hz", -1250.0),
            }
        elif "SET_TX_TIME_OFFSET_CLI" in actions and self.controller_id in {"tx_time_offset", "auto"}:
            decision = {
                "type": "SET_TX_TIME_OFFSET_CLI",
                "sector_id": radio_runtime.get("sector_id", 0),
                "tx_time_offset_us": radio_runtime.get("target_tx_time_offset_us", 7.5),
            }
        elif "SET_SSB_BLOCK_POWER_WS" in actions and self.controller_id in {"fixed_ssb", "auto"}:
            decision = {"type": "SET_SSB_BLOCK_POWER_WS", "nci": 6733824, "ssb_block_power_dbm": -16}
        elif "SET_PRB_POLICY_RATIO_CCC" in actions and self.controller_id in {"e2_ccc", "auto"}:
            decision = {"type": "SET_PRB_POLICY_RATIO_CCC", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}
        elif "SET_PRB_POLICY_RATIO_RC_DU" in actions and self.controller_id in {"rc_du", "auto"}:
            decision = {
                "type": "SET_PRB_POLICY_RATIO_RC_DU",
                "min_prb_policy_ratio": 20,
                "max_prb_policy_ratio": 80,
                "du_ue_id": payload["observation"].get("evidence", {}).get("ue_identity", {}).get("du_ue_id", 1),
            }
        elif "SET_PRB_POLICY_RATIO_WS" in actions and self.controller_id in {"fixed_prb", "auto"}:
            decision = {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}
        elif "NO_ACTION" in actions:
            decision = None
        else:
            decision = {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80}
        return {"decision": decision, "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2}}

    def _auto_decision(
        self,
        task_id: str,
        actions: list[str],
        evidence: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_key = _anchor_task_id(task_id)
        if task_id == "regression_harness_invalid_action_repair_v1" and self.step == 1:
            return {
                "decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10},
                "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2},
            }
        previous_feedback = payload.get("previous_feedback")
        if self.sent and not _previous_rejection(previous_feedback):
            return self._noop()
        if self._should_wait(task_key, actions, evidence, payload):
            return self._noop()
        action_type = self._select_action_type(task_key, actions, evidence, payload)
        if action_type is None:
            return self._noop()
        decision = self._build_decision(action_type, evidence, payload)
        if decision is None:
            return self._noop()
        self.sent = True
        return {"decision": decision, "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2}}

    def _should_wait(self, task_id: str, actions: list[str], evidence: dict[str, Any], payload: dict[str, Any]) -> bool:
        metrics = evidence.get("metrics", {})
        e2 = evidence.get("e2_kpm_v05", {})
        previous_feedback = payload.get("previous_feedback")
        if _previous_rejection(previous_feedback):
            return False
        if metrics and (not metrics.get("present") or metrics.get("stale")):
            return True
        if evidence.get("radio_runtime", {}).get("condition_profile") == "ambiguous_radio":
            return True
        if self.step == 1 and _core_nf_degraded(evidence):
            return True
        if task_id in FIRST_STEP_WAIT_TASKS and self.step == 1:
            return True
        if task_id in NO_ACTION_TASKS:
            return True
        if task_id in WAIT_UNTIL_STEP3_TASKS and self.step < 3:
            return True
        if _all_evidence_healthy(evidence) and not _has_repair_target(actions, evidence):
            return True
        if task_id in {"base_prb_stale_metrics_then_rebalance_v1", "base_prb_e2_kpm_gated_v1"}:
            if not metrics.get("present") or metrics.get("stale"):
                return True
            if task_id in E2_EVIDENCE_GATED_TASKS and (
                not e2.get("enabled") or int(e2.get("kpm_indications", 0) or 0) <= 0
            ):
                return True
        if task_id == "base_prb_telemetry_gap_fallback_v1" and (
            not metrics.get("present") or metrics.get("stale")
        ):
            return True
        if task_id in E2_EVIDENCE_GATED_TASKS and (
            not e2.get("enabled") or int(e2.get("kpm_indications", 0) or 0) <= 0
        ):
            return True
        if not _has_repair_target(actions, evidence):
            return True
        return False

    def _select_action_type(self, task_id: str, actions: list[str], evidence: dict[str, Any], payload: dict[str, Any] | None = None) -> str | None:
        previous_feedback = (payload or {}).get("previous_feedback") if payload else None
        if _previous_rejection(previous_feedback) and "SET_PRB_POLICY_RATIO_WS" in actions:
            return "SET_PRB_POLICY_RATIO_WS"
        if task_id in {"base_core_nf_recovery_v1", "base_core_nf_partial_recovery_no_repeat_v1"} and "RESTART_CORE_NF" in actions:
            return "RESTART_CORE_NF"
        if task_id in {
            "base_core_ue_registration_repair_v1",
            "base_core_ue_auth_profile_repair_v1",
        } and "UPDATE_CORE_UE_REGISTRATION" in actions:
            return "UPDATE_CORE_UE_REGISTRATION"
        if task_id == "base_radio_cli_cfo_correction_v1" and "SET_CFO_CLI" in actions:
            return "SET_CFO_CLI"
        if task_id == "base_radio_cli_tx_time_offset_correction_v1" and "SET_TX_TIME_OFFSET_CLI" in actions:
            return "SET_TX_TIME_OFFSET_CLI"
        if task_id == "base_radio_cli_diagnose_cfo_vs_timing_v1":
            radio = evidence.get("radio_runtime", {})
            if radio.get("target_cfo_hz") is not None and "SET_CFO_CLI" in actions:
                return "SET_CFO_CLI"
            if radio.get("target_tx_time_offset_us") is not None and "SET_TX_TIME_OFFSET_CLI" in actions:
                return "SET_TX_TIME_OFFSET_CLI"
        if task_id == "base_mobility_conditional_handover_planning_v1" and "TRIGGER_CONDITIONAL_HANDOVER_CLI" in actions:
            return "TRIGGER_CONDITIONAL_HANDOVER_CLI"
        if task_id in {"base_mobility_immediate_handover_v1", "base_mobility_reject_then_current_identity_repair_v1"} and "TRIGGER_HANDOVER_CLI" in actions:
            return "TRIGGER_HANDOVER_CLI"
        if task_id in SSB_CONTROL_TASKS and "SET_SSB_BLOCK_POWER_WS" in actions:
            return "SET_SSB_BLOCK_POWER_WS"
        if task_id in {"base_prb_backend_e2_vs_ws_v1", "base_prb_e2_kpm_gated_v1"} and "SET_PRB_POLICY_RATIO_CCC" in actions:
            return "SET_PRB_POLICY_RATIO_CCC"
        if task_id == "base_prb_ric_xapp_ws_fallback_v1" and "SET_PRB_POLICY_RATIO_WS" in actions:
            return "SET_PRB_POLICY_RATIO_WS"
        if task_id in PRB_CONTROL_TASKS and "SET_PRB_POLICY_RATIO_WS" in actions:
            return "SET_PRB_POLICY_RATIO_WS"
        if _core_nf_degraded(evidence) and "RESTART_CORE_NF" in actions:
            return "RESTART_CORE_NF"
        if _core_ue_mismatch(evidence) and "UPDATE_CORE_UE_REGISTRATION" in actions:
            return "UPDATE_CORE_UE_REGISTRATION"
        if _mobility_ready(evidence) and "TRIGGER_CONDITIONAL_HANDOVER_CLI" in actions and len(evidence.get("ue_identity", {}).get("target_pcis") or []) > 1:
            return "TRIGGER_CONDITIONAL_HANDOVER_CLI"
        if _mobility_ready(evidence) and "TRIGGER_HANDOVER_CLI" in actions:
            return "TRIGGER_HANDOVER_CLI"
        if _coverage_repair_ready(evidence) and "SET_SSB_BLOCK_POWER_WS" in actions:
            return "SET_SSB_BLOCK_POWER_WS"
        if _tx_offset_ready(evidence) and "SET_TX_TIME_OFFSET_CLI" in actions:
            return "SET_TX_TIME_OFFSET_CLI"
        if _cfo_ready(evidence) and "SET_CFO_CLI" in actions:
            return "SET_CFO_CLI"
        if _slice_repair_ready(evidence):
            if _e2_ready(evidence) and "SET_PRB_POLICY_RATIO_CCC" in actions:
                return "SET_PRB_POLICY_RATIO_CCC"
            if "SET_PRB_POLICY_RATIO_WS" in actions:
                return "SET_PRB_POLICY_RATIO_WS"
        for action_type in (
            "SET_PRB_POLICY_RATIO_WS",
            "SET_PRB_POLICY_RATIO_CCC",
            "SET_PRB_POLICY_RATIO_RC_DU",
            "SET_SSB_BLOCK_POWER_WS",
            "TRIGGER_HANDOVER_CLI",
            "TRIGGER_CONDITIONAL_HANDOVER_CLI",
            "SET_CFO_CLI",
            "SET_TX_TIME_OFFSET_CLI",
            "RESTART_CORE_NF",
            "UPDATE_CORE_UE_REGISTRATION",
        ):
            if action_type in actions:
                return action_type
        return None

    def _build_decision(self, action_type: str, evidence: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        ue_identity = evidence.get("ue_identity", {})
        core_registration = evidence.get("core_runtime", {}).get("ue_registration", {})
        radio_runtime = evidence.get("radio_runtime", {})
        slice_runtime = evidence.get("slice_runtime", {})
        active_slice = slice_runtime.get("active_slice", {})
        target_policy = _slice_policy_target(slice_runtime) or {}
        if action_type in {"SET_PRB_POLICY_RATIO_WS", "SET_PRB_POLICY_RATIO_CCC", "SET_PRB_POLICY_RATIO_RC_DU"}:
            decision = {
                "type": action_type,
                "plmn": active_slice.get("plmn", "00101"),
                "sst": active_slice.get("sst", 1),
                "sd": active_slice.get("sd"),
                "min_prb_policy_ratio": target_policy.get("min_prb_policy_ratio", 20),
                "max_prb_policy_ratio": target_policy.get("max_prb_policy_ratio", 80),
            }
            if action_type == "SET_PRB_POLICY_RATIO_RC_DU":
                decision["du_ue_id"] = ue_identity.get("du_ue_id", 1)
            return decision
        if action_type == "SET_SSB_BLOCK_POWER_WS":
            cell = evidence.get("cell_identity", {})
            return {
                "type": action_type,
                "plmn": cell.get("plmn", "00101"),
                "nci": cell.get("nci", 6733824),
                "ssb_block_power_dbm": radio_runtime.get("target_ssb_block_power_dbm", -16),
            }
        if action_type == "TRIGGER_HANDOVER_CLI":
            target_pcis = ue_identity.get("target_pcis") or [2]
            return {
                "type": action_type,
                "serving_pci": ue_identity.get("serving_pci", 1),
                "rnti": ue_identity.get("rnti", "0x4601"),
                "target_pci": target_pcis[0],
            }
        if action_type == "TRIGGER_CONDITIONAL_HANDOVER_CLI":
            return {
                "type": action_type,
                "serving_pci": ue_identity.get("serving_pci", 1),
                "rnti": ue_identity.get("rnti", "0x4601"),
                "target_pcis": ue_identity.get("target_pcis", [2]),
                "timeout_s": 5,
            }
        if action_type == "SET_CFO_CLI":
            return {
                "type": action_type,
                "sector_id": radio_runtime.get("sector_id", 0),
                "cfo_hz": radio_runtime.get("target_cfo_hz", -1250.0),
            }
        if action_type == "SET_TX_TIME_OFFSET_CLI":
            return {
                "type": action_type,
                "sector_id": radio_runtime.get("sector_id", 0),
                "tx_time_offset_us": radio_runtime.get("target_tx_time_offset_us", 7.5),
            }
        if action_type == "RESTART_CORE_NF":
            return {"type": action_type, "nf": evidence.get("core_runtime", {}).get("degraded_nf", "open5gs")}
        if action_type == "UPDATE_CORE_UE_REGISTRATION":
            desired = dict(core_registration.get("desired") or {})
            return {
                "type": action_type,
                "ue_id": desired.get("ue_id", "ue1"),
                "supi": desired.get("supi", "001010000000001"),
                "plmn": desired.get("plmn", "00101"),
                "dnn": desired.get("dnn", "internet"),
                "sst": desired.get("sst", 1),
                "sd": desired.get("sd"),
                "auth_profile_id": desired.get("auth_profile_id", "ue1_test_profile"),
            }
        return None

    def _noop(self) -> dict[str, Any]:
        return {"decision": None, "telemetry": {"prompt_tokens": 2, "completion_tokens": 1, "reasoning_tokens": 0}}


def run_repeated(config: ControllerConfig) -> dict[str, Any]:
    results = []
    summaries = []
    task_ids = [config.task_id] if config.task_id else sorted(
        load_tasks_for_suite(
            suite=config.suite,
            seed=config.seed,
            count=config.suite_count,
            family=config.family,
            task_sets_dir=config.tasks_dir,
        )
    )
    run_manifest = {
        "task_id": config.task_id or f"{config.suite}_suite",
        "controller_id": config.controller_id,
        "suite": config.suite,
        "agent_session_policy": config.agent_session_policy,
        "seed_identifiers": [],
        "run_ids": [],
        "scored_summary_locations": [],
    }
    for task_id in task_ids:
        if task_id is None:
            continue
        for index in range(1, config.runs + 1):
            run_id = f"{task_id}-seed{config.seed + index - 1}-r{index:03d}"
            run_seed = config.seed + index - 1
            agent = BaselineController(config.controller_id)
            result = run_episode(
                EpisodeConfig(
                    task_id=task_id,
                    run_id=run_id,
                    seed=run_seed,
                    tasks_dir=config.tasks_dir,
                    suite=config.suite,
                    suite_count=config.suite_count,
                    suite_seed=config.seed,
                    family=config.family,
                    output_dir=config.output_dir,
                    agent_session_id=f"{run_id}-session",
                ),
                agent=agent,
            )
            results.append(result)
            summaries.append(result["summary"])
            run_manifest["seed_identifiers"].append(run_seed)
            run_manifest["run_ids"].append(run_id)
            run_manifest["scored_summary_locations"].append(result.get("scored_summary_path", f"memory://{run_id}/summary"))
    return {
        "run_manifest": run_manifest,
        "runs": results,
        "suite_summary": aggregate_summaries(summaries, run_manifest),
    }


def _anchor_task_id(task_id: str) -> str:
    return task_id.split("__gen_", 1)[0]


def _previous_rejection(feedback: dict[str, Any] | None) -> bool:
    if not isinstance(feedback, dict):
        return False
    if feedback.get("status") == "rejected":
        return True
    outcome = feedback.get("action_outcome", {})
    if isinstance(outcome, dict):
        return outcome.get("accepted") is False or outcome.get("safe_error_class") in {"runtime_unavailable", "runtime_rejected"}
    return False


def _all_evidence_healthy(evidence: dict[str, Any]) -> bool:
    metrics = evidence.get("metrics", {})
    if metrics and (not metrics.get("present") or metrics.get("stale")):
        return False
    slice_runtime = evidence.get("slice_runtime", {})
    radio = evidence.get("radio_runtime", {})
    backhaul = evidence.get("backhaul_runtime", {})
    core = evidence.get("core_runtime", {})
    return (
        slice_runtime.get("demand_level") in {None, "nominal", "low"}
        and float(slice_runtime.get("queue_pressure", 0.0) or 0.0) <= 0.5
        and radio.get("condition_profile") in {None, "nominal"}
        and float(backhaul.get("loss_rate", 0.0) or 0.0) == 0.0
        and not core.get("degraded_nf")
        and core.get("ue_registration", {}).get("status", "registered") == "registered"
    )


def _has_repair_target(actions: list[str], evidence: dict[str, Any]) -> bool:
    for action in actions:
        if _slice_repair_ready(evidence) and action in {"SET_PRB_POLICY_RATIO_WS", "SET_PRB_POLICY_RATIO_CCC"}:
            return True
        if _coverage_repair_ready(evidence) and action == "SET_SSB_BLOCK_POWER_WS":
            return True
        if _cfo_ready(evidence) and action == "SET_CFO_CLI":
            return True
        if _tx_offset_ready(evidence) and action == "SET_TX_TIME_OFFSET_CLI":
            return True
        if _mobility_ready(evidence) and action in {"TRIGGER_HANDOVER_CLI", "TRIGGER_CONDITIONAL_HANDOVER_CLI"}:
            return True
        if _core_nf_degraded(evidence) and action == "RESTART_CORE_NF":
            return True
        if _core_ue_mismatch(evidence) and action == "UPDATE_CORE_UE_REGISTRATION":
            return True
    return False


def _slice_repair_ready(evidence: dict[str, Any]) -> bool:
    slice_runtime = evidence.get("slice_runtime", {})
    target = _slice_policy_target(slice_runtime)
    current = slice_runtime.get("current_prb_policy") or {}
    if not isinstance(target, dict):
        return False
    if slice_runtime.get("demand_level") in {"nominal", "low"} and float(slice_runtime.get("queue_pressure", 0.0) or 0.0) < 0.5:
        return False
    ratios_match = (
        target.get("min_prb_policy_ratio") == current.get("min_prb_policy_ratio")
        and target.get("max_prb_policy_ratio") == current.get("max_prb_policy_ratio")
    )
    if ratios_match and current.get("backend") in {"websocket", "e2_control"}:
        return False
    return (
        not ratios_match
        or current.get("backend") not in {"websocket", "e2_control"}
    )


def _slice_policy_target(slice_runtime: dict[str, Any]) -> dict[str, Any] | None:
    target = slice_runtime.get("target_prb_policy")
    if isinstance(target, dict):
        return target
    queue_pressure = _ratio_from_symptom(slice_runtime.get("queue_pressure"))
    prb_utilization = _ratio_from_symptom(slice_runtime.get("prb_utilization"))
    if queue_pressure is None or prb_utilization is None:
        return None
    if (
        slice_runtime.get("demand_level") not in {"high", "congested", "overloaded"}
        and float(slice_runtime.get("queue_pressure", 0.0) or 0.0) < 0.5
    ):
        return None
    return {
        "min_prb_policy_ratio": queue_pressure,
        "max_prb_policy_ratio": max(queue_pressure, prb_utilization),
    }


def _ratio_from_symptom(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        return int(round(numeric * 100))
    if 0.0 <= numeric <= 100.0:
        return int(round(numeric))
    return None


def _coverage_repair_ready(evidence: dict[str, Any]) -> bool:
    radio = evidence.get("radio_runtime", {})
    return radio.get("condition_profile") in {"coverage_edge", "coverage_bad"} or radio.get("target_ssb_block_power_dbm") is not None


def _cfo_ready(evidence: dict[str, Any]) -> bool:
    radio = evidence.get("radio_runtime", {})
    return radio.get("condition_profile") == "cfo_offset"


def _tx_offset_ready(evidence: dict[str, Any]) -> bool:
    radio = evidence.get("radio_runtime", {})
    return radio.get("condition_profile") == "timing_offset" or radio.get("target_tx_time_offset_us") is not None and radio.get("condition_profile") == "timing_offset"


def _mobility_ready(evidence: dict[str, Any]) -> bool:
    identity = evidence.get("ue_identity", {})
    return bool(identity.get("mobility_path_id") and identity.get("target_pcis"))


def _core_nf_degraded(evidence: dict[str, Any]) -> bool:
    return bool(evidence.get("core_runtime", {}).get("degraded_nf"))


def _core_ue_mismatch(evidence: dict[str, Any]) -> bool:
    return evidence.get("core_runtime", {}).get("ue_registration", {}).get("status") == "mismatch"


def _e2_ready(evidence: dict[str, Any]) -> bool:
    e2 = evidence.get("e2_kpm_v05", {})
    backend = evidence.get("backend", {})
    return bool(backend.get("e2_control") and e2.get("enabled") and int(e2.get("kpm_indications", 0) or 0) > 0)
