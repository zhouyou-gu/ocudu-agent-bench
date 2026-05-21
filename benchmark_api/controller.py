"""Repeated-run controller and deterministic baseline agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.episode import EpisodeConfig, run_episode
from benchmark.benchmark_api.suite import aggregate_summaries


@dataclass(frozen=True)
class ControllerConfig:
    task_id: str
    controller_id: str = "fixed_prb"
    runs: int = 1
    seed: int = 1
    output_dir: Path | None = None
    tasks_dir: Path | None = None
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
        if task_id == "invalid_action_repair_regression_v1" and self.step == 1:
            return {
                "decision": {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10},
                "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2},
            }
        if self.sent:
            return self._noop()
        if self._should_wait(task_id, evidence):
            return self._noop()
        action_type = self._select_action_type(task_id, actions, evidence)
        if action_type is None:
            return self._noop()
        decision = self._build_decision(action_type, evidence, payload)
        if decision is None:
            return self._noop()
        self.sent = True
        return {"decision": decision, "telemetry": {"prompt_tokens": 8, "completion_tokens": 4, "reasoning_tokens": 2}}

    def _should_wait(self, task_id: str, evidence: dict[str, Any]) -> bool:
        metrics = evidence.get("metrics", {})
        e2 = evidence.get("e2_kpm_v05", {})
        if task_id in {
            "slice_congestion_prb_rebalance_v1",
            "coverage_edge_ssb_recovery_v1",
            "diagnose_congestion_prb_v1",
            "diagnose_coverage_ssb_v1",
            "immediate_handover_v1",
            "conditional_handover_planning_v1",
            "wrong_cell_identity_trap_v1",
            "core_nf_recovery_multistep_v1",
            "api_backend_selection_e2_vs_ws_v1",
            "ric_xapp_ws_fallback_v1",
        } and self.step == 1:
            return True
        if task_id in {"backhaul_ran_isolation_v1", "minimal_intervention_budget_v1"}:
            return True
        if task_id == "telemetry_gap_fallback_v1" and self.step < 3:
            return True
        if task_id == "diagnose_cfo_vs_timing_v1" and self.step < 3:
            return True
        if task_id in {"stale_metrics_then_prb_v1", "e2_kpm_gated_prb_v1"}:
            if not metrics.get("present") or metrics.get("stale"):
                return True
            if task_id == "e2_kpm_gated_prb_v1" and (
                not e2.get("enabled") or int(e2.get("kpm_indications", 0) or 0) <= 0
            ):
                return True
        if task_id == "telemetry_gap_fallback_v1" and (not metrics.get("present") or metrics.get("stale")):
            return True
        return False

    def _select_action_type(self, task_id: str, actions: list[str], evidence: dict[str, Any]) -> str | None:
        if task_id == "core_nf_recovery_multistep_v1" and "RESTART_CORE_NF" in actions:
            return "RESTART_CORE_NF"
        if task_id == "core_ue_registration_repair_multistep_v1" and "UPDATE_CORE_UE_REGISTRATION" in actions:
            return "UPDATE_CORE_UE_REGISTRATION"
        if task_id == "cfo_correction_v1" and "SET_CFO_CLI" in actions:
            return "SET_CFO_CLI"
        if task_id == "tx_time_offset_correction_v1" and "SET_TX_TIME_OFFSET_CLI" in actions:
            return "SET_TX_TIME_OFFSET_CLI"
        if task_id == "diagnose_cfo_vs_timing_v1":
            radio = evidence.get("radio_runtime", {})
            if radio.get("target_cfo_hz") is not None and "SET_CFO_CLI" in actions:
                return "SET_CFO_CLI"
            if radio.get("target_tx_time_offset_us") is not None and "SET_TX_TIME_OFFSET_CLI" in actions:
                return "SET_TX_TIME_OFFSET_CLI"
        if task_id == "conditional_handover_planning_v1" and "TRIGGER_CONDITIONAL_HANDOVER_CLI" in actions:
            return "TRIGGER_CONDITIONAL_HANDOVER_CLI"
        if task_id == "immediate_handover_v1" and "TRIGGER_HANDOVER_CLI" in actions:
            return "TRIGGER_HANDOVER_CLI"
        if task_id in {"coverage_edge_ssb_recovery_v1", "diagnose_coverage_ssb_v1", "wrong_cell_identity_trap_v1"}:
            if "SET_SSB_BLOCK_POWER_WS" in actions:
                return "SET_SSB_BLOCK_POWER_WS"
        if task_id == "e2_kpm_gated_prb_v1" and "SET_PRB_POLICY_RATIO_CCC" in actions:
            return "SET_PRB_POLICY_RATIO_CCC"
        if task_id == "api_backend_selection_e2_vs_ws_v1" and evidence.get("backend", {}).get("e2_control"):
            if "SET_PRB_POLICY_RATIO_CCC" in actions:
                return "SET_PRB_POLICY_RATIO_CCC"
        if task_id == "ric_xapp_ws_fallback_v1" and "SET_PRB_POLICY_RATIO_WS" in actions:
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
        target_policy = slice_runtime.get("target_prb_policy") or {}
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
    run_manifest = {
        "task_id": config.task_id,
        "controller_id": config.controller_id,
        "agent_session_policy": config.agent_session_policy,
        "seed_identifiers": [],
        "run_ids": [],
        "scored_summary_locations": [],
    }
    for index in range(1, config.runs + 1):
        run_id = f"{config.task_id}-seed{config.seed + index - 1}-r{index:03d}"
        run_seed = config.seed + index - 1
        agent = BaselineController(config.controller_id)
        result = run_episode(
            EpisodeConfig(
                task_id=config.task_id,
                run_id=run_id,
                seed=run_seed,
                tasks_dir=config.tasks_dir,
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
