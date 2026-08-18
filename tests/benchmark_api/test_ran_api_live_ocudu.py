"""Tests for the live_ocudu dispatch branch in ran_api.py.

All live_ocudu I/O is mocked — no WebSocket or real gNB required.
Covers:
  - SET_PRB_POLICY_RATIO_WS happy path (WS-backed)
  - SET_SSB_BLOCK_POWER_WS happy path (WS-backed)
  - TRIGGER_HANDOVER_CLI / TRIGGER_CONDITIONAL_HANDOVER_CLI / SET_CFO_CLI /
    SET_TX_TIME_OFFSET_CLI: OCUDU exposes these only on stdin; routed through
    live_ocudu.dispatch_cli_action when adapter=live_ocudu. send_command must
    NOT be called for them.
  - LiveOcuduError from dispatch_cli_action → RUNTIME_UNAVAILABLE, accepted=False
  - LiveOcuduError → RUNTIME_UNAVAILABLE, accepted=False
  - Non-WS action (RESTART_CORE_NF) on live_ocudu → backend gate blocks
  - WS action on simulated adapter → simulated path, send_command not called
  - NO_ACTION → dispatched=False, accepted=True
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch, call
from typing import Any

from benchmark.benchmark_api.live_ocudu import LiveOcuduConfig, LiveOcuduError
from benchmark.benchmark_api.ran_api import dispatch_runtime_action, build_request
from benchmark.benchmark_api.runtime_setup import RuntimeHandle, LIVE_OCUDU_ADAPTER, SIMULATED_ADAPTER
from benchmark.benchmark_api.types import RanActionType, SafeErrorClass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_live_ocudu_handle(
    state_overrides: dict[str, Any] | None = None,
) -> RuntimeHandle:
    """Build a RuntimeHandle that looks like a ready live_ocudu adapter.

    backend.websocket=True and backend.ocudu_cli=True so all six WS-backed
    actions pass the backend_ready gate.
    """
    state: dict[str, Any] = {
        "backend": {
            "websocket": True,
            "ocudu_cli": True,
            "core_control": False,
            "json_metrics": True,
            "e2_kpm": False,
            "e2_control": False,
        },
        "core_runtime": {
            "running": False,
            "available_nfs": [],
            "nf_status": {},
            "restart_counts": {},
            "last_restarted_nf": None,
            "ue_registration": {},
        },
        "control_outcomes": [],
    }
    if state_overrides:
        state.update(state_overrides)
    return RuntimeHandle(
        run_id="test-run-lo",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=LIVE_OCUDU_ADAPTER,
        components=(),
        state=state,
        ready=True,
    )


def _make_simulated_handle(
    state_overrides: dict[str, Any] | None = None,
) -> RuntimeHandle:
    """Build a RuntimeHandle for the simulated_ocudu adapter with websocket+ocudu_cli=True."""
    state: dict[str, Any] = {
        "backend": {
            "websocket": True,
            "ocudu_cli": True,
            "core_control": True,
            "json_metrics": True,
            "e2_kpm": False,
            "e2_control": False,
        },
        "core_runtime": {
            "running": True,
            "available_nfs": ["amf", "smf", "upf", "open5gs"],
            "nf_status": {"amf": "running", "smf": "running", "upf": "running", "open5gs": "running"},
            "restart_counts": {},
            "last_restarted_nf": None,
            "ue_registration": {
                "ue_id": "ue1",
                "status": "registered",
                "desired": {},
                "current": {},
                "mismatch_fields": [],
                "last_updated_by": "runtime_setup",
            },
        },
        "radio_runtime": {
            "sector_id": 0, "cfo_hz": 0.0, "target_cfo_hz": -1250.0,
            "tx_time_offset_us": 0.0, "target_tx_time_offset_us": 7.5,
            "current_ssb_block_power_dbm": -20, "condition_profile": None,
            "pathloss_db": 80.0, "noise_dbm": -96.0, "sinr_db": 20.0,
            "cqi": 10, "rsrp_dbm": -82.0, "rsrq_db": -9.0, "zmq_impairment": None,
        },
        "slice_runtime": {
            "active_slice": {"plmn": "00101", "sst": 1, "sd": None},
            "demand_level": "nominal",
            "active_ues": 1,
            "queue_pressure": 0.0,
            "prb_utilization": 0.35,
            "target_prb_policy": None,
            "current_prb_policy": {
                "plmn": "00101", "sst": 1, "sd": None,
                "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80,
                "backend": "bootstrap", "action_type": None,
            },
        },
        "ue_identity": {"du_ue_id": 1, "rnti": "0x4601", "serving_pci": 1, "target_pcis": [2]},
        "control_outcomes": [],
    }
    if state_overrides:
        state.update(state_overrides)
    return RuntimeHandle(
        run_id="test-run-sim",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=SIMULATED_ADAPTER,
        components=("open5gs", "ocudu_websocket", "ocudu_cli"),
        state=state,
        ready=True,
    )


# Shared action fixtures
_PRB_ACTION = {
    "type": "SET_PRB_POLICY_RATIO_WS",
    "plmn": "00101",
    "sst": 1,
    "min_prb_policy_ratio": 20,
    "max_prb_policy_ratio": 80,
}
_SSB_ACTION = {
    "type": "SET_SSB_BLOCK_POWER_WS",
    "plmn": "00101",
    "nci": 6733824,
    "ssb_block_power_dbm": -16,
}
_HO_ACTION = {
    "type": "TRIGGER_HANDOVER_CLI",
    "serving_pci": 1,
    "rnti": "0x4601",
    "target_pci": 2,
}
_CHO_ACTION = {
    "type": "TRIGGER_CONDITIONAL_HANDOVER_CLI",
    "serving_pci": 1,
    "rnti": "0x4601",
    "target_pcis": [2, 3],
}
_CFO_ACTION = {
    "type": "SET_CFO_CLI",
    "sector_id": 0,
    "cfo_hz": -1250.0,
}
_TX_TIME_ACTION = {
    "type": "SET_TX_TIME_OFFSET_CLI",
    "sector_id": 0,
    "tx_time_offset_us": 7.5,
}
_RESTART_NF_ACTION = {
    "type": "RESTART_CORE_NF",
    "nf": "upf",
}
_NO_ACTION = {"type": "NO_ACTION"}


def _ok_ack(cmd: str, timestamp: str = "2026-05-25T00:00:00Z") -> dict[str, Any]:
    return {"cmd": cmd, "timestamp": timestamp}


# ---------------------------------------------------------------------------
# 1. SET_PRB_POLICY_RATIO_WS happy path
# ---------------------------------------------------------------------------

class SetPrbPolicyWsHappyPath(unittest.TestCase):

    def test_dispatched_accepted_safe_message_contains_timestamp(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("rrm_policy_ratio_set", "2026-05-25T10:00:00Z")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _PRB_ACTION)
        mock_sc.assert_called_once()
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.safe_error_class)
        self.assertIn("2026-05-25T10:00:00Z", result.safe_message)
        self.assertEqual(result.backend, "websocket")

    def test_send_command_called_with_correct_cmd(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("rrm_policy_ratio_set")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            dispatch_runtime_action(handle, "act-1", _PRB_ACTION)
        sent_request = mock_sc.call_args[0][1]
        self.assertEqual(sent_request["cmd"], "rrm_policy_ratio_set")


# ---------------------------------------------------------------------------
# 2. SET_SSB_BLOCK_POWER_WS happy path
# ---------------------------------------------------------------------------

class SetSsbBlockPowerWsHappyPath(unittest.TestCase):

    def test_dispatched_accepted(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("ssb_set", "2026-05-25T11:00:00Z")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _SSB_ACTION)
        mock_sc.assert_called_once()
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIn("2026-05-25T11:00:00Z", result.safe_message)
        self.assertEqual(result.backend, "websocket")


# ---------------------------------------------------------------------------
# 3. CLI-backed actions are dispatched via dispatch_cli_action on live_ocudu
# ---------------------------------------------------------------------------
# Per the 2026-05-25 live smoke, OCUDU's remote_control WebSocket dispatcher
# (apps/services/remote_control/remote_server.cpp) only registers handlers
# for `rrm_policy_ratio_set`, `ssb_set`, `metrics_subscribe`,
# `metrics_unsubscribe`, and `quit`. The `ho`, `cho`, `cfo`, and
# `tx_time_offset` commands live in *_cmdline_commands.h and are stdin-only.
# When the live_ocudu adapter is active, those actions are routed through
# live_ocudu.dispatch_cli_action — send_command must NOT be called.

_CLI_OK_RESULT = {"ok": True, "line": "cfo 0 -1250.0", "stdout_tail": ""}


class CliActionsDispatchedViaCli(unittest.TestCase):

    def _assert_cli_dispatch(self, action, expected_action_type_value: str | None = None):
        """Assert dispatch_cli_action is called once and result is dispatched+accepted."""
        handle = _make_live_ocudu_handle()
        with patch("benchmark.benchmark_api.live_ocudu.send_command") as mock_sc, \
             patch("benchmark.benchmark_api.live_ocudu.dispatch_cli_action",
                   return_value=_CLI_OK_RESULT) as mock_cli:
            result = dispatch_runtime_action(handle, "act-1", action)
        mock_sc.assert_not_called()
        mock_cli.assert_called_once()
        # action_type_value is the second positional arg to dispatch_cli_action
        if expected_action_type_value is not None:
            self.assertEqual(mock_cli.call_args[0][1], expected_action_type_value)
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.safe_error_class)

    def test_ho_dispatched_via_cli(self):
        self._assert_cli_dispatch(_HO_ACTION, "TRIGGER_HANDOVER_CLI")

    def test_cho_dispatched_via_cli(self):
        self._assert_cli_dispatch(_CHO_ACTION, "TRIGGER_CONDITIONAL_HANDOVER_CLI")

    def test_cfo_dispatched_via_cli(self):
        self._assert_cli_dispatch(_CFO_ACTION, "SET_CFO_CLI")

    def test_tx_time_offset_dispatched_via_cli(self):
        self._assert_cli_dispatch(_TX_TIME_ACTION, "SET_TX_TIME_OFFSET_CLI")

    def test_build_request_still_produces_ho_payload(self):
        """build_request itself is unchanged — it produces the expected ho payload
        regardless of dispatch routing."""
        request = build_request(RanActionType.TRIGGER_HANDOVER_CLI, _HO_ACTION)
        self.assertEqual(request["cmd"], "ho")
        self.assertEqual(request["argv"], [1, "0x4601", 2])

    def test_cli_error_returns_runtime_unavailable(self):
        """LiveOcuduError from dispatch_cli_action → dispatched=True, accepted=False,
        RUNTIME_UNAVAILABLE."""
        handle = _make_live_ocudu_handle()
        exc = LiveOcuduError("gnb rejected 'cfo 0 -1250.0': Invalid cfo command structure.")
        with patch("benchmark.benchmark_api.live_ocudu.dispatch_cli_action",
                   side_effect=exc):
            result = dispatch_runtime_action(handle, "act-1", _CFO_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertIn("Invalid cfo command structure", result.safe_message)


# ---------------------------------------------------------------------------
# 7. LiveOcuduError → RUNTIME_UNAVAILABLE, accepted=False
# ---------------------------------------------------------------------------

class LiveOcuduErrorHandling(unittest.TestCase):

    def test_live_ocudu_error_returns_runtime_unavailable(self):
        """LiveOcuduError from send_command → dispatched=True, accepted=False, RUNTIME_UNAVAILABLE."""
        handle = _make_live_ocudu_handle()
        exc = LiveOcuduError("ws closed unexpectedly")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", side_effect=exc):
            result = dispatch_runtime_action(handle, "act-1", _PRB_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertEqual(result.safe_message, "ws closed unexpectedly")

    def test_live_ocudu_error_on_ssb_action(self):
        """LiveOcuduError on another WS-backed action (SSB) also surfaces as RUNTIME_UNAVAILABLE."""
        handle = _make_live_ocudu_handle()
        exc = LiveOcuduError("connection timeout")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", side_effect=exc):
            result = dispatch_runtime_action(handle, "act-1", _SSB_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertIn("connection timeout", result.safe_message)


# ---------------------------------------------------------------------------
# 8. Non-WS action (RESTART_CORE_NF) on live_ocudu → simulated path
# ---------------------------------------------------------------------------

class NonWsActionOnLiveOcuduAdapter(unittest.TestCase):

    def test_restart_core_nf_falls_through_to_simulated(self):
        """RESTART_CORE_NF with core_control=False → RUNTIME_UNAVAILABLE from backend gate.

        live_ocudu.send_command must NOT be called.
        """
        # live_ocudu handle has core_control=False, so the backend gate blocks it
        handle = _make_live_ocudu_handle()
        with patch("benchmark.benchmark_api.live_ocudu.send_command") as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _RESTART_NF_ACTION)
        mock_sc.assert_not_called()
        # Backend gate fires before any dispatch
        self.assertFalse(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)


# ---------------------------------------------------------------------------
# 9. WS action on simulated adapter → simulated path (send_command not called)
# ---------------------------------------------------------------------------

class WsActionOnSimulatedAdapter(unittest.TestCase):

    def test_prb_action_on_simulated_adapter_does_not_call_send_command(self):
        """SET_PRB_POLICY_RATIO_WS on simulated_ocudu uses simulated path; send_command not called."""
        handle = _make_simulated_handle()
        with patch("benchmark.benchmark_api.live_ocudu.send_command") as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _PRB_ACTION)
        mock_sc.assert_not_called()
        # Simulated path should dispatch successfully
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)


# ---------------------------------------------------------------------------
# 10. NO_ACTION always returns dispatched=False, accepted=True
# ---------------------------------------------------------------------------

class NoActionTests(unittest.TestCase):

    def test_no_action_on_live_ocudu_adapter(self):
        """NO_ACTION always returns dispatched=False, accepted=True regardless of adapter."""
        handle = _make_live_ocudu_handle()
        with patch("benchmark.benchmark_api.live_ocudu.send_command") as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _NO_ACTION)
        mock_sc.assert_not_called()
        self.assertFalse(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.safe_error_class)


if __name__ == "__main__":
    unittest.main()
