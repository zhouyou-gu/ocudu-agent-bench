"""Unit tests for the live_core adapter branch in runtime_setup.instantiate_runtime.

All live_core I/O is mocked — no docker or live mongo required.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch, call
from typing import Any

from benchmark.benchmark_api.runtime_setup import (
    instantiate_runtime,
    cleanup_runtime,
    RuntimeHandle,
    SIMULATED_ADAPTER,
    LIVE_CORE_ADAPTER,
    KNOWN_ADAPTERS,
    _live_core_config_from_setup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_readiness(ready: bool, failures: list[str] | None = None) -> dict[str, Any]:
    """Return a minimal readiness dict as live_core.readiness() would."""
    return {
        "ready": ready,
        "checks": {},
        "failures": failures or [],
    }


_SENTINEL_RUNTIME = {
    "running": True,
    "available_nfs": ["amf", "smf", "upf"],
    "nf_status": {"amf": "running", "smf": "running", "upf": "running"},
    "restart_counts": {},
    "last_restarted_nf": None,
    "ue_registration": {"status": "registered", "ue_id": "ue1", "imsi": "001010000000001"},
    "errors": [],
}

# A minimal setup dict for the live_core adapter
_LIVE_CORE_SETUP = {
    "runtime_adapter": "live_core",
    "components": (),
}

# A minimal setup dict for the simulated adapter
_SIM_SETUP = {
    "runtime_adapter": "simulated_ocudu",
    "components": ("open5gs", "srsue_zmq"),
}


# ---------------------------------------------------------------------------
# 1. Adapter selection tests (4 tests)
# ---------------------------------------------------------------------------


class AdapterSelectionTests(unittest.TestCase):
    """instantiate_runtime picks the correct code path based on runtime_adapter."""

    def test_simulated_adapter_ready_no_live_core_calls(self):
        """simulated_ocudu → ready=True; live_core is never imported or called."""
        # We import live_core functions here to patch them; if the simulated path
        # is correct they should never be invoked.
        with patch("benchmark.benchmark_api.live_core.readiness") as mock_readiness, \
             patch("benchmark.benchmark_api.live_core.read_runtime") as mock_read_rt:
            handle = instantiate_runtime(_SIM_SETUP, "r-sim")
        self.assertTrue(handle.ready)
        self.assertIsNone(handle.setup_metadata.get("blocking_reason"))
        mock_readiness.assert_not_called()
        mock_read_rt.assert_not_called()

    def test_live_core_ready_true(self):
        """live_core adapter + readiness ready=True → handle.ready==True, metadata correct."""
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value=_make_readiness(True)) as mock_readiness, \
             patch("benchmark.benchmark_api.live_core.read_runtime",
                   return_value=dict(_SENTINEL_RUNTIME)) as mock_read_rt:
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-lc-ok")

        self.assertTrue(handle.ready)
        self.assertIsNone(handle.setup_metadata.get("blocking_reason"))
        self.assertTrue(handle.setup_metadata.get("live_core"))
        mock_readiness.assert_called_once()
        mock_read_rt.assert_called_once()

    def test_live_core_ready_false_blocking_reason(self):
        """live_core adapter + readiness ready=False → handle.ready==False, blocking_reason lists failures."""
        failures = ["compose_running: exited 1", "mongo_loopback: timeout"]
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value=_make_readiness(False, failures=failures)), \
             patch("benchmark.benchmark_api.live_core.read_runtime") as mock_read_rt:
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-lc-fail")

        self.assertFalse(handle.ready)
        reason = handle.setup_metadata.get("blocking_reason") or ""
        self.assertIn("compose_running: exited 1", reason)
        self.assertIn("mongo_loopback: timeout", reason)
        self.assertFalse(handle.setup_metadata.get("live_core"))
        # read_runtime must NOT be called when readiness fails
        mock_read_rt.assert_not_called()

    def test_unknown_adapter_not_ready(self):
        """Unknown adapter string → ready=False, blocking_reason mentions the adapter name."""
        setup = {"runtime_adapter": "totally_unknown_adapter", "components": ()}
        handle = instantiate_runtime(setup, "r-unk")
        self.assertFalse(handle.ready)
        reason = handle.setup_metadata.get("blocking_reason") or ""
        self.assertIn("totally_unknown_adapter", reason)
        self.assertIn("not available", reason)


# ---------------------------------------------------------------------------
# 2. Backend block for live_core (1 test)
# ---------------------------------------------------------------------------


class LiveCoreBackendBlockTests(unittest.TestCase):
    """When live_core adapter is ready, only core_control is True; rest are False."""

    def test_backend_flags_when_live_core_ready(self):
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value=_make_readiness(True)), \
             patch("benchmark.benchmark_api.live_core.read_runtime",
                   return_value=dict(_SENTINEL_RUNTIME)):
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-backend")

        backend = handle.state["backend"]
        self.assertTrue(backend["core_control"], "core_control should be True when live_core is ready")
        self.assertFalse(backend["websocket"])
        self.assertFalse(backend["ocudu_cli"])
        self.assertFalse(backend["json_metrics"])
        self.assertFalse(backend["e2_kpm"])
        self.assertFalse(backend["e2_control"])


# ---------------------------------------------------------------------------
# 3. core_runtime population (2 tests)
# ---------------------------------------------------------------------------


class LiveCoreCoreRuntimeTests(unittest.TestCase):
    """core_runtime is populated from live_core.read_runtime when ready, simulated fallback otherwise."""

    def test_ready_true_core_runtime_equals_sentinel(self):
        """When live_core is ready, state['core_runtime'] is the read_runtime result."""
        sentinel = dict(_SENTINEL_RUNTIME)
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value=_make_readiness(True)), \
             patch("benchmark.benchmark_api.live_core.read_runtime",
                   return_value=sentinel):
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-core-ok")

        self.assertIs(handle.state["core_runtime"], sentinel)

    def test_ready_false_core_runtime_uses_simulated_fallback(self):
        """When live_core readiness fails, core_runtime falls back to simulated bootstrap (running=False)."""
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value=_make_readiness(False, failures=["compose_running: error"])), \
             patch("benchmark.benchmark_api.live_core.read_runtime") as mock_read_rt:
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-core-fail")

        mock_read_rt.assert_not_called()
        core = handle.state["core_runtime"]
        # simulated bootstrap: running = ready and "open5gs" in components
        # ready=False and components=() → running=False
        self.assertFalse(core["running"])
        # The simulated bootstrap keys must be present
        self.assertIn("available_nfs", core)
        self.assertIn("nf_status", core)
        self.assertIn("restart_counts", core)
        self.assertIn("last_restarted_nf", core)
        self.assertIn("ue_registration", core)


# ---------------------------------------------------------------------------
# 4. LiveCoreConfig plumbing (2 tests)
# ---------------------------------------------------------------------------


class LiveCoreConfigPlumbingTests(unittest.TestCase):
    """_live_core_config_from_setup builds LiveCoreConfig with correct defaults and overrides."""

    def test_default_setup_yields_default_config(self):
        """No 'live_core' block in setup → defaults are used."""
        from benchmark.benchmark_api.live_core import LiveCoreConfig
        cfg = _live_core_config_from_setup({})
        self.assertEqual(cfg.compose_project, "open5gs")
        self.assertIsNone(cfg.compose_file)
        self.assertEqual(cfg.mongo_uri, "mongodb://127.0.0.1:27017/")
        self.assertEqual(cfg.mongo_db, "open5gs")
        self.assertAlmostEqual(cfg.timeout_s, 30.0)

    def test_live_core_block_overrides_fields(self):
        """setup['live_core'] overrides reflect in LiveCoreConfig fields."""
        from benchmark.benchmark_api.live_core import LiveCoreConfig
        setup = {
            "live_core": {
                "compose_project": "my_project",
                "compose_file": "/path/to/compose.yml",
                "mongo_uri": "mongodb://10.0.0.1:27017/",
                "mongo_db": "mydb",
                "timeout_s": 30.0,
            }
        }
        cfg = _live_core_config_from_setup(setup)
        self.assertEqual(cfg.compose_project, "my_project")
        self.assertEqual(cfg.compose_file, "/path/to/compose.yml")
        self.assertEqual(cfg.mongo_uri, "mongodb://10.0.0.1:27017/")
        self.assertEqual(cfg.mongo_db, "mydb")
        self.assertAlmostEqual(cfg.timeout_s, 30.0)


# ---------------------------------------------------------------------------
# 5. cleanup_runtime no-op for live_core (1 test)
# ---------------------------------------------------------------------------


class CleanupRuntimeLiveCoreTests(unittest.TestCase):
    """cleanup_runtime marks closed=True and returns ok; compose is NOT torn down."""

    def test_cleanup_sets_closed_returns_ok(self):
        """For a live_core handle, cleanup_runtime returns ok and sets closed=True.

        live_core is not called — compose stays running.
        """
        with patch("benchmark.benchmark_api.live_core.readiness",
                   return_value=_make_readiness(True)), \
             patch("benchmark.benchmark_api.live_core.read_runtime",
                   return_value=dict(_SENTINEL_RUNTIME)):
            handle = instantiate_runtime(_LIVE_CORE_SETUP, "r-cleanup")

        # Patch the entire live_core module at the runtime_setup import seam
        # so we can verify no teardown call goes through.
        with patch("benchmark.benchmark_api.live_core.restart_nf") as mock_restart, \
             patch("benchmark.benchmark_api.live_core.apply_tc_netem") as mock_tc:
            result = cleanup_runtime(handle)

        self.assertTrue(handle.closed)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["run_id"], "r-cleanup")
        self.assertIn("cleanup_completed_at", result)
        mock_restart.assert_not_called()
        mock_tc.assert_not_called()


# ---------------------------------------------------------------------------
# 6. KNOWN_ADAPTERS constant sanity (1 test)
# ---------------------------------------------------------------------------


class KnownAdaptersTests(unittest.TestCase):
    """KNOWN_ADAPTERS contains the expected adapter names."""

    def test_known_adapters_contains_both(self):
        self.assertIn(SIMULATED_ADAPTER, KNOWN_ADAPTERS)
        self.assertIn(LIVE_CORE_ADAPTER, KNOWN_ADAPTERS)
        # KNOWN_ADAPTERS may contain additional adapters (e.g. live_ocudu) added in later slices
        self.assertGreaterEqual(len(KNOWN_ADAPTERS), 2)


if __name__ == "__main__":
    unittest.main()
