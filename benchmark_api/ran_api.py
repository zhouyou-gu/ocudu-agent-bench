"""RAN API request building, evidence reads, and dispatch metadata."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchmark_api.api_catalog import descriptor_for_action, validate_api_selection
from benchmark_api.runtime_setup import RuntimeHandle, LIVE_CORE_ADAPTER, LIVE_OCUDU_ADAPTER, LIVE_E2_ADAPTER
from benchmark_api.simulated_ocudu import apply_simulated_action
from benchmark_api.types import RanActionType, RanObservationSource, SafeErrorClass


@dataclass(frozen=True)
class DispatchResult:
    action_id: str
    dispatched: bool
    accepted: bool
    backend: str | None
    safe_error_class: SafeErrorClass | None
    safe_message: str
    private_request: dict[str, Any] | None
    completed_at_s: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "dispatched": self.dispatched,
            "accepted": self.accepted,
            "backend": self.backend,
            "safe_error_class": self.safe_error_class.value if self.safe_error_class else None,
            "safe_message": self.safe_message,
            "completed_at_s": self.completed_at_s,
        }


def read_evidence(
    runtime: RuntimeHandle,
    sources: tuple[str, ...],
    selected_api_kinds: tuple[str, ...] = (),
    observation_detail: str = "repair_targets",
) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    selected = {RanObservationSource(source) for source in sources}
    selected_backend_names = {
        descriptor.backend.value
        for descriptor in validate_api_selection(list(selected_api_kinds))
    }
    state = runtime.state
    if RanObservationSource.PING in selected:
        evidence["ping"] = dict(state.get("ping", {}))
    if RanObservationSource.JSON_METRICS in selected:
        # TODO: refresh metrics evidence via live_ocudu.read_metrics when JSON_METRICS in selected
        evidence["metrics"] = dict(state.get("metrics", {}))
    if RanObservationSource.WEBSOCKET_CONTROL_OUTCOMES in selected:
        evidence["websocket_control_outcomes"] = [
            item for item in state.get("control_outcomes", []) if item.get("backend") == "websocket"
        ]
    if RanObservationSource.CELL_IDENTITY in selected:
        evidence["cell_identity"] = dict(state.get("cell_identity", {}))
    if RanObservationSource.E2_KPM_V05 in selected:
        e2_state = state.get("e2", {})
        if runtime.runtime_adapter == LIVE_E2_ADAPTER:
            try:
                from benchmark_api import live_e2
                cfg = _live_e2_cfg_from_runtime(runtime)
                records = live_e2.read_kpm(cfg, count=5)
                latest_meas: dict[str, Any] = {}
                if records:
                    for m in records[-1].get("measurements", []) or []:
                        if isinstance(m, dict) and "name" in m and "value" in m:
                            latest_meas[m["name"]] = m["value"]
                evidence["e2_kpm_v05"] = {
                    "enabled": True,
                    "kpm_indications": len(records),
                    "has_prb_measurement": any(k.startswith("RRU.Prb") for k in latest_meas),
                    "kpm_version": records[-1].get("kpm_version") if records else None,
                    "measurements": latest_meas,
                }
            except Exception:  # noqa: BLE001
                # Fall back to the simulated path on transient read failure.
                evidence["e2_kpm_v05"] = {
                    "enabled": bool(e2_state.get("enabled")),
                    "kpm_indications": int(e2_state.get("kpm_indications", 0) or 0),
                    "has_prb_measurement": bool(e2_state.get("has_prb_measurement")),
                }
        else:
            evidence["e2_kpm_v05"] = {
                "enabled": bool(e2_state.get("enabled")),
                "kpm_indications": int(e2_state.get("kpm_indications", 0) or 0),
                "has_prb_measurement": bool(e2_state.get("has_prb_measurement")),
            }
    if RanObservationSource.E2_CONTROL_OUTCOME in selected:
        evidence["e2_control_outcome"] = {
            "accepted_records": [
                item for item in state.get("control_outcomes", []) if item.get("backend") == "e2_control" and item.get("accepted")
            ]
        }
    if RanObservationSource.UE_IDENTITY in selected:
        identity = dict(state.get("ue_identity", {}))
        identity.setdefault("du_ue_id", state.get("e2", {}).get("du_ue_id"))
        evidence["ue_identity"] = identity
    if RanObservationSource.UE_RUNTIME in selected:
        evidence["ue_runtime"] = dict(state.get("ue_runtime", {}))
    if RanObservationSource.CORE_RUNTIME in selected:
        core_runtime = state.get("core_runtime", {})
        if runtime.runtime_adapter == LIVE_CORE_ADAPTER:
            try:
                fresh = _refresh_live_core_runtime(runtime)
                # Preserve restart_counts / last_restarted_nf (live module returns
                # empty {} / None for those; the dispatcher tracks them in state).
                fresh["restart_counts"] = core_runtime.get("restart_counts", {})
                fresh["last_restarted_nf"] = core_runtime.get("last_restarted_nf")
                # Same for ue_registration if local has a more recent write
                if core_runtime.get("ue_registration"):
                    fresh["ue_registration"] = core_runtime["ue_registration"]
                state["core_runtime"] = fresh
                core_runtime = fresh
            except Exception:
                # Don't fail observation on a transient read; fall back to state.
                pass
        evidence["core_runtime"] = _agent_core_runtime(core_runtime)
    if RanObservationSource.RADIO_RUNTIME in selected:
        evidence["radio_runtime"] = _agent_radio_runtime(state.get("radio_runtime", {}), observation_detail)
    if RanObservationSource.SLICE_RUNTIME in selected:
        evidence["slice_runtime"] = _agent_slice_runtime(state.get("slice_runtime", {}), observation_detail)
    if RanObservationSource.BACKHAUL_RUNTIME in selected:
        evidence["backhaul_runtime"] = _agent_backhaul_runtime(state.get("backhaul_runtime", {}))
    evidence["backend"] = {
        backend: state.get("backend", {}).get(backend)
        for backend in sorted(selected_backend_names)
    }
    return evidence


def _agent_radio_runtime(radio_runtime: dict[str, Any], observation_detail: str = "repair_targets") -> dict[str, Any]:
    allowed = {
        "sector_id",
        "cfo_hz",
        "target_cfo_hz",
        "tx_time_offset_us",
        "target_tx_time_offset_us",
        "condition_profile",
        "pathloss_db",
        "noise_dbm",
        "sinr_db",
        "cqi",
        "rsrp_dbm",
        "rsrq_db",
        "target_ssb_block_power_dbm",
        "current_ssb_block_power_dbm",
        "ssb_power_cell",
        "cfo_corrected",
        "tx_time_offset_corrected",
    }
    if observation_detail == "diagnosis_symptoms":
        allowed -= {"target_cfo_hz", "target_tx_time_offset_us", "target_ssb_block_power_dbm"}
    public = {key: radio_runtime[key] for key in allowed if key in radio_runtime}
    impairment = radio_runtime.get("zmq_impairment")
    if isinstance(impairment, dict):
        public["zmq_impairment"] = {
            "enabled": bool(impairment.get("enabled")),
            "impairment_kind": str(impairment.get("impairment_kind", "sample_path")),
        }
    return public


def _agent_slice_runtime(slice_runtime: dict[str, Any], observation_detail: str = "repair_targets") -> dict[str, Any]:
    public = {
        "active_slice": dict(slice_runtime.get("active_slice", {})),
        "demand_level": slice_runtime.get("demand_level"),
        "active_ues": slice_runtime.get("active_ues"),
    }
    for field in ("queue_pressure", "prb_utilization"):
        if field in slice_runtime:
            public[field] = slice_runtime[field]
    target_prb_policy = slice_runtime.get("target_prb_policy")
    if observation_detail != "diagnosis_symptoms" and isinstance(target_prb_policy, dict):
        public["target_prb_policy"] = {
            "min_prb_policy_ratio": target_prb_policy.get("min_prb_policy_ratio"),
            "max_prb_policy_ratio": target_prb_policy.get("max_prb_policy_ratio"),
        }
    current_prb_policy = slice_runtime.get("current_prb_policy")
    if isinstance(current_prb_policy, dict):
        public["current_prb_policy"] = {
            "plmn": current_prb_policy.get("plmn"),
            "sst": current_prb_policy.get("sst"),
            "sd": current_prb_policy.get("sd"),
            "min_prb_policy_ratio": current_prb_policy.get("min_prb_policy_ratio"),
            "max_prb_policy_ratio": current_prb_policy.get("max_prb_policy_ratio"),
            "backend": current_prb_policy.get("backend"),
            "action_type": current_prb_policy.get("action_type"),
        }
    return public


def _agent_backhaul_runtime(backhaul_runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        key: backhaul_runtime[key]
        for key in ("delay_ms", "loss_rate", "throughput_mbps")
        if key in backhaul_runtime
    }


def _agent_core_runtime(core_runtime: dict[str, Any]) -> dict[str, Any]:
    public = dict(core_runtime)
    registration = dict(public.get("ue_registration", {}))
    registration.pop("last_updated_by", None)
    if "desired" in registration:
        registration["desired"] = dict(registration["desired"])
    if "current" in registration:
        registration["current"] = dict(registration["current"])
    if registration:
        public["ue_registration"] = registration
    return public


def dispatch_runtime_action(runtime: RuntimeHandle, action_id: str, action: dict[str, Any]) -> DispatchResult:
    action_type = RanActionType(action["type"])
    if action_type == RanActionType.NO_ACTION:
        return DispatchResult(
            action_id=action_id,
            dispatched=False,
            accepted=True,
            backend=None,
            safe_error_class=None,
            safe_message="no action selected",
            private_request=None,
            completed_at_s=time.time(),
        )
    descriptor = descriptor_for_action(action_type)
    if descriptor is None:
        return DispatchResult(
            action_id=action_id,
            dispatched=False,
            accepted=False,
            backend=None,
            safe_error_class=SafeErrorClass.PERMISSION_ERROR,
            safe_message="action type is not bound to a task-selected RAN API",
            private_request=None,
            completed_at_s=time.time(),
        )
    backend_ready = bool(runtime.state.get("backend", {}).get(descriptor.backend.value))
    if not runtime.ready or not backend_ready:
        return DispatchResult(
            action_id=action_id,
            dispatched=False,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message="selected runtime backend is unavailable",
            private_request=None,
            completed_at_s=time.time(),
        )
    if runtime.runtime_adapter == LIVE_OCUDU_ADAPTER and action_type in _WS_BACKED_ACTIONS:
        return _dispatch_live_ocudu_action(
            runtime=runtime,
            action_id=action_id,
            action_type=action_type,
            action=action,
            descriptor=descriptor,
        )
    if runtime.runtime_adapter == LIVE_OCUDU_ADAPTER and action_type in _CLI_BACKED_ACTIONS:
        return _dispatch_live_ocudu_cli_action(
            runtime=runtime,
            action_id=action_id,
            action_type=action_type,
            action=action,
            descriptor=descriptor,
        )
    if runtime.runtime_adapter == LIVE_CORE_ADAPTER and action_type in {
        RanActionType.RESTART_CORE_NF,
        RanActionType.UPDATE_CORE_UE_REGISTRATION,
    }:
        return _dispatch_live_core_action(
            runtime=runtime,
            action_id=action_id,
            action_type=action_type,
            action=action,
            descriptor=descriptor,
        )
    if runtime.runtime_adapter == LIVE_E2_ADAPTER and action_type == RanActionType.SET_PRB_POLICY_RATIO_RC_DU:
        return _dispatch_live_e2_rc_du_action(
            runtime=runtime,
            action_id=action_id,
            action_type=action_type,
            action=action,
            descriptor=descriptor,
        )
    if runtime.runtime_adapter == LIVE_E2_ADAPTER and action_type == RanActionType.SET_PRB_POLICY_RATIO_CCC:
        return _dispatch_live_e2_ccc_action(
            runtime=runtime,
            action_id=action_id,
            action_type=action_type,
            action=action,
            descriptor=descriptor,
        )
    request = build_request(action_type, action)
    simulated = apply_simulated_action(
        runtime,
        action_id=action_id,
        action_type=action_type,
        action=action,
        backend=descriptor.backend.value,
    )
    return DispatchResult(
        action_id=action_id,
        dispatched=True,
        accepted=simulated.accepted,
        backend=descriptor.backend.value,
        safe_error_class=simulated.safe_error_class,
        safe_message=simulated.safe_message,
        private_request=request,
        completed_at_s=time.time(),
    )


# Only the OCUDU WebSocket remote_server (apps/services/remote_control/remote_server.cpp)
# registers these two cmd handlers. The other gnb commands (`ho`, `cho`, `cfo`,
# `tx_time_offset`) are stdin-only `cmdline_command` subclasses — the WS dispatcher
# returns "Unknown command type" if asked. Live smoke 2026-05-25 confirmed this on
# 5090pc against ocudu/gnb:latest (commit 2563975).
_WS_BACKED_ACTIONS: frozenset[RanActionType] = frozenset({
    RanActionType.SET_PRB_POLICY_RATIO_WS,
    RanActionType.SET_SSB_BLOCK_POWER_WS,
})

# OCUDU stdin-CLI cmdline_command actions (registered in *_cmdline_commands.h,
# NOT in the remote_control WS dispatcher). Routed through live_ocudu.send_cli
# which writes the CLI line into the gnb's stdin FIFO via `docker exec` and
# reads docker logs for an "Invalid ..." error response.
_CLI_BACKED_ACTIONS: frozenset[RanActionType] = frozenset({
    RanActionType.TRIGGER_HANDOVER_CLI,
    RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI,
    RanActionType.SET_CFO_CLI,
    RanActionType.SET_TX_TIME_OFFSET_CLI,
})


def _live_ocudu_cfg_from_runtime(runtime: RuntimeHandle) -> "live_ocudu.LiveOcuduConfig":
    """Build a LiveOcuduConfig from a RuntimeHandle. Mirrors
    _live_core_cfg_from_runtime: reads runtime.state['live_ocudu_cfg'] if
    present, else defaults. (Non-default plumbing through RuntimeHandle is
    a follow-up.)"""
    from benchmark_api import live_ocudu
    block = runtime.state.get("live_ocudu_cfg") or {}
    return live_ocudu.LiveOcuduConfig(
        ws_url=str(block.get("ws_url", "ws://127.0.0.1:8001/")),
        connect_timeout_s=float(block.get("connect_timeout_s", 5.0)),
        recv_timeout_s=float(block.get("recv_timeout_s", 3.0)),
        container_name=str(block.get("container_name", "ocudu-gnb")),
        stdin_file_path=str(block.get("stdin_file_path", "/tmp/gnb_stdin")),
        cli_response_wait_s=float(block.get("cli_response_wait_s", 1.5)),
        cli_subprocess_timeout_s=float(block.get("cli_subprocess_timeout_s", 10.0)),
    )


def _live_e2_cfg_from_runtime(runtime: RuntimeHandle) -> "live_e2.LiveE2Config":
    """Build a LiveE2Config from a RuntimeHandle. Reads runtime.state['live_e2_cfg']
    if present, else uses defaults. Non-default plumbing through RuntimeHandle is
    the same follow-up flagged for live_core and live_ocudu."""
    from benchmark_api import live_e2
    block = runtime.state.get("live_e2_cfg") or {}
    return live_e2.LiveE2Config(
        container_name=str(block.get("container_name", "flexric-ric")),
        jsonl_path=str(block.get("jsonl_path", "/var/log/flexric/kpm.jsonl")),
        subprocess_timeout_s=float(block.get("subprocess_timeout_s", 10.0)),
        min_records_for_ready=int(block.get("min_records_for_ready", 1)),
        rc_du_xapp_path=str(block.get(
            "rc_du_xapp_path",
            "/opt/flexric/build/examples/xApp/c/control/ocudu-rc-du-prb-control",
        )),
        rc_du_xapp_conf=str(block.get("rc_du_xapp_conf", "/etc/xapp/xapp_oran_sm.conf")),
        rc_du_xapp_timeout_s=float(block.get("rc_du_xapp_timeout_s", 30.0)),
        ccc_xapp_path=str(block.get(
            "ccc_xapp_path",
            "/opt/flexric/build/examples/xApp/c/control/ocudu-ccc-prb-control",
        )),
        ccc_xapp_conf=str(block.get("ccc_xapp_conf", "/etc/xapp/xapp_oran_sm.conf")),
        ccc_xapp_timeout_s=float(block.get("ccc_xapp_timeout_s", 30.0)),
    )


def _dispatch_live_ocudu_action(
    runtime: RuntimeHandle,
    *,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    descriptor: Any,
) -> DispatchResult:
    """Send a WS-backed action through live_ocudu.send_command and map the
    result back into a DispatchResult.

    Catches live_ocudu.LiveOcuduError -> RUNTIME_UNAVAILABLE. Other unexpected
    exceptions propagate."""
    from benchmark_api import live_ocudu
    cfg = _live_ocudu_cfg_from_runtime(runtime)
    request = build_request(action_type, action)
    try:
        ack = live_ocudu.send_command(cfg, request)
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=True,
            backend=descriptor.backend.value,
            safe_error_class=None,
            safe_message=f"{action_type.value} accepted at {ack.get('timestamp', '?')}",
            private_request=request,
            completed_at_s=time.time(),
        )
    except live_ocudu.LiveOcuduError as exc:
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message=exc.safe_message,
            private_request=request,
            completed_at_s=time.time(),
        )


def _dispatch_live_ocudu_cli_action(
    runtime: RuntimeHandle,
    *,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    descriptor: Any,
) -> DispatchResult:
    """Route a CLI-backed action through live_ocudu.dispatch_cli_action.

    Writes the command line to the gnb's stdin FIFO via docker exec and
    checks docker logs for an "Invalid ..." response.

    LiveOcuduError -> RUNTIME_UNAVAILABLE. Other exceptions propagate.
    """
    from benchmark_api import live_ocudu
    cfg = _live_ocudu_cfg_from_runtime(runtime)
    request = build_request(action_type, action)
    try:
        result = live_ocudu.dispatch_cli_action(cfg, action_type.value, action)
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=True,
            backend=descriptor.backend.value,
            safe_error_class=None,
            safe_message=f"{action_type.value} sent via CLI: {result.get('line', '')}",
            private_request=request,
            completed_at_s=time.time(),
        )
    except live_ocudu.LiveOcuduError as exc:
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message=exc.safe_message,
            private_request=request,
            completed_at_s=time.time(),
        )


def _live_core_cfg_from_runtime(runtime: RuntimeHandle) -> "live_core.LiveCoreConfig":
    """Build a LiveCoreConfig from a RuntimeHandle's setup_metadata.

    Reads state['live_core_cfg'] if present; otherwise constructs defaults.
    Non-default cfg values (compose_project / compose_file / mongo_uri overrides
    from task setup) are not currently threaded through RuntimeHandle.setup_metadata
    — that plumbing is a follow-up task."""
    from benchmark_api import live_core
    block = runtime.state.get("live_core_cfg") or {}
    return live_core.LiveCoreConfig(
        compose_project=str(block.get("compose_project", "open5gs")),
        compose_file=block.get("compose_file"),
        mongo_uri=str(block.get("mongo_uri", "mongodb://127.0.0.1:27017/")),
        mongo_db=str(block.get("mongo_db", "open5gs")),
        timeout_s=float(block.get("timeout_s", 30.0)),
    )


def _refresh_live_core_runtime(runtime: RuntimeHandle) -> dict[str, Any]:
    """Re-read live core state. Raises any underlying exception to the caller."""
    from benchmark_api import live_core
    return live_core.read_runtime(_live_core_cfg_from_runtime(runtime))


def _dispatch_live_core_action(
    runtime: RuntimeHandle,
    *,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    descriptor: Any,
) -> DispatchResult:
    """Execute a core action against the live stack via live_core module.

    Updates runtime.state["core_runtime"] in-place to reflect the change
    (restart_counts, last_restarted_nf, ue_registration). Catches
    live_core.LiveCoreError and returns a safe DispatchResult with
    RUNTIME_UNAVAILABLE. Other unexpected exceptions propagate."""
    from benchmark_api import live_core
    cfg = _live_core_cfg_from_runtime(runtime)
    completed_at_s_now = time.time
    request = build_request(action_type, action)
    try:
        if action_type == RanActionType.RESTART_CORE_NF:
            nf = str(action["nf"])
            live_core.restart_nf(cfg, nf)
            core_state = runtime.state.setdefault("core_runtime", {})
            counts = dict(core_state.get("restart_counts", {}))
            counts[nf] = int(counts.get(nf, 0)) + 1
            core_state["restart_counts"] = counts
            core_state["last_restarted_nf"] = nf
            safe_message = f"restarted nf={nf} (count={counts[nf]})"
        elif action_type == RanActionType.UPDATE_CORE_UE_REGISTRATION:
            registration = {
                "ue_id": action["ue_id"],
                "supi": action["supi"],
                "plmn": action["plmn"],
                "dnn": action["dnn"],
                "sst": action["sst"],
                "sd": action.get("sd"),
                "auth_profile_id": action["auth_profile_id"],
            }
            result = live_core.upsert_subscriber(cfg, registration)
            core_state = runtime.state.setdefault("core_runtime", {})
            from benchmark_api.runtime_setup import core_ue_registration_state
            new_state = core_ue_registration_state(
                desired=registration,
                current=registration,  # write-through; we just upserted
                last_updated_by="live_core",
            )
            core_state["ue_registration"] = new_state
            safe_message = f"upserted subscriber imsi={result.get('imsi')}"
        else:
            # Unreachable — caller already filtered
            raise AssertionError(f"unsupported live_core action: {action_type}")
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=True,
            backend=descriptor.backend.value,
            safe_error_class=None,
            safe_message=safe_message,
            private_request=request,
            completed_at_s=completed_at_s_now(),
        )
    except live_core.LiveCoreError as exc:
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message=exc.safe_message,
            private_request=request,
            completed_at_s=completed_at_s_now(),
        )


def _dispatch_live_e2_rc_du_action(
    runtime: RuntimeHandle,
    *,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    descriptor: Any,
) -> DispatchResult:
    """Route SET_PRB_POLICY_RATIO_RC_DU through live_e2.dispatch_rc_du_prb_policy.

    Maps the xApp's structured JSON to a DispatchResult:
      - accepted=true  -> dispatched=True, accepted=True
      - accepted=false -> dispatched=True, accepted=False, RUNTIME_UNAVAILABLE
    LiveE2Error -> dispatched=True, accepted=False, RUNTIME_UNAVAILABLE.
    """
    from benchmark_api import live_e2
    cfg = _live_e2_cfg_from_runtime(runtime)
    request = build_request(action_type, action)
    try:
        result = live_e2.dispatch_rc_du_prb_policy(
            cfg,
            du_ue_id=int(action["du_ue_id"]),
            plmn=str(action.get("plmn", "00101")),
            sst=int(action.get("sst", 1)),
            sd=action.get("sd"),
            min_prb_policy_ratio=action.get("min_prb_policy_ratio"),
            max_prb_policy_ratio=action.get("max_prb_policy_ratio"),
        )
    except live_e2.LiveE2Error as exc:
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message=exc.safe_message,
            private_request=request,
            completed_at_s=time.time(),
        )

    accepted = bool(result.get("accepted"))
    if accepted:
        outcome = result.get("outcome", {})
        evidence = outcome.get("evidence", "")
        safe_message = f"SET_PRB_POLICY_RATIO_RC_DU acknowledged: {evidence}"
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=True,
            backend=descriptor.backend.value,
            safe_error_class=None,
            safe_message=safe_message,
            private_request=request,
            completed_at_s=time.time(),
        )
    error = result.get("error", "control rejected")
    return DispatchResult(
        action_id=action_id,
        dispatched=True,
        accepted=False,
        backend=descriptor.backend.value,
        safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
        safe_message=f"SET_PRB_POLICY_RATIO_RC_DU rejected: {error}",
        private_request=request,
        completed_at_s=time.time(),
    )


def _dispatch_live_e2_ccc_action(
    runtime: RuntimeHandle,
    *,
    action_id: str,
    action_type: RanActionType,
    action: dict[str, Any],
    descriptor: Any,
) -> DispatchResult:
    """Route SET_PRB_POLICY_RATIO_CCC through live_e2.dispatch_ccc_prb_policy.

    CCC is cell-level (no du_ue_id). Mapping rules mirror the RC-DU branch:
      - xApp accepted=true  -> dispatched=True, accepted=True
      - xApp accepted=false -> dispatched=True, accepted=False, RUNTIME_UNAVAILABLE
    LiveE2Error -> dispatched=True, accepted=False, RUNTIME_UNAVAILABLE.
    """
    from benchmark_api import live_e2
    cfg = _live_e2_cfg_from_runtime(runtime)
    request = build_request(action_type, action)
    try:
        result = live_e2.dispatch_ccc_prb_policy(
            cfg,
            plmn=str(action.get("plmn", "00101")),
            sst=int(action.get("sst", 1)),
            sd=action.get("sd"),
            min_prb_policy_ratio=action.get("min_prb_policy_ratio"),
            max_prb_policy_ratio=action.get("max_prb_policy_ratio"),
            dedicated_ratio=action.get("dedicated_ratio"),
        )
    except live_e2.LiveE2Error as exc:
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=False,
            backend=descriptor.backend.value,
            safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
            safe_message=exc.safe_message,
            private_request=request,
            completed_at_s=time.time(),
        )

    accepted = bool(result.get("accepted"))
    if accepted:
        outcome = result.get("outcome", {})
        evidence = outcome.get("evidence", "")
        return DispatchResult(
            action_id=action_id,
            dispatched=True,
            accepted=True,
            backend=descriptor.backend.value,
            safe_error_class=None,
            safe_message=f"SET_PRB_POLICY_RATIO_CCC acknowledged: {evidence}",
            private_request=request,
            completed_at_s=time.time(),
        )
    error = result.get("error", "control rejected")
    return DispatchResult(
        action_id=action_id,
        dispatched=True,
        accepted=False,
        backend=descriptor.backend.value,
        safe_error_class=SafeErrorClass.RUNTIME_UNAVAILABLE,
        safe_message=f"SET_PRB_POLICY_RATIO_CCC rejected: {error}",
        private_request=request,
        completed_at_s=time.time(),
    )


def build_request(action_type: RanActionType, action: dict[str, Any]) -> dict[str, Any]:
    if action_type in {
        RanActionType.SET_PRB_POLICY_RATIO_WS,
        RanActionType.SET_PRB_POLICY_RATIO_CCC,
        RanActionType.SET_PRB_POLICY_RATIO_RC_DU,
    }:
        request = {
            "cmd": (
                "rrm_policy_ratio_set"
                if action_type == RanActionType.SET_PRB_POLICY_RATIO_WS
                else (
                    "E2SM-CCC O-RRMPolicyRatio"
                    if action_type == RanActionType.SET_PRB_POLICY_RATIO_CCC
                    else "E2SM-RC DU style 2 action 6"
                )
            ),
            "policies": {
                "resourceType": "PRB",
                "rRMPolicyMemberList": [
                    {
                        "plmn": action.get("plmn", "00101"),
                        "sst": action.get("sst", 1),
                    }
                ],
                "min_prb_policy_ratio": action["min_prb_policy_ratio"],
                "max_prb_policy_ratio": action["max_prb_policy_ratio"],
            },
        }
        if action.get("sd") is not None:
            request["policies"]["rRMPolicyMemberList"][0]["sd"] = action["sd"]
        if action.get("dedicated_ratio") is not None:
            request["policies"]["dedicated_ratio"] = action["dedicated_ratio"]
        if action_type == RanActionType.SET_PRB_POLICY_RATIO_RC_DU and action.get("du_ue_id") is not None:
            request["du_ue_id"] = action["du_ue_id"]
        return request
    if action_type == RanActionType.SET_SSB_BLOCK_POWER_WS:
        return {
            "cmd": "ssb_set",
            "cells": [
                {
                    "plmn": action.get("plmn", "00101"),
                    "nci": action["nci"],
                    "ssb_block_power_dbm": action["ssb_block_power_dbm"],
                }
            ],
        }
    if action_type == RanActionType.TRIGGER_HANDOVER_CLI:
        return {
            "cmd": "ho",
            "argv": [action["serving_pci"], action["rnti"], action["target_pci"]],
        }
    if action_type == RanActionType.TRIGGER_CONDITIONAL_HANDOVER_CLI:
        argv: list[Any] = [action["serving_pci"], action["rnti"], *action["target_pcis"]]
        if action.get("timeout_s") is not None:
            argv.extend(["timeout", action["timeout_s"]])
        return {
            "cmd": "cho",
            "argv": argv,
        }
    if action_type == RanActionType.SET_CFO_CLI:
        return {
            "cmd": "cfo",
            "argv": [action["sector_id"], action["cfo_hz"]],
        }
    if action_type == RanActionType.SET_TX_TIME_OFFSET_CLI:
        return {
            "cmd": "tx_time_offset",
            "argv": [action["sector_id"], action["tx_time_offset_us"]],
        }
    if action_type == RanActionType.RESTART_CORE_NF:
        return {
            "cmd": "benchmark_core_nf_restart",
            "nf": action["nf"],
        }
    if action_type == RanActionType.UPDATE_CORE_UE_REGISTRATION:
        return {
            "cmd": "benchmark_core_ue_registration_update",
            "ue_id": action["ue_id"],
            "registration": {
                "supi": action["supi"],
                "plmn": action["plmn"],
                "dnn": action["dnn"],
                "sst": action["sst"],
                "sd": action["sd"],
                "auth_profile_id": action["auth_profile_id"],
            },
        }
    raise ValueError(f"Unsupported action type: {action_type.value}")
