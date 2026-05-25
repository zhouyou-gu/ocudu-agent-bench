"""Unit tests for benchmark_api.live_core.

All subprocess and pymongo I/O is mocked — no docker or live mongo required.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch, call
from typing import Any

from benchmark_api.live_core import (
    LiveCoreConfig,
    LiveCoreError,
    _compose_args,
    _run_subprocess,
    _to_subscriber_csv_row,
    apply_tc_netem,
    read_runtime,
    read_subscriber,
    readiness,
    restart_nf,
    upsert_subscriber,
)

# Stub for pymongo.errors.ServerSelectionTimeoutError so the test file is
# importable even when pymongo is not installed on the host. The live_core
# module only lazy-imports pymongo at call time; tests mock _mongo_client so
# pymongo itself is never invoked. We only need an exception class to use as a
# side_effect — any Exception subclass works.
try:
    from pymongo.errors import ServerSelectionTimeoutError as _MongoTimeoutError
except ModuleNotFoundError:
    class _MongoTimeoutError(Exception):  # type: ignore[no-redef]
        """Stand-in for pymongo.errors.ServerSelectionTimeoutError."""

_DEFAULT_CFG = LiveCoreConfig()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_proc(stdout: str = "", stderr: str = "", rc: int = 0):
    """Return a (rc, stdout, stderr) triple simulating a successful subprocess."""
    return (rc, stdout, stderr)


def _fail_proc(stdout: str = "", stderr: str = "error detail", rc: int = 1):
    return (rc, stdout, stderr)


# ---------------------------------------------------------------------------
# 1. Subprocess composition tests
# ---------------------------------------------------------------------------


class ComposeArgsTests(unittest.TestCase):
    """_compose_args constructs the docker compose command correctly."""

    def test_no_compose_file_uses_p_flag(self):
        """Without compose_file, only -p <project> is included."""
        cfg = LiveCoreConfig(compose_project="myproject", compose_file=None)
        args = _compose_args(cfg)
        self.assertEqual(args, ["docker", "compose", "-p", "myproject"])

    def test_no_compose_file_extra_args(self):
        cfg = LiveCoreConfig(compose_project="myproject", compose_file=None)
        args = _compose_args(cfg, "ps", "--services")
        self.assertEqual(args, ["docker", "compose", "-p", "myproject", "ps", "--services"])

    def test_with_compose_file_includes_f_flag(self):
        """When compose_file is set, both -p and -f are included, -f before extra args."""
        cfg = LiveCoreConfig(compose_project="myproject", compose_file="/path/to/compose.yml")
        args = _compose_args(cfg, "up")
        self.assertEqual(
            args,
            ["docker", "compose", "-p", "myproject", "-f", "/path/to/compose.yml", "up"],
        )

    def test_with_compose_file_no_extra_args(self):
        cfg = LiveCoreConfig(compose_project="myproject", compose_file="/some/file.yml")
        args = _compose_args(cfg)
        self.assertEqual(
            args,
            ["docker", "compose", "-p", "myproject", "-f", "/some/file.yml"],
        )

    def test_default_config_project_name(self):
        args = _compose_args(_DEFAULT_CFG)
        self.assertIn("open5gs", args)
        self.assertIn("-p", args)
        self.assertNotIn("-f", args)


class RestartNfSubprocessTests(unittest.TestCase):
    """restart_nf invokes the correct docker compose command."""

    @patch("benchmark_api.live_core._run_subprocess")
    def test_restart_nf_correct_argv(self, mock_run):
        mock_run.return_value = _ok_proc()
        result = restart_nf(_DEFAULT_CFG, "amf")
        mock_run.assert_called_once()
        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[:4], ["docker", "compose", "-p", "open5gs"])
        self.assertIn("restart", argv)
        self.assertIn("amf", argv)

    @patch("benchmark_api.live_core._run_subprocess")
    def test_restart_nf_passes_timeout(self, mock_run):
        mock_run.return_value = _ok_proc()
        cfg = LiveCoreConfig(timeout_s=15.0)
        restart_nf(cfg, "smf")
        _, kwargs = mock_run.call_args
        self.assertAlmostEqual(kwargs["timeout"], 15.0)

    @patch("benchmark_api.live_core._run_subprocess")
    def test_restart_nf_returns_ok_shape(self, mock_run):
        mock_run.return_value = _ok_proc()
        result = restart_nf(_DEFAULT_CFG, "upf")
        self.assertTrue(result["ok"])
        self.assertEqual(result["nf"], "upf")
        self.assertIn("restarted_at_s", result)


class ApplyTcNetemSubprocessTests(unittest.TestCase):
    """apply_tc_netem invokes tc qdisc replace with the right arguments."""

    @patch("benchmark_api.live_core._run_subprocess")
    def test_tc_netem_argv_contains_correct_params(self, mock_run):
        mock_run.return_value = _ok_proc(stdout="qdisc netem 8001: dev eth0 root ...")
        apply_tc_netem(_DEFAULT_CFG, "amf", delay_ms=5, jitter_ms=2, loss_rate=0.01, dev="eth0")
        # First call is tc qdisc replace, second is tc qdisc show
        replace_call_argv = mock_run.call_args_list[0][0][0]
        self.assertIn("tc", replace_call_argv)
        self.assertIn("netem", replace_call_argv)
        self.assertIn("delay", replace_call_argv)
        self.assertIn("5ms", replace_call_argv)
        self.assertIn("2ms", replace_call_argv)
        # loss is 0.01 * 100 = 1.0%
        loss_idx = replace_call_argv.index("loss")
        self.assertIn("1.0%", replace_call_argv[loss_idx + 1])

    @patch("benchmark_api.live_core._run_subprocess")
    def test_tc_netem_loss_rate_converted_to_percent(self, mock_run):
        mock_run.return_value = _ok_proc(stdout="qdisc netem ...")
        apply_tc_netem(_DEFAULT_CFG, "smf", delay_ms=10, loss_rate=0.05)
        replace_argv = mock_run.call_args_list[0][0][0]
        loss_idx = replace_argv.index("loss")
        # 0.05 -> 5.0%
        self.assertIn("5.0%", replace_argv[loss_idx + 1])

    @patch("benchmark_api.live_core._run_subprocess")
    def test_tc_netem_uses_exec_T_nf(self, mock_run):
        mock_run.return_value = _ok_proc(stdout="qdisc info")
        apply_tc_netem(_DEFAULT_CFG, "upf", delay_ms=0)
        replace_argv = mock_run.call_args_list[0][0][0]
        self.assertIn("exec", replace_argv)
        self.assertIn("-T", replace_argv)
        self.assertIn("upf", replace_argv)

    @patch("benchmark_api.live_core._run_subprocess")
    def test_tc_netem_returns_correct_shape(self, mock_run):
        mock_run.return_value = _ok_proc(stdout="qdisc netem 8001: root")
        result = apply_tc_netem(_DEFAULT_CFG, "amf", delay_ms=20, loss_rate=0.1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["nf"], "amf")
        self.assertEqual(result["delay_ms"], 20)
        self.assertAlmostEqual(result["loss_pct"], 10.0)
        self.assertIn("qdisc_after", result)


# ---------------------------------------------------------------------------
# 2. Error mapping tests
# ---------------------------------------------------------------------------


class RestartNfErrorTests(unittest.TestCase):
    """restart_nf raises LiveCoreError on failure."""

    @patch("benchmark_api.live_core._run_subprocess")
    def test_nonzero_exit_raises_live_core_error(self, mock_run):
        mock_run.return_value = _fail_proc(stderr="Error response from daemon: No such container")
        with self.assertRaises(LiveCoreError) as ctx:
            restart_nf(_DEFAULT_CFG, "amf")
        self.assertIn("Error response from daemon", ctx.exception.safe_message)

    @patch("benchmark_api.live_core._run_subprocess")
    def test_timeout_raises_live_core_error_with_timeout_message(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker"], timeout=10.0)
        with self.assertRaises(LiveCoreError) as ctx:
            restart_nf(_DEFAULT_CFG, "amf")
        self.assertIn("timed out", ctx.exception.safe_message.lower())

    @patch("benchmark_api.live_core._run_subprocess")
    def test_live_core_error_carries_cause(self, mock_run):
        exc = subprocess.TimeoutExpired(cmd=["docker"], timeout=5.0)
        mock_run.side_effect = exc
        with self.assertRaises(LiveCoreError) as ctx:
            restart_nf(_DEFAULT_CFG, "amf")
        self.assertIs(ctx.exception.cause, exc)


class ApplyTcNetemErrorTests(unittest.TestCase):
    """apply_tc_netem raises LiveCoreError on failure."""

    @patch("benchmark_api.live_core._run_subprocess")
    def test_nonzero_exit_raises_live_core_error(self, mock_run):
        mock_run.return_value = _fail_proc(rc=1, stderr="RTNETLINK answers: Operation not permitted")
        with self.assertRaises(LiveCoreError) as ctx:
            apply_tc_netem(_DEFAULT_CFG, "amf")
        self.assertIsInstance(ctx.exception, LiveCoreError)
        self.assertIn("RTNETLINK", ctx.exception.safe_message)

    @patch("benchmark_api.live_core._run_subprocess")
    def test_timeout_raises_live_core_error(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker"], timeout=10.0)
        with self.assertRaises(LiveCoreError):
            apply_tc_netem(_DEFAULT_CFG, "amf")


class UpsertSubscriberErrorTests(unittest.TestCase):
    """upsert_subscriber raises LiveCoreError for validation and mongo failures."""

    @patch("benchmark_api.live_core._load_subscriber_document")
    @patch("benchmark_api.live_core._mongo_client")
    def test_empty_imsi_raises_live_core_error(self, mock_mc, mock_load_doc):
        # subscriber_document raises ValueError for empty imsi; upsert_subscriber wraps it
        mock_load_doc.side_effect = ValueError("invalid imsi: ''")
        reg = {
            "ue_id": "ue1", "supi": "",  # empty supi → empty imsi
            "plmn": "00101", "dnn": "internet", "sst": 1, "sd": None,
            "auth_profile_id": "ue1_test_profile",
        }
        with self.assertRaises(LiveCoreError) as ctx:
            upsert_subscriber(_DEFAULT_CFG, reg)
        self.assertIn("imsi", ctx.exception.safe_message.lower())

    @patch("benchmark_api.live_core._load_subscriber_document")
    @patch("benchmark_api.live_core._mongo_client")
    def test_mongo_connection_failure_raises_live_core_error(self, mock_mc, mock_load_doc):
        mock_doc = {
            "imsi": "001010000000001",
            "security": {},
            "meta": {"auth_profile_id": "ue1_test_profile", "ue_id": "ue1"},
            "slice": [{"sst": 1}],
        }
        mock_load_doc.return_value = mock_doc
        mock_coll = MagicMock()
        mock_coll.replace_one.side_effect = _MongoTimeoutError("connection refused")
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        reg = {
            "ue_id": "ue1", "supi": "001010000000001",
            "plmn": "00101", "dnn": "internet", "sst": 1, "sd": None,
            "auth_profile_id": "ue1_test_profile",
        }
        with self.assertRaises(LiveCoreError):
            upsert_subscriber(_DEFAULT_CFG, reg)


class ReadSubscriberErrorTests(unittest.TestCase):
    """read_subscriber raises LiveCoreError on mongo failure."""

    @patch("benchmark_api.live_core._mongo_client")
    def test_mongo_failure_raises_live_core_error(self, mock_mc):
        mock_coll = MagicMock()
        mock_coll.find_one.side_effect = _MongoTimeoutError("timeout")
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        with self.assertRaises(LiveCoreError):
            read_subscriber(_DEFAULT_CFG, "001010000000001")


class ReadinessNoRaiseTests(unittest.TestCase):
    """readiness() must not raise even when all checks fail."""

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_readiness_does_not_raise_on_all_failures(self, mock_mc, mock_run):
        mock_run.return_value = _fail_proc(rc=1, stderr="compose error")
        mock_mc.side_effect = _MongoTimeoutError("no mongo")
        result = readiness(_DEFAULT_CFG)
        self.assertIsInstance(result, dict)
        self.assertIn("ready", result)
        self.assertFalse(result["ready"])

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_readiness_returns_five_checks(self, mock_mc, mock_run):
        mock_run.return_value = _fail_proc(rc=1)
        mock_mc.side_effect = Exception("fail")
        result = readiness(_DEFAULT_CFG)
        checks = result["checks"]
        expected_keys = {"compose_running", "amf_sctp_bound", "amf_nf_registered",
                         "subscriber_present", "mongo_loopback"}
        self.assertEqual(set(checks.keys()), expected_keys)

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_readiness_returns_failures_list(self, mock_mc, mock_run):
        mock_run.return_value = _fail_proc(rc=1, stderr="error")
        mock_mc.side_effect = Exception("fail")
        result = readiness(_DEFAULT_CFG)
        self.assertIn("failures", result)
        self.assertIsInstance(result["failures"], list)
        self.assertGreater(len(result["failures"]), 0)

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_readiness_failure_labels_include_check_name(self, mock_mc, mock_run):
        mock_run.return_value = _fail_proc(rc=1, stderr="no container")
        mock_mc.side_effect = Exception("fail")
        result = readiness(_DEFAULT_CFG)
        failure_str = " ".join(result["failures"])
        # at least one failure message should contain a recognisable check label
        self.assertTrue(
            any(k in failure_str for k in ("compose", "amf", "subscriber", "mongo")),
            f"No recognisable label in failures: {result['failures']}",
        )


class ReadinessAllPassTests(unittest.TestCase):
    """When all checks pass, readiness returns ready=True and empty failures."""

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_all_checks_pass_returns_ready_true(self, mock_mc, mock_run):
        # compose ps running
        # amf_sctp_bound: ss/netstat shows sctp
        # amf_nf_registered: compose logs shows NF registered
        # subscriber_present: mongo find returns doc
        # mongo_loopback: ping succeeds
        def _side(argv, **kw):
            cmd_str = " ".join(argv)
            if "ps" in cmd_str and "services" in cmd_str:
                return (0, "amf\nsmf\nupf\n", "")
            if "ps" in cmd_str and "format" in cmd_str:
                return (0, '[{"Service":"amf","State":"running"},{"Service":"smf","State":"running"}]', "")
            if "exec" in cmd_str and "ss" in cmd_str:
                return (0, "LISTEN 0 0 *:38412", "")
            if "logs" in cmd_str:
                return (0, "NF registered", "")
            # tc qdisc show
            return (0, "", "")

        mock_run.side_effect = _side

        mock_coll = MagicMock()
        mock_coll.find_one.return_value = {"imsi": "001010000000001"}
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        result = readiness(_DEFAULT_CFG)
        self.assertTrue(result["ready"], f"Expected ready=True, got failures: {result['failures']}")
        self.assertEqual(result["failures"], [])

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_compose_ps_failure_gives_compose_running_false(self, mock_mc, mock_run):
        def _side(argv, **kw):
            return (1, "", "no such project")
        mock_run.side_effect = _side
        mock_mc.side_effect = Exception("mongo down")
        result = readiness(_DEFAULT_CFG)
        self.assertFalse(result["checks"]["compose_running"])
        self.assertFalse(result["ready"])


# ---------------------------------------------------------------------------
# 3. CSV-row translation tests
# ---------------------------------------------------------------------------


class ToSubscriberCsvRowTests(unittest.TestCase):
    """_to_subscriber_csv_row maps runtime registration dict to seeder CSV row."""

    _DEFAULT_REG = {
        "ue_id": "ue1",
        "supi": "001010000000001",
        "plmn": "00101",
        "dnn": "internet",
        "sst": 1,
        "sd": None,
        "auth_profile_id": "ue1_test_profile",
    }

    def test_supi_maps_to_imsi(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["imsi"], "001010000000001")

    def test_ue_id_passthrough(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["ue_id"], "ue1")

    def test_sd_none_becomes_empty_string(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["sd"], "")

    def test_sd_int_becomes_string(self):
        reg = {**self._DEFAULT_REG, "sd": 42}
        row = _to_subscriber_csv_row(reg)
        self.assertEqual(row["sd"], "42")

    def test_sd_hex_string_passthrough(self):
        reg = {**self._DEFAULT_REG, "sd": "0x00002a"}
        row = _to_subscriber_csv_row(reg)
        self.assertEqual(row["sd"], "0x00002a")

    def test_default_k_matches_seeded_triplet(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["k"], "00112233445566778899aabbccddeeff")

    def test_default_opc_matches_seeded_triplet(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["opc"], "63bfa50ee6523365ff14c1f45f88737d")

    def test_default_amf_matches_seeded_triplet(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["amf"], "8000")

    def test_default_sqn_matches_seeded_triplet(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["sqn"], "000000000000")

    def test_security_override_takes_precedence(self):
        reg = {
            **self._DEFAULT_REG,
            "security": {
                "k": "aabbccddeeff00112233445566778899",
                "opc": "11223344556677889900aabbccddeeff",
                "amf": "9001",
                "sqn": "000000000001",
            },
        }
        row = _to_subscriber_csv_row(reg)
        self.assertEqual(row["k"], "aabbccddeeff00112233445566778899")
        self.assertEqual(row["opc"], "11223344556677889900aabbccddeeff")
        self.assertEqual(row["amf"], "9001")
        self.assertEqual(row["sqn"], "000000000001")

    def test_partial_security_override_keeps_defaults_for_missing_keys(self):
        reg = {**self._DEFAULT_REG, "security": {"k": "deadbeefdeadbeefdeadbeefdeadbeef"}}
        row = _to_subscriber_csv_row(reg)
        self.assertEqual(row["k"], "deadbeefdeadbeefdeadbeefdeadbeef")
        # opc, amf, sqn fall back to defaults
        self.assertEqual(row["opc"], "63bfa50ee6523365ff14c1f45f88737d")
        self.assertEqual(row["amf"], "8000")
        self.assertEqual(row["sqn"], "000000000000")

    def test_sst_coerced_to_string(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        self.assertEqual(row["sst"], "1")

    def test_all_required_seeder_keys_present(self):
        row = _to_subscriber_csv_row(self._DEFAULT_REG)
        required = {"imsi", "k", "opc", "amf", "sqn", "plmn", "dnn", "sst", "sd",
                    "auth_profile_id", "ue_id"}
        self.assertEqual(required, set(row.keys()))


# ---------------------------------------------------------------------------
# 4. read_runtime structure tests
# ---------------------------------------------------------------------------


class ReadRuntimeTests(unittest.TestCase):
    """read_runtime returns the documented core_runtime schema shape."""

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def _run_read_runtime(self, mock_mc, mock_run, *,
                          ps_services_stdout="amf\nsmf\nupf\n",
                          ps_format_stdout='[{"Service":"amf","State":"running"},{"Service":"smf","State":"running"},{"Service":"upf","State":"running"}]',
                          ps_rc=0,
                          subscriber_doc=None):
        def _side(argv, **kw):
            cmd_str = " ".join(argv)
            if "services" in cmd_str:
                return (ps_rc, ps_services_stdout, "")
            if "format" in cmd_str:
                return (ps_rc, ps_format_stdout, "")
            return (0, "", "")

        mock_run.side_effect = _side

        mock_coll = MagicMock()
        mock_coll.find_one.return_value = subscriber_doc
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        return read_runtime(_DEFAULT_CFG)

    def test_returns_top_level_keys(self):
        result = self._run_read_runtime()
        expected = {"running", "available_nfs", "nf_status", "restart_counts",
                    "last_restarted_nf", "ue_registration", "errors"}
        self.assertTrue(expected.issubset(set(result.keys())),
                        f"Missing keys: {expected - set(result.keys())}")

    def test_restart_counts_is_empty_dict(self):
        result = self._run_read_runtime()
        self.assertEqual(result["restart_counts"], {})

    def test_last_restarted_nf_is_none(self):
        result = self._run_read_runtime()
        self.assertIsNone(result["last_restarted_nf"])

    def test_available_nfs_from_compose_ps(self):
        result = self._run_read_runtime(ps_services_stdout="amf\nsmf\nupf\n")
        self.assertIn("amf", result["available_nfs"])
        self.assertIn("smf", result["available_nfs"])
        self.assertIn("upf", result["available_nfs"])

    def test_ue_registration_present_when_subscriber_found(self):
        sub_doc = {"imsi": "001010000000001", "_id": "some_id"}
        result = self._run_read_runtime(subscriber_doc=sub_doc)
        reg = result["ue_registration"]
        self.assertIsInstance(reg, dict)
        # When subscriber found, status should be "registered"
        self.assertEqual(reg.get("status"), "registered")

    def test_ue_registration_unknown_when_subscriber_not_found(self):
        result = self._run_read_runtime(subscriber_doc=None)
        reg = result["ue_registration"]
        self.assertEqual(reg.get("status"), "unknown")

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_compose_ps_failure_gives_empty_available_nfs(self, mock_mc, mock_run):
        mock_run.return_value = (1, "", "compose error")
        mock_coll = MagicMock()
        mock_coll.find_one.return_value = None
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client
        result = read_runtime(_DEFAULT_CFG)
        self.assertEqual(result["available_nfs"], [])
        self.assertTrue(len(result["errors"]) > 0)

    @patch("benchmark_api.live_core._run_subprocess")
    @patch("benchmark_api.live_core._mongo_client")
    def test_subscriber_lookup_failure_populates_errors(self, mock_mc, mock_run):
        def _side(argv, **kw):
            cmd_str = " ".join(argv)
            if "services" in cmd_str:
                return (0, "amf\nsmf\n", "")
            return (0, '[{"Service":"amf","State":"running"}]', "")

        mock_run.side_effect = _side
        mock_coll = MagicMock()
        mock_coll.find_one.side_effect = _MongoTimeoutError("timeout")
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        result = read_runtime(_DEFAULT_CFG)
        self.assertEqual(result["ue_registration"]["status"], "unknown")
        self.assertTrue(len(result["errors"]) > 0)


# ---------------------------------------------------------------------------
# 5. LiveCoreError tests
# ---------------------------------------------------------------------------


class LiveCoreErrorTests(unittest.TestCase):
    """LiveCoreError carries safe_message and cause."""

    def test_safe_message_attribute(self):
        err = LiveCoreError("something went wrong")
        self.assertEqual(err.safe_message, "something went wrong")

    def test_cause_none_by_default(self):
        err = LiveCoreError("msg")
        self.assertIsNone(err.cause)

    def test_cause_stored(self):
        original = ValueError("original")
        err = LiveCoreError("wrapped", cause=original)
        self.assertIs(err.cause, original)

    def test_is_runtime_error(self):
        err = LiveCoreError("msg")
        self.assertIsInstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# 6. upsert_subscriber happy-path test
# ---------------------------------------------------------------------------


class UpsertSubscriberHappyPathTests(unittest.TestCase):
    """upsert_subscriber returns expected shape on success."""

    @patch("benchmark_api.live_core._load_subscriber_document")
    @patch("benchmark_api.live_core._mongo_client")
    def test_returns_ok_shape(self, mock_mc, mock_load_doc):
        mock_doc = {
            "imsi": "001010000000001",
            "security": {},
            "meta": {"auth_profile_id": "ue1_test_profile", "ue_id": "ue1"},
            "slice": [{"sst": 1}],
        }
        mock_load_doc.return_value = mock_doc

        mock_result = MagicMock()
        mock_result.matched_count = 0
        mock_result.modified_count = 0
        mock_result.upserted_id = "abc123"

        mock_coll = MagicMock()
        mock_coll.replace_one.return_value = mock_result

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)

        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        reg = {
            "ue_id": "ue1", "supi": "001010000000001",
            "plmn": "00101", "dnn": "internet", "sst": 1, "sd": None,
            "auth_profile_id": "ue1_test_profile",
        }
        result = upsert_subscriber(_DEFAULT_CFG, reg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["imsi"], "001010000000001")
        self.assertIn("matched", result)
        self.assertIn("modified", result)
        self.assertIn("upserted", result)
        self.assertTrue(result["upserted"])


# ---------------------------------------------------------------------------
# 7. read_subscriber tests
# ---------------------------------------------------------------------------


class ReadSubscriberTests(unittest.TestCase):
    """read_subscriber returns doc without _id, or None when not found."""

    @patch("benchmark_api.live_core._mongo_client")
    def test_returns_none_when_not_found(self, mock_mc):
        mock_coll = MagicMock()
        mock_coll.find_one.return_value = None
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        result = read_subscriber(_DEFAULT_CFG, "999990000000000")
        self.assertIsNone(result)

    @patch("benchmark_api.live_core._mongo_client")
    def test_returns_doc_without_id_field(self, mock_mc):
        mock_coll = MagicMock()
        mock_coll.find_one.return_value = {
            "_id": "some_bson_id",
            "imsi": "001010000000001",
            "schema_version": 1,
        }
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_mc.return_value = mock_client

        result = read_subscriber(_DEFAULT_CFG, "001010000000001")
        self.assertIsNotNone(result)
        self.assertNotIn("_id", result)
        self.assertEqual(result["imsi"], "001010000000001")


if __name__ == "__main__":
    unittest.main()
