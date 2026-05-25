"""Unit tests for the live_ocudu adapter branch in runtime_setup.instantiate_runtime.

All live_ocudu I/O is mocked — no WebSocket or real gNB required.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any

from benchmark_api.runtime_setup import (
    instantiate_runtime,
    RuntimeHandle,
    SIMULATED_ADAPTER,
    LIVE_CORE_ADAPTER,
    LIVE_OCUDU_ADAPTER,
    KNOWN_ADAPTERS,
    _live_ocudu_config_from_setup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_readiness(ready: bool, failures: list[str] | None = None) -> dict[str, Any]:
    """Return a minimal readiness dict as live_ocudu.readiness() would."""
    return {
        "ready": ready,
        "checks": {
            "connect": ready,
            "subscribe_ack": ready,
            "metric_frame": ready,
        },
        "failures": failures or [],
    }


# A minimal setup dict for the live_ocudu adapter
_LIVE_OCUDU_SETUP = {
    "runtime_adapter": "live_ocudu",
    "components": (),
}

# A minimal setup dict for the simulated adapter
_SIM_SETUP = {
    "runtime_adapter": "simulated_ocudu",
    "components": ("open5gs", "srsue_zmq"),
}

# A minimal setup dict for the live_core adapter
_LIVE_CORE_SETUP = {
    "runtime_adapter": "live_core",
    "components": (),
}


# ---------------------------------------------------------------------------
# 1. ready=True with live_ocudu adapter
# ---------------------------------------------------------------------------

class LiveOcuduReadyTrueTests(unittest.TestCase):
    """live_ocudu adapter + readiness=True → handle is ready, metadata correct."""

    def test_handle_ready_true(self):
        """setup with runtime_adapter='live_ocudu' + readiness=True → handle.ready==True."""
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-ok")
        self.assertTrue(handle.ready)

    def test_blocking_reason_is_none(self):
        """blocking_reason is None when readiness=True."""
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-ok")
        self.assertIsNone(handle.setup_metadata.get("blocking_reason"))

    def test_setup_metadata_live_ocudu_true(self):
        """setup_metadata['live_ocudu'] is True when adapter=live_ocudu and ready=True."""
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-ok")
        self.assertTrue(handle.setup_metadata.get("live_ocudu"))

    def test_setup_metadata_live_core_false(self):
        """setup_metadata['live_core'] is False when adapter=live_ocudu."""
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-ok")
        self.assertFalse(handle.setup_metadata.get("live_core"))


# ---------------------------------------------------------------------------
# 2. ready=False with live_ocudu adapter
# ---------------------------------------------------------------------------

class LiveOcuduReadyFalseTests(unittest.TestCase):
    """live_ocudu adapter + readiness=False → handle not ready, blocking_reason has failures."""

    def test_handle_ready_false(self):
        """readiness=False → handle.ready==False."""
        failures = ["connect: connection refused", "subscribe_ack: timeout"]
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-fail")
        self.assertFalse(handle.ready)

    def test_blocking_reason_contains_failures(self):
        """blocking_reason lists all failure messages when readiness=False."""
        failures = ["connect: connection refused", "subscribe_ack: timeout"]
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-fail")
        reason = handle.setup_metadata.get("blocking_reason") or ""
        self.assertIn("connect: connection refused", reason)
        self.assertIn("subscribe_ack: timeout", reason)

    def test_setup_metadata_live_ocudu_false_when_not_ready(self):
        """setup_metadata['live_ocudu'] is False when readiness=False."""
        failures = ["connect: connection refused"]
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-lo-fail")
        self.assertFalse(handle.setup_metadata.get("live_ocudu"))


# ---------------------------------------------------------------------------
# 3. Backend block for live_ocudu
# ---------------------------------------------------------------------------

class LiveOcuduBackendBlockTests(unittest.TestCase):
    """When live_ocudu adapter is ready, websocket/ocudu_cli/json_metrics=True; rest False."""

    def test_backend_flags_when_live_ocudu_ready(self):
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-backend")

        backend = handle.state["backend"]
        self.assertTrue(backend["websocket"], "websocket should be True when live_ocudu is ready")
        self.assertTrue(backend["ocudu_cli"], "ocudu_cli should be True when live_ocudu is ready")
        self.assertTrue(backend["json_metrics"], "json_metrics should be True when live_ocudu is ready")
        self.assertFalse(backend["core_control"])
        self.assertFalse(backend["e2_kpm"])
        self.assertFalse(backend["e2_control"])

    def test_backend_flags_all_false_when_not_ready(self):
        with patch("benchmark_api.live_ocudu.readiness",
                   return_value=_make_readiness(False, failures=["connect: refused"])):
            handle = instantiate_runtime(_LIVE_OCUDU_SETUP, "r-backend-fail")

        backend = handle.state["backend"]
        self.assertFalse(backend["websocket"])
        self.assertFalse(backend["ocudu_cli"])
        self.assertFalse(backend["json_metrics"])
        self.assertFalse(backend["core_control"])
        self.assertFalse(backend["e2_kpm"])
        self.assertFalse(backend["e2_control"])


# ---------------------------------------------------------------------------
# 4. LiveOcuduConfig defaults when setup has no live_ocudu block
# ---------------------------------------------------------------------------

class LiveOcuduConfigDefaultsTests(unittest.TestCase):
    """_live_ocudu_config_from_setup returns defaults when no 'live_ocudu' block."""

    def test_default_setup_yields_default_config(self):
        from benchmark_api.live_ocudu import LiveOcuduConfig
        cfg = _live_ocudu_config_from_setup({})
        self.assertEqual(cfg.ws_url, "ws://127.0.0.1:8001/")
        self.assertAlmostEqual(cfg.connect_timeout_s, 5.0)
        self.assertAlmostEqual(cfg.recv_timeout_s, 3.0)


# ---------------------------------------------------------------------------
# 5. LiveOcuduConfig with overrides from setup["live_ocudu"]
# ---------------------------------------------------------------------------

class LiveOcuduConfigOverridesTests(unittest.TestCase):
    """_live_ocudu_config_from_setup applies overrides from setup['live_ocudu']."""

    def test_overrides_reflect_in_config(self):
        from benchmark_api.live_ocudu import LiveOcuduConfig
        setup = {
            "live_ocudu": {
                "ws_url": "ws://10.53.1.3:8001/",
                "connect_timeout_s": 10.0,
                "recv_timeout_s": 5.0,
            }
        }
        cfg = _live_ocudu_config_from_setup(setup)
        self.assertEqual(cfg.ws_url, "ws://10.53.1.3:8001/")
        self.assertAlmostEqual(cfg.connect_timeout_s, 10.0)
        self.assertAlmostEqual(cfg.recv_timeout_s, 5.0)


# ---------------------------------------------------------------------------
# 6. Simulated adapter: live_ocudu.readiness NOT called
# ---------------------------------------------------------------------------

class SimulatedAdapterNotCallingLiveOcudu(unittest.TestCase):
    """simulated_ocudu adapter never calls live_ocudu.readiness."""

    def test_simulated_adapter_does_not_call_live_ocudu_readiness(self):
        with patch("benchmark_api.live_ocudu.readiness") as mock_readiness:
            handle = instantiate_runtime(_SIM_SETUP, "r-sim")
        mock_readiness.assert_not_called()
        self.assertTrue(handle.ready)


# ---------------------------------------------------------------------------
# 7. live_core adapter: live_ocudu.readiness NOT called
# ---------------------------------------------------------------------------

class LiveCoreAdapterNotCallingLiveOcudu(unittest.TestCase):
    """live_core adapter never calls live_ocudu.readiness."""

    def test_live_core_adapter_does_not_call_live_ocudu_readiness(self):
        with patch("benchmark_api.live_core.readiness",
                   return_value={"ready": True, "checks": {}, "failures": []}), \
             patch("benchmark_api.live_core.read_runtime",
                   return_value={"running": True, "available_nfs": [], "nf_status": {},
                                 "restart_counts": {}, "last_restarted_nf": None,
                                 "ue_registration": {}}), \
             patch("benchmark_api.live_ocudu.readiness") as mock_lo_readiness:
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-lc")
        mock_lo_readiness.assert_not_called()


# ---------------------------------------------------------------------------
# 8. KNOWN_ADAPTERS contains "live_ocudu"
# ---------------------------------------------------------------------------

class KnownAdaptersTests(unittest.TestCase):
    """KNOWN_ADAPTERS contains all three expected adapter names."""

    def test_known_adapters_contains_live_ocudu(self):
        self.assertIn(LIVE_OCUDU_ADAPTER, KNOWN_ADAPTERS)

    def test_known_adapters_contains_all_three(self):
        # KNOWN_ADAPTERS now has 4 entries (simulated + live_core + live_ocudu + live_e2).
        # This test only verifies the three that matter for live_ocudu wiring.
        self.assertIn(SIMULATED_ADAPTER, KNOWN_ADAPTERS)
        self.assertIn(LIVE_CORE_ADAPTER, KNOWN_ADAPTERS)
        self.assertIn(LIVE_OCUDU_ADAPTER, KNOWN_ADAPTERS)
        self.assertGreaterEqual(len(KNOWN_ADAPTERS), 3)


if __name__ == "__main__":
    unittest.main()
