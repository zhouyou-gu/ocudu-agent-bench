"""Tests for the live_core dispatch branch in ran_api.py.

All live_core I/O is mocked — no docker or live mongo required.
Covers:
  - RESTART_CORE_NF happy path (single + double dispatch for count increment)
  - RESTART_CORE_NF failure path (LiveCoreError → accepted=False, counts unchanged)
  - UPDATE_CORE_UE_REGISTRATION happy path
  - UPDATE_CORE_UE_REGISTRATION failure path
  - Non-core actions on live_core adapter → RUNTIME_UNAVAILABLE (backend gate)
  - NO_ACTION still works (unchanged path)
  - Simulated adapter still uses apply_simulated_action for core actions
  - read_evidence refreshes live core_runtime + preserves restart_counts
  - read_evidence falls back on refresh failure
  - read_evidence does NOT call live_core for simulated adapter
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from typing import Any

from benchmark_api.live_core import LiveCoreConfig, LiveCoreError
from benchmark_api.ran_api import dispatch_runtime_action, read_evidence
from benchmark_api.runtime_setup import RuntimeHandle, LIVE_CORE_ADAPTER, SIMULATED_ADAPTER
from benchmark_api.types import RanActionType, SafeErrorClass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_live_core_handle(
    state_overrides: dict[str, Any] | None = None,
) -> RuntimeHandle:
    """Build a RuntimeHandle that looks like a ready live_core adapter.

    backend.core_control=True so RESTART_CORE_NF / UPDATE_CORE_UE_REGISTRATION
    pass the backend_ready gate. All other backends are False so other action
    types are rejected by that gate.
    """
    state: dict[str, Any] = {
        "backend": {
            "websocket": False,
            "ocudu_cli": False,
            "core_control": True,
            "json_metrics": False,
            "e2_kpm": False,
            "e2_control": False,
        },
        "core_runtime": {
            "running": True,
            "available_nfs": ["amf", "smf", "upf"],
            "nf_status": {"amf": "running", "smf": "running", "upf": "running"},
            "restart_counts": {},
            "last_restarted_nf": None,
            "ue_registration": {
                "ue_id": "ue1",
                "status": "registered",
                "imsi": "001010000000001",
            },
        },
    }
    if state_overrides:
        state.update(state_overrides)
    return RuntimeHandle(
        run_id="test-run-live",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=LIVE_CORE_ADAPTER,
        components=(),
        state=state,
        ready=True,
    )


def _make_simulated_handle(
    state_overrides: dict[str, Any] | None = None,
) -> RuntimeHandle:
    """Build a RuntimeHandle for the simulated_ocudu adapter with core_control=True."""
    state: dict[str, Any] = {
        "backend": {
            "websocket": False,
            "ocudu_cli": False,
            "core_control": True,
            "json_metrics": False,
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
        "control_outcomes": [],
    }
    if state_overrides:
        state.update(state_overrides)
    return RuntimeHandle(
        run_id="test-run-sim",
        runtime="ocudu_zmq_open5gs",
        runtime_adapter=SIMULATED_ADAPTER,
        components=("open5gs",),
        state=state,
        ready=True,
    )


_RESTART_ACTION = {"type": "RESTART_CORE_NF", "nf": "upf"}
_UE_REG_ACTION = {
    "type": "UPDATE_CORE_UE_REGISTRATION",
    "ue_id": "ue1",
    "supi": "001010000000001",
    "plmn": "00101",
    "dnn": "internet",
    "sst": 1,
    "sd": None,
    "auth_profile_id": "ue1_test_profile",
}


# ---------------------------------------------------------------------------
# 1. RESTART_CORE_NF happy path
# ---------------------------------------------------------------------------

class RestartNFHappyPath(unittest.TestCase):

    def _ok_restart(self):
        return {"ok": True, "nf": "upf", "restarted_at_s": 1.0}

    def test_dispatch_returns_dispatched_accepted(self):
        """dispatch returns dispatched=True, accepted=True, safe_message mentions nf and count."""
        handle = _make_live_core_handle()
        with patch("benchmark_api.live_core.restart_nf", return_value=self._ok_restart()) as mock_rn:
            result = dispatch_runtime_action(handle, "act-1", _RESTART_ACTION)
        mock_rn.assert_called_once()
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.safe_error_class)
        self.assertIn("nf=upf", result.safe_message)
        self.assertIn("count=1", result.safe_message)
        self.assertEqual(result.backend, "core_control")

    def test_restart_counts_increments_on_second_dispatch(self):
        """Calling dispatch twice increments restart_counts[nf] to 2."""
        handle = _make_live_core_handle()
        with patch("benchmark_api.live_core.restart_nf", return_value=self._ok_restart()):
            dispatch_runtime_action(handle, "act-1", _RESTART_ACTION)
            result2 = dispatch_runtime_action(handle, "act-2", _RESTART_ACTION)
        self.assertIn("count=2", result2.safe_message)
        self.assertEqual(handle.state["core_runtime"]["restart_counts"]["upf"], 2)
        self.assertEqual(handle.state["core_runtime"]["last_restarted_nf"], "upf")


# ---------------------------------------------------------------------------
# 2. RESTART_CORE_NF failure path
# ---------------------------------------------------------------------------

class RestartNFFailurePath(unittest.TestCase):

    def test_live_core_error_returns_runtime_unavailable(self):
        """LiveCoreError from restart_nf → accepted=False, RUNTIME_UNAVAILABLE."""
        handle = _make_live_core_handle()
        exc = LiveCoreError("nf not found")
        with patch("benchmark_api.live_core.restart_nf", side_effect=exc):
            result = dispatch_runtime_action(handle, "act-1", _RESTART_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertIn("nf not found", result.safe_message)

    def test_restart_counts_unchanged_on_failure(self):
        """restart_counts is not modified when restart_nf raises LiveCoreError."""
        handle = _make_live_core_handle()
        exc = LiveCoreError("nf not found")
        with patch("benchmark_api.live_core.restart_nf", side_effect=exc):
            dispatch_runtime_action(handle, "act-1", _RESTART_ACTION)
        counts = handle.state["core_runtime"].get("restart_counts", {})
        self.assertEqual(counts.get("upf", 0), 0)


# ---------------------------------------------------------------------------
# 3. UPDATE_CORE_UE_REGISTRATION happy path
# ---------------------------------------------------------------------------

class UeRegistrationHappyPath(unittest.TestCase):

    def _ok_upsert(self):
        return {"ok": True, "imsi": "001010000000001", "matched": 1, "modified": 1, "upserted": False}

    def test_dispatch_accepted_and_state_updated(self):
        """dispatch passes action to upsert_subscriber; state ue_registration.status == registered."""
        handle = _make_live_core_handle()
        with patch("benchmark_api.live_core.upsert_subscriber", return_value=self._ok_upsert()) as mock_ups:
            result = dispatch_runtime_action(handle, "act-1", _UE_REG_ACTION)
        mock_ups.assert_called_once()
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.safe_error_class)
        reg = handle.state["core_runtime"]["ue_registration"]
        self.assertEqual(reg["status"], "registered")
        self.assertEqual(reg["last_updated_by"], "live_core")

    def test_dispatch_safe_message_contains_imsi(self):
        """safe_message mentions imsi from upsert_subscriber return value."""
        handle = _make_live_core_handle()
        with patch("benchmark_api.live_core.upsert_subscriber", return_value=self._ok_upsert()):
            result = dispatch_runtime_action(handle, "act-1", _UE_REG_ACTION)
        self.assertIn("001010000000001", result.safe_message)


# ---------------------------------------------------------------------------
# 4. UPDATE_CORE_UE_REGISTRATION failure path
# ---------------------------------------------------------------------------

class UeRegistrationFailurePath(unittest.TestCase):

    def test_live_core_error_returns_runtime_unavailable(self):
        """LiveCoreError from upsert_subscriber → accepted=False, RUNTIME_UNAVAILABLE."""
        handle = _make_live_core_handle()
        exc = LiveCoreError("mongo connection refused")
        with patch("benchmark_api.live_core.upsert_subscriber", side_effect=exc):
            result = dispatch_runtime_action(handle, "act-1", _UE_REG_ACTION)
        self.assertTrue(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)
        self.assertIn("mongo connection refused", result.safe_message)


# ---------------------------------------------------------------------------
# 5. Non-core actions on live_core adapter → RUNTIME_UNAVAILABLE (backend gate)
# ---------------------------------------------------------------------------

class NonCoreActionsOnLiveCoreAdapter(unittest.TestCase):

    def test_set_prb_policy_ws_rejected_by_backend_gate(self):
        """SET_PRB_POLICY_RATIO_WS with websocket=False → RUNTIME_UNAVAILABLE from backend gate."""
        handle = _make_live_core_handle()
        action = {
            "type": "SET_PRB_POLICY_RATIO_WS",
            "plmn": "00101",
            "sst": 1,
            "min_prb_policy_ratio": 20,
            "max_prb_policy_ratio": 80,
        }
        result = dispatch_runtime_action(handle, "act-1", action)
        self.assertFalse(result.dispatched)
        self.assertFalse(result.accepted)
        self.assertEqual(result.safe_error_class, SafeErrorClass.RUNTIME_UNAVAILABLE)

    def test_no_action_returns_accepted_not_dispatched(self):
        """NO_ACTION always returns dispatched=False, accepted=True regardless of adapter."""
        handle = _make_live_core_handle()
        action = {"type": "NO_ACTION"}
        result = dispatch_runtime_action(handle, "act-1", action)
        self.assertFalse(result.dispatched)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.safe_error_class)


# ---------------------------------------------------------------------------
# 6. Simulated adapter still uses apply_simulated_action for core actions
# ---------------------------------------------------------------------------

class SimulatedAdapterCoreActions(unittest.TestCase):

    def test_restart_nf_on_simulated_adapter_does_not_call_live_core(self):
        """RESTART_CORE_NF on simulated_ocudu → apply_simulated_action path, live_core not called."""
        handle = _make_simulated_handle()
        with patch("benchmark_api.live_core.restart_nf") as mock_rn:
            result = dispatch_runtime_action(handle, "act-1", _RESTART_ACTION)
        mock_rn.assert_not_called()
        # Simulated path dispatches successfully
        self.assertTrue(result.dispatched)
        self.assertTrue(result.accepted)


# ---------------------------------------------------------------------------
# 7. read_evidence refreshes core_runtime for live adapter
# ---------------------------------------------------------------------------

_SENTINEL_FRESH: dict[str, Any] = {
    "running": True,
    "available_nfs": ["amf", "smf", "upf"],
    "nf_status": {"amf": "running", "smf": "running", "upf": "running"},
    "restart_counts": {},
    "last_restarted_nf": None,
    "ue_registration": {"ue_id": "ue1", "status": "registered"},
    "errors": [],
}


class ReadEvidenceLiveCoreRefresh(unittest.TestCase):

    def _sources(self):
        return ("core_runtime",)

    def test_refresh_populates_evidence_from_live_state(self):
        """When adapter=live_core, read_evidence calls live_core.read_runtime and uses result."""
        handle = _make_live_core_handle()
        sentinel = dict(_SENTINEL_FRESH)
        sentinel["nf_status"] = {"amf": "sentinel_running"}
        with patch("benchmark_api.live_core.read_runtime", return_value=sentinel):
            evidence = read_evidence(handle, self._sources())
        # The sentinel nf_status should show up in the evidence
        self.assertEqual(
            evidence["core_runtime"]["nf_status"],
            {"amf": "sentinel_running"},
        )

    def test_restart_counts_preserved_across_refresh(self):
        """restart_counts from prior dispatch are preserved when read_runtime returns empty {}."""
        handle = _make_live_core_handle(
            state_overrides={
                "core_runtime": {
                    "running": True,
                    "available_nfs": ["amf"],
                    "nf_status": {"amf": "running"},
                    "restart_counts": {"upf": 3},
                    "last_restarted_nf": "upf",
                    "ue_registration": {"ue_id": "ue1", "status": "registered"},
                }
            }
        )
        fresh = dict(_SENTINEL_FRESH)
        # read_runtime always returns empty restart_counts
        fresh["restart_counts"] = {}
        fresh["last_restarted_nf"] = None
        with patch("benchmark_api.live_core.read_runtime", return_value=fresh):
            evidence = read_evidence(handle, self._sources())
        self.assertEqual(evidence["core_runtime"]["restart_counts"], {"upf": 3})
        self.assertEqual(evidence["core_runtime"]["last_restarted_nf"], "upf")

    def test_refresh_failure_falls_back_to_state(self):
        """When read_runtime raises, evidence falls back to existing state without exception."""
        handle = _make_live_core_handle()
        with patch(
            "benchmark_api.live_core.read_runtime",
            side_effect=RuntimeError("docker not responding"),
        ):
            try:
                evidence = read_evidence(handle, self._sources())
            except Exception as exc:  # noqa: BLE001
                self.fail(f"read_evidence raised unexpectedly: {exc}")
        # Falls back to state; the state has the original ue_registration
        self.assertIn("core_runtime", evidence)


# ---------------------------------------------------------------------------
# 8. read_evidence does NOT call live_core for simulated adapter
# ---------------------------------------------------------------------------

class ReadEvidenceSimulatedAdapter(unittest.TestCase):

    def test_simulated_adapter_does_not_call_read_runtime(self):
        """read_evidence for simulated_ocudu adapter never calls live_core.read_runtime."""
        handle = _make_simulated_handle()
        with patch("benchmark_api.live_core.read_runtime") as mock_rr:
            evidence = read_evidence(handle, ("core_runtime",))
        mock_rr.assert_not_called()
        self.assertIn("core_runtime", evidence)


if __name__ == "__main__":
    unittest.main()
