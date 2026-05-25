"""Live-E2 adapter: KPM v05 evidence via FlexRIC JSONL + E2 control xApp dispatch.

Single seam between the benchmark harness and the live FlexRIC deployment.
FlexRIC decodes OCUDU E2SM-KPM v05 indications into JSONL and hosts two
OCUDU-specific one-shot control xApps for slice PRB quota actions.

Public API
----------
readiness               -- check container running + JSONL exists + has records
read_kpm                -- return last N parsed KPM indication records
latest_kpm_measurements -- convenience: flatten the most-recent record's
                           measurements to {name: value}
dispatch_rc_du_prb_policy -- run ocudu-rc-du-prb-control (RAN func 3, RIC style 2
                             action 6) for per-UE slice PRB quota
dispatch_ccc_prb_policy   -- run ocudu-ccc-prb-control (RAN func 4, CCC style 2
                             action 6) for cell-level slice PRB quota

All subprocess I/O flows through _run_subprocess; tests patch that one symbol.

JSONL format (one record per line, written by the FlexRIC xApp subscriber)::

    {
        "decoded_by": "ocudu-generated-asn1-cpp",
        "kpm_version": "E2SM-KPM-R003-v05.00",
        "format": "ind_msg_format1",
        "measurements": [
            {"name": "RRU.PrbAvailDl", "type": "integer", "value": 106},
            {"name": "RRU.PrbUsedDl",  "type": "integer", "value": 0},
            {"name": "RRU.PrbTotDl",   "type": "integer", "value": 0}
        ]
    }

Records arrive at ~1/s per E2 node. File grows monotonically; no rotation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveE2Config:
    container_name: str = "flexric-ric"
    jsonl_path: str = "/var/log/flexric/kpm.jsonl"
    subprocess_timeout_s: float = 10.0
    # readiness considers the stack healthy if the JSONL has at least
    # this many records — guards against "container up but no indications yet"
    min_records_for_ready: int = 1
    # RC-DU control xApp dispatch (Phase 2b)
    rc_du_xapp_path: str = "/opt/flexric/build/examples/xApp/c/control/ocudu-rc-du-prb-control"
    rc_du_xapp_conf: str = "/etc/xapp/xapp_oran_sm.conf"
    rc_du_xapp_timeout_s: float = 30.0  # xApp takes ~1-3s to init RIC + send + cleanup
    # CCC control xApp dispatch (Phase 2b)
    ccc_xapp_path: str = "/opt/flexric/build/examples/xApp/c/control/ocudu-ccc-prb-control"
    ccc_xapp_conf: str = "/etc/xapp/xapp_oran_sm.conf"
    ccc_xapp_timeout_s: float = 30.0


class LiveE2Error(RuntimeError):
    """Raised when a live-e2 operation fails. Carries safe_message + cause.

    safe_message is suitable for logging and agent feedback (no stack frames,
    no raw exception types). cause is the underlying exception, if any.
    """

    def __init__(self, safe_message: str, cause: BaseException | None = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.cause = cause


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_subprocess(argv: list[str], *, timeout: float) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr).

    All module subprocess calls go through here so tests can patch one symbol.
    Raises LiveE2Error on timeout or OSError.
    """
    import subprocess
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        raise LiveE2Error(f"subprocess timed out: {' '.join(argv)}", cause=exc) from exc
    except OSError as exc:
        raise LiveE2Error(f"subprocess failed: {' '.join(argv)}: {exc}", cause=exc) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def readiness(cfg: LiveE2Config) -> dict[str, Any]:
    """Verify the flexric container is running and the JSONL has records.

    Returns::

        {
            "ready": bool,
            "checks": {
                "container_running": bool,
                "jsonl_exists": bool,
                "jsonl_has_records": bool,
                "record_count": int,
            },
            "failures": ["check name: reason", ...],
        }

    Does not raise. Caller decides what to do with the result.

    Implementation
    --------------
    1. ``docker inspect <container> --format {{.State.Running}}`` to check
       container is up.
    2. ``docker exec <container> wc -l <jsonl_path>`` to check existence and
       count lines (each line is one record). Failure of docker exec could mean
       the container is not running OR the file does not exist yet.
    """
    checks: dict[str, Any] = {
        "container_running": False,
        "jsonl_exists": False,
        "jsonl_has_records": False,
        "record_count": 0,
    }
    failures: list[str] = []

    # 1. Container running check
    try:
        rc, stdout, stderr = _run_subprocess(
            ["docker", "inspect", cfg.container_name, "--format", "{{.State.Running}}"],
            timeout=cfg.subprocess_timeout_s,
        )
        if rc == 0 and stdout.strip().lower() == "true":
            checks["container_running"] = True
        else:
            failures.append(
                f"container_running: {cfg.container_name} not running "
                f"(rc={rc}, out={stdout.strip()!r}, err={stderr.strip()[:80]!r})"
            )
    except LiveE2Error as exc:
        failures.append(f"container_running: {exc.safe_message}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"container_running: {exc}")

    # 2. JSONL existence + record count via wc -l inside container
    try:
        rc, stdout, stderr = _run_subprocess(
            ["docker", "exec", cfg.container_name, "wc", "-l", cfg.jsonl_path],
            timeout=cfg.subprocess_timeout_s,
        )
        if rc == 0:
            checks["jsonl_exists"] = True
            # wc -l outputs "<count> <filename>" or just "<count>"
            count_str = stdout.strip().split()[0] if stdout.strip() else "0"
            try:
                record_count = int(count_str)
            except ValueError:
                record_count = 0
            checks["record_count"] = record_count
            if record_count >= cfg.min_records_for_ready:
                checks["jsonl_has_records"] = True
            else:
                failures.append(
                    f"jsonl_has_records: only {record_count} record(s) in "
                    f"{cfg.jsonl_path} (need {cfg.min_records_for_ready})"
                )
        else:
            # Non-zero exit: file might not exist yet
            failures.append(
                f"jsonl_exists: wc -l exited {rc}: {stderr.strip()[:120]}"
            )
    except LiveE2Error as exc:
        failures.append(f"jsonl_exists: {exc.safe_message}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"jsonl_exists: {exc}")

    ready = (
        checks["container_running"]
        and checks["jsonl_exists"]
        and checks["jsonl_has_records"]
    )
    return {"ready": ready, "checks": checks, "failures": failures}


def read_kpm(cfg: LiveE2Config, *, count: int = 10) -> list[dict[str, Any]]:
    """Return the last ``count`` decoded KPM v05 records from the JSONL.

    Each returned dict has the structure from the file: decoded_by,
    kpm_version, format, measurements[].

    Implementation: ``docker exec <container_name> tail -n <count> <jsonl_path>``,
    parse each line as JSON, return parsed dicts. Lines that fail to parse are
    silently skipped (not counted toward the returned list).

    Raises LiveE2Error if docker exec fails (container missing, file missing, etc.).
    """
    argv = [
        "docker", "exec", cfg.container_name,
        "tail", "-n", str(count), cfg.jsonl_path,
    ]
    rc, stdout, stderr = _run_subprocess(argv, timeout=cfg.subprocess_timeout_s)
    if rc != 0:
        raise LiveE2Error(
            f"docker exec tail failed for {cfg.container_name}:{cfg.jsonl_path} "
            f"(rc={rc}): {stderr.strip()[:200]}",
        )

    records: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
        except (json.JSONDecodeError, ValueError):
            # Silently skip malformed lines
            continue

    return records


def latest_kpm_measurements(cfg: LiveE2Config) -> dict[str, Any] | None:
    """Return the most-recent record's measurements as ``{name: value, ...}``.

    Returns None if the file has no records.
    Raises LiveE2Error on subprocess failure (same as read_kpm).
    """
    records = read_kpm(cfg, count=1)
    if not records:
        return None
    last = records[-1]
    flat: dict[str, Any] = {}
    for m in last.get("measurements", []) or []:
        if isinstance(m, dict) and "name" in m and "value" in m:
            flat[m["name"]] = m["value"]
    return flat


def dispatch_rc_du_prb_policy(
    cfg: LiveE2Config,
    *,
    du_ue_id: int,
    plmn: str = "00101",
    sst: int = 1,
    sd: int | None = None,
    min_prb_policy_ratio: int | None = None,
    max_prb_policy_ratio: int | None = None,
) -> dict[str, Any]:
    """Dispatch SET_PRB_POLICY_RATIO_RC_DU via the ocudu-rc-du-prb-control xApp
    inside the FlexRIC container.

    Required: du_ue_id (the gNB-assigned DU UE ID, NOT the RNTI).
    Optional: plmn, sst, sd (defaults match the standard OCUDU+srsUE ZMQ test
    triplet); min/max ratio (xApp will use its built-in defaults if omitted).

    Returns the parsed JSON dict from the xApp. Always contains "accepted"
    (bool) and "action_type". On success also contains "outcome", "ran_function_id",
    "control_name", "control_style", "control_action", "request" subfields.

    Raises LiveE2Error if:
      * docker exec fails (container missing, etc.)
      * subprocess timeout
      * xApp stdout has no parseable JSON line (highly unexpected since --json
        always emits structured output)

    Does NOT raise on accepted=false — the caller (ran_api dispatch) is
    responsible for mapping that to a non-accepted DispatchResult.
    """
    argv = [
        "docker", "exec", cfg.container_name,
        cfg.rc_du_xapp_path,
        "--json",
        "--conf", cfg.rc_du_xapp_conf,
        "--du-ue-id", str(du_ue_id),
        "--plmn", str(plmn),
        "--sst", str(sst),
    ]
    if sd is not None:
        argv.extend(["--sd", str(sd)])
    if min_prb_policy_ratio is not None:
        argv.extend(["--min-prb-policy-ratio", str(min_prb_policy_ratio)])
    if max_prb_policy_ratio is not None:
        argv.extend(["--max-prb-policy-ratio", str(max_prb_policy_ratio)])

    code, out, err = _run_subprocess(argv, timeout=cfg.rc_du_xapp_timeout_s)
    return _parse_xapp_json(out, err, code, tool_name="ocudu-rc-du-prb-control")


def dispatch_ccc_prb_policy(
    cfg: LiveE2Config,
    *,
    plmn: str = "00101",
    sst: int = 1,
    sd: int | None = None,
    min_prb_policy_ratio: int | None = None,
    max_prb_policy_ratio: int | None = None,
    dedicated_ratio: int | None = None,
) -> dict[str, Any]:
    """Dispatch SET_PRB_POLICY_RATIO_CCC via the ocudu-ccc-prb-control xApp
    inside the FlexRIC container.

    CCC is cell-level (no du_ue_id). Optional: dedicated_ratio (CCC-only;
    the RC-DU SM does not carry it). plmn/sst/sd default to the OCUDU+srsUE
    ZMQ test triplet; min/max/ded ratio fall back to the xApp's built-in
    defaults if omitted.

    Returns the parsed JSON dict from the xApp (same contract as
    dispatch_rc_du_prb_policy: always has "accepted" + "action_type"; on
    success also has "outcome", "ran_function_id", "control_name",
    "control_style", "control_action", "request" subfields).
    """
    argv = [
        "docker", "exec", cfg.container_name,
        cfg.ccc_xapp_path,
        "--json",
        "--conf", cfg.ccc_xapp_conf,
        "--plmn", str(plmn),
        "--sst", str(sst),
    ]
    if sd is not None:
        argv.extend(["--sd", str(sd)])
    if min_prb_policy_ratio is not None:
        argv.extend(["--min-prb-policy-ratio", str(min_prb_policy_ratio)])
    if max_prb_policy_ratio is not None:
        argv.extend(["--max-prb-policy-ratio", str(max_prb_policy_ratio)])
    if dedicated_ratio is not None:
        argv.extend(["--dedicated-ratio", str(dedicated_ratio)])

    code, out, err = _run_subprocess(argv, timeout=cfg.ccc_xapp_timeout_s)
    return _parse_xapp_json(out, err, code, tool_name="ocudu-ccc-prb-control")


def _parse_xapp_json(stdout: str, stderr: str, code: int, *, tool_name: str) -> dict[str, Any]:
    """Extract the last `{...}` line from the xApp's combined output.

    Earlier lines are FlexRIC stdout noise from the xApp's startup; the
    structured JSON ack/error is always the final `{`-prefixed line under
    --json.
    """
    combined = (stdout or "") + (stderr or "")
    json_lines = [line for line in combined.splitlines() if line.startswith("{")]
    if not json_lines:
        raise LiveE2Error(
            f"{tool_name} produced no JSON line "
            f"(code={code}, stderr_tail={(stderr or '').splitlines()[-3:]})"
        )
    last_json = json_lines[-1]
    try:
        return json.loads(last_json)
    except json.JSONDecodeError as exc:
        raise LiveE2Error(
            f"{tool_name} returned unparseable JSON: {last_json!r}",
            cause=exc,
        ) from exc
