"""Unit tests for benchmark_api.live_e2.

All subprocess I/O is mocked — no docker or FlexRIC container required.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch, call
from typing import Any

from benchmark_api.live_e2 import (
    LiveE2Config,
    LiveE2Error,
    _run_subprocess,
    readiness,
    read_kpm,
    latest_kpm_measurements,
    dispatch_rc_du_prb_policy,
    dispatch_ccc_prb_policy,
)

_DEFAULT_CFG = LiveE2Config()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_proc(stdout: str = "", stderr: str = "", rc: int = 0) -> tuple[int, str, str]:
    return (rc, stdout, stderr)


def _fail_proc(stdout: str = "", stderr: str = "error detail", rc: int = 1) -> tuple[int, str, str]:
    return (rc, stdout, stderr)


def _make_kpm_record(
    name: str = "RRU.PrbAvailDl",
    value: int = 106,
    kpm_version: str = "E2SM-KPM-R003-v05.00",
) -> dict[str, Any]:
    return {
        "decoded_by": "ocudu-generated-asn1-cpp",
        "kpm_version": kpm_version,
        "format": "ind_msg_format1",
        "measurements": [
            {"name": name, "type": "integer", "value": value},
        ],
    }


def _jsonl_lines(*records: dict[str, Any]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


# ---------------------------------------------------------------------------
# 1. _run_subprocess tests
# ---------------------------------------------------------------------------

class RunSubprocessTests(unittest.TestCase):
    """_run_subprocess wraps subprocess.run and maps errors to LiveE2Error."""

    @patch("subprocess.run")
    def test_happy_path_returns_triple(self, mock_run):
        """Success: returns (returncode, stdout, stderr) tuple."""
        import subprocess
        mock_proc = unittest.mock.MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "hello\n"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc
        rc, out, err = _run_subprocess(["echo", "hello"], timeout=5.0)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "hello\n")
        self.assertEqual(err, "")

    @patch("subprocess.run")
    def test_timeout_raises_live_e2_error(self, mock_run):
        """TimeoutExpired → LiveE2Error with 'timed out' in message."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker"], timeout=5.0)
        with self.assertRaises(LiveE2Error) as ctx:
            _run_subprocess(["docker", "exec", "flexric-ric", "tail", "-n", "10"], timeout=5.0)
        self.assertIn("timed out", ctx.exception.safe_message)

    @patch("subprocess.run")
    def test_oserror_raises_live_e2_error(self, mock_run):
        """OSError (e.g. docker not found) → LiveE2Error."""
        mock_run.side_effect = OSError("No such file or directory: 'docker'")
        with self.assertRaises(LiveE2Error) as ctx:
            _run_subprocess(["docker", "version"], timeout=5.0)
        self.assertIn("subprocess failed", ctx.exception.safe_message)


# ---------------------------------------------------------------------------
# 2. readiness tests
# ---------------------------------------------------------------------------

class ReadinessAllGreenTests(unittest.TestCase):
    """readiness returns ready=True when container is up and JSONL has records."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_ready_true_all_checks_pass(self, mock_run):
        """All checks pass → ready=True, failures empty."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (0, "true\n", "")
            if "wc" in argv:
                return (0, "5 /var/log/flexric/kpm.jsonl\n", "")
            return (0, "", "")
        mock_run.side_effect = _side
        result = readiness(_DEFAULT_CFG)
        self.assertTrue(result["ready"])
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["checks"]["record_count"], 5)
        self.assertTrue(result["checks"]["jsonl_has_records"])

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_record_count_populated(self, mock_run):
        """record_count is set from wc -l output."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (0, "true\n", "")
            if "wc" in argv:
                return (0, "42 /var/log/flexric/kpm.jsonl\n", "")
            return (0, "", "")
        mock_run.side_effect = _side
        result = readiness(_DEFAULT_CFG)
        self.assertEqual(result["checks"]["record_count"], 42)


class ReadinessContainerNotRunningTests(unittest.TestCase):
    """readiness returns ready=False when docker inspect shows not running."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_container_not_running_ready_false(self, mock_run):
        """docker inspect returns 'false' → container_running=False, ready=False."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (0, "false\n", "")
            if "wc" in argv:
                return (1, "", "Error: No such container")
            return (0, "", "")
        mock_run.side_effect = _side
        result = readiness(_DEFAULT_CFG)
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["container_running"])
        self.assertTrue(len(result["failures"]) > 0)
        failure_str = " ".join(result["failures"])
        self.assertIn("container_running", failure_str)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_inspect_nonzero_exit_marks_container_not_running(self, mock_run):
        """docker inspect exits non-zero → container_running=False."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (1, "", "Error: No such object: flexric-ric")
            return (0, "", "")
        mock_run.side_effect = _side
        result = readiness(_DEFAULT_CFG)
        self.assertFalse(result["checks"]["container_running"])
        self.assertFalse(result["ready"])


class ReadinessJsonlMissingTests(unittest.TestCase):
    """readiness returns ready=False when JSONL file doesn't exist."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_wc_nonzero_jsonl_missing(self, mock_run):
        """wc -l fails → jsonl_exists=False, ready=False."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (0, "true\n", "")
            if "wc" in argv:
                return (1, "", "No such file or directory")
            return (0, "", "")
        mock_run.side_effect = _side
        result = readiness(_DEFAULT_CFG)
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["jsonl_exists"])
        failure_str = " ".join(result["failures"])
        self.assertIn("jsonl_exists", failure_str)


class ReadinessJsonlEmptyTests(unittest.TestCase):
    """readiness returns ready=False when JSONL has 0 records."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_zero_records_jsonl_has_records_false(self, mock_run):
        """wc -l returns 0 → jsonl_exists=True but jsonl_has_records=False."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (0, "true\n", "")
            if "wc" in argv:
                return (0, "0 /var/log/flexric/kpm.jsonl\n", "")
            return (0, "", "")
        mock_run.side_effect = _side
        result = readiness(_DEFAULT_CFG)
        self.assertFalse(result["ready"])
        self.assertTrue(result["checks"]["jsonl_exists"])
        self.assertFalse(result["checks"]["jsonl_has_records"])
        self.assertEqual(result["checks"]["record_count"], 0)


class ReadinessDoesNotRaiseTests(unittest.TestCase):
    """readiness never raises, even on unexpected exceptions."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_readiness_does_not_raise_on_all_failures(self, mock_run):
        """Even if all subprocess calls fail, readiness returns a dict, not an exception."""
        mock_run.side_effect = LiveE2Error("docker not found")
        result = readiness(_DEFAULT_CFG)
        self.assertIsInstance(result, dict)
        self.assertIn("ready", result)
        self.assertFalse(result["ready"])
        self.assertIn("checks", result)
        self.assertIn("failures", result)


# ---------------------------------------------------------------------------
# 3. read_kpm tests
# ---------------------------------------------------------------------------

class ReadKpmHappyPathTests(unittest.TestCase):
    """read_kpm returns parsed KPM records."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_three_records_returned(self, mock_run):
        """3 valid JSONL lines → list of 3 dicts."""
        r1 = _make_kpm_record("RRU.PrbAvailDl", 106)
        r2 = _make_kpm_record("RRU.PrbUsedDl", 12)
        r3 = _make_kpm_record("RRU.PrbTotDl", 0)
        mock_run.return_value = (0, _jsonl_lines(r1, r2, r3), "")
        records = read_kpm(_DEFAULT_CFG, count=5)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["measurements"][0]["name"], "RRU.PrbAvailDl")

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_fewer_records_than_count_returns_all(self, mock_run):
        """count=10 requested but only 3 in file → 3 returned."""
        r1 = _make_kpm_record()
        r2 = _make_kpm_record()
        r3 = _make_kpm_record()
        mock_run.return_value = (0, _jsonl_lines(r1, r2, r3), "")
        records = read_kpm(_DEFAULT_CFG, count=10)
        self.assertEqual(len(records), 3)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_malformed_line_is_skipped(self, mock_run):
        """One malformed JSON line is silently skipped; others are returned."""
        r1 = _make_kpm_record("RRU.PrbAvailDl", 106)
        r2 = _make_kpm_record("RRU.PrbUsedDl", 12)
        stdout = json.dumps(r1) + "\n" + "NOT VALID JSON\n" + json.dumps(r2) + "\n"
        mock_run.return_value = (0, stdout, "")
        records = read_kpm(_DEFAULT_CFG, count=5)
        self.assertEqual(len(records), 2)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_empty_file_returns_empty_list(self, mock_run):
        """If tail returns empty output, read_kpm returns []."""
        mock_run.return_value = (0, "", "")
        records = read_kpm(_DEFAULT_CFG, count=10)
        self.assertEqual(records, [])


class ReadKpmDockerExecFailsTests(unittest.TestCase):
    """read_kpm raises LiveE2Error when docker exec fails."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_nonzero_exit_raises_live_e2_error(self, mock_run):
        """docker exec tail exits non-zero → LiveE2Error."""
        mock_run.return_value = (1, "", "Error: No such container: flexric-ric")
        with self.assertRaises(LiveE2Error) as ctx:
            read_kpm(_DEFAULT_CFG, count=5)
        self.assertIn("tail failed", ctx.exception.safe_message)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_subprocess_live_e2_error_propagates(self, mock_run):
        """LiveE2Error from _run_subprocess propagates out of read_kpm."""
        mock_run.side_effect = LiveE2Error("subprocess timed out")
        with self.assertRaises(LiveE2Error):
            read_kpm(_DEFAULT_CFG, count=5)


# ---------------------------------------------------------------------------
# 4. latest_kpm_measurements tests
# ---------------------------------------------------------------------------

class LatestKpmMeasurementsTests(unittest.TestCase):
    """latest_kpm_measurements returns flattened {name: value} from last record."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_returns_flattened_measurements(self, mock_run):
        """3 records: last record's measurements are returned as flat dict."""
        r1 = {
            "decoded_by": "ocudu-generated-asn1-cpp",
            "kpm_version": "E2SM-KPM-R003-v05.00",
            "format": "ind_msg_format1",
            "measurements": [
                {"name": "RRU.PrbAvailDl", "type": "integer", "value": 100},
                {"name": "RRU.PrbUsedDl", "type": "integer", "value": 10},
                {"name": "RRU.PrbTotDl", "type": "integer", "value": 5},
            ],
        }
        # read_kpm(count=1) tails 1 line — we simulate that directly
        mock_run.return_value = (0, json.dumps(r1) + "\n", "")
        result = latest_kpm_measurements(_DEFAULT_CFG)
        self.assertIsNotNone(result)
        self.assertEqual(result["RRU.PrbAvailDl"], 100)
        self.assertEqual(result["RRU.PrbUsedDl"], 10)
        self.assertEqual(result["RRU.PrbTotDl"], 5)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_empty_file_returns_none(self, mock_run):
        """If read_kpm returns [], latest_kpm_measurements returns None."""
        mock_run.return_value = (0, "", "")
        result = latest_kpm_measurements(_DEFAULT_CFG)
        self.assertIsNone(result)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_docker_exec_fails_raises_live_e2_error(self, mock_run):
        """LiveE2Error from read_kpm propagates out of latest_kpm_measurements."""
        mock_run.return_value = (1, "", "Error: No such container: flexric-ric")
        with self.assertRaises(LiveE2Error):
            latest_kpm_measurements(_DEFAULT_CFG)


# ---------------------------------------------------------------------------
# 5. Command construction tests
# ---------------------------------------------------------------------------

class CommandConstructionTests(unittest.TestCase):
    """readiness and read_kpm invoke the correct docker commands."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_readiness_invokes_docker_inspect_and_wc(self, mock_run):
        """readiness calls docker inspect ... and docker exec ... wc -l ..."""
        def _side(argv, **kw):
            if "inspect" in argv:
                return (0, "true\n", "")
            if "wc" in argv:
                return (0, "3 /var/log/flexric/kpm.jsonl\n", "")
            return (0, "", "")
        mock_run.side_effect = _side
        readiness(LiveE2Config(container_name="flexric-ric", jsonl_path="/var/log/flexric/kpm.jsonl"))
        calls = [c[0][0] for c in mock_run.call_args_list]
        # First call: docker inspect
        self.assertIn("inspect", calls[0])
        self.assertIn("flexric-ric", calls[0])
        # Second call: docker exec ... wc -l
        wc_call = calls[1]
        self.assertIn("wc", wc_call)
        self.assertIn("-l", wc_call)
        self.assertIn("/var/log/flexric/kpm.jsonl", wc_call)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_read_kpm_invokes_docker_exec_tail(self, mock_run):
        """read_kpm calls docker exec <container> tail -n <count> <path>."""
        mock_run.return_value = (0, "", "")
        read_kpm(LiveE2Config(container_name="flexric-ric",
                              jsonl_path="/var/log/flexric/kpm.jsonl"), count=7)
        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[0], "docker")
        self.assertEqual(argv[1], "exec")
        self.assertIn("flexric-ric", argv)
        self.assertIn("tail", argv)
        self.assertIn("-n", argv)
        tail_n_idx = argv.index("-n")
        self.assertEqual(argv[tail_n_idx + 1], "7")
        self.assertIn("/var/log/flexric/kpm.jsonl", argv)


# ---------------------------------------------------------------------------
# 6. LiveE2DispatchRcDuTests
# ---------------------------------------------------------------------------

_SUCCESS_JSON = json.dumps({
    "accepted": True,
    "action_type": "SET_PRB_POLICY_RATIO_RC_DU",
    "ran_function_id": 3,
    "control_name": "slice-level PRB quota",
    "control_style": 2,
    "control_action": 6,
    "request": {},
    "outcome": {
        "acknowledged": True,
        "evidence": "OCUDU E2SM-RC control acknowledged",
    },
})

_FAILURE_JSON = json.dumps({
    "action_type": "SET_PRB_POLICY_RATIO_RC_DU",
    "accepted": False,
    "error": "no E2 node found with the given du_ue_id",
})


class LiveE2DispatchRcDuTests(unittest.TestCase):
    """dispatch_rc_du_prb_policy: happy path, failure path, error handling, argv."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_happy_path_returns_accepted_true(self, mock_run):
        """Success JSON → result["accepted"] is True."""
        mock_run.return_value = (0, _SUCCESS_JSON + "\n", "")
        result = dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["action_type"], "SET_PRB_POLICY_RATIO_RC_DU")
        self.assertEqual(result["outcome"]["evidence"], "OCUDU E2SM-RC control acknowledged")

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_failure_path_returns_accepted_false(self, mock_run):
        """Failure JSON → result["accepted"] is False, result["error"] present."""
        mock_run.return_value = (4, _FAILURE_JSON + "\n", "")
        result = dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=999)
        self.assertFalse(result["accepted"])
        self.assertIn("error", result)
        self.assertIn("du_ue_id", result["error"])

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_subprocess_timeout_propagates(self, mock_run):
        """LiveE2Error from _run_subprocess (timeout) propagates out."""
        mock_run.side_effect = LiveE2Error("subprocess timed out: docker exec flexric-ric ...")
        with self.assertRaises(LiveE2Error) as ctx:
            dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        self.assertIn("timed out", ctx.exception.safe_message)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_no_json_line_in_output_raises(self, mock_run):
        """No line starting with '{' → LiveE2Error with 'no JSON line'."""
        mock_run.return_value = (1, "Error response from daemon: No such container", "")
        with self.assertRaises(LiveE2Error) as ctx:
            dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        self.assertIn("no JSON line", ctx.exception.safe_message)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_garbage_output_no_json_line(self, mock_run):
        """Purely non-JSON output → LiveE2Error('no JSON line')."""
        mock_run.return_value = (0, "garbage output\nmore garbage", "")
        with self.assertRaises(LiveE2Error) as ctx:
            dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        self.assertIn("no JSON line", ctx.exception.safe_message)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_malformed_json_line_raises(self, mock_run):
        """Line starts with '{' but is not valid JSON → LiveE2Error('unparseable JSON')."""
        mock_run.return_value = (0, "{not_valid_json\n", "")
        with self.assertRaises(LiveE2Error) as ctx:
            dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        self.assertIn("unparseable JSON", ctx.exception.safe_message)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_argv_construction_base(self, mock_run):
        """argv includes docker exec, container, xapp path, --json, --conf, --du-ue-id, --plmn, --sst."""
        mock_run.return_value = (0, _SUCCESS_JSON, "")
        cfg = LiveE2Config(
            container_name="flexric-ric",
            rc_du_xapp_path="/opt/flexric/build/examples/xApp/c/control/ocudu-rc-du-prb-control",
            rc_du_xapp_conf="/etc/xapp/xapp_oran_sm.conf",
        )
        dispatch_rc_du_prb_policy(cfg, du_ue_id=7, plmn="00101", sst=1)
        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[0], "docker")
        self.assertEqual(argv[1], "exec")
        self.assertIn("flexric-ric", argv)
        self.assertIn("/opt/flexric/build/examples/xApp/c/control/ocudu-rc-du-prb-control", argv)
        self.assertIn("--json", argv)
        self.assertIn("--conf", argv)
        self.assertIn("/etc/xapp/xapp_oran_sm.conf", argv)
        self.assertIn("--du-ue-id", argv)
        du_idx = argv.index("--du-ue-id")
        self.assertEqual(argv[du_idx + 1], "7")
        self.assertIn("--plmn", argv)
        self.assertIn("--sst", argv)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_optional_sd_included_when_provided(self, mock_run):
        """When sd is provided, argv contains --sd <value>."""
        mock_run.return_value = (0, _SUCCESS_JSON, "")
        dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0, sd=255)
        argv = mock_run.call_args[0][0]
        self.assertIn("--sd", argv)
        sd_idx = argv.index("--sd")
        self.assertEqual(argv[sd_idx + 1], "255")

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_optional_min_max_included_when_provided(self, mock_run):
        """When min/max provided, argv contains --min-prb-policy-ratio and --max-prb-policy-ratio."""
        mock_run.return_value = (0, _SUCCESS_JSON, "")
        dispatch_rc_du_prb_policy(
            _DEFAULT_CFG, du_ue_id=0,
            min_prb_policy_ratio=10,
            max_prb_policy_ratio=90,
        )
        argv = mock_run.call_args[0][0]
        self.assertIn("--min-prb-policy-ratio", argv)
        self.assertIn("--max-prb-policy-ratio", argv)
        min_idx = argv.index("--min-prb-policy-ratio")
        max_idx = argv.index("--max-prb-policy-ratio")
        self.assertEqual(argv[min_idx + 1], "10")
        self.assertEqual(argv[max_idx + 1], "90")

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_optional_flags_excluded_when_none(self, mock_run):
        """When sd/min/max are None (defaults), those flags are absent from argv."""
        mock_run.return_value = (0, _SUCCESS_JSON, "")
        dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        argv = mock_run.call_args[0][0]
        self.assertNotIn("--sd", argv)
        self.assertNotIn("--min-prb-policy-ratio", argv)
        self.assertNotIn("--max-prb-policy-ratio", argv)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_leading_noise_json_at_end_parsed(self, mock_run):
        """Stdout with FlexRIC startup noise before JSON → last JSON line is parsed."""
        noise = (
            "FlexRIC initializing...\n"
            "Connecting to RIC at 127.0.0.1:36421\n"
            "E2 setup complete\n"
        )
        mock_run.return_value = (0, noise + _SUCCESS_JSON + "\n", "")
        result = dispatch_rc_du_prb_policy(_DEFAULT_CFG, du_ue_id=0)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["action_type"], "SET_PRB_POLICY_RATIO_RC_DU")


# ---------------------------------------------------------------------------
# 7. LiveE2DispatchCccTests
# ---------------------------------------------------------------------------

_CCC_SUCCESS_JSON = json.dumps({
    "accepted": True,
    "action_type": "SET_PRB_POLICY_RATIO_CCC",
    "ran_function_id": 4,
    "control_name": "O-RRMPolicyRatio",
    "control_style": 2,
    "control_action": 6,
    "request": {"plmn": "00101", "sst": 1, "sd": "FFFFFF", "min_prb_policy_ratio": 30,
                "max_prb_policy_ratio": 70, "dedicated_ratio": 50},
    "outcome": {
        "acknowledged": True,
        "evidence": "FlexRIC E2SM-CCC control acknowledged",
    },
})

_CCC_FAILURE_JSON = json.dumps({
    "action_type": "SET_PRB_POLICY_RATIO_CCC",
    "accepted": False,
    "error": "missing required PRB min/max policy ratio",
})


class LiveE2DispatchCccTests(unittest.TestCase):
    """dispatch_ccc_prb_policy: happy path, failure path, argv (no du_ue_id, has dedicated)."""

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_happy_path_returns_accepted_true(self, mock_run):
        mock_run.return_value = (0, _CCC_SUCCESS_JSON + "\n", "")
        result = dispatch_ccc_prb_policy(
            _DEFAULT_CFG, min_prb_policy_ratio=30, max_prb_policy_ratio=70, dedicated_ratio=50,
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["action_type"], "SET_PRB_POLICY_RATIO_CCC")
        self.assertEqual(result["ran_function_id"], 4)
        self.assertEqual(result["outcome"]["evidence"], "FlexRIC E2SM-CCC control acknowledged")

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_failure_path_returns_accepted_false(self, mock_run):
        mock_run.return_value = (3, _CCC_FAILURE_JSON + "\n", "")
        result = dispatch_ccc_prb_policy(_DEFAULT_CFG)
        self.assertFalse(result["accepted"])
        self.assertIn("missing", result["error"])

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_argv_construction_no_du_ue_id(self, mock_run):
        """CCC argv must NOT contain --du-ue-id (cell-level control)."""
        mock_run.return_value = (0, _CCC_SUCCESS_JSON, "")
        dispatch_ccc_prb_policy(
            _DEFAULT_CFG, plmn="00101", sst=1, sd=0xFFFFFF,
            min_prb_policy_ratio=30, max_prb_policy_ratio=70, dedicated_ratio=50,
        )
        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[0], "docker")
        self.assertEqual(argv[1], "exec")
        self.assertIn("flexric-ric", argv)
        self.assertIn("/opt/flexric/build/examples/xApp/c/control/ocudu-ccc-prb-control", argv)
        self.assertIn("--json", argv)
        self.assertNotIn("--du-ue-id", argv)
        for flag in ("--plmn", "--sst", "--sd",
                     "--min-prb-policy-ratio", "--max-prb-policy-ratio", "--dedicated-ratio"):
            self.assertIn(flag, argv)
        ded_idx = argv.index("--dedicated-ratio")
        self.assertEqual(argv[ded_idx + 1], "50")

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_optional_flags_excluded_when_none(self, mock_run):
        """All optional flags absent if not provided."""
        mock_run.return_value = (0, _CCC_SUCCESS_JSON, "")
        dispatch_ccc_prb_policy(_DEFAULT_CFG)
        argv = mock_run.call_args[0][0]
        for flag in ("--sd", "--min-prb-policy-ratio", "--max-prb-policy-ratio", "--dedicated-ratio"):
            self.assertNotIn(flag, argv)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_no_json_line_raises_with_ccc_tool_name(self, mock_run):
        mock_run.return_value = (1, "Error response from daemon\n", "")
        with self.assertRaises(LiveE2Error) as ctx:
            dispatch_ccc_prb_policy(_DEFAULT_CFG)
        self.assertIn("no JSON line", ctx.exception.safe_message)
        self.assertIn("ocudu-ccc-prb-control", ctx.exception.safe_message)

    @patch("benchmark_api.live_e2._run_subprocess")
    def test_malformed_json_raises(self, mock_run):
        mock_run.return_value = (0, "{garbage\n", "")
        with self.assertRaises(LiveE2Error) as ctx:
            dispatch_ccc_prb_policy(_DEFAULT_CFG)
        self.assertIn("unparseable JSON", ctx.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
