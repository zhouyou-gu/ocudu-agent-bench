"""V3 WebSocket PRB-control episode runtime."""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.ric import (
    E2_PCAP_CONTAINER_PREFIX,
    FLEXRIC_CONTAINER_PREFIX,
    FLEXRIC_IMAGE,
    KPM_XAPP_CONTAINER_PREFIX,
    RIC_PORT,
    RIC_PROVIDER_FLEXRIC,
)
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager
from benchmark.benchmark_api.tasks import (
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_E2_KPM_JSON_CONSISTENCY_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
    TASK_WS_PRB_ACTION_BUDGET_V1,
    TASK_WS_PRB_ERROR_REPAIR_V1,
    TASK_WS_PRB_NOOP_GUARD_V1,
    TASK_WS_PRB_PING_V1,
    episode_stage_for_task,
    is_implemented_episode_task,
)
from benchmark.benchmark_api.websocket_client import WebSocketClient, WebSocketFrame, WebSocketProtocolError


DEFAULT_EPISODE_DURATION = 30
DEFAULT_WS_PORT = 8001
DEFAULT_ATTACH_TIMEOUT = 90
DEFAULT_LAUNCH_TIMEOUT = 60
DEFAULT_PROBE_TIMEOUT = 5
PING_TARGET = "10.45.1.1"
STALE_METRICS_OBSERVATIONS = 2
ACTION_SET_PRB_POLICY_RATIO_WS = "SET_PRB_POLICY_RATIO_WS"
ACTION_SET_PRB_POLICY_RATIO_CCC = "SET_PRB_POLICY_RATIO_CCC"
ACTION_SET_PRB_POLICY_RATIO_RC_DU = "SET_PRB_POLICY_RATIO_RC_DU"
E2_CCC_CONTROL_TOOL = "ocudu-ccc-prb-control"
E2_RC_DU_CONTROL_TOOL = "ocudu-rc-du-prb-control"


@dataclass(frozen=True)
class EpisodeTaskPolicy:
    task_id: str
    scenario_name: str
    runtime_family: str
    requires_e2: bool = False
    default_smoke_agent: str = "fixed_prb"
    expected_behavior: str = "one accepted valid PRB action"
    requires_valid_action: bool = True
    max_actions: int | None = None
    require_no_actions: bool = False
    require_first_invalid_then_valid: bool = False
    require_evidence_before_action: bool = False
    stale_metrics_observations: int = 0
    allowed_action_types: tuple[str, ...] = (ACTION_SET_PRB_POLICY_RATIO_WS,)
    expected_action_type: str | None = ACTION_SET_PRB_POLICY_RATIO_WS
    requires_e2_control: bool = False
    requires_ue_identity: bool = False


TASK_POLICIES: dict[str, EpisodeTaskPolicy] = {
    TASK_WS_PRB_PING_V1: EpisodeTaskPolicy(
        task_id=TASK_WS_PRB_PING_V1,
        scenario_name="healthy_ws_prb_ping",
        runtime_family="docker_e2e",
        default_smoke_agent="fixed_prb",
        expected_behavior="issue one accepted valid WebSocket PRB action while ping and metrics remain healthy",
    ),
    TASK_E2_KPM_PRB_PING_V1: EpisodeTaskPolicy(
        task_id=TASK_E2_KPM_PRB_PING_V1,
        scenario_name="healthy_e2_kpm_prb_ping",
        runtime_family="docker_e2e_flexric",
        requires_e2=True,
        default_smoke_agent="fixed_prb",
        expected_behavior="issue one accepted valid WebSocket PRB action while decoded E2SM-KPM v05 records remain available",
    ),
    TASK_E2_CCC_PRB_POLICY_PING_V1: EpisodeTaskPolicy(
        task_id=TASK_E2_CCC_PRB_POLICY_PING_V1,
        scenario_name="healthy_e2_ccc_prb_policy_ping",
        runtime_family="docker_e2e_flexric",
        requires_e2=True,
        requires_e2_control=True,
        default_smoke_agent="ccc_prb",
        expected_behavior="issue one accepted E2SM-CCC PRB policy action while ping, JSON metrics, and KPM records remain healthy",
        max_actions=1,
        require_evidence_before_action=True,
        allowed_action_types=(ACTION_SET_PRB_POLICY_RATIO_CCC,),
        expected_action_type=ACTION_SET_PRB_POLICY_RATIO_CCC,
    ),
    TASK_E2_RC_DU_PRB_POLICY_PING_V1: EpisodeTaskPolicy(
        task_id=TASK_E2_RC_DU_PRB_POLICY_PING_V1,
        scenario_name="healthy_e2_rc_du_prb_policy_ping",
        runtime_family="docker_e2e_flexric",
        requires_e2=True,
        requires_e2_control=True,
        requires_ue_identity=True,
        default_smoke_agent="rc_du_prb",
        expected_behavior="wait for UE identity evidence, then issue one accepted E2SM-RC DU PRB quota action",
        max_actions=1,
        require_evidence_before_action=True,
        allowed_action_types=(ACTION_SET_PRB_POLICY_RATIO_RC_DU,),
        expected_action_type=ACTION_SET_PRB_POLICY_RATIO_RC_DU,
    ),
    TASK_WS_PRB_NOOP_GUARD_V1: EpisodeTaskPolicy(
        task_id=TASK_WS_PRB_NOOP_GUARD_V1,
        scenario_name="healthy_noop_guard",
        runtime_family="docker_e2e",
        default_smoke_agent="noop",
        expected_behavior="take no RAN control action when ping and JSON metrics are healthy",
        requires_valid_action=False,
        require_no_actions=True,
    ),
    TASK_WS_PRB_ERROR_REPAIR_V1: EpisodeTaskPolicy(
        task_id=TASK_WS_PRB_ERROR_REPAIR_V1,
        scenario_name="invalid_action_repair",
        runtime_family="docker_e2e",
        default_smoke_agent="invalid_then_fixed",
        expected_behavior="recover from one locally rejected invalid PRB action by issuing one accepted valid PRB action",
        require_first_invalid_then_valid=True,
        max_actions=2,
    ),
    TASK_WS_PRB_ACTION_BUDGET_V1: EpisodeTaskPolicy(
        task_id=TASK_WS_PRB_ACTION_BUDGET_V1,
        scenario_name="single_action_budget",
        runtime_family="docker_e2e",
        default_smoke_agent="fixed_prb",
        expected_behavior="issue at most one accepted valid PRB action and then stop acting",
        max_actions=1,
    ),
    TASK_E2_KPM_JSON_CONSISTENCY_V1: EpisodeTaskPolicy(
        task_id=TASK_E2_KPM_JSON_CONSISTENCY_V1,
        scenario_name="e2_json_evidence_gate",
        runtime_family="docker_e2e_flexric",
        requires_e2=True,
        default_smoke_agent="evidence_gated_prb",
        expected_behavior="wait for JSON metrics and E2 PRB evidence before issuing one accepted valid PRB action",
        max_actions=1,
        require_evidence_before_action=True,
    ),
    TASK_E2_CONTROL_API_CONSISTENCY_V1: EpisodeTaskPolicy(
        task_id=TASK_E2_CONTROL_API_CONSISTENCY_V1,
        scenario_name="e2_control_api_consistency",
        runtime_family="docker_e2e_flexric",
        requires_e2=True,
        requires_e2_control=True,
        default_smoke_agent="e2_control_consistency",
        expected_behavior="choose the cell/slice E2SM-CCC PRB control API rather than DU UE-scoped RC control",
        max_actions=1,
        require_evidence_before_action=True,
        allowed_action_types=(ACTION_SET_PRB_POLICY_RATIO_CCC, ACTION_SET_PRB_POLICY_RATIO_RC_DU),
        expected_action_type=ACTION_SET_PRB_POLICY_RATIO_CCC,
    ),
    TASK_METRICS_STALENESS_NOOP_V1: EpisodeTaskPolicy(
        task_id=TASK_METRICS_STALENESS_NOOP_V1,
        scenario_name="metrics_staleness_guard",
        runtime_family="docker_e2e",
        default_smoke_agent="stale_guard_prb",
        expected_behavior="take no action while metrics are masked stale, then issue at most one accepted valid PRB action after freshness returns",
        max_actions=1,
        require_evidence_before_action=True,
        stale_metrics_observations=STALE_METRICS_OBSERVATIONS,
    ),
}


@dataclass(frozen=True)
class EpisodeOptions:
    run_id: str
    task: str = TASK_WS_PRB_PING_V1
    duration: int = DEFAULT_EPISODE_DURATION
    ws_port: int = DEFAULT_WS_PORT
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT
    attach_timeout: int = DEFAULT_ATTACH_TIMEOUT
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT


def task_policy(task_id: str) -> EpisodeTaskPolicy:
    try:
        return TASK_POLICIES[task_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported episode task: {task_id}") from exc


def default_smoke_agent_for_task(task_id: str) -> str:
    return task_policy(task_id).default_smoke_agent


def default_episode_run_id() -> str:
    return f"ep-{int(time.time())}"


def safe_run_id(value: str) -> str:
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        raise ValueError(f"Invalid episode run_id: {value!r}")
    return value


def container_suffix(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).replace("_", "-").replace(".", "-")


def episode_paths(workspace: str, run_id: str) -> dict[str, str]:
    run_dir = f"{workspace}/runs/{run_id}"
    episode_dir = f"{run_dir}/episode"
    return {
        "run_dir": run_dir,
        "episode_dir": episode_dir,
        "configs_dir": f"{episode_dir}/configs",
        "logs_dir": f"{episode_dir}/logs",
        "gnb_config": f"{episode_dir}/configs/gnb_zmq.yaml",
        "gnb_overlay": f"{episode_dir}/configs/gnb_v3_overlay.yaml",
        "kpm_xapp_config": f"{episode_dir}/configs/xapp_mon_e2sm_kpm.conf",
        "ue_config": f"{episode_dir}/configs/ue_zmq.conf",
        "containers": f"{episode_dir}/pids_or_containers.json",
        "scenario": f"{episode_dir}/scenario.json",
        "actions": f"{episode_dir}/actions.jsonl",
        "observations": f"{episode_dir}/observations.jsonl",
        "metrics_raw": f"{episode_dir}/metrics_raw.jsonl",
        "summary": f"{episode_dir}/summary.json",
        "cleanup": f"{episode_dir}/cleanup.json",
        "gnb_log": f"{episode_dir}/logs/gnb.log",
        "ue_log": f"{episode_dir}/logs/ue.log",
        "ping_log": f"{episode_dir}/logs/ping.log",
        "core_log": f"{episode_dir}/logs/core.log",
        "ric_log": f"{episode_dir}/logs/ric.log",
        "kpm_xapp_log": f"{episode_dir}/logs/kpm_xapp.log",
        "e2_kpm_raw": f"{episode_dir}/e2_kpm_raw.jsonl",
        "e2_control_raw": f"{episode_dir}/e2_control_raw.jsonl",
        "e2_oracle": f"{episode_dir}/e2_oracle.json",
        "e2ap_du_pcap": f"{episode_dir}/logs/e2ap_du.pcap",
        "e2ap_cu_cp_pcap": f"{episode_dir}/logs/e2ap_cu_cp.pcap",
        "e2ap_sctp_pcap": f"{episode_dir}/logs/e2ap_sctp.pcap",
        "e2ap_tcpdump_log": f"{episode_dir}/logs/e2ap_tcpdump.log",
    }


def generate_v3_gnb_overlay(ws_port: int) -> str:
    return (
        "metrics:\n"
        "  enable_json: true\n"
        "  autostart_stdout_metrics: false\n"
        "  layers:\n"
        "    enable_app_usage: true\n"
        "  periodicity:\n"
        "    app_usage_report_period: 1000\n"
        "remote_control:\n"
        "  enabled: true\n"
        "  bind_addr: 127.0.0.1\n"
        f"  port: {ws_port}\n"
        "log:\n"
        "  filename: /stage/logs/gnb_internal.log\n"
        "  all_level: info\n"
    )


def generate_v4_e2_gnb_overlay(ws_port: int) -> str:
    e2_addr, e2_port = "127.0.0.1", RIC_PORT
    bind_addr = "127.0.0.1"
    return (
        generate_v3_gnb_overlay(ws_port)
        + "e2:\n"
        + "  enable_du_e2: true\n"
        + "  enable_cu_cp_e2: true\n"
        + f"  addr: {e2_addr}\n"
        + f"  port: {e2_port}\n"
        + f"  bind_addr: {bind_addr}\n"
        + "  e2sm_kpm_enabled: true\n"
        + "  e2sm_rc_enabled: true\n"
        + "  e2sm_ccc_enabled: true\n"
        + "pcap:\n"
        + "  e2ap_enable: true\n"
        + "  e2ap_du_filename: /stage/logs/e2ap_du.pcap\n"
        + "  e2ap_cu_cp_filename: /stage/logs/e2ap_cu_cp.pcap\n"
    )


def generate_kpm_xapp_config() -> str:
    return (
        'SM_DIR = "/usr/local/lib/flexric/"\n'
        "\n"
        'Name = "xApp"\n'
        'NearRT_RIC_IP = "127.0.0.1"\n'
        "E42_Port = 36422\n"
        "\n"
        "Sub_ORAN_SM_List = (\n"
        "    { name = \"KPM\", time = 1000,\n"
        "      format = 1,\n"
        '      ran_type = "ngran_gNB_DU",\n'
        "      actions = (\n"
        '            { name = "RRU.PrbUsedDl" },\n'
        '            { name = "RRU.PrbUsedUl" },\n'
        '            { name = "RRU.PrbTotDl" },\n'
        '            { name = "RRU.PrbTotUl" }\n'
        "            )\n"
        "    }\n"
        ")\n"
        "\n"
        "xApp_DB = {\n"
        '    enable = "OFF"\n'
        '    ip = "127.0.0.1"\n'
        '    dir = "/tmp/"\n'
        '    filename = "testdb"\n'
        '    username = "your_username"\n'
        '    password = "your_passwd"\n'
        "}\n"
    )


def parse_kpm_indication_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for index, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\bKPM v\d+\s+ind_msg\b", line, flags=re.IGNORECASE):
            if current is not None:
                records.append(current)
            current = {"line_no": index, "text": line[-1000:], "measurements": []}
        elif current is not None and re.search(r"\bmeas record\b", line, flags=re.IGNORECASE):
            current["measurements"].append({"line_no": index, "text": line[-1000:]})
    if current is not None:
        records.append(current)
    return records


def kpm_record_has_prb_measurement(record: dict[str, Any]) -> bool:
    measurements = record.get("measurements")
    if not isinstance(measurements, list):
        return False
    for measurement in measurements:
        if isinstance(measurement, dict):
            haystack = " ".join(str(measurement.get(key, "")) for key in ("name", "text")).lower()
        else:
            haystack = str(measurement).lower()
        if "prb" in haystack:
            return True
    return False


def parse_ping_log(text: str) -> dict[str, Any]:
    replies = len(re.findall(r"bytes from ", text))
    transmitted = replies
    received = replies
    loss_percent: float | None = None
    summary = re.search(r"(\d+) packets transmitted, (\d+) received,.*?(\d+(?:\.\d+)?)% packet loss", text)
    if summary:
        transmitted = int(summary.group(1))
        received = int(summary.group(2))
        loss_percent = float(summary.group(3))
    return {
        "packets_transmitted": transmitted,
        "packets_received": received,
        "reply_lines": replies,
        "loss_percent": loss_percent,
        "success_ratio": (received / transmitted) if transmitted else (1.0 if replies else 0.0),
    }


def normalize_metrics_frame(frame: Any) -> dict[str, Any]:
    if not isinstance(frame, dict):
        return {"present": False, "component_keys": [], "timestamp": None, "raw": frame}
    component_keys = sorted(key for key in frame if key != "timestamp")
    return {
        "present": bool(component_keys),
        "component_keys": component_keys,
        "timestamp": frame.get("timestamp"),
        "raw": frame,
    }


def scenario_metadata(task_id: str, duration: int) -> dict[str, Any]:
    policy = task_policy(task_id)
    return {
        "task": task_id,
        "scenario_name": policy.scenario_name,
        "runtime_family": policy.runtime_family,
        "duration": duration,
        "seed_policy": "suite seed controls built-in baseline behavior; episode scenario is deterministic",
        "ue_count": 1,
        "traffic": {"type": "ping", "target": PING_TARGET, "interval_seconds": 0.2},
        "labels": {
            "healthy_ping": True,
            "json_metrics_expected": True,
            "e2_kpm_expected": policy.requires_e2,
            "e2_control_expected": policy.requires_e2_control,
            "ue_identity_expected": policy.requires_ue_identity,
            "stale_metrics_observations": policy.stale_metrics_observations,
        },
        "expected_behavior": policy.expected_behavior,
        "scoring_contract": {
            "requires_valid_action": policy.requires_valid_action,
            "require_no_actions": policy.require_no_actions,
            "max_actions": policy.max_actions,
            "require_first_invalid_then_valid": policy.require_first_invalid_then_valid,
            "require_evidence_before_action": policy.require_evidence_before_action,
            "requires_e2": policy.requires_e2,
            "requires_e2_control": policy.requires_e2_control,
            "requires_ue_identity": policy.requires_ue_identity,
            "allowed_action_types": list(policy.allowed_action_types),
            "expected_action_type": policy.expected_action_type,
        },
    }


def validate_prb_action(
    action: Any,
    allowed_types: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {"valid": False, "reason": "action must be a dictionary", "normalized": None, "request": None}
    allowed = allowed_types or (ACTION_SET_PRB_POLICY_RATIO_WS,)
    action_type = action.get("type")
    if action_type not in allowed:
        return {
            "valid": False,
            "reason": "unsupported action type for this task",
            "normalized": None,
            "request": None,
            "dispatch": None,
        }

    normalized: dict[str, Any] = {
        "type": str(action_type),
        "plmn": str(action.get("plmn", "00101")),
        "sst": action.get("sst", 1),
        "sd": action.get("sd"),
        "min_prb_policy_ratio": action.get("min_prb_policy_ratio"),
        "max_prb_policy_ratio": action.get("max_prb_policy_ratio"),
        "dedicated_ratio": action.get("dedicated_ratio"),
        "du_ue_id": action.get("du_ue_id"),
    }
    for field in ["sst", "min_prb_policy_ratio", "max_prb_policy_ratio"]:
        value = normalized[field]
        if isinstance(value, bool) or not isinstance(value, int):
            return {"valid": False, "reason": f"{field} must be an integer", "normalized": normalized, "request": None}
    for field in ["min_prb_policy_ratio", "max_prb_policy_ratio"]:
        value = normalized[field]
        if value < 0 or value > 100:
            return {"valid": False, "reason": f"{field} must be in [0, 100]", "normalized": normalized, "request": None}
    if normalized["min_prb_policy_ratio"] > normalized["max_prb_policy_ratio"]:
        return {
            "valid": False,
            "reason": "min_prb_policy_ratio must be <= max_prb_policy_ratio",
            "normalized": normalized,
            "request": None,
        }
    if normalized["dedicated_ratio"] is not None:
        value = normalized["dedicated_ratio"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
            return {"valid": False, "reason": "dedicated_ratio must be an integer in [0, 100]", "normalized": normalized, "request": None}
    if normalized["sd"] is not None:
        value = normalized["sd"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 0xFFFFFF:
            return {"valid": False, "reason": "sd must be an integer in [0, 16777215]", "normalized": normalized, "request": None}
    if normalized["du_ue_id"] is not None:
        value = normalized["du_ue_id"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return {"valid": False, "reason": "du_ue_id must be a non-negative integer", "normalized": normalized, "request": None}

    request = build_prb_request(normalized)
    return {
        "valid": True,
        "reason": "valid",
        "normalized": normalized,
        "request": request,
        "dispatch": dispatch_for_action_type(normalized["type"]),
    }


def build_prb_request(action: dict[str, Any]) -> dict[str, Any]:
    action_type = action.get("type", ACTION_SET_PRB_POLICY_RATIO_WS)
    member = {"plmn": action["plmn"], "sst": action["sst"]}
    if action.get("sd") is not None:
        member["sd"] = action["sd"]
    policies = {
        "resourceType": "PRB",
        "rRMPolicyMemberList": [member],
        "min_prb_policy_ratio": action["min_prb_policy_ratio"],
        "max_prb_policy_ratio": action["max_prb_policy_ratio"],
    }
    if action.get("dedicated_ratio") is not None:
        policies["dedicated_ratio"] = action["dedicated_ratio"]
    if action_type == ACTION_SET_PRB_POLICY_RATIO_WS:
        return {"cmd": "rrm_policy_ratio_set", "policies": policies}
    if action_type == ACTION_SET_PRB_POLICY_RATIO_CCC:
        return {
            "interface": "e2sm_ccc",
            "control": "O-RRMPolicyRatio",
            "policies": policies,
            "tool": E2_CCC_CONTROL_TOOL,
        }
    if action_type == ACTION_SET_PRB_POLICY_RATIO_RC_DU:
        request: dict[str, Any] = {
            "interface": "e2sm_rc",
            "ran_function": "RC",
            "control_style": 2,
            "control_action": 6,
            "control": "slice-level PRB quota",
            "policies": policies,
            "tool": E2_RC_DU_CONTROL_TOOL,
        }
        if action.get("du_ue_id") is not None:
            request["du_ue_id"] = action["du_ue_id"]
        return request
    raise ValueError(f"Unsupported PRB action type: {action_type}")


def dispatch_for_action_type(action_type: str) -> str:
    if action_type == ACTION_SET_PRB_POLICY_RATIO_WS:
        return "websocket"
    if action_type == ACTION_SET_PRB_POLICY_RATIO_CCC:
        return "e2_ccc"
    if action_type == ACTION_SET_PRB_POLICY_RATIO_RC_DU:
        return "e2_rc_du"
    return "unsupported"


def validate_episode_action(action: Any, task: str) -> dict[str, Any]:
    return validate_prb_action(action, allowed_types=task_policy(task).allowed_action_types)


def fixed_prb_action_for_type(action_type: str = ACTION_SET_PRB_POLICY_RATIO_WS) -> dict[str, Any]:
    return {
        "type": action_type,
        "plmn": "00101",
        "sst": 1,
        "sd": 0xFFFFFF,
        "min_prb_policy_ratio": 10,
        "max_prb_policy_ratio": 90,
        "dedicated_ratio": 0,
    }


def score_episode(
    ping: dict[str, Any],
    actions: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    cleanup_success: bool,
    unscored_reason: str | None = None,
    require_e2: bool = False,
    e2_oracle: dict[str, Any] | None = None,
    task: str = TASK_WS_PRB_PING_V1,
) -> dict[str, Any]:
    policy = task_policy(task)
    valid_actions = [item for item in actions if item.get("validation", {}).get("valid")]
    accepted_valid = [item for item in valid_actions if item.get("accepted")]
    def action_record_type(item: dict[str, Any]) -> str | None:
        value = item.get("validation", {}).get("normalized", {}).get("type") or item.get("action", {}).get("type")
        if value is None and policy.expected_action_type == ACTION_SET_PRB_POLICY_RATIO_WS:
            return ACTION_SET_PRB_POLICY_RATIO_WS
        return value
    accepted_expected = [
        item
        for item in accepted_valid
        if policy.expected_action_type is None
        or action_record_type(item) == policy.expected_action_type
    ]
    accepted_e2_control = [
        item
        for item in accepted_valid
        if item.get("validation", {}).get("dispatch") in {"e2_ccc", "e2_rc_du"}
    ]
    invalid_actions = [item for item in actions if not item.get("validation", {}).get("valid")]
    rejected_invalid = [item for item in invalid_actions if not item.get("dispatched")]
    metrics_frames = [
        item
        for item in observations
        if item.get("observation", {}).get("metrics", {}).get("present") or item.get("metrics", {}).get("present")
    ]
    setup_failed = bool(unscored_reason)
    valid_action_requirement_met = bool(accepted_expected) if policy.requires_valid_action else True
    scored = not setup_failed and valid_action_requirement_met and ping.get("packets_received", 0) > 0 and bool(metrics_frames)
    e2_indications = 0
    e2_oracle_available = False
    if e2_oracle:
        e2_indications = int(e2_oracle.get("kpm_indications", 0) or 0)
        e2_oracle_available = bool(e2_oracle.get("oracle_available"))
    e2_control_records = e2_oracle.get("control_records", []) if e2_oracle else []
    if not isinstance(e2_control_records, list):
        e2_control_records = []
    e2_control_oracle_available = bool(e2_oracle.get("control_oracle_available")) if e2_oracle else False

    e2_required = require_e2 or policy.requires_e2
    observations_only = [item.get("observation", item) for item in observations]
    stale_observations = [
        item
        for item in observations_only
        if item.get("metrics", {}).get("stale") or item.get("scenario", {}).get("metrics_stale")
    ]
    fresh_metrics_frames = [
        item
        for item in observations_only
        if item.get("metrics", {}).get("present") and not item.get("metrics", {}).get("stale")
    ]
    actions_during_stale = [
        item
        for item in actions
        if item.get("decision_context", {}).get("metrics", {}).get("stale")
        or item.get("decision_context", {}).get("scenario", {}).get("metrics_stale")
    ]
    decision_context_errors = [item for item in actions if item.get("decision_context_error")]
    decision_context_required = policy.require_evidence_before_action or bool(policy.stale_metrics_observations)
    evidence_gated_actions = [
        item
        for item in accepted_valid
        if item.get("decision_context", {}).get("metrics", {}).get("present")
        and not item.get("decision_context", {}).get("metrics", {}).get("stale")
        and (
            not policy.requires_e2
            or item.get("decision_context", {}).get("e2", {}).get("has_prb_measurement")
            or item.get("decision_context", {}).get("e2", {}).get("oracle_available")
            or int(item.get("decision_context", {}).get("e2", {}).get("kpm_indications", 0) or 0) >= 3
        )
    ]

    task_success = True
    task_failure_reason: str | None = None
    if policy.require_no_actions and actions:
        task_success = False
        task_failure_reason = "agent acted during no-op task"
    elif policy.require_first_invalid_then_valid:
        if len(actions) < 2:
            task_success = False
            task_failure_reason = "expected invalid action followed by valid repair action"
        elif actions[0].get("validation", {}).get("valid"):
            task_success = False
            task_failure_reason = "first action was not locally invalid"
        elif actions[0].get("dispatched"):
            task_success = False
            task_failure_reason = "invalid action was dispatched"
        elif not any(item.get("validation", {}).get("valid") and item.get("accepted") for item in actions[1:]):
            task_success = False
            task_failure_reason = "no later accepted valid repair action"
        elif len(invalid_actions) > 1:
            task_success = False
            task_failure_reason = "more than one invalid action"
    elif policy.requires_valid_action and not accepted_expected:
        task_success = False
        task_failure_reason = "no accepted valid expected action"
    if task_success and policy.max_actions is not None and len(actions) > policy.max_actions:
        task_success = False
        task_failure_reason = "action budget exceeded"
    if task_success and task == TASK_WS_PRB_ACTION_BUDGET_V1 and invalid_actions:
        task_success = False
        task_failure_reason = "invalid action used in action-budget task"
    if task_success and decision_context_required and decision_context_errors:
        task_success = False
        task_failure_reason = "decision context capture failed"
    if task_success and policy.require_evidence_before_action and not evidence_gated_actions:
        task_success = False
        task_failure_reason = "no accepted valid action after required observation evidence"
    if task_success and policy.stale_metrics_observations:
        if len(stale_observations) < policy.stale_metrics_observations:
            task_success = False
            task_failure_reason = "metrics staleness window was not observed"
        elif not fresh_metrics_frames:
            task_success = False
            task_failure_reason = "metrics freshness was not restored"
        elif actions_during_stale:
            task_success = False
            task_failure_reason = "agent acted while metrics were stale"

    if e2_required and (e2_indications < 3 or not e2_oracle_available):
        scored = False
    if policy.requires_e2_control and (not accepted_e2_control or not e2_control_oracle_available):
        scored = False
    if not task_success:
        scored = False
    if not cleanup_success:
        scored = False
        unscored_reason = unscored_reason or "cleanup failed"
    elif not scored:
        if unscored_reason is None and task_failure_reason is not None:
            unscored_reason = task_failure_reason
        elif unscored_reason is None and policy.requires_valid_action and not accepted_expected:
            unscored_reason = "no accepted valid expected action"
        elif unscored_reason is None and ping.get("packets_received", 0) <= 0:
            unscored_reason = "no successful ping replies"
        elif unscored_reason is None and not metrics_frames:
            unscored_reason = "no metrics observations"
        elif unscored_reason is None and e2_required and e2_indications < 3:
            unscored_reason = "insufficient E2 KPM indications"
        elif unscored_reason is None and e2_required and not e2_oracle_available:
            unscored_reason = "E2 oracle unavailable"
        elif unscored_reason is None and policy.requires_e2_control and not accepted_e2_control:
            unscored_reason = "no accepted E2 control action"
        elif unscored_reason is None and policy.requires_e2_control and not e2_control_oracle_available:
            unscored_reason = "E2 control oracle unavailable"
    return {
        "scored": scored,
        "unscored_reason": None if scored else unscored_reason,
        "scores": {
            "valid_action_accepted_rate": (len(accepted_valid) / len(valid_actions)) if valid_actions else 0.0,
            "invalid_local_rejection_correctness": (len(rejected_invalid) / len(invalid_actions)) if invalid_actions else 1.0,
            "ping_success_ratio": ping.get("success_ratio", 0.0),
            "metrics_continuity": len(metrics_frames),
            "e2_kpm_continuity": e2_indications,
            "e2_oracle_available": e2_oracle_available,
            "e2_control_oracle_available": e2_control_oracle_available,
            "expected_action_type_correct": bool(accepted_expected) if policy.requires_valid_action else True,
            "accepted_e2_control_actions": len(accepted_e2_control),
            "clean_teardown": cleanup_success,
            "task_success": task_success,
            "action_budget_ok": policy.max_actions is None or len(actions) <= policy.max_actions,
            "noop_correctness": 1.0 if (not policy.require_no_actions or not actions) else 0.0,
            "evidence_gated_action": bool(evidence_gated_actions) if policy.require_evidence_before_action else True,
            "stale_action_avoidance": 1.0 if not actions_during_stale else 0.0,
        },
        "counts": {
            "actions": len(actions),
            "valid_actions": len(valid_actions),
            "accepted_valid_actions": len(accepted_valid),
            "accepted_expected_actions": len(accepted_expected),
            "accepted_e2_control_actions": len(accepted_e2_control),
            "invalid_actions": len(invalid_actions),
            "locally_rejected_invalid_actions": len(rejected_invalid),
            "observations": len(observations),
            "metrics_frames": len(metrics_frames),
            "fresh_metrics_frames": len(fresh_metrics_frames),
            "stale_metric_observations": len(stale_observations),
            "actions_during_stale_metrics": len(actions_during_stale),
            "evidence_gated_actions": len(evidence_gated_actions),
            "decision_context_errors": len(decision_context_errors),
            "e2_kpm_indications": e2_indications,
            "e2_control_records": len(e2_control_records),
        },
        "ping": ping,
        "e2_oracle": e2_oracle or {},
    }


def episode_exit_code(result: dict[str, Any]) -> int:
    if result.get("status") != "ok":
        return 1
    summary = result.get("summary", result)
    return 0 if summary.get("scored") else 1


class EpisodeRuntime:
    def __init__(self, remote: RemoteManager, repo_root: Path | None = None) -> None:
        self.remote = remote
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.options: EpisodeOptions | None = None
        self.paths: dict[str, str] = {}

    def check_docker_assets(self) -> dict[str, Any]:
        payload = {
            "compose": self.remote.config.runtime.open5gs_compose,
            "config_dir": self.remote.config.runtime.e2e_config_dir,
            "ocudu_root": self.remote.config.ocudu_root,
            "images": [
                self.remote.config.runtime.open5gs_image,
                self.remote.config.runtime.gnb_image,
                self.remote.config.runtime.ue_image,
            ],
            "workspace": self.remote.config.workspace,
        }
        data = self._remote_json(
            f"""
import json
import pathlib
import shutil
import subprocess
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
payload["compose"] = expand_remote_path(payload["compose"])
payload["config_dir"] = expand_remote_path(payload["config_dir"])
payload["ocudu_root"] = expand_remote_path(payload["ocudu_root"])
payload["workspace"] = expand_remote_path(payload["workspace"])
def inside_workspace(value):
    try:
        pathlib.Path(value).resolve().relative_to(pathlib.Path(payload["workspace"]).resolve())
        return True
    except ValueError:
        return False
images = {{}}
for image in payload["images"]:
    proc = subprocess.run(["docker", "image", "inspect", image], check=False, text=True, capture_output=True)
    images[image] = proc.returncode == 0
compose_proc = subprocess.run(["docker", "compose", "version"], check=False, text=True, capture_output=True)
files = {{
    "compose": pathlib.Path(payload["compose"]).is_file(),
    "gnb_config": (pathlib.Path(payload["config_dir"]) / "gnb_zmq.yaml").is_file(),
    "ue_config": (pathlib.Path(payload["config_dir"]) / "ue_zmq.conf").is_file(),
    "gnb_install": (pathlib.Path(payload["ocudu_root"]) / "install" / "ocudu").is_dir(),
    "ue_install": (pathlib.Path(payload["ocudu_root"]) / "install" / "srsran-4g").is_dir(),
}}
workspace_owned = {{
    "compose": inside_workspace(payload["compose"]),
    "config_dir": inside_workspace(payload["config_dir"]),
    "ocudu_root": inside_workspace(payload["ocudu_root"]),
}}
print(json.dumps({{
    "docker": shutil.which("docker") or "",
    "docker_compose": compose_proc.returncode == 0,
    "docker_compose_stdout": compose_proc.stdout.strip(),
    "docker_compose_stderr": compose_proc.stderr.strip(),
    "images": images,
    "files": files,
    "workspace_owned": workspace_owned,
}}))
"""
        )
        missing = []
        if not data.get("docker"):
            missing.append("docker")
        if not data.get("docker_compose"):
            missing.append("docker compose")
        missing.extend(name for name, ok in data.get("images", {}).items() if not ok)
        missing.extend(name for name, ok in data.get("files", {}).items() if not ok)
        missing.extend(f"outside_workspace:{name}" for name, ok in data.get("workspace_owned", {}).items() if not ok)
        return {
            "status": "pass" if not missing else "fail",
            "summary": "Docker e2e assets are available" if not missing else "Missing Docker e2e assets: " + ", ".join(missing),
            "details": data,
        }

    def start(self, options: EpisodeOptions) -> dict[str, Any]:
        if not is_implemented_episode_task(options.task):
            raise ValueError(f"Unsupported episode task: {options.task}")
        safe_run_id(options.run_id)
        self.options = options
        self.paths = episode_paths(self.remote.config.workspace, options.run_id)
        suffix = container_suffix(options.run_id)
        policy = task_policy(options.task)
        is_v4 = policy.requires_e2
        stage = episode_stage_for_task(options.task)
        provider = self.remote.config.ric_provider
        overlay = generate_v4_e2_gnb_overlay(options.ws_port) if is_v4 else generate_v3_gnb_overlay(options.ws_port)
        payload = {
            "run_id": options.run_id,
            "task": options.task,
            "stage": stage,
            "is_v4": is_v4,
            "policy": {
                "scenario_name": policy.scenario_name,
                "runtime_family": policy.runtime_family,
                "requires_e2": policy.requires_e2,
                "requires_e2_control": policy.requires_e2_control,
                "requires_ue_identity": policy.requires_ue_identity,
                "allowed_action_types": list(policy.allowed_action_types),
                "expected_action_type": policy.expected_action_type,
                "stale_metrics_observations": policy.stale_metrics_observations,
            },
            "scenario": scenario_metadata(options.task, options.duration),
            "ric_provider": provider,
            "paths": self.paths,
            "compose": self.remote.config.runtime.open5gs_compose,
            "config_dir": self.remote.config.runtime.e2e_config_dir,
            "ocudu_root": self.remote.config.ocudu_root,
            "open5gs_image": self.remote.config.runtime.open5gs_image,
            "gnb_image": self.remote.config.runtime.gnb_image,
            "ue_image": self.remote.config.runtime.ue_image,
            "flexric_image": FLEXRIC_IMAGE,
            "gnb_container": f"skillful-ran-bench-gnb-{suffix}",
            "ue_container": f"skillful-ran-bench-ue-{suffix}",
            "ric_container": f"{FLEXRIC_CONTAINER_PREFIX}-{suffix}",
            "kpm_xapp_container": f"{KPM_XAPP_CONTAINER_PREFIX}-{suffix}",
            "e2_pcap_container": f"{E2_PCAP_CONTAINER_PREFIX}-{suffix}",
            "ws_port": options.ws_port,
            "ric_port": RIC_PORT,
            "launch_timeout": options.launch_timeout,
            "attach_timeout": options.attach_timeout,
            "probe_timeout": options.probe_timeout,
            "overlay": overlay,
            "kpm_xapp_config": generate_kpm_xapp_config() if is_v4 else "",
        }
        data = self._remote_json(
            f"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})

def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value

paths = {{key: expand_remote_path(value) for key, value in payload["paths"].items()}}
for key in ["compose", "config_dir", "ocudu_root"]:
    payload[key] = expand_remote_path(payload[key])

def run(argv, check=False):
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "command failed: " + " ".join(argv))
    return proc

def fail(stage, summary, details=None):
    print(json.dumps({{"status": "error", "stage": stage, "summary": summary, "details": details or {{}}, "paths": paths}}))
    raise SystemExit(0)

def port_listening(port):
    proc = run(["ss", "-ln"])
    token = ":" + str(port)
    for line in proc.stdout.splitlines()[1:]:
        if any(part.endswith(token) for part in line.split()):
            return True
    return False

def read_tail(path, limit=4000):
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8", errors="replace")[-limit:] if p.exists() else ""

def file_size(path):
    item = pathlib.Path(path)
    return item.stat().st_size if item.exists() else 0

def e2_setup_seen():
    text = (read_tail(paths["ric_log"], 12000) + "\\n" + read_tail(paths["gnb_log"], 12000)).lower()
    return "e2" in text and ("setup" in text or "connected" in text or "ric" in text)

def parse_kpm_records():
    records = []
    raw = pathlib.Path(paths["e2_kpm_raw"])
    if raw.exists():
        for line in raw.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    log = pathlib.Path(paths["kpm_xapp_log"])
    if not log.exists():
        return records
    current = None
    for index, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if re.search(r"\\bKPM v\\d+\\s+ind_msg\\b", line, flags=re.IGNORECASE):
            if current is not None:
                records.append(current)
            current = {{"line_no": index, "text": line[-1000:], "measurements": [], "timestamp": time.time()}}
        elif current is not None and re.search(r"\\bmeas record\\b", line, flags=re.IGNORECASE):
            current["measurements"].append({{"line_no": index, "text": line[-1000:]}})
    if current is not None:
        records.append(current)
    return records

def read_jsonl(path):
    records = []
    item = pathlib.Path(path)
    if not item.exists():
        return records
    for line in item.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records

def flexric_decode_error():
    text = read_tail(paths["ric_log"], 12000)
    if "kpm_dec_ind_msg_asn" in text or "Are you sending data in ATS_ALIGNED_BASIC_PER syntax" in text:
        return "FlexRIC KPM decoder failed while decoding OCUDU KPM indication payload"
    return None

def record_has_prb_measurement(record):
    measurements = record.get("measurements")
    if not isinstance(measurements, list):
        return False
    for measurement in measurements:
        if isinstance(measurement, dict):
            haystack = " ".join(str(measurement.get(key, "")) for key in ("name", "text")).lower()
        else:
            haystack = str(measurement).lower()
        if "prb" in haystack:
            return True
    return False

def write_e2_oracle():
    records = parse_kpm_records()
    with open(paths["e2_kpm_raw"], "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\\n")
    pcap = {{
        "du": file_size(paths["e2ap_du_pcap"]),
        "cu_cp": file_size(paths["e2ap_cu_cp_pcap"]),
        "sctp_capture": file_size(paths["e2ap_sctp_pcap"]),
    }}
    pcap_available = any(size > 0 for size in pcap.values())
    raw_kpm_available = file_size(paths["e2_kpm_raw"]) > 0
    log_available = file_size(paths["gnb_log"]) > 0 and file_size(paths["kpm_xapp_log"]) > 0
    e2_setup = e2_setup_seen()
    decode_error = flexric_decode_error()
    has_prb_measurement = any(record_has_prb_measurement(record) for record in records)
    control_records = read_jsonl(paths["e2_control_raw"])
    accepted_control_records = [record for record in control_records if record.get("accepted")]
    control_types = sorted({{record.get("action", {{}}).get("type") for record in accepted_control_records if isinstance(record.get("action"), dict)}})
    control_oracle_available = bool(accepted_control_records) and e2_setup and pcap_available and log_available
    oracle_available = e2_setup and decode_error is None and len(records) >= 3 and has_prb_measurement and pcap_available
    oracle = {{
        "provider": payload.get("ric_provider"),
        "e2_setup_seen": e2_setup,
        "kpm_indications": len(records),
        "last_kpm": records[-1] if records else None,
        "decode_error": decode_error,
        "has_prb_measurement": has_prb_measurement,
        "pcap_sizes": pcap,
        "pcap_available": pcap_available,
        "log_available": log_available,
        "raw_kpm_available": raw_kpm_available,
        "control_records": control_records,
        "accepted_control_records": len(accepted_control_records),
        "control_types": control_types,
        "control_oracle_available": control_oracle_available,
        "oracle_available": oracle_available,
    }}
    pathlib.Path(paths["e2_oracle"]).write_text(json.dumps(oracle, indent=2, sort_keys=True), encoding="utf-8")
    return oracle

episode_dir = pathlib.Path(paths["episode_dir"])
for key in ["configs_dir", "logs_dir"]:
    pathlib.Path(paths[key]).mkdir(parents=True, exist_ok=True)
for path_key in ["actions", "observations", "metrics_raw", "e2_kpm_raw", "e2_control_raw"]:
    pathlib.Path(paths[path_key]).write_text("", encoding="utf-8")
pathlib.Path(paths["scenario"]).write_text(json.dumps(payload["scenario"], indent=2, sort_keys=True), encoding="utf-8")
shutil.copy2(pathlib.Path(payload["config_dir"]) / "gnb_zmq.yaml", paths["gnb_config"])
shutil.copy2(pathlib.Path(payload["config_dir"]) / "ue_zmq.conf", paths["ue_config"])
pathlib.Path(paths["gnb_overlay"]).write_text(payload["overlay"], encoding="utf-8")
if payload["is_v4"]:
    pathlib.Path(paths["kpm_xapp_config"]).write_text(payload["kpm_xapp_config"], encoding="utf-8")

run(["docker", "rm", "-f", payload["gnb_container"], payload["ue_container"], payload["ric_container"], payload["kpm_xapp_container"], payload["e2_pcap_container"]])

core = run(["docker", "compose", "-f", payload["compose"], "up", "-d"])
pathlib.Path(paths["core_log"]).write_text(core.stdout + core.stderr, encoding="utf-8")
if core.returncode != 0:
    fail("open5gs_core_health", "Open5GS compose up failed", {{"stderr": core.stderr, "stdout": core.stdout}})

deadline = time.monotonic() + int(payload["launch_timeout"])
core_healthy = False
while time.monotonic() < deadline:
    proc = run(["docker", "inspect", "-f", "{{{{.State.Health.Status}}}}", "skillful_ran_5gc"])
    if proc.returncode == 0 and proc.stdout.strip() == "healthy":
        core_healthy = True
        break
    proc_state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", "skillful_ran_5gc"])
    if proc_state.returncode == 0 and proc_state.stdout.strip() == "exited":
        fail("open5gs_core_health", "Open5GS container exited before health", {{"inspect": proc.stdout, "state": proc_state.stdout}})
    time.sleep(1)
if not core_healthy:
    fail("open5gs_core_health", "Open5GS healthcheck timed out")

if payload["is_v4"]:
    capture_cmd = "tcpdump -i lo -s 0 -w /stage/logs/e2ap_sctp.pcap 'sctp and port 36421' >/stage/logs/e2ap_tcpdump.log 2>&1"
    capture = run([
        "docker", "run", "-d", "--name", payload["e2_pcap_container"], "--network", "host",
        "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
        "-v", str(episode_dir) + ":/stage",
        payload["flexric_image"], "bash", "-lc", capture_cmd,
    ])
    if capture.returncode != 0:
        fail("e2_pcap_log_oracle", "E2AP tcpdump sidecar failed to start", {{"stderr": capture.stderr, "stdout": capture.stdout}})
    time.sleep(0.5)
    if port_listening(int(payload["ric_port"])):
        fail("near_rt_ric_health", f"port {{payload['ric_port']}} is already listening")
    ric_cmd = "export FLEXRIC_KPM_V05_JSONL=/stage/e2_kpm_raw.jsonl; flexric-ric >/stage/logs/ric.log 2>&1"
    ric = run([
        "docker", "run", "-d", "--name", payload["ric_container"], "--network", "host",
        "-v", str(episode_dir) + ":/stage",
        payload["flexric_image"], "bash", "-lc", ric_cmd,
    ])
    if ric.returncode != 0:
        fail("near_rt_ric_health", "FlexRIC RIC container failed to start", {{"stderr": ric.stderr, "stdout": ric.stdout}})
    deadline = time.monotonic() + int(payload["launch_timeout"])
    while time.monotonic() < deadline:
        if port_listening(int(payload["ric_port"])):
            break
        state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["ric_container"]])
        if state.returncode == 0 and state.stdout.strip() == "exited":
            fail("near_rt_ric_health", "FlexRIC RIC exited before readiness", {{"tail": read_tail(paths["ric_log"])}})
        time.sleep(0.5)
    else:
        fail("near_rt_ric_health", "FlexRIC RIC readiness timed out", {{"tail": read_tail(paths["ric_log"])}})

if port_listening(int(payload["ws_port"])):
    fail("ocudu_launch", f"port {{payload['ws_port']}} is already listening")

gnb_cmd = (
    "export PATH=/install/bin:$PATH; "
    "export LD_LIBRARY_PATH=/install/lib:${{LD_LIBRARY_PATH:-}}; "
    "gnb -c /config/gnb_zmq.yaml -c /config/gnb_v3_overlay.yaml >/stage/logs/gnb.log 2>&1"
)
gnb = run([
    "docker", "run", "-d", "--name", payload["gnb_container"], "--network", "host",
    "-v", str(pathlib.Path(payload["ocudu_root"]) / "install" / "ocudu") + ":/install:ro",
    "-v", str(pathlib.Path(paths["configs_dir"])) + ":/config:ro",
    "-v", str(episode_dir) + ":/stage",
    payload["gnb_image"], "bash", "-lc", gnb_cmd,
])
if gnb.returncode != 0:
    fail("ocudu_launch", "gNB container failed to start", {{"stderr": gnb.stderr, "stdout": gnb.stdout}})

deadline = time.monotonic() + int(payload["launch_timeout"])
while time.monotonic() < deadline:
    if port_listening(int(payload["ws_port"])):
        break
    state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["gnb_container"]])
    if state.returncode == 0 and state.stdout.strip() == "exited":
        tail = pathlib.Path(paths["gnb_log"]).read_text(encoding="utf-8", errors="replace")[-4000:] if pathlib.Path(paths["gnb_log"]).exists() else ""
        fail("ocudu_launch", "gNB container exited before WebSocket readiness", {{"tail": tail}})
    time.sleep(0.5)
else:
    fail("ocudu_launch", "gNB WebSocket readiness timed out")

if payload["is_v4"]:
    deadline = time.monotonic() + int(payload["launch_timeout"])
    while time.monotonic() < deadline:
        if e2_setup_seen():
            break
        state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["gnb_container"]])
        ric_state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["ric_container"]])
        if state.returncode == 0 and state.stdout.strip() == "exited":
            fail("e2_setup_path", "gNB exited before E2 setup", {{"gnb_tail": read_tail(paths["gnb_log"]), "ric_tail": read_tail(paths["ric_log"])}})
        if ric_state.returncode == 0 and ric_state.stdout.strip() == "exited":
            fail("e2_setup_path", "RIC exited before E2 setup", {{"gnb_tail": read_tail(paths["gnb_log"]), "ric_tail": read_tail(paths["ric_log"])}})
        time.sleep(1)
    else:
        fail("e2_setup_path", "E2 setup evidence timed out", {{"gnb_tail": read_tail(paths["gnb_log"]), "ric_tail": read_tail(paths["ric_log"])}})

ue_cmd = (
    "mkdir -p /run/netns; ip netns add ue1 2>/dev/null || true; "
    "export PATH=/install/bin:$PATH; "
    "export LD_LIBRARY_PATH=/install/lib:${{LD_LIBRARY_PATH:-}}; "
    "srsue /config/ue_zmq.conf >/stage/logs/ue.log 2>&1"
)
ue = run([
    "docker", "run", "-d", "--name", payload["ue_container"], "--network", "host", "--privileged",
    "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
    "-v", str(pathlib.Path(payload["ocudu_root"]) / "install" / "srsran-4g") + ":/install:ro",
    "-v", str(pathlib.Path(paths["configs_dir"])) + ":/config:ro",
    "-v", str(episode_dir) + ":/stage",
    payload["ue_image"], "bash", "-lc", ue_cmd,
])
if ue.returncode != 0:
    fail("srsue_zmq_attach", "srsUE container failed to start", {{"stderr": ue.stderr, "stdout": ue.stdout}})

deadline = time.monotonic() + int(payload["attach_timeout"])
attached = False
while time.monotonic() < deadline:
    log_text = pathlib.Path(paths["ue_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ue_log"]).exists() else ""
    if "PDU Session Establishment successful" in log_text or "RRC NR reconfiguration successful" in log_text:
        attached = True
        break
    state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["ue_container"]])
    if state.returncode == 0 and state.stdout.strip() == "exited":
        fail("srsue_zmq_attach", "srsUE exited before attach", {{"tail": log_text[-4000:]}})
    time.sleep(1)
if not attached:
    log_text = pathlib.Path(paths["ue_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ue_log"]).exists() else ""
    fail("srsue_zmq_attach", "srsUE attach timed out", {{"tail": log_text[-4000:]}})

ping = run([
    "docker", "exec", "-d", payload["ue_container"], "bash", "-lc",
    "ip netns exec ue1 ping -D -i 0.2 10.45.1.1 >/stage/logs/ping.log 2>&1",
])
if ping.returncode != 0:
    fail("ping_traffic_path", "ping command failed to start", {{"stderr": ping.stderr, "stdout": ping.stdout}})

if payload["is_v4"]:
    xapp_cmd = (
        "XAPP=$({{ find /opt/flexric/build/examples /usr/local/bin -type f -path '*/monitor/xapp_oran_moni' -print 2>/dev/null; "
        "find /opt/flexric/build/examples /usr/local/bin -type f -executable -iname '*oran*moni*' -print 2>/dev/null; }} | head -1); "
        "if [ -z \\"$XAPP\\" ]; then echo 'no KPM monitor xApp found' >&2; exit 66; fi; "
        "CONF=/stage/configs/xapp_mon_e2sm_kpm.conf; "
        "echo xapp=$XAPP conf=$CONF > /stage/logs/kpm_xapp_command.txt; "
        "stdbuf -oL -eL $XAPP -c $CONF >/stage/logs/kpm_xapp.log 2>&1"
    )
    xapp = run([
        "docker", "run", "-d", "--name", payload["kpm_xapp_container"], "--network", "host",
        "-v", str(episode_dir) + ":/stage",
        payload["flexric_image"], "bash", "-lc", xapp_cmd,
    ])
    if xapp.returncode != 0:
        fail("e2_kpm_subscription", "KPM xApp container failed to start", {{"stderr": xapp.stderr, "stdout": xapp.stdout}})
    deadline = time.monotonic() + max(float(payload["probe_timeout"]) * 3, 10.0)
    while time.monotonic() < deadline:
        records = parse_kpm_records()
        if len(records) >= 3:
            break
        state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["kpm_xapp_container"]])
        if state.returncode == 0 and state.stdout.strip() == "exited" and not records:
            fail("e2_kpm_subscription", "KPM xApp exited before indications", {{"tail": read_tail(paths["kpm_xapp_log"])}})
        time.sleep(1)
    oracle = write_e2_oracle()
    if oracle["kpm_indications"] < 3 or not oracle.get("has_prb_measurement"):
        if oracle.get("decode_error"):
            fail("e2_kpm_subscription", oracle["decode_error"], {{"oracle": oracle, "ric_tail": read_tail(paths["ric_log"]), "xapp_tail": read_tail(paths["kpm_xapp_log"])}})
        fail("e2_kpm_subscription", "FlexRIC KPM path did not produce at least 3 decoded PRB indication records", {{"oracle": oracle, "tail": read_tail(paths["kpm_xapp_log"])}})

containers = {{
    "open5gs_container": "skillful_ran_5gc",
    "gnb_container": payload["gnb_container"],
    "ue_container": payload["ue_container"],
    "ric_provider": payload.get("ric_provider"),
    "ric_container": payload["ric_container"] if payload["is_v4"] else None,
    "kpm_xapp_container": payload["kpm_xapp_container"] if payload["is_v4"] else None,
    "e2_pcap_container": payload["e2_pcap_container"] if payload["is_v4"] else None,
    "ws_port": payload["ws_port"],
    "ric_port": payload["ric_port"] if payload["is_v4"] else None,
    "started_at": time.time(),
}}
pathlib.Path(paths["containers"]).write_text(json.dumps(containers, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({{
    "status": "ok",
    "stage": payload["stage"],
    "summary": "Docker e2e episode is running",
    "containers": containers,
    "paths": paths,
}}))
"""
        )
        if data.get("status") != "ok":
            self.cleanup(options.run_id)
        return data

    def observe(self) -> dict[str, Any]:
        options = self._require_options()
        policy = task_policy(options.task)
        payload = {
            "run_id": options.run_id,
            "task": options.task,
            "stage": episode_stage_for_task(options.task),
            "requires_e2": policy.requires_e2,
            "requires_e2_control": policy.requires_e2_control,
            "requires_ue_identity": policy.requires_ue_identity,
            "stale_metrics_observations": policy.stale_metrics_observations,
            "ric_provider": self.remote.config.ric_provider,
            "paths": self.paths,
            "ws_port": options.ws_port,
            "timeout": options.probe_timeout,
        }
        script = self._websocket_client_remote_source()
        script += f"""
import json
import pathlib
import re
import time
payload = json.loads({json.dumps(json.dumps(payload))})

def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value

paths = {{key: expand_remote_path(value) for key, value in payload["paths"].items()}}
observations_path = pathlib.Path(paths["observations"])
observation_index = 1
if observations_path.exists():
    observation_index += len([line for line in observations_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()])

def parse_ping(text):
    import re
    replies = len(re.findall(r"bytes from ", text))
    transmitted = replies
    received = replies
    loss = None
    match = re.search(r"(\\d+) packets transmitted, (\\d+) received,.*?(\\d+(?:\\.\\d+)?)% packet loss", text)
    if match:
        transmitted = int(match.group(1))
        received = int(match.group(2))
        loss = float(match.group(3))
    return {{
        "packets_transmitted": transmitted,
        "packets_received": received,
        "reply_lines": replies,
        "loss_percent": loss,
        "success_ratio": (received / transmitted) if transmitted else (1.0 if replies else 0.0),
    }}

def read_tail(path, limit=4000):
    p = pathlib.Path(path)
    return p.read_text(encoding="utf-8", errors="replace")[-limit:] if p.exists() else ""

def file_size(path):
    item = pathlib.Path(path)
    return item.stat().st_size if item.exists() else 0

def e2_setup_seen():
    text = (read_tail(paths["ric_log"], 12000) + "\\n" + read_tail(paths["gnb_log"], 12000)).lower()
    return "e2" in text and ("setup" in text or "connected" in text or "ric" in text)

def discover_du_ue_id():
    text = read_tail(paths["gnb_log"], 50000) + "\\n" + read_tail(paths["ric_log"], 50000) + "\\n" + read_tail(paths["kpm_xapp_log"], 50000)
    patterns = [
        r"gNB-DU-UE-F1AP-ID\\D+(\\d+)",
        r"gnb[_ -]?du[_ -]?ue[_ -]?f1ap[_ -]?id\\D+(\\d+)",
        r"du[_ -]?ue[_ -]?id\\D+(\\d+)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            return int(matches[-1])
    return None

ping_text = pathlib.Path(paths["ping_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ping_log"]).exists() else ""
metric = None
metric_error = None
try:
    with WebSocketClient("127.0.0.1", int(payload["ws_port"]), timeout=float(payload["timeout"])) as client:
        client.send_text(json.dumps({{"cmd": "metrics_subscribe"}}))
        deadline = time.monotonic() + float(payload["timeout"])
        while time.monotonic() < deadline:
            text = client.recv_text(timeout=max(0.1, deadline - time.monotonic()))
            if text is None:
                break
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if decoded.get("cmd") == "metrics_subscribe":
                continue
            metric = decoded
            with open(paths["metrics_raw"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps(decoded, sort_keys=True) + "\\n")
            break
except Exception as exc:
    metric_error = str(exc)

last_action = None
actions_path = pathlib.Path(paths["actions"])
if actions_path.exists():
    lines = [line for line in actions_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if lines:
        try:
            last_action = json.loads(lines[-1])
        except json.JSONDecodeError:
            last_action = {{"decode_error": lines[-1]}}

metrics = {{
    "present": isinstance(metric, dict),
    "component_keys": sorted([key for key in metric.keys() if key != "timestamp"]) if isinstance(metric, dict) else [],
    "timestamp": metric.get("timestamp") if isinstance(metric, dict) else None,
    "raw": metric,
    "error": metric_error,
    "stale": False,
    "fresh": isinstance(metric, dict),
}}
metrics_raw_present = metrics["present"]
metrics_stale = False
if payload.get("stale_metrics_observations", 0) and observation_index <= int(payload.get("stale_metrics_observations", 0)):
    metrics_stale = True
    metrics["stale"] = True
    metrics["fresh"] = False
    metrics["present"] = False
    metrics["stale_reason"] = "scenario_mask"
    metrics["masked_raw_present"] = metrics_raw_present
e2_oracle = {{}}
e2_records = []
if payload.get("requires_e2"):
    oracle_path = pathlib.Path(paths["e2_oracle"])
    if oracle_path.exists():
        try:
            e2_oracle = json.loads(oracle_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            e2_oracle = {{"decode_error": True}}
    raw_path = pathlib.Path(paths["e2_kpm_raw"])
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                e2_records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
observation = {{
    "type": payload["task"],
    "timestamp": time.time(),
    "observation_index": observation_index,
    "scenario": {{
        "metrics_stale": metrics_stale,
        "stale_metrics_window": int(payload.get("stale_metrics_observations", 0) or 0),
    }},
    "ping": parse_ping(ping_text),
    "metrics": metrics,
    "e2": {{
        "enabled": bool(payload.get("requires_e2")),
        "kpm_indications": int(e2_oracle.get("kpm_indications", len(e2_records)) or 0),
        "last_kpm": e2_oracle.get("last_kpm") or (e2_records[-1] if e2_records else None),
        "has_prb_measurement": bool(e2_oracle.get("has_prb_measurement")),
        "oracle_available": bool(e2_oracle.get("oracle_available")),
        "pcap_available": bool(e2_oracle.get("pcap_available")),
        "log_available": bool(e2_oracle.get("log_available")),
        "control_records": len(e2_oracle.get("control_records", [])) if isinstance(e2_oracle.get("control_records"), list) else 0,
        "accepted_control_records": int(e2_oracle.get("accepted_control_records", 0) or 0),
        "control_types": e2_oracle.get("control_types", []),
        "control_oracle_available": bool(e2_oracle.get("control_oracle_available")),
        "ccc_control_available": bool(payload.get("requires_e2_control")),
        "rc_du_control_available": bool(payload.get("requires_e2_control")),
        "du_ue_id": discover_du_ue_id() if payload.get("requires_ue_identity") else None,
        "raw_path": paths["e2_kpm_raw"] if payload.get("requires_e2") else None,
        "control_raw_path": paths["e2_control_raw"] if payload.get("requires_e2_control") else None,
    }},
    "last_action": last_action,
    "backend": {{
        "websocket": metric_error is None,
        "ping": bool(ping_text),
        "e2_kpm": bool(e2_oracle.get("oracle_available")),
        "e2_control": bool(e2_oracle.get("control_oracle_available")),
    }},
}}
record = {{"run_id": payload["run_id"], "state": "running", "observation": observation}}
with open(paths["observations"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
stage = payload["stage"]
print(json.dumps({{"status": "ok", "stage": stage, "run_id": payload["run_id"], "state": "running", "observation": observation}}))
"""
        return self._remote_json(script)

    def act(self, action: Any) -> dict[str, Any]:
        options = self._require_options()
        validation = validate_episode_action(action, options.task)
        decision_context = self._latest_decision_context()
        decision_context_error = decision_context.pop("_decision_context_error", None)
        record = {
            "timestamp": time.time(),
            "action": action,
            "validation": validation,
            "decision_context": decision_context,
            "dispatched": False,
            "accepted": False,
            "request": validation.get("request"),
            "response": None,
            "reason": validation["reason"],
        }
        if decision_context_error:
            record["decision_context_error"] = decision_context_error
        if not validation["valid"]:
            self._append_jsonl(self.paths["actions"], record)
            return {
                "status": "rejected",
                "stage": episode_stage_for_task(options.task),
                "run_id": options.run_id,
                "accepted": False,
                "reason": validation["reason"],
                "validation": validation,
                "decision_context_error": decision_context_error,
            }
        if validation.get("dispatch") in {"e2_ccc", "e2_rc_du"}:
            return self._dispatch_e2_control_action(record, validation.get("dispatch") or "e2_control")
        payload = {
            "run_id": options.run_id,
            "task": options.task,
            "stage": episode_stage_for_task(options.task),
            "paths": self.paths,
            "ws_port": options.ws_port,
            "timeout": options.probe_timeout,
            "record": record,
        }
        script = self._websocket_client_remote_source()
        script += f"""
import json
import pathlib
import time
payload = json.loads({json.dumps(json.dumps(payload))})
record = payload["record"]
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
paths = {{key: expand_remote_path(value) for key, value in payload["paths"].items()}}
try:
    with WebSocketClient("127.0.0.1", int(payload["ws_port"]), timeout=float(payload["timeout"])) as client:
        client.send_text(json.dumps(record["request"]))
        raw = client.recv_text(timeout=float(payload["timeout"]))
        response = json.loads(raw) if raw else None
        record["response"] = response
        record["raw_response"] = raw
        record["dispatched"] = True
        record["accepted"] = isinstance(response, dict) and "error" not in response
        record["reason"] = "accepted" if record["accepted"] else (response.get("error") if isinstance(response, dict) else "empty response")
except Exception as exc:
    record["reason"] = str(exc)
record["completed_at"] = time.time()
with open(paths["actions"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
print(json.dumps({{
    "status": "ok" if record["accepted"] else "rejected",
    "stage": payload["stage"],
    "run_id": payload["run_id"],
    "accepted": record["accepted"],
    "reason": record["reason"],
    "request": record["request"],
    "response": record["response"],
    "record": record,
}}))
"""
        return self._remote_json(script)

    def _dispatch_e2_control_action(self, record: dict[str, Any], dispatch: str) -> dict[str, Any]:
        options = self._require_options()
        suffix = container_suffix(f"{options.run_id}-{dispatch}-{int(time.time() * 1000)}")
        payload = {
            "run_id": options.run_id,
            "task": options.task,
            "stage": episode_stage_for_task(options.task),
            "paths": self.paths,
            "image": FLEXRIC_IMAGE,
            "container": f"skillful-ran-bench-e2-control-{suffix}",
            "dispatch": dispatch,
            "record": record,
            "ric_port": RIC_PORT,
            "timeout": options.probe_timeout,
        }
        return self._remote_json(
            f"""
import json
import pathlib
import re
import shlex
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})
record = payload["record"]

def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value

paths = {{key: expand_remote_path(value) for key, value in payload["paths"].items()}}
episode_dir = pathlib.Path(paths["episode_dir"])

def read_tail(path, limit=50000):
    item = pathlib.Path(path)
    return item.read_text(encoding="utf-8", errors="replace")[-limit:] if item.exists() else ""

def discover_du_ue_id():
    normalized = record.get("validation", {{}}).get("normalized", {{}})
    explicit = normalized.get("du_ue_id")
    if isinstance(explicit, int) and explicit >= 0:
        return explicit
    text = read_tail(paths["gnb_log"]) + "\\n" + read_tail(paths["ric_log"]) + "\\n" + read_tail(paths["kpm_xapp_log"])
    patterns = [
        r"gNB-DU-UE-F1AP-ID\\D+(\\d+)",
        r"gnb[_ -]?du[_ -]?ue[_ -]?f1ap[_ -]?id\\D+(\\d+)",
        r"du[_ -]?ue[_ -]?id\\D+(\\d+)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            return int(matches[-1])
    return None

def append_jsonl(path, value):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\\n")

normalized = record.get("validation", {{}}).get("normalized", {{}})
request = record.get("request", {{}})
tool = request.get("tool")
if payload["dispatch"] == "e2_rc_du":
    du_ue_id = discover_du_ue_id()
    if du_ue_id is None:
        record["reason"] = "DU UE identity unavailable for E2SM-RC DU control"
        record["completed_at"] = time.time()
        append_jsonl(paths["actions"], record)
        append_jsonl(paths["e2_control_raw"], {{"accepted": False, "reason": record["reason"], "action": record.get("action"), "request": request}})
        print(json.dumps({{"status": "rejected", "stage": payload["stage"], "run_id": payload["run_id"], "accepted": False, "reason": record["reason"], "record": record}}))
        raise SystemExit(0)
    request["du_ue_id"] = du_ue_id
else:
    du_ue_id = None

args = [
    "--ric", "127.0.0.1",
    "--port", str(payload["ric_port"]),
    "--plmn", str(normalized.get("plmn", "00101")),
    "--sst", str(normalized.get("sst", 1)),
    "--min-prb-policy-ratio", str(normalized.get("min_prb_policy_ratio")),
    "--max-prb-policy-ratio", str(normalized.get("max_prb_policy_ratio")),
]
if normalized.get("sd") is not None:
    args.extend(["--sd", str(normalized["sd"])])
if normalized.get("dedicated_ratio") is not None:
    args.extend(["--dedicated-ratio", str(normalized["dedicated_ratio"])])
if du_ue_id is not None:
    args.extend(["--du-ue-id", str(du_ue_id)])
args.extend(["--json"])
quoted_args = " ".join(shlex.quote(arg) for arg in args)
shell = (
    "set -eu; "
    f"TOOL=$(command -v {{shlex.quote(str(tool))}} || true); "
    "if [ -z \\"$TOOL\\" ]; then echo '{{\\"error\\":\\"missing E2 control tool\\"}}'; exit 66; fi; "
    f"$TOOL {quoted_args}"
)
proc = subprocess.run(
    [
        "docker", "run", "--rm", "--name", payload["container"], "--network", "host",
        "-v", str(episode_dir) + ":/stage",
        payload["image"], "bash", "-lc", shell,
    ],
    check=False,
    text=True,
    capture_output=True,
    timeout=max(1.0, float(payload["timeout"]) * 4),
)
raw_text = (proc.stdout or "").strip()
response = None
for line in reversed([line for line in raw_text.splitlines() if line.strip()]):
    try:
        response = json.loads(line)
        break
    except json.JSONDecodeError:
        continue
if response is None:
    response = {{"stdout": raw_text, "stderr": proc.stderr, "returncode": proc.returncode}}
accepted = proc.returncode == 0 and isinstance(response, dict) and "error" not in response
record["response"] = response
record["raw_response"] = raw_text
record["stderr"] = proc.stderr
record["returncode"] = proc.returncode
record["dispatched"] = True
record["accepted"] = accepted
record["reason"] = "accepted" if accepted else str(response.get("error") if isinstance(response, dict) else proc.stderr or "E2 control failed")
record["request"] = request
record["completed_at"] = time.time()
append_jsonl(paths["actions"], record)
append_jsonl(paths["e2_control_raw"], {{
    "timestamp": record["completed_at"],
    "accepted": accepted,
    "dispatch": payload["dispatch"],
    "action": record.get("action"),
    "request": request,
    "response": response,
    "reason": record["reason"],
}})
print(json.dumps({{
    "status": "ok" if accepted else "rejected",
    "stage": payload["stage"],
    "run_id": payload["run_id"],
    "accepted": accepted,
    "reason": record["reason"],
    "request": request,
    "response": response,
    "record": record,
}}))
"""
        )

    def _latest_decision_context(self) -> dict[str, Any]:
        if not self.paths.get("observations"):
            return {"_decision_context_error": "observations path is not initialized"}
        payload = {"path": self.paths["observations"]}
        try:
            data = self._remote_json(
                f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
path = pathlib.Path(expand_remote_path(payload["path"]))
context = {{}}
context_error = None
if path.exists():
    lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if lines:
        try:
            context = json.loads(lines[-1]).get("observation", {{}})
        except json.JSONDecodeError:
            context_error = "latest observation record is not valid JSON"
print(json.dumps({{"status": "ok", "context": context, "context_error": context_error}}))
"""
            )
            context_error = data.get("context_error")
            if context_error:
                return {"_decision_context_error": str(context_error)}
            context = data.get("context")
            return context if isinstance(context, dict) else {}
        except Exception as exc:
            return {"_decision_context_error": str(exc)}

    def cleanup(self, run_id: str | None = None) -> dict[str, Any]:
        run_id = safe_run_id(run_id or self._require_options().run_id)
        paths = self.paths or episode_paths(self.remote.config.workspace, run_id)
        suffix = container_suffix(run_id)
        cleanup_task = self.options.task if self.options is not None else TASK_WS_PRB_PING_V1
        cleanup_policy = task_policy(cleanup_task)
        payload = {
            "run_id": run_id,
            "task": cleanup_task,
            "requires_e2": cleanup_policy.requires_e2,
            "ric_provider": self.remote.config.ric_provider,
            "paths": paths,
            "compose": self.remote.config.runtime.open5gs_compose,
            "gnb_container": f"skillful-ran-bench-gnb-{suffix}",
            "ue_container": f"skillful-ran-bench-ue-{suffix}",
            "ric_container": f"{FLEXRIC_CONTAINER_PREFIX}-{suffix}",
            "kpm_xapp_container": f"{KPM_XAPP_CONTAINER_PREFIX}-{suffix}",
            "e2_pcap_container": f"{E2_PCAP_CONTAINER_PREFIX}-{suffix}",
            "e2_control_container_prefix": f"skillful-ran-bench-e2-control-{suffix}",
            "ws_port": self.options.ws_port if self.options is not None else DEFAULT_WS_PORT,
            "ric_port": RIC_PORT if self.remote.config.ric_provider == RIC_PROVIDER_FLEXRIC else None,
        }
        return self._remote_json(
            f"""
import json
import pathlib
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
paths = {{key: expand_remote_path(value) for key, value in payload["paths"].items()}}
payload["compose"] = expand_remote_path(payload["compose"])
container_metadata = {{}}
metadata_path = pathlib.Path(paths.get("containers", ""))
if metadata_path.exists():
    try:
        container_metadata = json.loads(metadata_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        container_metadata = {{}}
if container_metadata.get("ric_port"):
    payload["requires_e2"] = True
    payload["ric_port"] = container_metadata.get("ric_port")
commands = []
def run(argv):
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    commands.append({{"argv": argv, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}})
    return proc
def port_listening(port):
    proc = run(["ss", "-ln"])
    if proc.returncode != 0:
        return None
    token = ":" + str(port)
    for line in proc.stdout.splitlines()[1:]:
        if any(part.endswith(token) for part in line.split()):
            return True
    return False
run(["docker", "exec", payload["ue_container"], "bash", "-lc", "pkill -f 'ping.*10.45.1.1' || true"])
time.sleep(0.5)
ps_before_rm = run(["docker", "ps", "-a", "--format", "{{{{.Names}}}}"])
e2_control_containers = []
if ps_before_rm.returncode == 0:
    e2_control_containers = [
        line.strip()
        for line in ps_before_rm.stdout.splitlines()
        if line.strip().startswith(payload["e2_control_container_prefix"])
    ]
run([
    "docker",
    "rm",
    "-f",
    payload["gnb_container"],
    payload["ue_container"],
    payload["ric_container"],
    payload["kpm_xapp_container"],
    payload["e2_pcap_container"],
    *e2_control_containers,
])
run(["docker", "compose", "-f", payload["compose"], "down"])
ps = run(["docker", "ps", "-a", "--format", "{{{{.Names}}}}"])
leftover = []
if ps.returncode == 0:
    names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    wanted = {{payload["gnb_container"], payload["ue_container"], payload["ric_container"], payload["kpm_xapp_container"], payload["e2_pcap_container"], "skillful_ran_5gc"}}
    leftover = [name for name in names if name in wanted or name.startswith(payload["e2_control_container_prefix"])]
port_open = port_listening(int(payload.get("ws_port", 8001)))
ric_port_open = port_listening(int(payload["ric_port"])) if payload.get("requires_e2") and payload.get("ric_port") else False
errors = []
if ps.returncode != 0:
    errors.append("unable to inspect docker containers")
if port_open is None:
    errors.append("unable to inspect listening ports")
if leftover:
    errors.append("leftover containers: " + ", ".join(leftover))
if port_open:
    errors.append("WebSocket port is still listening")
if ric_port_open:
    errors.append("RIC port is still listening")
status = "error" if errors else "ok"
result = {{
    "status": status,
    "run_id": payload["run_id"],
    "commands": commands,
    "leftover_containers": leftover,
    "e2_control_containers_removed": e2_control_containers,
    "ws_port_open": port_open,
    "ric_port_open": ric_port_open,
    "errors": errors,
    "completed_at": time.time(),
}}
pathlib.Path(paths["episode_dir"]).mkdir(parents=True, exist_ok=True)
pathlib.Path(paths["cleanup"]).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(result))
"""
        )

    def finalize(self, unscored_reason: str | None = None, cleanup_success: bool = True) -> dict[str, Any]:
        options = self._require_options()
        policy = task_policy(options.task)
        scoring_source = self._episode_scoring_remote_source()
        payload = {
            "run_id": options.run_id,
            "task": options.task,
            "stage": episode_stage_for_task(options.task),
            "policy": {
                "requires_e2": policy.requires_e2,
                "requires_valid_action": policy.requires_valid_action,
                "require_no_actions": policy.require_no_actions,
                "max_actions": policy.max_actions,
                "require_first_invalid_then_valid": policy.require_first_invalid_then_valid,
                "require_evidence_before_action": policy.require_evidence_before_action,
                "stale_metrics_observations": policy.stale_metrics_observations,
                "requires_e2_control": policy.requires_e2_control,
                "requires_ue_identity": policy.requires_ue_identity,
                "allowed_action_types": list(policy.allowed_action_types),
                "expected_action_type": policy.expected_action_type,
            },
            "ric_provider": self.remote.config.ric_provider,
            "paths": self.paths,
            "unscored_reason": unscored_reason,
            "cleanup_success": cleanup_success,
        }
        return self._remote_json(
            f"""
import json
import pathlib
import re
{scoring_source}
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
paths = {{key: expand_remote_path(value) for key, value in payload["paths"].items()}}
def read_jsonl(path):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    result = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result
def parse_ping(text):
    replies = len(re.findall(r"bytes from ", text))
    transmitted = replies
    received = replies
    loss = None
    match = re.search(r"(\\d+) packets transmitted, (\\d+) received,.*?(\\d+(?:\\.\\d+)?)% packet loss", text)
    if match:
        transmitted = int(match.group(1))
        received = int(match.group(2))
        loss = float(match.group(3))
    return {{
        "packets_transmitted": transmitted,
        "packets_received": received,
        "reply_lines": replies,
        "loss_percent": loss,
        "success_ratio": (received / transmitted) if transmitted else (1.0 if replies else 0.0),
    }}
def parse_kpm_records(text, raw_path=None):
    records = []
    if raw_path is not None:
        path = pathlib.Path(raw_path)
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    current = None
    for index, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\\bKPM v\\d+\\s+ind_msg\\b", line, flags=re.IGNORECASE):
            if current is not None:
                records.append(current)
            current = {{"line_no": index, "text": line[-1000:], "measurements": []}}
        elif current is not None and re.search(r"\\bmeas record\\b", line, flags=re.IGNORECASE):
            current["measurements"].append({{"line_no": index, "text": line[-1000:]}})
    if current is not None:
        records.append(current)
    return records
def read_jsonl(path):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    result = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result
def record_has_prb_measurement(record):
    measurements = record.get("measurements")
    if not isinstance(measurements, list):
        return False
    for measurement in measurements:
        if isinstance(measurement, dict):
            haystack = " ".join(str(measurement.get(key, "")) for key in ("name", "text")).lower()
        else:
            haystack = str(measurement).lower()
        if "prb" in haystack:
            return True
    return False
def file_size(path):
    item = pathlib.Path(path)
    return item.stat().st_size if item.exists() else 0
def build_e2_oracle(paths, requires_e2):
    if not requires_e2:
        return {{}}
    ric_text = pathlib.Path(paths["ric_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ric_log"]).exists() else ""
    gnb_text = pathlib.Path(paths["gnb_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["gnb_log"]).exists() else ""
    xapp_text = pathlib.Path(paths["kpm_xapp_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["kpm_xapp_log"]).exists() else ""
    records = parse_kpm_records(xapp_text, paths["e2_kpm_raw"])
    with open(paths["e2_kpm_raw"], "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\\n")
    pcap_sizes = {{
        "du": file_size(paths["e2ap_du_pcap"]),
        "cu_cp": file_size(paths["e2ap_cu_cp_pcap"]),
        "sctp_capture": file_size(paths["e2ap_sctp_pcap"]),
    }}
    lower = (ric_text + "\\n" + gnb_text).lower()
    e2_setup_seen = "e2" in lower and ("setup" in lower or "connected" in lower or "ric" in lower)
    pcap_available = any(size > 0 for size in pcap_sizes.values())
    raw_kpm_available = file_size(paths["e2_kpm_raw"]) > 0
    log_available = file_size(paths["gnb_log"]) > 0 and file_size(paths["kpm_xapp_log"]) > 0
    has_prb_measurement = any(record_has_prb_measurement(record) for record in records)
    control_records = read_jsonl(paths["e2_control_raw"])
    accepted_control_records = [record for record in control_records if record.get("accepted")]
    control_types = sorted({{record.get("action", {{}}).get("type") for record in accepted_control_records if isinstance(record.get("action"), dict)}})
    control_oracle_available = bool(accepted_control_records) and e2_setup_seen and pcap_available and log_available
    oracle_available = (
        e2_setup_seen
        and len(records) >= 3
        and has_prb_measurement
        and pcap_available
    )
    oracle = {{
        "provider": payload.get("ric_provider"),
        "e2_setup_seen": e2_setup_seen,
        "kpm_indications": len(records),
        "last_kpm": records[-1] if records else None,
        "has_prb_measurement": has_prb_measurement,
        "pcap_sizes": pcap_sizes,
        "pcap_available": pcap_available,
        "log_available": log_available,
        "raw_kpm_available": raw_kpm_available,
        "control_records": control_records,
        "accepted_control_records": len(accepted_control_records),
        "control_types": control_types,
        "control_oracle_available": control_oracle_available,
        "oracle_available": oracle_available,
    }}
    pathlib.Path(paths["e2_oracle"]).write_text(json.dumps(oracle, indent=2, sort_keys=True), encoding="utf-8")
    return oracle
ping_text = pathlib.Path(paths["ping_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ping_log"]).exists() else ""
actions = read_jsonl(paths["actions"])
observations = read_jsonl(paths["observations"])
observations_only = [item.get("observation", item) for item in observations]
valid_actions = [item for item in actions if item.get("validation", {{}}).get("valid")]
accepted_valid = [item for item in valid_actions if item.get("accepted")]
invalid_actions = [item for item in actions if not item.get("validation", {{}}).get("valid")]
rejected_invalid = [item for item in invalid_actions if not item.get("dispatched")]
metrics_frames = [
    item for item in observations
    if item.get("observation", {{}}).get("metrics", {{}}).get("present") or item.get("metrics", {{}}).get("present")
]
fresh_metrics_frames = [
    item for item in observations_only
    if item.get("metrics", {{}}).get("present") and not item.get("metrics", {{}}).get("stale")
]
stale_observations = [
    item for item in observations_only
    if item.get("metrics", {{}}).get("stale") or item.get("scenario", {{}}).get("metrics_stale")
]
actions_during_stale = [
    item for item in actions
    if item.get("decision_context", {{}}).get("metrics", {{}}).get("stale")
    or item.get("decision_context", {{}}).get("scenario", {{}}).get("metrics_stale")
]
policy = payload["policy"]
evidence_gated_actions = [
    item for item in accepted_valid
    if item.get("decision_context", {{}}).get("metrics", {{}}).get("present")
    and not item.get("decision_context", {{}}).get("metrics", {{}}).get("stale")
    and (
        not policy.get("requires_e2")
        or item.get("decision_context", {{}}).get("e2", {{}}).get("has_prb_measurement")
        or item.get("decision_context", {{}}).get("e2", {{}}).get("oracle_available")
        or int(item.get("decision_context", {{}}).get("e2", {{}}).get("kpm_indications", 0) or 0) >= 3
    )
]
ping = parse_ping(ping_text)
e2_oracle = build_e2_oracle(paths, bool(policy.get("requires_e2")))
cleanup_success = bool(payload["cleanup_success"])
scoring = score_episode(
    ping=ping,
    actions=actions,
    observations=observations,
    cleanup_success=cleanup_success,
    unscored_reason=payload["unscored_reason"],
    require_e2=bool(policy.get("requires_e2")),
    e2_oracle=e2_oracle,
    task=payload["task"],
)
summary = {{
    "status": "ok",
    "stage": payload["stage"],
    "task": payload["task"],
    "run_id": payload["run_id"],
    **scoring,
    "artifacts": paths,
}}
pathlib.Path(paths["summary"]).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary))
"""
        )

    def _episode_scoring_remote_source(self) -> str:
        constant_names = [
            "ACTION_SET_PRB_POLICY_RATIO_WS",
            "ACTION_SET_PRB_POLICY_RATIO_CCC",
            "ACTION_SET_PRB_POLICY_RATIO_RC_DU",
            "TASK_WS_PRB_PING_V1",
            "TASK_E2_KPM_PRB_PING_V1",
            "TASK_E2_CCC_PRB_POLICY_PING_V1",
            "TASK_E2_RC_DU_PRB_POLICY_PING_V1",
            "TASK_WS_PRB_NOOP_GUARD_V1",
            "TASK_WS_PRB_ERROR_REPAIR_V1",
            "TASK_WS_PRB_ACTION_BUDGET_V1",
            "TASK_E2_KPM_JSON_CONSISTENCY_V1",
            "TASK_E2_CONTROL_API_CONSISTENCY_V1",
            "TASK_METRICS_STALENESS_NOOP_V1",
            "STALE_METRICS_OBSERVATIONS",
        ]
        constants = "\n".join(f"{name} = {globals()[name]!r}" for name in constant_names)
        policy_entries = []
        for task_id, policy in TASK_POLICIES.items():
            args = ", ".join(
                f"{field.name}={getattr(policy, field.name)!r}" for field in fields(EpisodeTaskPolicy)
            )
            policy_entries.append(f"    {task_id!r}: EpisodeTaskPolicy({args}),")
        policies = "TASK_POLICIES = {\n" + "\n".join(policy_entries) + "\n}"
        return "\n\n".join(
            [
                "from dataclasses import dataclass",
                "from typing import Any",
                constants,
                inspect.getsource(EpisodeTaskPolicy),
                policies,
                inspect.getsource(task_policy),
                inspect.getsource(score_episode),
            ]
        )

    def run(
        self,
        options: EpisodeOptions,
        action: dict[str, Any] | None = None,
        unscored_reason: str | None = None,
    ) -> dict[str, Any]:
        policy = task_policy(options.task)
        fixed_action = action or fixed_prb_action_for_type(policy.expected_action_type or policy.allowed_action_types[0])
        try:
            start = self.start(options)
        except Exception as exc:
            self.options = options
            self.paths = episode_paths(self.remote.config.workspace, options.run_id)
            cleanup = self._cleanup_after_error(options.run_id)
            summary = self._finalize_after_error(
                reason=str(exc),
                cleanup_success=cleanup.get("status") == "ok",
            )
            return {"status": "error", "run_id": options.run_id, "error": str(exc), "cleanup": cleanup, "summary": summary}
        if start.get("status") != "ok":
            self.options = options
            self.paths = episode_paths(self.remote.config.workspace, options.run_id)
            cleanup = self._cleanup_after_error(options.run_id)
            summary = self._finalize_after_error(
                reason=start.get("summary", "episode start failed"),
                cleanup_success=cleanup.get("status") == "ok",
            )
            return {"status": "error", "run_id": options.run_id, "start": start, "cleanup": cleanup, "summary": summary}

        observations: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        episode_error: str | None = None
        sent_fixed = False
        sent_invalid = False
        try:
            observation = self.observe()
            observations.append(observation)
            if policy.require_first_invalid_then_valid:
                actions.append(
                    self.act(
                        {
                            "type": policy.expected_action_type or policy.allowed_action_types[0],
                            "min_prb_policy_ratio": 90,
                            "max_prb_policy_ratio": 10,
                        }
                    )
                )
                sent_invalid = True
                observation = self.observe()
                observations.append(observation)
                actions.append(self.act(fixed_action))
                sent_fixed = True
            elif policy.require_no_actions:
                sent_fixed = False
            elif not policy.require_evidence_before_action:
                actions.append(self.act(fixed_action))
                sent_fixed = True
            elif self._observation_has_required_evidence(observation, policy):
                actions.append(self.act(fixed_action))
                sent_fixed = True
            deadline = time.monotonic() + max(0, options.duration)
            while time.monotonic() < deadline:
                observation = self.observe()
                observations.append(observation)
                if policy.require_first_invalid_then_valid and sent_invalid and not sent_fixed:
                    actions.append(self.act(fixed_action))
                    sent_fixed = True
                elif not policy.require_no_actions and not sent_fixed and self._observation_has_required_evidence(observation, policy):
                    actions.append(self.act(fixed_action))
                    sent_fixed = True
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        except Exception as exc:
            episode_error = str(exc)
        cleanup = self._cleanup_after_error(options.run_id)
        final_unscored_reason = unscored_reason or episode_error
        summary = self._finalize_after_error(
            reason=final_unscored_reason,
            cleanup_success=cleanup.get("status") == "ok",
        )
        if episode_error is not None:
            return {
                "status": "error",
                "run_id": options.run_id,
                "start": start,
                "error": episode_error,
                "actions": actions,
                "observations": observations,
                "cleanup": cleanup,
                "summary": summary,
            }
        return {
            "status": "ok" if summary.get("scored") else "error",
            "run_id": options.run_id,
            "start": start,
            "actions": actions,
            "observations": observations,
            "cleanup": cleanup,
            "summary": summary,
        }

    def _observation_has_required_evidence(self, observation: dict[str, Any], policy: EpisodeTaskPolicy) -> bool:
        frame = observation.get("observation", observation)
        metrics = frame.get("metrics", {})
        if policy.stale_metrics_observations and (
            metrics.get("stale") or frame.get("scenario", {}).get("metrics_stale")
        ):
            return False
        if not metrics.get("present"):
            return False
        if not policy.requires_e2:
            return True
        e2 = frame.get("e2", {})
        if policy.requires_ue_identity and e2.get("du_ue_id") is None:
            return False
        return bool(e2.get("has_prb_measurement") or e2.get("oracle_available") or int(e2.get("kpm_indications", 0) or 0) >= 3)

    def _cleanup_after_error(self, run_id: str) -> dict[str, Any]:
        try:
            return self.cleanup(run_id)
        except Exception as exc:
            return {"status": "error", "run_id": run_id, "errors": [str(exc)], "commands": []}

    def _finalize_after_error(self, reason: str | None, cleanup_success: bool) -> dict[str, Any]:
        try:
            return self.finalize(unscored_reason=reason, cleanup_success=cleanup_success)
        except Exception as exc:
            options = self._require_options()
            return {
                "status": "error",
                "stage": episode_stage_for_task(options.task),
                "run_id": options.run_id,
                "scored": False,
                "unscored_reason": reason or str(exc),
                "finalize_error": str(exc),
            }

    def _append_jsonl(self, path: str, record: dict[str, Any]) -> None:
        payload = {"path": path, "record": record}
        self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
path = expand_remote_path(payload["path"])
pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload["record"], sort_keys=True) + "\\n")
print(json.dumps({{"status": "ok"}}))
"""
        )

    def _require_options(self) -> EpisodeOptions:
        if self.options is None:
            raise RuntimeError("episode runtime has not been started")
        return self.options

    def _remote_json(self, python_body: str) -> dict[str, Any]:
        command = f"python3 - <<'PY'\n{python_body.rstrip()}\nPY"
        proc = self.remote.exec([command], shell=True)
        if proc.get("status") != "ok":
            raise RemoteCommandError(
                f"Remote command failed with code {proc.get('returncode')}: {proc.get('stderr') or proc.get('stdout')}"
            )
        try:
            return json.loads(proc.get("stdout", "") or "{}")
        except json.JSONDecodeError as exc:
            raise RemoteCommandError(f"Remote command did not return JSON: {proc.get('stdout', '')}") from exc

    def _websocket_client_remote_source(self) -> str:
        parts = [
            "import base64",
            "import hashlib",
            "import os",
            "import socket",
            "import struct",
            "from dataclasses import dataclass",
            inspect.getsource(WebSocketProtocolError),
            inspect.getsource(WebSocketFrame),
            inspect.getsource(WebSocketClient),
        ]
        return "\n\n".join(parts) + "\n"


def run_episode(
    remote: RemoteManager,
    run_id: str | None = None,
    task: str = TASK_WS_PRB_PING_V1,
    duration: int = DEFAULT_EPISODE_DURATION,
    ws_port: int = DEFAULT_WS_PORT,
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT,
    attach_timeout: int = DEFAULT_ATTACH_TIMEOUT,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
    unscored_reason: str | None = None,
) -> dict[str, Any]:
    runtime = EpisodeRuntime(remote)
    options = EpisodeOptions(
        run_id=safe_run_id(run_id or default_episode_run_id()),
        task=task,
        duration=duration,
        ws_port=ws_port,
        launch_timeout=launch_timeout,
        attach_timeout=attach_timeout,
        probe_timeout=probe_timeout,
    )
    return runtime.run(options, unscored_reason=unscored_reason)


def cleanup_episode(remote: RemoteManager, run_id: str) -> dict[str, Any]:
    runtime = EpisodeRuntime(remote)
    return runtime.cleanup(safe_run_id(run_id))
