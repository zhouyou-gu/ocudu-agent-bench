"""Live-OCUDU adapter: all interaction with the running OCUDU gNB's WebSocket
and stdin CLI (via docker exec into the gnb's stdin FIFO).

This module is the single seam between the benchmark harness and the live
OCUDU gNB deployed by ``benchmark/provision/``.

Public API (WebSocket transport)
---------------------------------
readiness       -- connect + subscribe + receive one metric frame (health check)
send_command    -- open WS, send one action request, await ack, close
read_metrics    -- open WS, subscribe, collect N metric frames, unsubscribe, close

Public API (CLI / stdin transport)
------------------------------------
send_cli            -- write one CLI line to gnb stdin FIFO via docker exec,
                       wait, check docker logs for "Invalid ..." errors
dispatch_cli_action -- convert a RanActionType + action dict to a CLI line
                       and call send_cli

All WebSocket I/O flows through a single ``_open_ws`` seam. Tests patch that
one symbol to mock all network I/O.

All subprocess I/O (docker exec + docker logs) flows through ``_run_subprocess``.
Tests patch that one symbol to avoid real docker calls.

Protocol (WebSocket)
---------------------
* Endpoint: ws://<host>:<port>/ (default ws://127.0.0.1:8001/)
* Each message is UTF-8 JSON. Every request carries a top-level ``"cmd"`` key.
* Success ack: ``{"cmd": "<echoed_name>", "timestamp": "<iso8601>", ...}``
* Error ack: ``{"cmd": "<echoed_name>", "error": "<msg>", ...}`` OR any
  response missing the expected ``"cmd"`` key.
* Metric frames: continuous JSON objects after the metrics_subscribe ack.

Protocol (CLI / stdin)
-----------------------
* The gnb container runs with stdin piped from ``stdbuf -oL tail -F /tmp/gnb_stdin``.
* Commands are appended to /tmp/gnb_stdin via ``docker exec ocudu-gnb sh -c "echo '<cmd>' >> /tmp/gnb_stdin"``.
* Errors appear in gnb stdout as lines starting with "Invalid " or "Usage:".
* Success is silent or emits a command-specific message (e.g. "CFO set to ...").

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
* _run_subprocess is a thin wrapper around subprocess.run; it converts
  TimeoutExpired and OSError to LiveOcuduError so callers don't need to
  import subprocess.
"""

from __future__ import annotations

import datetime
import json
import re
import time
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
    # CLI-side transport (docker exec into the running gnb's stdin FIFO).
    container_name: str = "ocudu-gnb"
    stdin_file_path: str = "/tmp/gnb_stdin"
    cli_response_wait_s: float = 1.5     # how long to wait for gnb stdout after writing
    cli_subprocess_timeout_s: float = 10.0


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


# ---------------------------------------------------------------------------
# CLI / stdin transport — subprocess seam + helpers
# ---------------------------------------------------------------------------


def _run_subprocess(argv: list[str], *, timeout: float) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr).

    All docker-exec / docker-logs subprocess calls in this module go through
    here so tests can patch one entry point.
    """
    import subprocess
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        raise LiveOcuduError(f"subprocess timed out: {' '.join(argv)}", cause=exc) from exc
    except OSError as exc:
        raise LiveOcuduError(f"subprocess failed: {' '.join(argv)}: {exc}", cause=exc) from exc


def _docker_logs_tail(cfg: LiveOcuduConfig, *, since_iso: str | None = None,
                      tail_lines: int | None = None) -> str:
    """Fetch the gnb container stdout via ``docker logs``.

    If ``since_iso`` is given, pass ``--since <iso>``; otherwise return up to
    ``tail_lines`` lines (default 200).
    """
    argv = ["docker", "logs"]
    if since_iso is not None:
        argv.extend(["--since", since_iso])
    else:
        argv.extend(["--tail", str(tail_lines if tail_lines is not None else 200)])
    argv.append(cfg.container_name)
    code, out, err = _run_subprocess(argv, timeout=cfg.cli_subprocess_timeout_s)
    if code != 0:
        raise LiveOcuduError(f"docker logs {cfg.container_name} failed: {err.strip()}")
    # docker logs streams stdout to stdout and stderr to stderr; combine.
    return out + err


def _write_stdin_line(cfg: LiveOcuduConfig, line: str) -> None:
    """Append a single line to the gnb stdin FIFO via docker exec."""
    if "\n" in line:
        raise LiveOcuduError(f"CLI line must not contain newlines: {line!r}")
    # Use sh -c with the line single-quoted; escape any embedded single quotes.
    escaped = line.replace("'", "'\\''")
    sh_cmd = f"echo '{escaped}' >> {cfg.stdin_file_path}"
    argv = ["docker", "exec", cfg.container_name, "sh", "-c", sh_cmd]
    code, out, err = _run_subprocess(argv, timeout=cfg.cli_subprocess_timeout_s)
    if code != 0:
        raise LiveOcuduError(f"docker exec write failed: {err.strip() or out.strip()}")


_INVALID_LINE_RE = re.compile(r"^\s*Invalid\b", re.MULTILINE)
_USAGE_LINE_RE = re.compile(r"^\s*Usage:", re.MULTILINE)


def _check_for_error_in_tail(tail: str) -> str | None:
    """Look for an OCUDU 'Invalid ...' or 'Usage:' line in the given stdout tail.

    Return the first matching line or None if no error found.

    OCUDU's cmdline_command implementations call fmt::print("Invalid ...")
    for malformed input; we use that as the failure indicator. Unknown
    commands reprint the help banner, which doesn't trigger this regex --
    that's intentional. Unknown commands surface as "no success line found"
    in the higher-level dispatcher rather than as a CLI parse error.
    """
    m = _INVALID_LINE_RE.search(tail)
    if m:
        line_start = tail.rfind('\n', 0, m.start()) + 1
        line_end = tail.find('\n', m.end())
        if line_end == -1:
            line_end = len(tail)
        return tail[line_start:line_end].strip()
    m = _USAGE_LINE_RE.search(tail)
    if m:
        line_start = tail.rfind('\n', 0, m.start()) + 1
        line_end = tail.find('\n', m.end())
        if line_end == -1:
            line_end = len(tail)
        return tail[line_start:line_end].strip()
    return None


# ---------------------------------------------------------------------------
# CLI / stdin transport — public API
# ---------------------------------------------------------------------------


def send_cli(cfg: LiveOcuduConfig, line: str) -> dict[str, Any]:
    """Send a single CLI command line to the gnb's stdin FIFO and report the outcome.

    Writes the line via ``docker exec`` into the gnb's stdin FIFO, waits
    ``cfg.cli_response_wait_s`` seconds, then reads ``docker logs --since``
    to check for an OCUDU error response.

    Returns ``{"ok": True, "line": line, "stdout_tail": str}`` on success.
    Raises ``LiveOcuduError`` if the gnb emitted an "Invalid ..." or
    "Usage: ..." line shortly after the write.

    Successful commands typically emit a command-specific success message
    (e.g. "CFO set to -1250Hz for sector 0."); silent-success commands
    also pass. We don't verify positive success here — the caller can do
    that via metrics_subscribe or other observation.
    """
    # Record the write timestamp so we only inspect NEW stdout lines.
    write_time = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _write_stdin_line(cfg, line)
    time.sleep(cfg.cli_response_wait_s)
    tail = _docker_logs_tail(cfg, since_iso=write_time)
    err = _check_for_error_in_tail(tail)
    if err is not None:
        raise LiveOcuduError(f"gnb rejected '{line}': {err}")
    return {"ok": True, "line": line, "stdout_tail": tail}


def dispatch_cli_action(cfg: LiveOcuduConfig, action_type_value: str,
                        action: dict[str, Any]) -> dict[str, Any]:
    """Convert a benchmark RanActionType value + action dict into a CLI line
    and dispatch it via ``send_cli``.

    Supports four action types (the OCUDU stdin-only commands):

    * ``TRIGGER_HANDOVER_CLI``
      → ``ho <serving_pci> <rnti> <target_pci> <target_plmn> <target_tac>``
    * ``TRIGGER_CONDITIONAL_HANDOVER_CLI``
      → ``cho <serving_pci> <rnti> <target_pci_1> [...] [timeout <s>]``
    * ``SET_CFO_CLI``
      → ``cfo <sector_id> <cfo_hz>``
    * ``SET_TX_TIME_OFFSET_CLI``
      → ``tx_time_offset <sector_id> <tx_time_offset_us>``

    Raises ``LiveOcuduError`` on unsupported action type or missing required fields.
    """
    if action_type_value == "TRIGGER_HANDOVER_CLI":
        try:
            serving_pci = action["serving_pci"]
            rnti = action["rnti"]
            target_pci = action["target_pci"]
        except KeyError as e:
            raise LiveOcuduError(f"missing required field for TRIGGER_HANDOVER_CLI: {e}") from e
        target_plmn = action.get("target_plmn", "00101")
        target_tac = action.get("target_tac", 1)
        line = f"ho {serving_pci} {rnti} {target_pci} {target_plmn} {target_tac}"

    elif action_type_value == "TRIGGER_CONDITIONAL_HANDOVER_CLI":
        try:
            serving_pci = action["serving_pci"]
            rnti = action["rnti"]
            target_pcis = action["target_pcis"]
        except KeyError as e:
            raise LiveOcuduError(
                f"missing required field for TRIGGER_CONDITIONAL_HANDOVER_CLI: {e}"
            ) from e
        if not isinstance(target_pcis, (list, tuple)) or not target_pcis:
            raise LiveOcuduError("target_pcis must be a non-empty list")
        parts = ["cho", str(serving_pci), str(rnti)]
        parts.extend(str(p) for p in target_pcis)
        if action.get("timeout_s") is not None:
            parts.extend(["timeout", str(action["timeout_s"])])
        line = " ".join(parts)

    elif action_type_value == "SET_CFO_CLI":
        try:
            sector_id = action["sector_id"]
            cfo_hz = action["cfo_hz"]
        except KeyError as e:
            raise LiveOcuduError(f"missing required field for SET_CFO_CLI: {e}") from e
        line = f"cfo {sector_id} {cfo_hz}"

    elif action_type_value == "SET_TX_TIME_OFFSET_CLI":
        try:
            sector_id = action["sector_id"]
            tx_time_offset_us = action["tx_time_offset_us"]
        except KeyError as e:
            raise LiveOcuduError(
                f"missing required field for SET_TX_TIME_OFFSET_CLI: {e}"
            ) from e
        line = f"tx_time_offset {sector_id} {tx_time_offset_us}"

    else:
        raise LiveOcuduError(
            f"dispatch_cli_action does not support action type: {action_type_value}"
        )

    return send_cli(cfg, line)
