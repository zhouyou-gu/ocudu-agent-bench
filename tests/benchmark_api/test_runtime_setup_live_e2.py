"""Unit tests for the live_e2 adapter branch in runtime_setup.instantiate_runtime.

All live_e2 I/O is mocked — no FlexRIC container required.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any

from benchmark.benchmark_api.runtime_setup import (
    instantiate_runtime,
    RuntimeHandle,
    SIMULATED_ADAPTER,
    LIVE_CORE_ADAPTER,
    LIVE_OCUDU_ADAPTER,
    LIVE_E2_ADAPTER,
    KNOWN_ADAPTERS,
    _live_e2_config_from_setup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_readiness(ready: bool, failures: list[str] | None = None) -> dict[str, Any]:
    """Return a minimal readiness dict as live_e2.readiness() would."""
    return {
        "ready": ready,
        "checks": {
            "container_running": ready,
            "jsonl_exists": ready,
            "jsonl_has_records": ready,
            "record_count": 5 if ready else 0,
        },
        "failures": failures or [],
    }


_LIVE_E2_SETUP = {
    "runtime_adapter": "live_e2",
    "components": (),
}

_SIM_SETUP = {
    "runtime_adapter": "simulated_ocudu",
    "components": ("open5gs", "srsue_zmq"),
}

_LIVE_CORE_SETUP = {
    "runtime_adapter": "live_core",
    "components": (),
}


# ---------------------------------------------------------------------------
# 1. ready=True with live_e2 adapter
# ---------------------------------------------------------------------------

class LiveE2ReadyTrueTests(unittest.TestCase):
    """live_e2 adapter + readiness=True → handle is ready, metadata correct."""

    def test_handle_ready_true(self):
        """setup with runtime_adapter='live_e2' + readiness=True → handle.ready==True."""
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-ok")
        self.assertTrue(handle.ready)

    def test_blocking_reason_is_none(self):
        """blocking_reason is None when readiness=True."""
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-ok")
        self.assertIsNone(handle.setup_metadata.get("blocking_reason"))

    def test_setup_metadata_live_e2_true(self):
        """setup_metadata['live_e2'] is True when adapter=live_e2 and ready=True."""
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-ok")
        self.assertTrue(handle.setup_metadata.get("live_e2"))

    def test_setup_metadata_live_core_false(self):
        """setup_metadata['live_core'] is False when adapter=live_e2."""
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-ok")
        self.assertFalse(handle.setup_metadata.get("live_core"))

    def test_setup_metadata_live_ocudu_false(self):
        """setup_metadata['live_ocudu'] is False when adapter=live_e2."""
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-ok")
        self.assertFalse(handle.setup_metadata.get("live_ocudu"))


# ---------------------------------------------------------------------------
# 2. ready=False with live_e2 adapter
# ---------------------------------------------------------------------------

class LiveE2ReadyFalseTests(unittest.TestCase):
    """live_e2 adapter + readiness=False → handle not ready, blocking_reason has failures."""

    def test_handle_ready_false(self):
        """readiness=False → handle.ready==False."""
        failures = ["container_running: flexric-ric not running", "jsonl_exists: wc -l exited 1"]
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-fail")
        self.assertFalse(handle.ready)

    def test_blocking_reason_contains_failures(self):
        """blocking_reason lists failure messages when readiness=False."""
        failures = ["container_running: flexric-ric not running"]
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-fail")
        reason = handle.setup_metadata.get("blocking_reason") or ""
        self.assertIn("container_running", reason)

    def test_setup_metadata_live_e2_false_when_not_ready(self):
        """setup_metadata['live_e2'] is False when readiness=False."""
        failures = ["container_running: not running"]
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-fail")
        self.assertFalse(handle.setup_metadata.get("live_e2"))


# ---------------------------------------------------------------------------
# 3. Backend block for live_e2
# ---------------------------------------------------------------------------

class LiveE2BackendBlockTests(unittest.TestCase):
    """When live_e2 adapter is ready, e2_kpm=True; all other backends False."""

    def test_backend_flags_when_live_e2_ready(self):
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(True)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-backend")

        backend = handle.state["backend"]
        self.assertTrue(backend["e2_kpm"], "e2_kpm should be True when live_e2 is ready")
        self.assertTrue(backend["e2_control"], "e2_control should be True when live_e2 is ready (RC-DU xApp)")
        self.assertFalse(backend["websocket"])
        self.assertFalse(backend["ocudu_cli"])
        self.assertFalse(backend["core_control"])
        self.assertFalse(backend["json_metrics"])

    def test_backend_flags_all_false_when_not_ready(self):
        failures = ["container_running: not running"]
        with patch("benchmark.benchmark_api.live_e2.readiness",
                   return_value=_make_readiness(False, failures=failures)):
            handle = instantiate_runtime(_LIVE_E2_SETUP, "r-e2-backend-fail")

        backend = handle.state["backend"]
        self.assertFalse(backend["e2_kpm"])
        self.assertFalse(backend["websocket"])
        self.assertFalse(backend["ocudu_cli"])
        self.assertFalse(backend["core_control"])
        self.assertFalse(backend["json_metrics"])
        self.assertFalse(backend["e2_control"])


# ---------------------------------------------------------------------------
# 4. LiveE2Config defaults
# ---------------------------------------------------------------------------

class LiveE2ConfigDefaultsTests(unittest.TestCase):
    """_live_e2_config_from_setup returns defaults when no 'live_e2' block."""

    def test_default_setup_yields_default_config(self):
        from benchmark.benchmark_api.live_e2 import LiveE2Config
        cfg = _live_e2_config_from_setup({})
        self.assertEqual(cfg.container_name, "flexric-ric")
        self.assertEqual(cfg.jsonl_path, "/var/log/flexric/kpm.jsonl")
        self.assertAlmostEqual(cfg.subprocess_timeout_s, 10.0)
        self.assertEqual(cfg.min_records_for_ready, 1)


# ---------------------------------------------------------------------------
# 5. LiveE2Config with overrides
# ---------------------------------------------------------------------------

class LiveE2ConfigOverridesTests(unittest.TestCase):
    """_live_e2_config_from_setup applies overrides from setup['live_e2']."""

    def test_overrides_reflect_in_config(self):
        setup = {
            "live_e2": {
                "container_name": "my-flexric",
                "jsonl_path": "/tmp/kpm.jsonl",
                "subprocess_timeout_s": 20.0,
                "min_records_for_ready": 5,
            }
        }
        cfg = _live_e2_config_from_setup(setup)
        self.assertEqual(cfg.container_name, "my-flexric")
        self.assertEqual(cfg.jsonl_path, "/tmp/kpm.jsonl")
        self.assertAlmostEqual(cfg.subprocess_timeout_s, 20.0)
        self.assertEqual(cfg.min_records_for_ready, 5)


# ---------------------------------------------------------------------------
# 6. Simulated adapter: live_e2.readiness NOT called
# ---------------------------------------------------------------------------

class SimulatedAdapterNotCallingLiveE2(unittest.TestCase):
    """simulated_ocudu adapter never calls live_e2.readiness."""

    def test_simulated_adapter_does_not_call_live_e2_readiness(self):
        with patch("benchmark.benchmark_api.live_e2.readiness") as mock_readiness:
            handle = instantiate_runtime(_SIM_SETUP, "r-sim")
        mock_readiness.assert_not_called()
        self.assertTrue(handle.ready)


# ---------------------------------------------------------------------------
# 7. live_core adapter: live_e2.readiness NOT called
# ---------------------------------------------------------------------------

class LiveCoreAdapterNotCallingLiveE2(unittest.TestCase):
    """live_core adapter never calls live_e2.readiness."""

    def test_live_core_adapter_does_not_call_live_e2_readiness(self):
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value={"ready": True, "checks": {}, "failures": []}), \
             patch("benchmark.benchmark_api.live_core.read_runtime",
                   return_value={"running": True, "available_nfs": [], "nf_status": {},
                                 "restart_counts": {}, "last_restarted_nf": None,
                                 "ue_registration": {}}), \
             patch("benchmark.benchmark_api.live_e2.readiness") as mock_e2_readiness:
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-lc")
        mock_e2_readiness.assert_not_called()


# ---------------------------------------------------------------------------
# 8. KNOWN_ADAPTERS contains "live_e2"
# ---------------------------------------------------------------------------

class KnownAdaptersTests(unittest.TestCase):
    """KNOWN_ADAPTERS contains all four expected adapter names."""

    def test_known_adapters_contains_live_e2(self):
        self.assertIn(LIVE_E2_ADAPTER, KNOWN_ADAPTERS)

    def test_known_adapters_contains_all_four(self):
        self.assertIn(SIMULATED_ADAPTER, KNOWN_ADAPTERS)
        self.assertIn(LIVE_CORE_ADAPTER, KNOWN_ADAPTERS)
        self.assertIn(LIVE_OCUDU_ADAPTER, KNOWN_ADAPTERS)
        self.assertIn(LIVE_E2_ADAPTER, KNOWN_ADAPTERS)
        self.assertEqual(len(KNOWN_ADAPTERS), 4)


if __name__ == "__main__":
    unittest.main()
