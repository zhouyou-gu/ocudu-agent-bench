"""Live-OCUDU adapter: all interaction with the running OCUDU gNB's WebSocket.

This module is the single seam between the benchmark harness and the live
OCUDU gNB deployed by ``benchmark/provision/``. It is NOT yet wired into
runtime_setup.py or ran_api.py dispatch — that happens in the next slice.

Public API
----------
readiness       -- connect + subscribe + receive one metric frame (health check)
send_command    -- open WS, send one action request, await ack, close
read_metrics    -- open WS, subscribe, collect N metric frames, unsubscribe, close

All WebSocket I/O flows through a single ``_open_ws`` seam. Tests patch that
one symbol to mock all network I/O.

Protocol
--------
* Endpoint: ws://<host>:<port>/ (default ws://127.0.0.1:8001/)
* Each message is UTF-8 JSON. Every request carries a top-level ``"cmd"`` key.
* Success ack: ``{"cmd": "<echoed_name>", "timestamp": "<iso8601>", ...}``
* Error ack: ``{"cmd": "<echoed_name>", "error": "<msg>", ...}`` OR any
  response missing the expected ``"cmd"`` key.
* Metric frames: continuous JSON objects after the metrics_subscribe ack.

Design notes
------------
* websocket-client is imported lazily inside _open_ws so the module can be
  imported on a machine that doesn't have it installed (e.g. CI).
* send_command / read_metrics / readiness always close the WS connection in a
  finally block, even when an exception is raised mid-exchange.
* read_metrics treats metric-frame recv timeouts as a soft stop (returns the
  partial list). subscribe/unsubscribe ack failures are hard errors.
* readiness never raises; it returns a structured dict so the caller can
  decide what to do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveOcuduConfig:
    ws_url: str = "ws://127.0.0.1:8001/"
    connect_timeout_s: float = 5.0
    recv_timeout_s: float = 3.0   # per-message timeout when waiting for ack / metric


class LiveOcuduError(RuntimeError):
    """Raised when a live-ocudu operation fails. Carries safe_message + cause.

    safe_message is suitable for logging and agent feedback (no stack frames).
    cause is the underlying exception, if any.
    """

    def __init__(self, safe_message: str, cause: BaseException | None = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.cause = cause


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_ws(cfg: LiveOcuduConfig):
    """Lazy-import websocket-client and return a connected WebSocket.

    Raises LiveOcuduError on any failure (ImportError, connection refused,
    timeout). Returned object supports .send(str), .recv() -> str|bytes,
    .settimeout(float), .close().

    In tests, _open_ws is mock.patched so no real network is required.
    """
    try:
        from websocket import create_connection  # type: ignore
    except ImportError as exc:
        raise LiveOcuduError("websocket-client is not installed", cause=exc) from exc
    try:
        return create_connection(cfg.ws_url, timeout=cfg.connect_timeout_s)
    except Exception as exc:  # noqa: BLE001
        raise LiveOcuduError(
            f"could not connect to {cfg.ws_url}: {exc}", cause=exc
        ) from exc


def _recv_ack(ws, expected_cmd: str, timeout_s: float) -> dict[str, Any]:
    """Recv one JSON message and verify its 'cmd' matches expected_cmd.

    Raises LiveOcuduError on:
      * recv timeout / socket error
      * JSON decode failure
      * 'error' field present in response
      * missing or mismatched 'cmd'
    """
    ws.settimeout(timeout_s)
    try:
        raw = ws.recv()
    except (TimeoutError, OSError) as exc:
        raise LiveOcuduError(
            f"timeout waiting for ack of '{expected_cmd}'", cause=exc
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise LiveOcuduError(
            f"recv error waiting for ack of '{expected_cmd}': {exc}", cause=exc
        ) from exc

    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LiveOcuduError(
            f"non-JSON response waiting for ack of '{expected_cmd}'", cause=exc
        ) from exc

    if "error" in msg:
        raise LiveOcuduError(
            f"error from gNB for '{expected_cmd}': {msg['error']}",
        )

    actual_cmd = msg.get("cmd")
    if actual_cmd != expected_cmd:
        raise LiveOcuduError(
            f"cmd mismatch: expected '{expected_cmd}', got '{actual_cmd}'"
        )

    return msg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_command(cfg: LiveOcuduConfig, request: dict[str, Any]) -> dict[str, Any]:
    """Open a WS connection, send the request JSON, await one ack response, close.

    Returns the parsed ack as a dict.

    Raises LiveOcuduError if:
      * request is missing "cmd" (before opening connection)
      * connection fails
      * recv times out
      * response missing "cmd" or cmd doesn't match
      * response contains "error"

    The ``request`` MUST include "cmd". Does NOT mutate or rebuild it.
    Suitable for one-shot action dispatch (PRB / SSB / HO / CHO / CFO / TX-time).
    """
    if "cmd" not in request:
        raise LiveOcuduError(
            "request is missing required 'cmd' key"
        )

    cmd = request["cmd"]
    ws = _open_ws(cfg)
    try:
        ws.send(json.dumps(request))
        ack = _recv_ack(ws, cmd, cfg.recv_timeout_s)
    finally:
        ws.close()

    return ack


def read_metrics(
    cfg: LiveOcuduConfig,
    *,
    count: int,
    timeout_s: float | None = None,
) -> list[dict[str, Any]]:
    """Open WS, send metrics_subscribe, collect up to ``count`` metric frames.

    Excludes the subscribe ack itself from the returned list. Sends
    metrics_unsubscribe and closes the connection before returning.

    If ``timeout_s`` is None, uses cfg.recv_timeout_s per recv. If the
    timeout elapses before ``count`` frames arrive, returns the partial list
    (does NOT raise). Connection failures DO raise LiveOcuduError.

    Frames whose JSON cannot be parsed are skipped silently.
    """
    per_recv = timeout_s if timeout_s is not None else cfg.recv_timeout_s
    frames: list[dict[str, Any]] = []

    ws = _open_ws(cfg)
    try:
        # Subscribe
        ws.send(json.dumps({"cmd": "metrics_subscribe"}))
        _recv_ack(ws, "metrics_subscribe", cfg.recv_timeout_s)

        # Collect frames
        while len(frames) < count:
            ws.settimeout(per_recv)
            try:
                raw = ws.recv()
            except (TimeoutError, OSError):
                # Soft stop — return what we have
                break
            except Exception:  # noqa: BLE001
                break

            try:
                frame = json.loads(raw)
                frames.append(frame)
            except (json.JSONDecodeError, TypeError, ValueError):
                # Silently skip malformed frames
                continue

        # Unsubscribe (best-effort; don't let this suppress the frames we have)
        try:
            ws.send(json.dumps({"cmd": "metrics_unsubscribe"}))
        except Exception:  # noqa: BLE001
            pass
    finally:
        ws.close()

    return frames


def readiness(cfg: LiveOcuduConfig) -> dict[str, Any]:
    """Connect + send metrics_subscribe + receive at least one metric frame.

    Returns:
        {
            "ready": bool,
            "checks": {
                "connect": bool,
                "subscribe_ack": bool,
                "metric_frame": bool,
            },
            "failures": ["check name: reason", ...]   # empty when ready=True
        }

    Does not raise. Caller decides what to do with the result.
    """
    checks: dict[str, bool] = {
        "connect": False,
        "subscribe_ack": False,
        "metric_frame": False,
    }
    failures: list[str] = []

    ws = None

    # 1. Connect
    try:
        ws = _open_ws(cfg)
        checks["connect"] = True
    except LiveOcuduError as exc:
        failures.append(f"connect: {exc.safe_message}")
        return {"ready": False, "checks": checks, "failures": failures}
    except Exception as exc:  # noqa: BLE001
        failures.append(f"connect: {exc}")
        return {"ready": False, "checks": checks, "failures": failures}

    try:
        # 2. Subscribe ack
        try:
            ws.send(json.dumps({"cmd": "metrics_subscribe"}))
            _recv_ack(ws, "metrics_subscribe", cfg.recv_timeout_s)
            checks["subscribe_ack"] = True
        except LiveOcuduError as exc:
            failures.append(f"subscribe_ack: {exc.safe_message}")
            return {"ready": False, "checks": checks, "failures": failures}
        except Exception as exc:  # noqa: BLE001
            failures.append(f"subscribe_ack: {exc}")
            return {"ready": False, "checks": checks, "failures": failures}

        # 3. At least one metric frame
        ws.settimeout(cfg.recv_timeout_s)
        try:
            raw = ws.recv()
            json.loads(raw)  # just check it parses; don't validate inner schema
            checks["metric_frame"] = True
        except (TimeoutError, OSError) as exc:
            failures.append(f"metric_frame: no metric frame received within timeout")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            failures.append(f"metric_frame: received non-JSON frame: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"metric_frame: {exc}")

        # Best-effort unsubscribe
        try:
            ws.send(json.dumps({"cmd": "metrics_unsubscribe"}))
        except Exception:  # noqa: BLE001
            pass

    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass

    ready = all(checks.values())
    return {"ready": ready, "checks": checks, "failures": failures}
