"""Live-core adapter: all interaction with the running Open5GS compose stack.

This module is the single seam between the benchmark harness and the live
Open5GS 5GC deployed by ``benchmark/provision/open5gs-core/``. It is NOT yet
wired into runtime_setup.py or ran_api.py dispatch — that happens in the next
slice. Import it standalone, or let the future live-adapter branch call it.

Public API
----------
readiness       -- run the 5 acceptance checks (port-of check_core_ready.sh)
restart_nf      -- docker compose restart <nf>
upsert_subscriber -- upsert one subscriber document via pymongo
read_subscriber -- find one subscriber by IMSI
read_runtime    -- build a core_runtime payload matching the simulated schema
apply_tc_netem  -- docker compose exec tc qdisc replace ... netem

All subprocess I/O flows through _run_subprocess; all mongo I/O flows through
_mongo_client. Both are thin wrappers patched by unit tests.

Design notes
------------
* pymongo is imported lazily inside _mongo_client so the module can be
  imported on a machine without pymongo (e.g. the benchmark's CI host). An
  ImportError from pymongo is re-raised as a LiveCoreError.
* subscriber_document() is loaded lazily via importlib from the seeder file at
  benchmark/provision/open5gs-core/compose/seed/add_users.py. This avoids
  making that non-package script a regular Python import dependency.
* read_subscriber raises LiveCoreError on mongo failure (unlike readiness,
  which never raises). The choice: callers doing explicit UE lookup want a
  clear error rather than silent None; read_runtime catches the error and
  populates "errors" so it stays non-raising.
* restart_counts is not tracked across runs — live adapter delegates counter
  management to handle.state in the dispatcher.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveCoreConfig:
    compose_project: str = "open5gs"
    compose_file: str | None = None   # if None, use -p <project> only
    mongo_uri: str = "mongodb://127.0.0.1:27017/"
    mongo_db: str = "open5gs"
    timeout_s: float = 10.0           # default timeout for docker exec / mongo


class LiveCoreError(RuntimeError):
    """Raised when a live-core operation fails. Carries safe_message + cause.

    safe_message is suitable for logging and agent feedback (no stack frames,
    no raw exception types). cause is the underlying exception, if any.
    """

    def __init__(self, safe_message: str, cause: BaseException | None = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.cause = cause


# ---------------------------------------------------------------------------
# Default security triplet (must match subscriber_db.csv in the seeder)
# ---------------------------------------------------------------------------

_DEFAULT_K = "00112233445566778899aabbccddeeff"
_DEFAULT_OPC = "63bfa50ee6523365ff14c1f45f88737d"
_DEFAULT_AMF = "8000"
_DEFAULT_SQN = "000000000000"

# IMSI seeded by subscriber_db.csv; used by read_runtime for UE registration
_SEEDED_IMSI = "001010000000001"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_subprocess(
    argv: list[str],
    *,
    timeout: float,
    input_bytes: bytes | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr).

    All module subprocess calls go through here so tests can patch one symbol.
    Raises subprocess.TimeoutExpired unchanged — callers translate to LiveCoreError.
    """
    result = subprocess.run(
        argv,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
    )
    return (result.returncode, result.stdout.decode("utf-8", errors="replace"),
            result.stderr.decode("utf-8", errors="replace"))


def _compose_args(cfg: LiveCoreConfig, *extra: str) -> list[str]:
    """Build the base docker compose argv for the given config.

    Returns:
        ['docker', 'compose', '-p', <project>, ['-f', <file>,] *extra]
    """
    base = ["docker", "compose", "-p", cfg.compose_project]
    if cfg.compose_file is not None:
        base += ["-f", cfg.compose_file]
    return base + list(extra)


def _mongo_client(cfg: LiveCoreConfig):
    """Lazy-import pymongo and return a MongoClient with the configured URI.

    serverSelectionTimeoutMS is set from cfg.timeout_s so mongo operations
    respect the same timeout budget as subprocess calls.

    Raises LiveCoreError if pymongo is not installed.
    """
    try:
        from pymongo import MongoClient  # type: ignore
    except ImportError as exc:
        raise LiveCoreError(
            "pymongo is not installed; cannot connect to Open5GS mongo",
            cause=exc,
        ) from exc
    return MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=int(cfg.timeout_s * 1000))


def _load_subscriber_document(row: dict[str, str]) -> dict[str, Any]:
    """Load subscriber_document() from the seeder script and call it with row.

    Uses importlib to load the seeder as a standalone module so it is not
    required to be on sys.path as a package.

    Raises LiveCoreError if the seeder file cannot be found.
    Raises ValueError (from subscriber_document) on validation failure —
    the caller (upsert_subscriber) wraps that in LiveCoreError.
    """
    seeder_path = (
        Path(__file__).resolve().parents[2]
        / "provision"
        / "open5gs-core"
        / "compose"
        / "seed"
        / "add_users.py"
    )
    if not seeder_path.exists():
        raise LiveCoreError(
            f"seeder script not found at expected path: {seeder_path}"
        )
    spec = importlib.util.spec_from_file_location("_live_core_add_users", seeder_path)
    if spec is None or spec.loader is None:
        raise LiveCoreError(f"could not load seeder spec from {seeder_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod.subscriber_document(row)


def _to_subscriber_csv_row(registration: dict[str, Any]) -> dict[str, str]:
    """Convert a runtime registration dict to the seeder's CSV-row dict.

    Field mapping:
        supi        → imsi
        sd None     → ""  (seeder treats empty string as absent)
        sd int/str  → str(sd)
        sst         → str(sst)
        security{}  → overrides for k/opc/amf/sqn; missing keys fall back to
                      the standard srsRAN ZMQ test triplet baked into
                      subscriber_db.csv.

    All other fields (ue_id, plmn, dnn, auth_profile_id) pass through as str.
    """
    security = registration.get("security") or {}
    sd_raw = registration.get("sd")
    if sd_raw is None:
        sd_str = ""
    else:
        sd_str = str(sd_raw)

    return {
        "imsi": str(registration.get("supi", "")),
        "k": str(security.get("k", _DEFAULT_K)),
        "opc": str(security.get("opc", _DEFAULT_OPC)),
        "amf": str(security.get("amf", _DEFAULT_AMF)),
        "sqn": str(security.get("sqn", _DEFAULT_SQN)),
        "plmn": str(registration.get("plmn", "")),
        "dnn": str(registration.get("dnn", "")),
        "sst": str(registration.get("sst", "")),
        "sd": sd_str,
        "auth_profile_id": str(registration.get("auth_profile_id", "")),
        "ue_id": str(registration.get("ue_id", "")),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def readiness(cfg: LiveCoreConfig) -> dict[str, Any]:
    """Run all 5 acceptance checks (port-of check_core_ready.sh).

    Returns a dict with:
        ready    -- bool; True iff all 5 checks pass
        checks   -- {check_name: bool}  (all 5 keys always present)
        failures -- ["check_name: reason", ...]  (empty when ready=True)

    This function never raises. On any internal exception the corresponding
    check is marked False and the failure is appended to `failures`.

    Checks
    ------
    1. compose_running    -- `docker compose ps --services` exits 0
    2. amf_sctp_bound     -- `docker compose exec -T amf ss -ln` shows 38412
    3. amf_nf_registered  -- `docker compose logs --tail=50 amf` contains "NF registered"
    4. subscriber_present -- mongo find_one({imsi: _SEEDED_IMSI}) is not None
    5. mongo_loopback     -- mongo client can be constructed and find_one succeeds
                            (same underlying call as subscriber_present, so they
                            travel or fail together; kept separate for clarity)
    """
    checks: dict[str, bool] = {
        "compose_running": False,
        "amf_sctp_bound": False,
        "amf_nf_registered": False,
        "subscriber_present": False,
        "mongo_loopback": False,
    }
    failures: list[str] = []

    # 1. compose_running
    try:
        rc, stdout, stderr = _run_subprocess(
            _compose_args(cfg, "ps", "--services"),
            timeout=cfg.timeout_s,
        )
        if rc == 0 and stdout.strip():
            checks["compose_running"] = True
        else:
            failures.append(f"compose_running: compose ps exited {rc}: {stderr.strip()[:120]}")
    except subprocess.TimeoutExpired:
        failures.append("compose_running: docker compose timed out")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"compose_running: {exc}")

    # 2. amf_sctp_bound — ss -ln inside the amf container
    try:
        rc, stdout, stderr = _run_subprocess(
            _compose_args(cfg, "exec", "-T", "amf", "ss", "-ln"),
            timeout=cfg.timeout_s,
        )
        if rc == 0 and "38412" in stdout:
            checks["amf_sctp_bound"] = True
        else:
            failures.append(
                f"amf_sctp_bound: 38412 not found in ss output (rc={rc}): {stderr.strip()[:80]}"
            )
    except subprocess.TimeoutExpired:
        failures.append("amf_sctp_bound: docker compose exec timed out")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"amf_sctp_bound: {exc}")

    # 3. amf_nf_registered — tail compose logs for AMF
    try:
        rc, stdout, stderr = _run_subprocess(
            _compose_args(cfg, "logs", "--tail=50", "amf"),
            timeout=cfg.timeout_s,
        )
        if rc == 0 and "NF registered" in stdout:
            checks["amf_nf_registered"] = True
        else:
            failures.append(
                f"amf_nf_registered: 'NF registered' not in amf logs (rc={rc})"
            )
    except subprocess.TimeoutExpired:
        failures.append("amf_nf_registered: docker compose logs timed out")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"amf_nf_registered: {exc}")

    # 4 + 5. subscriber_present + mongo_loopback (one mongo round-trip covers both)
    try:
        client = _mongo_client(cfg)
        coll = client[cfg.mongo_db]["subscribers"]
        doc = coll.find_one({"imsi": _SEEDED_IMSI})
        checks["mongo_loopback"] = True
        if doc is not None:
            checks["subscriber_present"] = True
        else:
            failures.append(
                f"subscriber_present: IMSI {_SEEDED_IMSI} not found in subscribers collection"
            )
    except Exception as exc:  # noqa: BLE001
        failures.append(f"mongo_loopback: {exc}")
        failures.append(f"subscriber_present: skipped (mongo_loopback failed)")

    ready = all(checks.values())
    return {"ready": ready, "checks": checks, "failures": failures}


def restart_nf(cfg: LiveCoreConfig, nf: str) -> dict[str, Any]:
    """Restart a named compose service.

    Runs: docker compose -p <project> restart <nf>

    Returns:
        {"ok": True, "nf": nf, "restarted_at_s": float}

    Raises:
        LiveCoreError -- on non-zero exit, timeout, or any subprocess error.
    """
    argv = _compose_args(cfg, "restart", nf)
    try:
        rc, stdout, stderr = _run_subprocess(argv, timeout=cfg.timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise LiveCoreError(
            f"docker compose timed out restarting NF {nf!r}",
            cause=exc,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise LiveCoreError(
            f"docker compose restart failed for NF {nf!r}: {exc}",
            cause=exc,
        ) from exc

    if rc != 0:
        raise LiveCoreError(
            f"docker compose restart {nf!r} exited {rc}: {stderr.strip()}",
            cause=None,
        )

    return {"ok": True, "nf": nf, "restarted_at_s": time.time()}


def upsert_subscriber(cfg: LiveCoreConfig, registration: dict[str, Any]) -> dict[str, Any]:
    """Upsert one subscriber document.

    The registration dict has the runtime shape (ue_id, supi, plmn, dnn, sst,
    sd, auth_profile_id, optional security{}). It is transcribed to a
    subscriber_document() CSV-row dict and then upserted into mongo.

    Returns:
        {"ok": True, "imsi": str, "matched": int, "modified": int, "upserted": bool}

    Raises:
        LiveCoreError -- on validation failure, missing seeder, or mongo error.
    """
    row = _to_subscriber_csv_row(registration)
    imsi = row["imsi"]

    try:
        doc = _load_subscriber_document(row)
    except LiveCoreError:
        raise
    except ValueError as exc:
        raise LiveCoreError(
            f"subscriber validation failed for imsi={imsi!r}: {exc}",
            cause=exc,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise LiveCoreError(
            f"failed to build subscriber document for imsi={imsi!r}: {exc}",
            cause=exc,
        ) from exc

    try:
        client = _mongo_client(cfg)
        coll = client[cfg.mongo_db]["subscribers"]
        result = coll.replace_one({"imsi": imsi}, doc, upsert=True)
    except LiveCoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LiveCoreError(
            f"mongo upsert failed for imsi={imsi!r}: {exc}",
            cause=exc,
        ) from exc

    return {
        "ok": True,
        "imsi": imsi,
        "matched": result.matched_count,
        "modified": result.modified_count,
        "upserted": result.upserted_id is not None,
    }


def read_subscriber(cfg: LiveCoreConfig, imsi: str) -> dict[str, Any] | None:
    """Find one subscriber by IMSI.

    Returns the bson document with _id stripped, or None if not found.

    Raises:
        LiveCoreError -- on any mongo error (connection failure, timeout, etc.).
                         Design choice: explicit lookup callers want a clear
                         failure; read_runtime catches this and populates errors.
    """
    try:
        client = _mongo_client(cfg)
        coll = client[cfg.mongo_db]["subscribers"]
        doc = coll.find_one({"imsi": imsi})
    except LiveCoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LiveCoreError(
            f"mongo find_one failed for imsi={imsi!r}: {exc}",
            cause=exc,
        ) from exc

    if doc is None:
        return None
    doc.pop("_id", None)
    return dict(doc)


def read_runtime(cfg: LiveCoreConfig) -> dict[str, Any]:
    """Build a core_runtime payload matching the simulated schema.

    Schema
    ------
    running            bool
    available_nfs      list[str]
    nf_status          dict[str, str]  -- service → state string
    restart_counts     {}              -- always empty; dispatcher tracks this
    last_restarted_nf  None            -- always None; dispatcher tracks this
    ue_registration    dict            -- {status, ue_id, ...}
    errors             list[str]       -- populated on partial failure

    This function never raises. Partial failures are recorded in "errors"
    alongside whatever could be collected.
    """
    errors: list[str] = []
    available_nfs: list[str] = []
    nf_status: dict[str, str] = {}
    running = False

    # --- compose ps --services → available_nfs ---
    try:
        rc, stdout, stderr = _run_subprocess(
            _compose_args(cfg, "ps", "--services"),
            timeout=cfg.timeout_s,
        )
        if rc == 0:
            available_nfs = [line.strip() for line in stdout.splitlines() if line.strip()]
            running = bool(available_nfs)
        else:
            errors.append(f"compose ps --services exited {rc}: {stderr.strip()[:120]}")
    except subprocess.TimeoutExpired:
        errors.append("compose ps --services timed out")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"compose ps --services failed: {exc}")

    # --- compose ps --format json → nf_status ---
    try:
        rc, stdout, stderr = _run_subprocess(
            _compose_args(cfg, "ps", "--format", "json"),
            timeout=cfg.timeout_s,
        )
        if rc == 0 and stdout.strip():
            rows = json.loads(stdout)
            if isinstance(rows, list):
                for row in rows:
                    svc = row.get("Service") or row.get("Name") or ""
                    state = row.get("State") or row.get("Status") or "unknown"
                    if svc:
                        nf_status[svc] = state
            # docker compose ps --format json may return one object per line (older versions)
            # try line-by-line fallback if top-level parse is an empty list
    except subprocess.TimeoutExpired:
        errors.append("compose ps --format json timed out")
    except json.JSONDecodeError as exc:
        # Older docker compose versions emit one JSON object per line, not an array.
        # Try line-by-line parse.
        try:
            for line in stdout.splitlines():
                line = line.strip()
                if line:
                    row = json.loads(line)
                    svc = row.get("Service") or row.get("Name") or ""
                    state = row.get("State") or row.get("Status") or "unknown"
                    if svc:
                        nf_status[svc] = state
        except Exception:  # noqa: BLE001
            errors.append(f"compose ps --format json parse error: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"compose ps --format json failed: {exc}")

    # --- subscriber lookup for UE registration ---
    ue_registration: dict[str, Any] = {
        "ue_id": "ue1",
        "status": "unknown",
        "imsi": _SEEDED_IMSI,
    }
    try:
        doc = read_subscriber(cfg, _SEEDED_IMSI)
        if doc is not None:
            ue_registration["status"] = "registered"
            ue_registration["subscriber"] = doc
        else:
            ue_registration["status"] = "unknown"
    except LiveCoreError as exc:
        errors.append(f"ue_registration lookup failed: {exc.safe_message}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ue_registration lookup failed: {exc}")

    return {
        "running": running,
        "available_nfs": available_nfs,
        "nf_status": nf_status,
        "restart_counts": {},
        "last_restarted_nf": None,
        "ue_registration": ue_registration,
        "errors": errors,
    }


def apply_tc_netem(
    cfg: LiveCoreConfig,
    nf: str,
    *,
    delay_ms: float = 0,
    jitter_ms: float = 0,
    loss_rate: float = 0.0,
    dev: str = "eth0",
) -> dict[str, Any]:
    """Apply tc netem impairment inside a compose service container.

    Runs:
        docker compose exec -T <nf> tc qdisc replace dev <dev> root netem \\
            delay <delay>ms <jitter>ms loss <loss>%

    Returns:
        {"ok": True, "nf": nf, "delay_ms": float, "loss_pct": float,
         "qdisc_after": str}

    Raises:
        LiveCoreError -- on non-zero exit or timeout.

    loss_rate is 0.0–1.0; it is converted to a percentage string.
    """
    loss_pct = round(loss_rate * 100, 4)
    tc_argv = _compose_args(
        cfg,
        "exec", "-T", nf,
        "tc", "qdisc", "replace", "dev", dev, "root", "netem",
        "delay", f"{delay_ms}ms", f"{jitter_ms}ms",
        "loss", f"{loss_pct}%",
    )

    try:
        rc, stdout, stderr = _run_subprocess(tc_argv, timeout=cfg.timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise LiveCoreError(
            f"docker compose exec tc timed out for NF {nf!r}",
            cause=exc,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise LiveCoreError(
            f"docker compose exec tc failed for NF {nf!r}: {exc}",
            cause=exc,
        ) from exc

    if rc != 0:
        raise LiveCoreError(
            f"tc qdisc replace failed for NF {nf!r} (rc={rc}): {stderr.strip()}",
            cause=None,
        )

    # Read back the qdisc so the caller can verify
    show_argv = _compose_args(cfg, "exec", "-T", nf, "tc", "qdisc", "show")
    try:
        _rc2, show_stdout, _se2 = _run_subprocess(show_argv, timeout=cfg.timeout_s)
        qdisc_after = show_stdout
    except Exception:  # noqa: BLE001
        qdisc_after = ""

    return {
        "ok": True,
        "nf": nf,
        "delay_ms": delay_ms,
        "loss_pct": loss_pct,
        "qdisc_after": qdisc_after,
    }
