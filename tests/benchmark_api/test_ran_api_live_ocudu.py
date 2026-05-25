"""Tests for the live_ocudu dispatch branch in ran_api.py.

All live_ocudu I/O is mocked — no WebSocket or real gNB required.
Covers:
  - SET_PRB_POLICY_RATIO_WS happy path
  - SET_SSB_BLOCK_POWER_WS happy path
  - TRIGGER_HANDOVER_CLI happy path (cmd and argv verified)
  - TRIGGER_CONDITIONAL_HANDOVER_CLI happy path
  - SET_CFO_CLI happy path
  - SET_TX_TIME_OFFSET_CLI happy path
  - LiveOcuduError → RUNTIME_UNAVAILABLE, accepted=False
  - Non-WS action (RESTART_CORE_NF) on live_ocudu → simulated path, send_command not called
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
# 3. TRIGGER_HANDOVER_CLI happy path (verify build_request payload)
# ---------------------------------------------------------------------------

class TriggerHandoverCliHappyPath(unittest.TestCase):

    def test_dispatched_accepted_with_ho_cmd(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("ho")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _HO_ACTION)
        mock_sc.assert_called_once()
        sent_request = mock_sc.call_args[0][1]
        self.assertEqual(sent_request["cmd"], "ho")
        self.assertEqual(sent_request["argv"], [1, "0x4601", 2])
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertEqual(result.backend, "ocudu_cli")

    def test_build_request_produces_ho_payload(self):
        """Standalone check that build_request produces the expected ho payload."""
        request = build_request(RanActionType.TRIGGER_HANDOVER_CLI, _HO_ACTION)
        self.assertEqual(request["cmd"], "ho")
        self.assertEqual(request["argv"], [1, "0x4601", 2])


# ---------------------------------------------------------------------------
# 4. TRIGGER_CONDITIONAL_HANDOVER_CLI happy path
# ---------------------------------------------------------------------------

class TriggerConditionalHandoverCliHappyPath(unittest.TestCase):

    def test_dispatched_accepted_with_cho_cmd(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("cho")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _CHO_ACTION)
        mock_sc.assert_called_once()
        sent_request = mock_sc.call_args[0][1]
        self.assertEqual(sent_request["cmd"], "cho")
        self.assertIn(1, sent_request["argv"])       # serving_pci
        self.assertIn("0x4601", sent_request["argv"])  # rnti
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertEqual(result.backend, "ocudu_cli")


# ---------------------------------------------------------------------------
# 5. SET_CFO_CLI happy path
# ---------------------------------------------------------------------------

class SetCfoCliHappyPath(unittest.TestCase):

    def test_dispatched_accepted_with_cfo_cmd(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("cfo")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _CFO_ACTION)
        mock_sc.assert_called_once()
        sent_request = mock_sc.call_args[0][1]
        self.assertEqual(sent_request["cmd"], "cfo")
        self.assertEqual(sent_request["argv"], [0, -1250.0])
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertEqual(result.backend, "ocudu_cli")


# ---------------------------------------------------------------------------
# 6. SET_TX_TIME_OFFSET_CLI happy path
# ---------------------------------------------------------------------------

class SetTxTimeOffsetCliHappyPath(unittest.TestCase):

    def test_dispatched_accepted_with_tx_time_offset_cmd(self):
        handle = _make_live_ocudu_handle()
        ack = _ok_ack("tx_time_offset")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", return_value=ack) as mock_sc:
            result = dispatch_runtime_action(handle, "act-1", _TX_TIME_ACTION)
        mock_sc.assert_called_once()
        sent_request = mock_sc.call_args[0][1]
        self.assertEqual(sent_request["cmd"], "tx_time_offset")
        self.assertEqual(sent_request["argv"], [0, 7.5])
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertEqual(result.backend, "ocudu_cli")


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

    def test_live_ocudu_error_on_cli_action(self):
        """LiveOcuduError on a CLI action (TRIGGER_HANDOVER_CLI) also surfaces as RUNTIME_UNAVAILABLE."""
        handle = _make_live_ocudu_handle()
        exc = LiveOcuduError("connection timeout")
        with patch("benchmark.benchmark_api.live_ocudu.send_command", side_effect=exc):
            result = dispatch_runtime_action(handle, "act-1", _HO_ACTION)
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
