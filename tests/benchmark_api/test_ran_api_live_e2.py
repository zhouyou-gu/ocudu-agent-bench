"""Tests for the live_e2 evidence refresh branch in ran_api.read_evidence.

All live_e2 I/O is mocked — no FlexRIC container required.
Covers:
  - E2_KPM_V05 evidence refreshed from live_e2.read_kpm when adapter=live_e2
  - evidence contains flattened measurements + has_prb_measurement=True
  - read_kpm raises → falls back to state-based evidence (no exception out)
  - E2_KPM_V05 with simulated adapter → live_e2.read_kpm NOT called
  - non-E2 evidence still works for live_e2 adapter
  - empty records → kpm_indications=0, has_prb_measurement=False
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
from typing import Any

from benchmark.benchmark_api.ran_api import read_evidence, dispatch_runtime_action
from benchmark.benchmark_api.runtime_setup import RuntimeHandle, LIVE_E2_ADAPTER, SIMULATED_ADAPTER
from benchmark.benchmark_api.live_e2 import LiveE2Error
from benchmark.benchmark_api.types import SafeErrorClass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_live_e2_handle(state_overrides: dict[str, Any] | None = None) -> RuntimeHandle:
    """Build a RuntimeHandle that looks like a ready live_e2 adapter."""
    state: dict[str, Any] = {
        "backend": {
            "websocket": False,
            "ocudu_cli": False,
            "core_control": False,
            "json_metrics": False,
            "e2_kpm": True,
            "e2_control": False,
        },
        "e2": {
            "enabled": True,
            "kpm_indications": 0,
            "has_prb_measurement": False,
            "ric_xapp_running": False,
            "du_ue_id": 1,
        },
        "ping": {"packets_transmitted": 0, "packets_received": 0, "success_ratio": 0.0},
        "cell_identity": {"plmn": "00101", "nci": 6733824, "gnb_id": 411, "sector_id": 0},
    }
    if state_overrides:
        state.update(state_overrides)
    return RuntimeHandle(
        run_id="test-run-e2",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=LIVE_E2_ADAPTER,
        components=(),
        state=state,
        ready=True,
    )


def _make_simulated_handle(state_overrides: dict[str, Any] | None = None) -> RuntimeHandle:
    """Build a RuntimeHandle for the simulated_ocudu adapter."""
    state: dict[str, Any] = {
        "backend": {
            "websocket": True,
            "ocudu_cli": True,
            "core_control": True,
            "json_metrics": True,
            "e2_kpm": True,
            "e2_control": False,
        },
        "e2": {
            "enabled": True,
            "kpm_indications": 3,
            "has_prb_measurement": True,
            "ric_xapp_running": False,
            "du_ue_id": 1,
        },
        "ping": {"packets_transmitted": 0, "packets_received": 0, "success_ratio": 0.0},
    }
    if state_overrides:
        state.update(state_overrides)
    return RuntimeHandle(
        run_id="test-run-sim",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=SIMULATED_ADAPTER,
        components=("open5gs", "flexric", "e2_kpm_decoder"),
        state=state,
        ready=True,
    )


def _make_kpm_records(n: int = 3) -> list[dict[str, Any]]:
    """Return a list of n KPM records with PRB measurements."""
    return [
        {
            "decoded_by": "ocudu-generated-asn1-cpp",
            "kpm_version": "E2SM-KPM-R003-v05.00",
            "format": "ind_msg_format1",
            "measurements": [
                {"name": "RRU.PrbAvailDl", "type": "integer", "value": 106 + i},
                {"name": "RRU.PrbUsedDl", "type": "integer", "value": i},
            ],
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. E2_KPM_V05 evidence refreshed from live_e2.read_kpm
# ---------------------------------------------------------------------------

class LiveE2KpmEvidenceRefreshTests(unittest.TestCase):

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_e2_kpm_v05_evidence_uses_live_records(self, mock_read_kpm):
        """With adapter=live_e2, e2_kpm_v05 evidence is built from live_e2.read_kpm."""
        records = _make_kpm_records(3)
        mock_read_kpm.return_value = records
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertIn("e2_kpm_v05", evidence)
        mock_read_kpm.assert_called_once()

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_e2_kpm_v05_kpm_indications_count(self, mock_read_kpm):
        """kpm_indications = number of records returned by read_kpm."""
        records = _make_kpm_records(3)
        mock_read_kpm.return_value = records
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertEqual(evidence["e2_kpm_v05"]["kpm_indications"], 3)

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_e2_kpm_v05_has_prb_measurement_true(self, mock_read_kpm):
        """has_prb_measurement=True when last record contains RRU.Prb* keys."""
        records = _make_kpm_records(2)
        mock_read_kpm.return_value = records
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertTrue(evidence["e2_kpm_v05"]["has_prb_measurement"])

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_e2_kpm_v05_measurements_flattened(self, mock_read_kpm):
        """measurements dict contains {name: value} from the last record."""
        records = _make_kpm_records(3)
        mock_read_kpm.return_value = records
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        meas = evidence["e2_kpm_v05"].get("measurements", {})
        self.assertIn("RRU.PrbAvailDl", meas)
        self.assertIn("RRU.PrbUsedDl", meas)
        # Last record (index 2): PrbAvailDl=108, PrbUsedDl=2
        self.assertEqual(meas["RRU.PrbAvailDl"], 108)

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_e2_kpm_v05_kpm_version_present(self, mock_read_kpm):
        """kpm_version is taken from the last record."""
        records = _make_kpm_records(1)
        mock_read_kpm.return_value = records
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertEqual(evidence["e2_kpm_v05"]["kpm_version"], "E2SM-KPM-R003-v05.00")


# ---------------------------------------------------------------------------
# 2. Fallback when read_kpm raises
# ---------------------------------------------------------------------------

class LiveE2KpmFallbackTests(unittest.TestCase):

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_read_kpm_raises_falls_back_to_state(self, mock_read_kpm):
        """LiveE2Error from read_kpm → falls back to state-based e2_kpm_v05, no exception."""
        mock_read_kpm.side_effect = LiveE2Error("docker exec failed")
        handle = _make_live_e2_handle({
            "e2": {
                "enabled": True,
                "kpm_indications": 5,
                "has_prb_measurement": True,
            }
        })
        # Should NOT raise
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertIn("e2_kpm_v05", evidence)
        # Falls back to state values
        self.assertEqual(evidence["e2_kpm_v05"]["kpm_indications"], 5)
        self.assertTrue(evidence["e2_kpm_v05"]["has_prb_measurement"])


# ---------------------------------------------------------------------------
# 3. Simulated adapter does NOT call live_e2.read_kpm
# ---------------------------------------------------------------------------

class SimulatedAdapterNotCallingLiveE2ReadKpm(unittest.TestCase):

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_simulated_adapter_does_not_call_read_kpm(self, mock_read_kpm):
        """With adapter=simulated_ocudu, live_e2.read_kpm must NOT be called."""
        handle = _make_simulated_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        mock_read_kpm.assert_not_called()
        self.assertIn("e2_kpm_v05", evidence)
        # State-based evidence: kpm_indications=3 from state
        self.assertEqual(evidence["e2_kpm_v05"]["kpm_indications"], 3)


# ---------------------------------------------------------------------------
# 4. Empty records
# ---------------------------------------------------------------------------

class EmptyRecordsTests(unittest.TestCase):

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_empty_records_kpm_indications_zero(self, mock_read_kpm):
        """If read_kpm returns [], kpm_indications=0 and has_prb_measurement=False."""
        mock_read_kpm.return_value = []
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertEqual(evidence["e2_kpm_v05"]["kpm_indications"], 0)
        self.assertFalse(evidence["e2_kpm_v05"]["has_prb_measurement"])
        self.assertIsNone(evidence["e2_kpm_v05"]["kpm_version"])

    @patch("benchmark.benchmark_api.live_e2.read_kpm")
    def test_empty_records_measurements_empty(self, mock_read_kpm):
        """Empty records → measurements dict is empty."""
        mock_read_kpm.return_value = []
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("e2_kpm_v05",))
        self.assertEqual(evidence["e2_kpm_v05"]["measurements"], {})


# ---------------------------------------------------------------------------
# 5. Non-E2 evidence still works for live_e2 adapter
# ---------------------------------------------------------------------------

class NonE2EvidenceWithLiveE2AdapterTests(unittest.TestCase):

    def test_ping_evidence_works_for_live_e2_adapter(self):
        """PING evidence reads from state normally even with live_e2 adapter."""
        handle = _make_live_e2_handle({
            "ping": {"packets_transmitted": 10, "packets_received": 10, "success_ratio": 1.0}
        })
        evidence = read_evidence(handle, ("ping",))
        self.assertIn("ping", evidence)
        self.assertEqual(evidence["ping"]["packets_transmitted"], 10)

    def test_cell_identity_evidence_works_for_live_e2_adapter(self):
        """CELL_IDENTITY evidence reads from state normally even with live_e2 adapter."""
        handle = _make_live_e2_handle()
        evidence = read_evidence(handle, ("cell_identity",))
        self.assertIn("cell_identity", evidence)
        self.assertEqual(evidence["cell_identity"]["plmn"], "00101")


# ---------------------------------------------------------------------------
# 6. SET_PRB_POLICY_RATIO_RC_DU dispatch tests (live_e2 adapter)
# ---------------------------------------------------------------------------

_SUCCESS_XAPP_RESULT = {
    "accepted": True,
    "action_type": "SET_PRB_POLICY_RATIO_RC_DU",
    "ran_function_id": 3,
    "control_name": "slice-level PRB quota",
    "control_style": 2,
    "control_action": 6,
    "request": {},
    "outcome": {
        "acknowledged": True,
        "evidence": "OCUDU E2SM-RC control acknowledged",
    },
}

_FAILURE_XAPP_RESULT = {
    "accepted": False,
    "action_type": "SET_PRB_POLICY_RATIO_RC_DU",
    "error": "no E2 node found with the given du_ue_id",
}


def _make_rc_du_live_e2_handle() -> RuntimeHandle:
    """Build a RuntimeHandle with live_e2 adapter and e2_control=True."""
    state: dict[str, Any] = {
        "backend": {
            "websocket": False,
            "ocudu_cli": False,
            "core_control": False,
            "json_metrics": False,
            "e2_kpm": True,
            "e2_control": True,
        },
        "e2": {
            "enabled": True,
            "kpm_indications": 5,
            "has_prb_measurement": True,
            "ric_xapp_running": True,
            "du_ue_id": 0,
        },
        "ping": {"packets_transmitted": 0, "packets_received": 0, "success_ratio": 0.0},
        "cell_identity": {"plmn": "00101", "nci": 6733824, "gnb_id": 411, "sector_id": 0},
    }
    return RuntimeHandle(
        run_id="test-run-rc-du",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=LIVE_E2_ADAPTER,
        components=(),
        state=state,
        ready=True,
    )


def _make_simulated_handle_with_e2_control() -> RuntimeHandle:
    """Build a RuntimeHandle for simulated_ocudu with e2_control=True."""
    state: dict[str, Any] = {
        "backend": {
            "websocket": True,
            "ocudu_cli": True,
            "core_control": True,
            "json_metrics": True,
            "e2_kpm": True,
            "e2_control": True,
        },
        "e2": {
            "enabled": True,
            "kpm_indications": 3,
            "has_prb_measurement": True,
            "ric_xapp_running": True,
            "du_ue_id": 0,
        },
        "ping": {"packets_transmitted": 0, "packets_received": 0, "success_ratio": 0.0},
        "slice_runtime": {
            "active_slice": {"plmn": "00101", "sst": 1, "sd": None},
            "demand_level": "nominal",
            "active_ues": 1,
            "queue_pressure": 0.0,
            "prb_utilization": 0.35,
            "target_prb_policy": {"min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80},
            "current_prb_policy": {
                "plmn": "00101", "sst": 1, "sd": None,
                "min_prb_policy_ratio": 20, "max_prb_policy_ratio": 80,
                "backend": "bootstrap", "action_type": None,
            },
        },
    }
    return RuntimeHandle(
        run_id="test-run-sim-e2",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=SIMULATED_ADAPTER,
        components=("open5gs", "flexric", "e2_kpm_decoder", "e2_control_xapp"),
        state=state,
        ready=True,
    )


_RC_DU_ACTION = {
    "type": "SET_PRB_POLICY_RATIO_RC_DU",
    "du_ue_id": 0,
    "plmn": "00101",
    "sst": 1,
    "min_prb_policy_ratio": 10,
    "max_prb_policy_ratio": 90,
}


class LiveE2RcDuDispatchHappyPathTests(unittest.TestCase):

    @patch("benchmark.benchmark_api.live_e2.dispatch_rc_du_prb_policy")
    def test_happy_path_dispatched_accepted(self, mock_dispatch):
        """SET_PRB_POLICY_RATIO_RC_DU + live_e2 + accepted=True → dispatched=True, accepted=True."""
        mock_dispatch.return_value = _SUCCESS_XAPP_RESULT
        handle = _make_rc_du_live_e2_handle()
        result = dispatch_runtime_action(handle, "act-001", _RC_DU_ACTION)
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIn("acknowledged", result.safe_message)
        mock_dispatch.assert_called_once()

    @patch("benchmark.benchmark_api.live_e2.dispatch_rc_du_prb_policy")
    def test_xapp_accepted_false_runtime_unavailable(self, mock_dispatch):
        """accepted=False from xApp → dispatched=True, accepted=False, RUNTIME_UNAVAILABLE."""
        mock_dispatch.return_value = _FAILURE_XAPP_RESULT
        handle = _make_rc_du_live_e2_handle()
        result = dispatch_runtime_action(handle, "act-002", _RC_DU_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertIn("rejected", result.safe_message)
        self.assertIn("du_ue_id", result.safe_message)

    @patch("benchmark.benchmark_api.live_e2.dispatch_rc_du_prb_policy")
    def test_live_e2_error_runtime_unavailable(self, mock_dispatch):
        """LiveE2Error from dispatch → dispatched=True, accepted=False, RUNTIME_UNAVAILABLE."""
        mock_dispatch.side_effect = LiveE2Error("docker exec: container not running")
        handle = _make_rc_du_live_e2_handle()
        result = dispatch_runtime_action(handle, "act-003", _RC_DU_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertEqual(result.safe_message, "docker exec: container not running")

    @patch("benchmark.benchmark_api.live_e2.dispatch_rc_du_prb_policy")
    def test_non_rc_action_on_live_e2_not_dispatched_via_rc_du(self, mock_dispatch):
        """A non-RC action on live_e2 falls through to simulated path; dispatch_rc_du_prb_policy NOT called."""
        # RESTART_CORE_NF is a core_control action — backend not ready for live_e2
        # so it will fail with RUNTIME_UNAVAILABLE before any dispatch; mock not called.
        handle = _make_rc_du_live_e2_handle()
        core_action = {"type": "RESTART_CORE_NF", "nf": "amf"}
        result = dispatch_runtime_action(handle, "act-004", core_action)
        mock_dispatch.assert_not_called()
        # core_control is False for live_e2, so it should be RUNTIME_UNAVAILABLE
        self.assertFalse(result.dispatched)
        self.assertFalse(result.accepted)

    @patch("benchmark.benchmark_api.live_e2.dispatch_rc_du_prb_policy")
    def test_simulated_adapter_does_not_call_dispatch_rc_du(self, mock_dispatch):
        """SET_PRB_POLICY_RATIO_RC_DU on simulated adapter → simulated path; dispatch_rc_du_prb_policy NOT called."""
        handle = _make_simulated_handle_with_e2_control()
        result = dispatch_runtime_action(handle, "act-005", _RC_DU_ACTION)
        mock_dispatch.assert_not_called()
        # simulated path accepts
        self.assertTrue(result.dispatched)

    def test_missing_du_ue_id_raises_key_error(self):
        """Action dict without du_ue_id → KeyError from int(action['du_ue_id']) propagates."""
        action_no_id = {
            "type": "SET_PRB_POLICY_RATIO_RC_DU",
            "plmn": "00101",
            "sst": 1,
            "min_prb_policy_ratio": 10,
            "max_prb_policy_ratio": 90,
            # du_ue_id intentionally absent
        }
        with patch("benchmark.benchmark_api.live_e2.dispatch_rc_du_prb_policy"):
            handle = _make_rc_du_live_e2_handle()
            with self.assertRaises(KeyError):
                dispatch_runtime_action(handle, "act-006", action_no_id)


# ---------------------------------------------------------------------------
# 7. SET_PRB_POLICY_RATIO_CCC dispatch tests (live_e2 adapter)
# ---------------------------------------------------------------------------

_CCC_SUCCESS_XAPP_RESULT = {
    "accepted": True,
    "action_type": "SET_PRB_POLICY_RATIO_CCC",
    "ran_function_id": 4,
    "control_name": "O-RRMPolicyRatio",
    "control_style": 2,
    "control_action": 6,
    "request": {},
    "outcome": {
        "acknowledged": True,
        "evidence": "FlexRIC E2SM-CCC control acknowledged",
    },
}

_CCC_FAILURE_XAPP_RESULT = {
    "accepted": False,
    "action_type": "SET_PRB_POLICY_RATIO_CCC",
    "error": "missing required PRB min/max policy ratio",
}

_CCC_ACTION = {
    "type": "SET_PRB_POLICY_RATIO_CCC",
    "plmn": "00101",
    "sst": 1,
    "sd": 0xFFFFFF,
    "min_prb_policy_ratio": 30,
    "max_prb_policy_ratio": 70,
    "dedicated_ratio": 50,
}


class LiveE2CccDispatchTests(unittest.TestCase):
    """SET_PRB_POLICY_RATIO_CCC + live_e2 routing tests."""

    @patch("benchmark.benchmark_api.live_e2.dispatch_ccc_prb_policy")
    def test_happy_path_dispatched_accepted(self, mock_dispatch):
        mock_dispatch.return_value = _CCC_SUCCESS_XAPP_RESULT
        handle = _make_rc_du_live_e2_handle()
        result = dispatch_runtime_action(handle, "ccc-001", _CCC_ACTION)
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIn("acknowledged", result.safe_message)
        mock_dispatch.assert_called_once()
        # CCC is cell-level: dispatch must NOT receive du_ue_id.
        kwargs = mock_dispatch.call_args.kwargs
        self.assertNotIn("du_ue_id", kwargs)
        self.assertEqual(kwargs.get("dedicated_ratio"), 50)

    @patch("benchmark.benchmark_api.live_e2.dispatch_ccc_prb_policy")
    def test_xapp_accepted_false_runtime_unavailable(self, mock_dispatch):
        mock_dispatch.return_value = _CCC_FAILURE_XAPP_RESULT
        handle = _make_rc_du_live_e2_handle()
        result = dispatch_runtime_action(handle, "ccc-002", _CCC_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertIn("rejected", result.safe_message)

    @patch("benchmark.benchmark_api.live_e2.dispatch_ccc_prb_policy")
    def test_live_e2_error_runtime_unavailable(self, mock_dispatch):
        mock_dispatch.side_effect = LiveE2Error("docker exec: container not running")
        handle = _make_rc_du_live_e2_handle()
        result = dispatch_runtime_action(handle, "ccc-003", _CCC_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertEqual(result.safe_message, "docker exec: container not running")

    @patch("benchmark.benchmark_api.live_e2.dispatch_ccc_prb_policy")
    def test_simulated_adapter_does_not_call_dispatch_ccc(self, mock_dispatch):
        handle = _make_simulated_handle_with_e2_control()
        result = dispatch_runtime_action(handle, "ccc-004", _CCC_ACTION)
        mock_dispatch.assert_not_called()
        self.assertTrue(result.dispatched)


if __name__ == "__main__":
    unittest.main()
