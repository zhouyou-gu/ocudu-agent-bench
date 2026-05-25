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

from benchmark.benchmark_api.ran_api import read_evidence
from benchmark.benchmark_api.runtime_setup import RuntimeHandle, LIVE_E2_ADAPTER, SIMULATED_ADAPTER
from benchmark.benchmark_api.live_e2 import LiveE2Error


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


if __name__ == "__main__":
    unittest.main()
