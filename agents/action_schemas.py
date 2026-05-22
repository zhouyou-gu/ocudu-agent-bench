"""Compact per-action schema cheatsheets used in LLM prompts.

The benchmark publishes the full schema at `schemas/action.schema.json`, but
that file is too large to inline in every prompt. The cheatsheet below is the
minimum a chat model needs to emit a valid action JSON: each entry lists the
required fields and one concrete example payload. Field bounds are kept short;
the executable validators in `benchmark/benchmark_api/action.py` remain
authoritative.
"""

from __future__ import annotations

from typing import Any


ACTION_CHEATSHEET: dict[str, dict[str, Any]] = {
    "SET_PRB_POLICY_RATIO_WS": {
        "summary": "Set PRB policy ratios over the OCUDU WebSocket backend.",
        "fields": {
            "plmn": "string PLMN id, e.g. '00101'",
            "sst": "int slice/service type 0..255",
            "sd": "int 0..16777215 or null",
            "min_prb_policy_ratio": "int 0..100",
            "max_prb_policy_ratio": "int 0..100, must be >= min",
        },
        "example": {
            "type": "SET_PRB_POLICY_RATIO_WS",
            "plmn": "00101",
            "sst": 1,
            "sd": None,
            "min_prb_policy_ratio": 30,
            "max_prb_policy_ratio": 90,
        },
    },
    "SET_PRB_POLICY_RATIO_CCC": {
        "summary": "Set PRB policy ratios over the E2 CCC control path. Requires KPM evidence.",
        "fields": {
            "plmn": "string PLMN id",
            "sst": "int 0..255",
            "sd": "int 0..16777215 or null",
            "min_prb_policy_ratio": "int 0..100",
            "max_prb_policy_ratio": "int 0..100, must be >= min",
        },
        "example": {
            "type": "SET_PRB_POLICY_RATIO_CCC",
            "plmn": "00101",
            "sst": 1,
            "sd": None,
            "min_prb_policy_ratio": 30,
            "max_prb_policy_ratio": 90,
        },
    },
    "SET_PRB_POLICY_RATIO_RC_DU": {
        "summary": "Set per-UE PRB ratios over the E2 RC DU control path.",
        "fields": {
            "plmn": "string PLMN id",
            "sst": "int 0..255",
            "sd": "int 0..16777215 or null",
            "min_prb_policy_ratio": "int 0..100",
            "max_prb_policy_ratio": "int 0..100, must be >= min",
            "du_ue_id": "int >= 0 (from evidence.ue_identity.du_ue_id)",
        },
        "example": {
            "type": "SET_PRB_POLICY_RATIO_RC_DU",
            "plmn": "00101",
            "sst": 1,
            "sd": None,
            "min_prb_policy_ratio": 30,
            "max_prb_policy_ratio": 90,
            "du_ue_id": 1,
        },
    },
    "SET_SSB_BLOCK_POWER_WS": {
        "summary": "Set per-cell SSB block power in dBm.",
        "fields": {
            "plmn": "string PLMN id",
            "nci": "int NCI from evidence.cell_identity.nci",
            "ssb_block_power_dbm": "int dBm in [-60, 50]",
        },
        "example": {
            "type": "SET_SSB_BLOCK_POWER_WS",
            "plmn": "00101",
            "nci": 6733824,
            "ssb_block_power_dbm": -16,
        },
    },
    "TRIGGER_HANDOVER_CLI": {
        "summary": "Trigger an immediate handover for one UE.",
        "fields": {
            "serving_pci": "int PCI 0..1007 (current serving cell)",
            "rnti": "hex string like '0x4601' or int 1..65535",
            "target_pci": "int PCI 0..1007, must be in evidence.ue_identity.target_pcis",
        },
        "example": {
            "type": "TRIGGER_HANDOVER_CLI",
            "serving_pci": 1,
            "rnti": "0x4601",
            "target_pci": 2,
        },
    },
    "TRIGGER_CONDITIONAL_HANDOVER_CLI": {
        "summary": "Install a conditional handover plan with multiple candidate targets.",
        "fields": {
            "serving_pci": "int PCI 0..1007",
            "rnti": "hex string or int 1..65535",
            "target_pcis": "list of 1..8 PCI ints, subset of evidence.ue_identity.target_pcis",
            "timeout_s": "int > 0 (defer time before the plan expires)",
        },
        "example": {
            "type": "TRIGGER_CONDITIONAL_HANDOVER_CLI",
            "serving_pci": 1,
            "rnti": "0x4601",
            "target_pcis": [2, 3],
            "timeout_s": 5,
        },
    },
    "SET_CFO_CLI": {
        "summary": "Apply a carrier-frequency-offset compensation in Hz on one sector.",
        "fields": {
            "sector_id": "int 0..255 (from evidence.radio_runtime.sector_id)",
            "cfo_hz": "float in [-100000, 100000]",
        },
        "example": {"type": "SET_CFO_CLI", "sector_id": 0, "cfo_hz": -1250.0},
    },
    "SET_TX_TIME_OFFSET_CLI": {
        "summary": "Set TX time offset in microseconds on one sector.",
        "fields": {
            "sector_id": "int 0..255",
            "tx_time_offset_us": "float in [-10000, 10000]",
        },
        "example": {"type": "SET_TX_TIME_OFFSET_CLI", "sector_id": 0, "tx_time_offset_us": 7.5},
    },
    "RESTART_CORE_NF": {
        "summary": "Restart one core network function in the simulated runtime.",
        "fields": {
            "nf": "one of 'amf', 'smf', 'upf', 'open5gs'",
        },
        "example": {"type": "RESTART_CORE_NF", "nf": "open5gs"},
    },
    "UPDATE_CORE_UE_REGISTRATION": {
        "summary": "Repair the core UE registration to the desired profile.",
        "fields": {
            "ue_id": "non-empty identifier (e.g. 'ue1')",
            "supi": "5..16 digit IMSI/SUPI string",
            "plmn": "5 or 6 digit PLMN id",
            "dnn": "lowercase DNN string",
            "sst": "int 0..255",
            "sd": "int 0..16777215 or null",
            "auth_profile_id": "non-empty profile id",
        },
        "example": {
            "type": "UPDATE_CORE_UE_REGISTRATION",
            "ue_id": "ue1",
            "supi": "001010000000001",
            "plmn": "00101",
            "dnn": "internet",
            "sst": 1,
            "sd": None,
            "auth_profile_id": "ue1_test_profile",
        },
    },
    "NO_ACTION": {
        "summary": "Do nothing this step. Use when no repair target is yet visible or the budget is exhausted.",
        "fields": {},
        "example": None,
    },
}


def cheatsheet_for(allowed_action_types: list[str]) -> dict[str, dict[str, Any]]:
    """Filter the cheatsheet to the action types the task permits."""

    return {name: ACTION_CHEATSHEET[name] for name in allowed_action_types if name in ACTION_CHEATSHEET}
