"""Unit tests for benchmark.benchmark_api.live_ocudu.

All WebSocket I/O is mocked via _open_ws — no live gNB required.
Tests are organised into 7 groups matching the spec:

  1. _open_ws error mapping
  2. send_command happy path
  3. send_command error paths
  4. read_metrics happy path
  5. read_metrics edge cases
  6. readiness checks
  7. Connection cleanup (close() guaranteed)
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, call, patch

from benchmark.benchmark_api.live_ocudu import (
    LiveOcuduConfig,
    LiveOcuduError,
    _open_ws,
    read_metrics,
    readiness,
    send_command,
)

_DEFAULT_CFG = LiveOcuduConfig()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws_mock(*recv_returns):
    """Return a MagicMock ws whose .recv() yields items from recv_returns in order.

    Each item in recv_returns may be:
      * str  → returned as-is (valid JSON string expected by callers)
      * dict → json.dumps(dict)
      * Exception subclass instance → raised as side_effect

    After the sequence is exhausted, recv() raises socket.timeout so tests
    don't hang waiting for more data.
    """
    import socket

    ws = MagicMock()
    responses = list(recv_returns)

    def _recv():
        if not responses:
            raise TimeoutError("mock exhausted")
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return json.dumps(item)
        return item

    ws.recv.side_effect = _recv
    return ws


# ---------------------------------------------------------------------------
# 1. _open_ws error mapping
# ---------------------------------------------------------------------------


class OpenWsErrorMappingTests(unittest.TestCase):
    """_open_ws maps import/connection errors to LiveOcuduError."""

    def test_missing_websocket_lib_raises_live_ocudu_error(self):
        """ImportError from websocket-client is wrapped in LiveOcuduError."""
        with patch.dict("sys.modules", {"websocket": None}):
            with self.assertRaises(LiveOcuduError) as ctx:
                _open_ws(_DEFAULT_CFG)
        self.assertIn("websocket-client", ctx.exception.safe_message)

    def test_connection_refused_raises_live_ocudu_error(self):
        """Connection-refused OSError from create_connection is wrapped in LiveOcuduError.

        websocket-client may not be installed on CI. We inject a fake websocket module
        with a create_connection that raises ConnectionRefusedError to drive the path
        inside _open_ws that catches generic Exception → LiveOcuduError.
        """
        fake_ws_mod = MagicMock()
        fake_ws_mod.create_connection.side_effect = ConnectionRefusedError(
            "[Errno 111] Connection refused"
        )
        with patch.dict("sys.modules", {"websocket": fake_ws_mod}):
            with self.assertRaises(LiveOcuduError) as ctx:
                _open_ws(_DEFAULT_CFG)
        self.assertIn("could not connect", ctx.exception.safe_message)
        self.assertIn(_DEFAULT_CFG.ws_url, ctx.exception.safe_message)

    def test_timeout_on_connect_raises_live_ocudu_error(self):
        """TimeoutError from create_connection is wrapped in LiveOcuduError."""
        fake_ws_mod = MagicMock()
        fake_ws_mod.create_connection.side_effect = TimeoutError("timed out")
        with patch.dict("sys.modules", {"websocket": fake_ws_mod}):
            with self.assertRaises(LiveOcuduError) as ctx:
                _open_ws(_DEFAULT_CFG)
        self.assertIn(_DEFAULT_CFG.ws_url, ctx.exception.safe_message)


# ---------------------------------------------------------------------------
# 2. send_command happy path
# ---------------------------------------------------------------------------


class SendCommandHappyPathTests(unittest.TestCase):
    """send_command opens WS, sends request, returns parsed ack."""

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_rrm_policy_ratio_set_returns_ack(self, mock_open):
        request = {
            "cmd": "rrm_policy_ratio_set",
            "policies": {
                "resourceType": "PRB",
                "rRMPolicyMemberList": [{"plmn": "00101", "sst": 1}],
                "min_prb_policy_ratio": 0,
                "max_prb_policy_ratio": 100,
            },
        }
        ack = {"cmd": "rrm_policy_ratio_set", "timestamp": "2026-05-25T00:00:00Z", "status": "ok"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        result = send_command(_DEFAULT_CFG, request)

        ws.send.assert_called_once_with(json.dumps(request))
        self.assertEqual(result["cmd"], "rrm_policy_ratio_set")

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_ssb_set_returns_ack(self, mock_open):
        request = {
            "cmd": "ssb_set",
            "cells": [{"plmn": "00101", "nci": 6733824, "ssb_block_power_dbm": -16}],
        }
        ack = {"cmd": "ssb_set", "timestamp": "2026-05-25T00:00:00Z"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        result = send_command(_DEFAULT_CFG, request)
        self.assertEqual(result["cmd"], "ssb_set")

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_ho_returns_ack(self, mock_open):
        request = {"cmd": "ho", "argv": [1, 0xC350, 2]}
        ack = {"cmd": "ho", "timestamp": "2026-05-25T00:00:00Z"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        result = send_command(_DEFAULT_CFG, request)
        self.assertEqual(result["cmd"], "ho")

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_cfo_returns_ack(self, mock_open):
        request = {"cmd": "cfo", "argv": [0, 100.5]}
        ack = {"cmd": "cfo", "timestamp": "2026-05-25T00:00:00Z"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        result = send_command(_DEFAULT_CFG, request)
        self.assertEqual(result["cmd"], "cfo")


# ---------------------------------------------------------------------------
# 3. send_command error paths
# ---------------------------------------------------------------------------


class SendCommandErrorPathTests(unittest.TestCase):
    """send_command raises LiveOcuduError on protocol and transport errors."""

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_response_with_wrong_cmd_raises(self, mock_open):
        """Response whose 'cmd' doesn't match the request raises LiveOcuduError."""
        request = {"cmd": "rrm_policy_ratio_set", "policies": {}}
        ack = {"cmd": "ssb_set", "timestamp": "2026-05-25T00:00:00Z"}  # wrong cmd
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        with self.assertRaises(LiveOcuduError) as ctx:
            send_command(_DEFAULT_CFG, request)
        self.assertIn("cmd mismatch", ctx.exception.safe_message.lower())

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_response_with_error_field_raises(self, mock_open):
        """Response containing an 'error' field raises LiveOcuduError."""
        request = {"cmd": "ho", "argv": [1, 0xC350, 999]}
        ack = {"cmd": "ho", "error": "unknown target cell"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        with self.assertRaises(LiveOcuduError) as ctx:
            send_command(_DEFAULT_CFG, request)
        self.assertIn("unknown target cell", ctx.exception.safe_message)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_recv_timeout_raises_live_ocudu_error(self, mock_open):
        """If recv() times out, LiveOcuduError is raised."""
        request = {"cmd": "cfo", "argv": [0, 50.0]}
        ws = _make_ws_mock(TimeoutError("timed out"))
        mock_open.return_value = ws

        with self.assertRaises(LiveOcuduError) as ctx:
            send_command(_DEFAULT_CFG, request)
        self.assertIn("timeout", ctx.exception.safe_message.lower())

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_non_json_response_raises_live_ocudu_error(self, mock_open):
        """Non-JSON response raises LiveOcuduError."""
        request = {"cmd": "ssb_set", "cells": []}
        ws = _make_ws_mock("not valid json {{{{")
        mock_open.return_value = ws

        with self.assertRaises(LiveOcuduError) as ctx:
            send_command(_DEFAULT_CFG, request)
        self.assertIn("json", ctx.exception.safe_message.lower())

    def test_request_missing_cmd_raises_before_opening_ws(self):
        """If request has no 'cmd' key, LiveOcuduError before any WS I/O."""
        with patch("benchmark.benchmark_api.live_ocudu._open_ws") as mock_open:
            with self.assertRaises(LiveOcuduError) as ctx:
                send_command(_DEFAULT_CFG, {"policies": {}})
            mock_open.assert_not_called()
        self.assertIn("cmd", ctx.exception.safe_message.lower())


# ---------------------------------------------------------------------------
# 4. read_metrics happy path
# ---------------------------------------------------------------------------


_SUBSCRIBE_ACK = {"cmd": "metrics_subscribe", "timestamp": "2026-05-25T00:00:00Z"}
_UNSUBSCRIBE_ACK = {"cmd": "metrics_unsubscribe", "timestamp": "2026-05-25T00:00:00Z"}

_FRAME_1 = {
    "du": {"du_high": {"mac": {"dl": [{"average_latency_us": 6.374}], "ul": []}}},
    "cells": [{"cell_metrics": {"average_latency": 2}}],
}
_FRAME_2 = {
    "du": {"du_high": {"mac": {"dl": [{"average_latency_us": 5.1}], "ul": []}}},
    "cells": [{"cell_metrics": {"average_latency": 3}}],
}
_FRAME_3 = {
    "du": {"du_high": {"mac": {"dl": [{"average_latency_us": 7.2}], "ul": []}}},
    "cells": [{"cell_metrics": {"average_latency": 1}}],
}


class ReadMetricsHappyPathTests(unittest.TestCase):
    """read_metrics collects frames, excluding the subscribe ack."""

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_count_3_returns_3_frames_excluding_ack(self, mock_open):
        """count=3: subscribe ack + 3 metric frames → 3 frames returned (no ack)."""
        ws = _make_ws_mock(_SUBSCRIBE_ACK, _FRAME_1, _FRAME_2, _FRAME_3, _UNSUBSCRIBE_ACK)
        mock_open.return_value = ws

        frames = read_metrics(_DEFAULT_CFG, count=3)

        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0], _FRAME_1)
        self.assertEqual(frames[1], _FRAME_2)
        self.assertEqual(frames[2], _FRAME_3)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_timeout_cuts_early_returns_partial_list(self, mock_open):
        """count=10 but timeout after 2 frames → returns 2 frames, no raise."""
        ws = _make_ws_mock(
            _SUBSCRIBE_ACK,
            _FRAME_1,
            _FRAME_2,
            TimeoutError("no more frames"),
        )
        mock_open.return_value = ws

        frames = read_metrics(_DEFAULT_CFG, count=10)

        self.assertEqual(len(frames), 2)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_count_0_returns_empty_list(self, mock_open):
        """count=0 → no metric recv calls beyond the subscribe ack."""
        ws = _make_ws_mock(_SUBSCRIBE_ACK, _UNSUBSCRIBE_ACK)
        mock_open.return_value = ws

        frames = read_metrics(_DEFAULT_CFG, count=0)

        self.assertEqual(frames, [])


# ---------------------------------------------------------------------------
# 5. read_metrics edge cases
# ---------------------------------------------------------------------------


class ReadMetricsEdgeCaseTests(unittest.TestCase):
    """read_metrics handles malformed frames, bad ack, and connection failure."""

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_malformed_json_frame_skipped_silently(self, mock_open):
        """Frames that cannot be JSON-decoded are skipped; good frames returned.

        Recv sequence: subscribe_ack, malformed, FRAME_2, timeout.
        count=2: only 1 good frame arrives before timeout → returns [FRAME_2].
        """
        ws = _make_ws_mock(_SUBSCRIBE_ACK, "{{not json", _FRAME_2, TimeoutError("no more"))
        mock_open.return_value = ws

        frames = read_metrics(_DEFAULT_CFG, count=2)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], _FRAME_2)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_subscribe_ack_with_wrong_cmd_raises(self, mock_open):
        """If the subscribe ack has the wrong cmd, LiveOcuduError is raised."""
        bad_ack = {"cmd": "something_else", "timestamp": "2026-05-25T00:00:00Z"}
        ws = _make_ws_mock(bad_ack)
        mock_open.return_value = ws

        with self.assertRaises(LiveOcuduError):
            read_metrics(_DEFAULT_CFG, count=1)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_connection_failure_raises_live_ocudu_error(self, mock_open):
        """Connection failure propagates as LiveOcuduError."""
        mock_open.side_effect = LiveOcuduError("could not connect to ws://127.0.0.1:8001/: refused")

        with self.assertRaises(LiveOcuduError) as ctx:
            read_metrics(_DEFAULT_CFG, count=3)
        self.assertIn("could not connect", ctx.exception.safe_message)


# ---------------------------------------------------------------------------
# 6. readiness checks
# ---------------------------------------------------------------------------


class ReadinessTests(unittest.TestCase):
    """readiness() performs connect + subscribe + metric frame check."""

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_all_green_returns_ready_true(self, mock_open):
        """All 3 checks pass → ready=True, failures=[]."""
        ws = _make_ws_mock(_SUBSCRIBE_ACK, _FRAME_1)
        mock_open.return_value = ws

        result = readiness(_DEFAULT_CFG)

        self.assertTrue(result["ready"])
        self.assertEqual(result["failures"], [])
        self.assertTrue(result["checks"]["connect"])
        self.assertTrue(result["checks"]["subscribe_ack"])
        self.assertTrue(result["checks"]["metric_frame"])

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_connection_failure_gives_ready_false(self, mock_open):
        """Connection failure → ready=False, 'connect' listed in failures."""
        mock_open.side_effect = LiveOcuduError("could not connect: refused")

        result = readiness(_DEFAULT_CFG)

        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["connect"])
        failure_str = " ".join(result["failures"])
        self.assertIn("connect", failure_str)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_subscribe_ack_ok_but_no_metric_frame_gives_ready_false(self, mock_open):
        """Subscribe ack arrives but metric frame times out → ready=False, metric_frame listed."""
        ws = _make_ws_mock(_SUBSCRIBE_ACK, TimeoutError("no metric"))
        mock_open.return_value = ws

        result = readiness(_DEFAULT_CFG)

        self.assertFalse(result["ready"])
        self.assertTrue(result["checks"]["connect"])
        self.assertTrue(result["checks"]["subscribe_ack"])
        self.assertFalse(result["checks"]["metric_frame"])
        failure_str = " ".join(result["failures"])
        self.assertIn("metric_frame", failure_str)

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_subscribe_ack_and_metric_frame_gives_ready_true(self, mock_open):
        """subscribe ack + metric frame within timeout → ready=True."""
        ws = _make_ws_mock(_SUBSCRIBE_ACK, _FRAME_1)
        mock_open.return_value = ws

        result = readiness(_DEFAULT_CFG)

        self.assertTrue(result["ready"])
        self.assertEqual(result["failures"], [])


# ---------------------------------------------------------------------------
# 7. Connection cleanup
# ---------------------------------------------------------------------------


class ConnectionCleanupTests(unittest.TestCase):
    """WS .close() is always called, even on error paths."""

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_send_command_closes_ws_on_success(self, mock_open):
        """send_command calls ws.close() after a successful exchange."""
        request = {"cmd": "cfo", "argv": [0, 100.0]}
        ack = {"cmd": "cfo", "timestamp": "2026-05-25T00:00:00Z"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        send_command(_DEFAULT_CFG, request)

        ws.close.assert_called_once()

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_send_command_closes_ws_on_error(self, mock_open):
        """send_command calls ws.close() even when LiveOcuduError is raised."""
        request = {"cmd": "ho", "argv": [1, 0xC350, 999]}
        ack = {"cmd": "ho", "error": "target not found"}
        ws = _make_ws_mock(ack)
        mock_open.return_value = ws

        with self.assertRaises(LiveOcuduError):
            send_command(_DEFAULT_CFG, request)

        ws.close.assert_called_once()

    @patch("benchmark.benchmark_api.live_ocudu._open_ws")
    def test_read_metrics_sends_unsubscribe_and_closes_on_exception(self, mock_open):
        """read_metrics sends metrics_unsubscribe and closes WS even when a recv raises."""
        # After subscribe ack, mid-stream exception occurs
        ws = _make_ws_mock(_SUBSCRIBE_ACK, _FRAME_1, TimeoutError("cut off"))
        mock_open.return_value = ws

        # Should NOT raise — partial list returned
        frames = read_metrics(_DEFAULT_CFG, count=5)

        # unsubscribe must be sent
        send_calls = [c[0][0] for c in ws.send.call_args_list]
        unsubscribe_sent = any(
            "metrics_unsubscribe" in s for s in send_calls
        )
        self.assertTrue(unsubscribe_sent, f"metrics_unsubscribe not sent; calls: {send_calls}")
        ws.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
