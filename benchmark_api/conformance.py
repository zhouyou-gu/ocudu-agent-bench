"""Executable conformance checks for the benchmark harness."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.episode import (
    ACTION_SET_PRB_POLICY_RATIO_CCC,
    ACTION_SET_PRB_POLICY_RATIO_RC_DU,
    ACTION_SET_PRB_POLICY_RATIO_WS,
    DEFAULT_ATTACH_TIMEOUT as DEFAULT_EPISODE_ATTACH_TIMEOUT,
    DEFAULT_LAUNCH_TIMEOUT as DEFAULT_EPISODE_LAUNCH_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT as DEFAULT_EPISODE_PROBE_TIMEOUT,
    DEFAULT_WS_PORT as DEFAULT_EPISODE_WS_PORT,
    E2_CCC_CONTROL_TOOL,
    E2_RC_DU_CONTROL_TOOL,
    TASK_E2_CCC_PRB_POLICY_PING_V1,
    TASK_E2_CONTROL_API_CONSISTENCY_V1,
    TASK_E2_KPM_PRB_PING_V1,
    TASK_E2_RC_DU_PRB_POLICY_PING_V1,
    TASK_METRICS_STALENESS_NOOP_V1,
    TASK_WS_PRB_PING_V1,
    EpisodeOptions,
    EpisodeRuntime,
    container_suffix,
    generate_v4_e2_gnb_overlay,
    fixed_prb_action_for_type,
)
from benchmark.benchmark_api.ric import (
    RIC_PROVIDER_FLEXRIC,
    FLEXRIC_IMAGE,
    RIC_PORT,
    flexric_workspace_paths,
    provider_setup_checks,
)
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager
from benchmark.benchmark_api.websocket_client import WebSocketClient, WebSocketFrame, WebSocketProtocolError


BASE_ZMQ_CONFIG = "$E2E_CONFIG_DIR/gnb_zmq.yaml"
GNB_BINARY = "$OCUDU_ROOT/install/ocudu/bin/gnb"
DEFAULT_WS_PORT = 8001
DEFAULT_LAUNCH_TIMEOUT = 20
DEFAULT_PROBE_TIMEOUT = 10

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_BLOCKED = "blocked"
RESULT_SKIP = "skip"
RESULT_ERROR = "error"


def classify_flexric_kpm_compatibility(evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify OCUDU/FlexRIC KPM compatibility from source and manifest evidence."""

    result = dict(evidence)
    ocudu_asn_version = result.get("ocudu_e2sm_kpm_asn_version")
    flexric_version = result.get("flexric_kpm_version")
    metric_versions = result.get("ocudu_kpm_metric_definition_versions") or []

    compatible = True
    summary = "OCUDU/FlexRIC KPM compatibility could not be fully determined"
    if ocudu_asn_version and flexric_version:
        compatible = str(ocudu_asn_version) == str(flexric_version)
        if compatible:
            summary = f"OCUDU E2SM-KPM ASN v{ocudu_asn_version} matches FlexRIC KPM v{flexric_version}"
        else:
            summary = (
                f"OCUDU E2SM-KPM ASN v{ocudu_asn_version} is incompatible with FlexRIC KPM v{flexric_version}; "
                "build the benchmark-owned FlexRIC KPM v05 image or pin both sides to a compatible KPM release"
            )
    elif metric_versions and flexric_version:
        summary = (
            f"OCUDU metric-definition comments mention E2SM-KPM versions {', '.join(map(str, metric_versions))}, "
            f"but no KPM ASN release header was found; FlexRIC KPM v{flexric_version} compatibility requires runtime "
            "KPM conformance"
        )
    elif result.get("ocudu_e2sm_common_version") and flexric_version:
        summary = (
            f"OCUDU generated ASN.1 reports E2SM common v{result['ocudu_e2sm_common_version']}; no KPM-specific ASN "
            f"release was found, so FlexRIC KPM v{flexric_version} compatibility requires runtime KPM conformance"
        )

    result["compatible"] = compatible
    result["summary"] = summary
    result["ocudu_e2sm_kpm_version"] = ocudu_asn_version
    return result

SETUP_CHECKS = {
    "remote_tools_ocudu_root",
    "remote_workspace_artifacts",
    "ocudu_runtime_dependencies",
}
CHECK_DEPENDENCIES = {
    "remote_workspace_artifacts": {"remote_tools_ocudu_root"},
    "ocudu_runtime_dependencies": {"remote_tools_ocudu_root"},
    "ocudu_launch": SETUP_CHECKS,
    "websocket_command_path": SETUP_CHECKS | {"ocudu_launch"},
    "json_metrics_stream": SETUP_CHECKS | {"ocudu_launch", "websocket_command_path"},
    "artifact_paths": SETUP_CHECKS | {"ocudu_launch"},
    "docker_e2e_assets": {"remote_tools_ocudu_root", "remote_workspace_artifacts"},
    "open5gs_core_health": {"docker_e2e_assets"},
    "srsue_zmq_attach": {"open5gs_core_health"},
    "ping_traffic_path": {"srsue_zmq_attach"},
    "websocket_prb_policy_action": {"srsue_zmq_attach"},
    "scenario_metrics_staleness_mask": {"ping_traffic_path"},
    "flexric_docker_assets": {"remote_tools_ocudu_root", "remote_workspace_artifacts"},
    "near_rt_ric_health": {"flexric_docker_assets"},
    "ocudu_e2_config": {"docker_e2e_assets"},
    "e2_setup_path": {"ocudu_e2_config"},
    "e2_kpm_subscription": {"e2_setup_path"},
    "e2_pcap_log_oracle": {"e2_kpm_subscription"},
    "e2_ccc_prb_control_path": {"e2_pcap_log_oracle"},
    "e2_rc_du_prb_control_path": {"e2_pcap_log_oracle"},
}
V3_DOCKER_CHECKS = {
    "docker_e2e_assets",
    "open5gs_core_health",
    "srsue_zmq_attach",
    "ping_traffic_path",
    "websocket_prb_policy_action",
    "scenario_metrics_staleness_mask",
}
V4_E2_CHECKS = {
    "flexric_docker_assets",
    "near_rt_ric_health",
    "ocudu_e2_config",
    "e2_setup_path",
    "e2_kpm_subscription",
    "e2_pcap_log_oracle",
    "e2_ccc_prb_control_path",
    "e2_rc_du_prb_control_path",
}
V4_E2_CONTROL_CHECKS = {
    "e2_ccc_prb_control_path",
    "e2_rc_du_prb_control_path",
}


@dataclass(frozen=True)
class ConformanceSpec:
    id: str
    name: str
    backend: str
    stage: str
    required_for_scoring: bool
    status: str
    executable: bool = False
    launch_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "backend": self.backend,
            "stage": self.stage,
            "required_for_scoring": self.required_for_scoring,
            "status": self.status,
            "executable": self.executable,
            "launch_required": self.launch_required,
        }


@dataclass(frozen=True)
class ConformanceOptions:
    run_id: str
    checks: set[str] | None = None
    ws_port: int = DEFAULT_WS_PORT
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT


@dataclass
class ConformanceCheckResult:
    id: str
    name: str
    backend: str
    required_for_scoring: bool
    status: str
    summary: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "backend": self.backend,
            "required_for_scoring": self.required_for_scoring,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


def load_conformance_specs(path: Path) -> list[ConformanceSpec]:
    data = json.loads(path.read_text(encoding="utf-8"))
    specs = data.get("tests", [])
    result: list[ConformanceSpec] = []
    for item in specs:
        result.append(
            ConformanceSpec(
                id=item["id"],
                name=item["name"],
                backend=item["backend"],
                stage=item.get("stage", "v2"),
                required_for_scoring=bool(item.get("required_for_scoring", False)),
                status=item.get("status", "stub"),
                executable=bool(item.get("executable", False)),
                launch_required=bool(item.get("launch_required", False)),
            )
        )
    return result


def default_run_id() -> str:
    return f"conf-{int(time.time())}"


def generate_overlay_config(ws_port: int, gnb_log_path: str) -> str:
    return (
        "cu_cp:\n"
        "  amf:\n"
        "    no_core: true\n"
        "cu_up:\n"
        "  ngu:\n"
        "    no_core: true\n"
        "metrics:\n"
        "  enable_json: true\n"
        "  autostart_stdout_metrics: false\n"
        "  layers:\n"
        "    enable_app_usage: true\n"
        "  periodicity:\n"
        "    app_usage_report_period: 1000\n"
        "remote_control:\n"
        "  enabled: true\n"
        "  bind_addr: 127.0.0.1\n"
        f"  port: {ws_port}\n"
        "log:\n"
        f"  filename: {gnb_log_path}\n"
    )


def compute_overall_status(results: list[ConformanceCheckResult]) -> str:
    required_results = [result for result in results if result.required_for_scoring]
    if any(result.status == RESULT_ERROR for result in required_results):
        return RESULT_ERROR
    if any(result.status in {RESULT_FAIL, RESULT_BLOCKED} for result in required_results):
        return RESULT_FAIL
    return RESULT_PASS


def compute_backend_enablement(results: list[ConformanceCheckResult]) -> dict[str, bool]:
    by_id = {result.id: result.status for result in results}
    return {
        "ssh": by_id.get("remote_tools_ocudu_root") == RESULT_PASS
        and by_id.get("remote_workspace_artifacts") == RESULT_PASS,
        "websocket": by_id.get("websocket_command_path") == RESULT_PASS
        or by_id.get("websocket_prb_policy_action") == RESULT_PASS,
        "json_metrics": by_id.get("json_metrics_stream") == RESULT_PASS,
        "e2_kpm": by_id.get("e2_kpm") == RESULT_PASS or by_id.get("e2_kpm_subscription") == RESULT_PASS,
        "v4_e2_kpm": by_id.get("e2_kpm_subscription") == RESULT_PASS,
        "e2_control": by_id.get("e2_rc_ccc") == RESULT_PASS
        or by_id.get("e2_ccc_prb_control_path") == RESULT_PASS
        or by_id.get("e2_rc_du_prb_control_path") == RESULT_PASS,
        "e2_ccc": by_id.get("e2_ccc_prb_control_path") == RESULT_PASS,
        "e2_rc_du": by_id.get("e2_rc_du_prb_control_path") == RESULT_PASS,
        "zmq": by_id.get("zmq_rf_path") == RESULT_PASS or by_id.get("srsue_zmq_attach") == RESULT_PASS,
        "pcap_log": by_id.get("pcap_log_oracle") == RESULT_PASS or by_id.get("e2_pcap_log_oracle") == RESULT_PASS,
        "docker_e2e": by_id.get("docker_e2e_assets") == RESULT_PASS,
        "ue_traffic": by_id.get("ping_traffic_path") == RESULT_PASS,
        "v3_websocket_prb": by_id.get("websocket_prb_policy_action") == RESULT_PASS,
        "scenario_metrics_staleness": by_id.get("scenario_metrics_staleness_mask") == RESULT_PASS,
    }


def conformance_exit_code(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == RESULT_PASS else 1


class ConformanceRunner:
    def __init__(self, remote: RemoteManager, repo_root: Path, specs_path: Path) -> None:
        self.remote = remote
        self.repo_root = repo_root
        self.specs = load_conformance_specs(specs_path)
        self.spec_by_id = {spec.id: spec for spec in self.specs}
        self.remote_check: dict[str, Any] | None = None
        self.artifacts: dict[str, str] = {}

    def run(self, options: ConformanceOptions) -> dict[str, Any]:
        self._validate_options(options)
        run_ids = self._expand_requested_checks(options.checks)
        results: list[ConformanceCheckResult] = []
        launch_started = False
        launch_passed = False
        probe_result: dict[str, Any] | None = None

        try:
            if "remote_tools_ocudu_root" in run_ids:
                remote_tools = self._check_remote_tools(options)
                results.append(remote_tools)

            if "remote_workspace_artifacts" in run_ids:
                if self._result_passed(results, "remote_tools_ocudu_root"):
                    workspace = self._prepare_workspace(options)
                else:
                    workspace = self._blocked_result("remote_workspace_artifacts", "Remote tools check did not pass")
                results.append(workspace)

            if "ocudu_runtime_dependencies" in run_ids:
                runtime_deps = (
                    self._check_runtime_dependencies(options)
                    if self._result_passed(results, "remote_tools_ocudu_root")
                    else self._blocked_result("ocudu_runtime_dependencies", "Remote tools check did not pass")
                )
                results.append(runtime_deps)

            if "ocudu_launch" in run_ids:
                if self._all_results_passed(results, SETUP_CHECKS):
                    launch = self._launch_gnb(options)
                    launch_started = bool(launch.details.get("pid"))
                    launch_passed = launch.status == RESULT_PASS
                else:
                    launch = self._blocked_result("ocudu_launch", "One or more setup checks did not pass")
                results.append(launch)

            if "websocket_command_path" in run_ids or "json_metrics_stream" in run_ids:
                if launch_passed:
                    probe_result = self._probe_websocket_and_metrics(options)
                    if "websocket_command_path" in run_ids:
                        results.append(self._probe_to_check_result("websocket_command_path", probe_result["websocket"]))
                    if "json_metrics_stream" in run_ids:
                        results.append(self._probe_to_check_result("json_metrics_stream", probe_result["metrics"]))
                else:
                    if "websocket_command_path" in run_ids:
                        results.append(self._blocked_result("websocket_command_path", "OCUDU launch did not pass"))
                    if "json_metrics_stream" in run_ids:
                        results.append(self._blocked_result("json_metrics_stream", "OCUDU launch did not pass"))

            if launch_started:
                self._terminate_gnb(options)
                launch_started = False

            if run_ids & V3_DOCKER_CHECKS:
                results.extend(self._run_v3_docker_checks(options, run_ids, results))
            if run_ids & V4_E2_CHECKS:
                results.extend(self._run_v4_e2_checks(options, run_ids, results))
        except Exception as exc:  # Keep the CLI returning structured JSON.
            results.append(self._error_result("conformance_runner", str(exc)))
        finally:
            if launch_started:
                self._terminate_gnb(options)

        if "artifact_paths" in run_ids:
            if self.artifacts:
                results.append(self._check_artifacts(options, launch_passed=launch_passed))
            else:
                results.append(self._blocked_result("artifact_paths", "Remote workspace artifacts were not prepared"))

        results.extend(self._skipped_results(run_ids, {result.id for result in results}))
        data = self._build_result(options, results, probe_result)
        self._write_result_json(options, data)
        return data

    def _validate_options(self, options: ConformanceOptions) -> None:
        if not options.run_id or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in options.run_id
        ):
            raise ValueError(f"Invalid conformance run_id: {options.run_id!r}")
        if not (0 < options.ws_port <= 65535):
            raise ValueError(f"Invalid WebSocket port: {options.ws_port}")
        if options.launch_timeout <= 0:
            raise ValueError("launch_timeout must be positive")
        if options.probe_timeout <= 0:
            raise ValueError("probe_timeout must be positive")
        unknown = (options.checks or set()) - set(self.spec_by_id)
        if unknown:
            raise ValueError(f"Unknown conformance check id(s): {', '.join(sorted(unknown))}")

    def _expand_requested_checks(self, checks: set[str] | None) -> set[str]:
        if checks is None:
            expanded = {spec.id for spec in self.specs if spec.executable}
            return expanded
        expanded = set(checks)
        changed = True
        while changed:
            changed = False
            for check_id in list(expanded):
                for dependency in CHECK_DEPENDENCIES.get(check_id, set()):
                    if dependency not in expanded:
                        expanded.add(dependency)
                        changed = True
                if check_id in {
                    "ocudu_e2_config",
                    "e2_setup_path",
                    "e2_kpm_subscription",
                    "e2_pcap_log_oracle",
                    "e2_ccc_prb_control_path",
                    "e2_rc_du_prb_control_path",
                }:
                    for dependency in provider_setup_checks(self.remote.config.ric_provider):
                        if dependency not in expanded:
                            expanded.add(dependency)
                            changed = True
        return expanded

    def _check_remote_tools(self, options: ConformanceOptions) -> ConformanceCheckResult:
        self.remote_check = self.remote.check()
        if self.remote_check.get("status") != "ok":
            return self._result(
                "remote_tools_ocudu_root",
                RESULT_FAIL,
                "SSH remote check failed",
                {"remote_check": self.remote_check},
            )

        remote_state = self.remote_check.get("remote", {})
        all_tools = dict(remote_state.get("tools", {}))
        missing_tools = [name for name in ["python3", "git", "rsync", "ss", "ldd"] if not all_tools.get(name)]
        failures = []
        if missing_tools:
            failures.append(f"missing tools: {', '.join(missing_tools)}")
        workspace_owned = self.remote_check.get("workspace_owned_runtime", {})
        outside_workspace = [name for name, ok in workspace_owned.items() if not ok]
        if outside_workspace:
            failures.append("runtime paths outside workspace: " + ", ".join(sorted(outside_workspace)))
        if not remote_state.get("ocudu_exists"):
            failures.append("OCUDU root does not exist")
        if not remote_state.get("ocudu_is_git") and not remote_state.get("ocudu_source_is_git"):
            failures.append("OCUDU source git tree does not exist")
        status = RESULT_FAIL if failures else RESULT_PASS
        summary = "; ".join(failures) if failures else "Remote tools and OCUDU source/build root are available"
        return self._result(
            "remote_tools_ocudu_root",
            status,
            summary,
            {
                "tools": all_tools,
                "ocudu_root": self.remote_check.get("ocudu_root"),
                "ocudu_commit": remote_state.get("ocudu_commit", "") or remote_state.get("ocudu_source_commit", ""),
                "ocudu_branch": remote_state.get("ocudu_branch", ""),
                "ocudu_origin": remote_state.get("ocudu_origin", "") or remote_state.get("ocudu_source_origin", ""),
                "srsran_4g_commit": remote_state.get("srsran_4g_commit", ""),
                "workspace_owned_runtime": self.remote_check.get("workspace_owned_runtime", {}),
            },
        )

    def _prepare_workspace(self, options: ConformanceOptions) -> ConformanceCheckResult:
        init_result = self.remote.init_workspace()
        sync_result = self.remote.sync(source=self.repo_root / "benchmark", repo_root=self.repo_root)
        run_dir = f"{self.remote.config.workspace}/runs/{options.run_id}"
        conformance_dir = f"{run_dir}/conformance"
        overlay_path = f"{conformance_dir}/configs/gnb_conformance_overlay.yaml"
        gnb_log_path = f"{conformance_dir}/logs/gnb.log"
        result_path = f"{conformance_dir}/results/conformance.json"
        overlay = generate_overlay_config(options.ws_port, "__GNB_LOG_PATH__")
        payload = {
            "run_id": options.run_id,
            "run_dir": run_dir,
            "conformance_dir": conformance_dir,
            "overlay_path": overlay_path,
            "overlay": overlay,
            "gnb_log_path": gnb_log_path,
            "result_path": result_path,
        }
        prep = self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
for key in ["run_dir", "conformance_dir", "overlay_path", "gnb_log_path", "result_path"]:
    payload[key] = expand_remote_path(payload[key])
payload["overlay"] = payload["overlay"].replace("__GNB_LOG_PATH__", payload["gnb_log_path"])
base = pathlib.Path(payload["conformance_dir"])
for name in ["configs", "logs", "pids", "results"]:
    (base / name).mkdir(parents=True, exist_ok=True)
pathlib.Path(payload["overlay_path"]).write_text(payload["overlay"], encoding="utf-8")
pathlib.Path(payload["result_path"]).write_text(json.dumps({{"status": "running", "run_id": payload["run_id"]}}, indent=2), encoding="utf-8")
print(json.dumps({{"created": True, "paths": payload}}))
"""
        )
        self.artifacts = {
            "run_dir": run_dir,
            "conformance_dir": conformance_dir,
            "overlay_config": overlay_path,
            "gnb_log": gnb_log_path,
            "result_json": result_path,
            "stdout": f"{conformance_dir}/logs/gnb.stdout",
            "stderr": f"{conformance_dir}/logs/gnb.stderr",
            "pid": f"{conformance_dir}/pids/gnb.pid",
            "command_metadata": f"{conformance_dir}/pids/gnb_command.json",
        }
        failures = []
        if init_result.get("status") != "ok":
            failures.append("remote workspace init failed")
        if sync_result.get("status") != "ok":
            failures.append("remote sync failed")
        if not prep.get("created"):
            failures.append("remote conformance run directory preparation failed")
        status = RESULT_FAIL if failures else RESULT_PASS
        summary = "; ".join(failures) if failures else "Remote workspace and conformance run directories are ready"
        return self._result(
            "remote_workspace_artifacts",
            status,
            summary,
            {
                "init": init_result,
                "sync": sync_result,
                "paths": self.artifacts,
            },
        )

    def _check_runtime_dependencies(self, options: ConformanceOptions) -> ConformanceCheckResult:
        payload = {
            "gnb_binary": GNB_BINARY,
            "base_config": BASE_ZMQ_CONFIG,
            "workspace": self.remote.config.workspace,
        }
        data = self._remote_json(
            f"""
import json
import os
import pathlib
import shutil
import subprocess
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
payload["workspace"] = expand_remote_path(payload["workspace"])
def expand(value):
    return expand_remote_path(value.replace("$OCUDU_ROOT", os.environ["OCUDU_ROOT"]).replace("$E2E_CONFIG_DIR", os.environ["E2E_CONFIG_DIR"]))
gnb = expand(payload["gnb_binary"])
base_config = expand(payload["base_config"])
runtime_root = pathlib.Path(payload["workspace"]) / "runtime-libs" / "root"
library_paths = [
    str(runtime_root / "usr" / "lib" / "x86_64-linux-gnu"),
    str(runtime_root / "usr" / "lib"),
    str(runtime_root / "lib" / "x86_64-linux-gnu"),
    str(runtime_root / "lib"),
]
library_paths = [path for path in library_paths if pathlib.Path(path).is_dir()]
env = os.environ.copy()
if library_paths:
    existing = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = ":".join(library_paths + ([existing] if existing else []))
result = {{
    "gnb_binary": gnb,
    "gnb_binary_exists": pathlib.Path(gnb).is_file(),
    "gnb_binary_executable": os.access(gnb, os.X_OK),
    "base_config": base_config,
    "base_config_exists": pathlib.Path(base_config).is_file(),
    "ldd": shutil.which("ldd") or "",
    "library_paths": library_paths,
    "ld_library_path": env.get("LD_LIBRARY_PATH", ""),
    "missing_libraries": [],
    "ldd_stdout": "",
    "ldd_stderr": "",
}}
if result["gnb_binary_exists"] and result["ldd"]:
    proc = subprocess.run(["ldd", gnb], check=False, text=True, capture_output=True, env=env)
    result["ldd_returncode"] = proc.returncode
    result["ldd_stdout"] = proc.stdout
    result["ldd_stderr"] = proc.stderr
    for line in proc.stdout.splitlines() + proc.stderr.splitlines():
        if "not found" in line:
            result["missing_libraries"].append(line.strip().split()[0])
print(json.dumps(result))
"""
        )
        failures = []
        if not data.get("gnb_binary_exists"):
            failures.append("gNB binary is missing")
        if not data.get("gnb_binary_executable"):
            failures.append("gNB binary is not executable")
        if not data.get("base_config_exists"):
            failures.append("base ZMQ config is missing")
        if not data.get("ldd"):
            failures.append("ldd is missing")
        if data.get("missing_libraries"):
            failures.append("missing runtime libraries: " + ", ".join(data["missing_libraries"]))
        status = RESULT_FAIL if failures else RESULT_PASS
        summary = "; ".join(failures) if failures else "OCUDU gNB binary, base config, and runtime dependencies are available"
        return self._result("ocudu_runtime_dependencies", status, summary, data)

    def _launch_gnb(self, options: ConformanceOptions) -> ConformanceCheckResult:
        payload = {
            "run_id": options.run_id,
            "run_dir": self.artifacts["run_dir"],
            "conformance_dir": self.artifacts["conformance_dir"],
            "base_config": BASE_ZMQ_CONFIG,
            "gnb_binary": GNB_BINARY,
            "workspace": self.remote.config.workspace,
            "overlay_path": self.artifacts["overlay_config"],
            "stdout": self.artifacts["stdout"],
            "stderr": self.artifacts["stderr"],
            "pid_path": self.artifacts["pid"],
            "command_metadata": self.artifacts["command_metadata"],
            "port": options.ws_port,
            "launch_timeout": options.launch_timeout,
        }
        data = self._remote_json(
            f"""
import json
import os
import pathlib
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
for key in ["run_dir", "conformance_dir", "workspace", "overlay_path", "stdout", "stderr", "pid_path", "command_metadata"]:
    payload[key] = expand_remote_path(payload[key])
def expand(value):
    return expand_remote_path(value.replace("$OCUDU_ROOT", os.environ["OCUDU_ROOT"]).replace("$E2E_CONFIG_DIR", os.environ["E2E_CONFIG_DIR"]))
def port_listening(port):
    proc = subprocess.run(["ss", "-ltn"], check=False, text=True, capture_output=True)
    token = ":" + str(port)
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3].endswith(token):
            return True
    return False
port = int(payload["port"])
if port_listening(port):
    print(json.dumps({{"status": "fail", "summary": f"port {{port}} is already listening", "details": payload}}))
    raise SystemExit(0)
gnb = expand(payload["gnb_binary"])
base_config = expand(payload["base_config"])
argv = [gnb, "-c", base_config, "-c", payload["overlay_path"]]
runtime_root = pathlib.Path(payload["workspace"]) / "runtime-libs" / "root"
library_paths = [
    str(runtime_root / "usr" / "lib" / "x86_64-linux-gnu"),
    str(runtime_root / "usr" / "lib"),
    str(runtime_root / "lib" / "x86_64-linux-gnu"),
    str(runtime_root / "lib"),
]
library_paths = [path for path in library_paths if pathlib.Path(path).is_dir()]
env = os.environ.copy()
if library_paths:
    existing = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = ":".join(library_paths + ([existing] if existing else []))
pathlib.Path(payload["stdout"]).parent.mkdir(parents=True, exist_ok=True)
stdout_f = open(payload["stdout"], "ab", buffering=0)
stderr_f = open(payload["stderr"], "ab", buffering=0)
proc = subprocess.Popen(argv, cwd=payload["conformance_dir"], stdout=stdout_f, stderr=stderr_f, env=env, start_new_session=True)
pathlib.Path(payload["pid_path"]).write_text(str(proc.pid), encoding="utf-8")
pathlib.Path(payload["command_metadata"]).write_text(json.dumps({{
    "argv": argv,
    "pid": proc.pid,
    "started_at": time.time(),
    "ws_port": port,
    "ld_library_path": env.get("LD_LIBRARY_PATH", ""),
    "library_paths": library_paths,
}}, indent=2, sort_keys=True), encoding="utf-8")
deadline = time.monotonic() + int(payload["launch_timeout"])
while time.monotonic() < deadline:
    if port_listening(port):
        print(json.dumps({{"status": "pass", "summary": "gNB launched and remote-control port is listening", "pid": proc.pid, "details": payload}}))
        raise SystemExit(0)
    returncode = proc.poll()
    if returncode is not None:
        tail = ""
        try:
            tail = pathlib.Path(payload["stderr"]).read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        print(json.dumps({{"status": "fail", "summary": f"gNB exited before readiness with code {{returncode}}", "pid": proc.pid, "stderr_tail": tail, "details": payload}}))
        raise SystemExit(0)
    time.sleep(0.25)
print(json.dumps({{"status": "fail", "summary": "gNB launch timed out before remote-control readiness", "pid": proc.pid, "details": payload}}))
"""
        )
        return self._result(
            "ocudu_launch",
            data.get("status", RESULT_ERROR),
            data.get("summary", "gNB launch returned an invalid result"),
            data,
        )

    def _probe_websocket_and_metrics(self, options: ConformanceOptions) -> dict[str, Any]:
        payload = {
            "port": options.ws_port,
            "timeout": options.probe_timeout,
        }
        script = self._websocket_client_remote_source()
        script += f"""
import json
import socket
import time
payload = json.loads({json.dumps(json.dumps(payload))})
def response_json(client, message):
    client.send_text(message)
    text = client.recv_text(timeout=payload["timeout"])
    if text is None:
        raise RuntimeError("connection closed before response")
    return json.loads(text), text
result = {{
    "websocket": {{"status": "pass", "summary": "WebSocket command path accepted expected error/success probes", "details": {{}}}},
    "metrics": {{"status": "fail", "summary": "No metrics frame received before timeout", "details": {{}}}},
}}
try:
    with WebSocketClient("127.0.0.1", int(payload["port"]), timeout=float(payload["timeout"])) as client:
        malformed, malformed_text = response_json(client, "{{bad json")
        if "error" not in malformed:
            raise RuntimeError("malformed JSON did not produce an error response")
        missing_cmd, missing_text = response_json(client, "{{}}")
        if "error" not in missing_cmd:
            raise RuntimeError("missing cmd did not produce an error response")
        unknown, unknown_text = response_json(client, json.dumps({{"cmd": "benchmark_unknown_command"}}))
        if "error" not in unknown:
            raise RuntimeError("unknown cmd did not produce an error response")
        subscribe, subscribe_text = response_json(client, json.dumps({{"cmd": "metrics_subscribe"}}))
        if subscribe.get("cmd") != "metrics_subscribe" or "error" in subscribe:
            result["websocket"] = {{"status": "fail", "summary": "metrics_subscribe did not return success", "details": {{"response": subscribe, "raw": subscribe_text}}}}
        else:
            result["websocket"]["details"] = {{
                "malformed": malformed,
                "missing_cmd": missing_cmd,
                "unknown": unknown,
                "metrics_subscribe": subscribe,
            }}
            deadline = time.monotonic() + float(payload["timeout"])
            while time.monotonic() < deadline:
                try:
                    frame = client.recv_text(timeout=max(0.1, deadline - time.monotonic()))
                except socket.timeout:
                    break
                if frame is None:
                    break
                if not frame:
                    continue
                try:
                    decoded = json.loads(frame)
                    result["metrics"] = {{"status": "pass", "summary": "Received a JSON metrics frame", "details": {{"format": "json", "sample": decoded}}}}
                except json.JSONDecodeError:
                    result["metrics"] = {{"status": "pass", "summary": "Received a text metrics frame", "details": {{"format": "text", "sample": frame[:1000]}}}}
                break
except Exception as exc:
    result["websocket"] = {{"status": "fail", "summary": str(exc), "details": {{}}}}
    result["metrics"] = {{"status": "blocked", "summary": "WebSocket command path failed", "details": {{}}}}
print(json.dumps(result))
"""
        return self._remote_json(script)

    def _check_artifacts(self, options: ConformanceOptions, launch_passed: bool) -> ConformanceCheckResult:
        payload = {"paths": self.artifacts}
        data = self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
paths = {{name: expand_remote_path(path) for name, path in payload["paths"].items()}}
exists = {{name: pathlib.Path(path).exists() for name, path in paths.items()}}
setup_required = ["overlay_config", "result_json"]
launch_required = ["gnb_log", "stdout", "stderr", "pid", "command_metadata"]
missing_setup = [name for name in setup_required if not exists.get(name)]
missing_launch = [name for name in launch_required if not exists.get(name)]
print(json.dumps({{
    "exists": exists,
    "missing_setup": missing_setup,
    "missing_launch": missing_launch,
    "paths": paths,
}}))
"""
        )
        data["launch_artifacts_status"] = "checked" if launch_passed else "blocked"
        missing: list[str] = list(data.get("missing_setup", []))
        if launch_passed:
            missing.extend(data.get("missing_launch", []))
        status = RESULT_FAIL if missing else RESULT_PASS
        if missing:
            summary = "Missing conformance artifacts: " + ", ".join(missing)
        elif launch_passed:
            summary = "Conformance setup and launch artifacts are present"
        else:
            summary = "Conformance setup artifacts are present; launch artifacts were blocked"
        return self._result("artifact_paths", status, summary, data)

    def _run_v3_docker_checks(
        self,
        options: ConformanceOptions,
        run_ids: set[str],
        existing: list[ConformanceCheckResult],
    ) -> list[ConformanceCheckResult]:
        results: list[ConformanceCheckResult] = []
        runtime = EpisodeRuntime(self.remote, repo_root=self.repo_root)

        if "docker_e2e_assets" in run_ids:
            if self._all_results_passed(existing, {"remote_tools_ocudu_root", "remote_workspace_artifacts"}):
                assets = runtime.check_docker_assets()
                status = RESULT_PASS if assets.get("status") == RESULT_PASS else RESULT_FAIL
                results.append(
                    self._result("docker_e2e_assets", status, assets.get("summary", "Docker e2e asset check failed"), assets)
                )
            else:
                results.append(self._blocked_result("docker_e2e_assets", "Remote tools/workspace checks did not pass"))

        v3_episode_checks = {
            "open5gs_core_health",
            "srsue_zmq_attach",
            "ping_traffic_path",
            "websocket_prb_policy_action",
            "scenario_metrics_staleness_mask",
        }
        need_episode = bool(run_ids & v3_episode_checks)
        if not need_episode:
            return results

        combined = existing + results
        if not self._result_passed(combined, "docker_e2e_assets"):
            for check_id in sorted(v3_episode_checks):
                if check_id in run_ids:
                    results.append(self._blocked_result(check_id, "Docker e2e assets check did not pass"))
            return results

        episode_task = TASK_METRICS_STALENESS_NOOP_V1 if "scenario_metrics_staleness_mask" in run_ids else TASK_WS_PRB_PING_V1
        episode_options = EpisodeOptions(
            run_id=f"{options.run_id}-v3",
            task=episode_task,
            duration=0,
            ws_port=options.ws_port or DEFAULT_EPISODE_WS_PORT,
            launch_timeout=max(options.launch_timeout, DEFAULT_EPISODE_LAUNCH_TIMEOUT),
            attach_timeout=DEFAULT_EPISODE_ATTACH_TIMEOUT,
            probe_timeout=max(options.probe_timeout, DEFAULT_EPISODE_PROBE_TIMEOUT),
        )
        cleanup_success = False
        start: dict[str, Any] | None = None
        try:
            start = runtime.start(episode_options)
            if start.get("status") == "ok":
                if "open5gs_core_health" in run_ids:
                    results.append(self._result("open5gs_core_health", RESULT_PASS, "Open5GS core became healthy", start))
                if "srsue_zmq_attach" in run_ids:
                    results.append(self._result("srsue_zmq_attach", RESULT_PASS, "srsUE attached and started UE traffic", start))
                time.sleep(2.0)
                observation = runtime.observe()
                ping = observation.get("observation", {}).get("ping", {})
                if "ping_traffic_path" in run_ids:
                    status = RESULT_PASS if ping.get("packets_received", 0) > 0 else RESULT_FAIL
                    summary = "UE ping traffic received replies" if status == RESULT_PASS else "UE ping traffic did not receive replies"
                    results.append(self._result("ping_traffic_path", status, summary, {"observation": observation}))
                if "scenario_metrics_staleness_mask" in run_ids:
                    frame = observation.get("observation", {})
                    metrics = frame.get("metrics", {})
                    scenario = frame.get("scenario", {})
                    mask_ok = (
                        metrics.get("stale") is True
                        and metrics.get("present") is False
                        and scenario.get("metrics_stale") is True
                        and int(scenario.get("stale_metrics_window", 0) or 0) > 0
                    )
                    results.append(
                        self._result(
                            "scenario_metrics_staleness_mask",
                            RESULT_PASS if mask_ok else RESULT_FAIL,
                            "Scenario mask marks early JSON metrics observations as stale"
                            if mask_ok
                            else "Scenario mask did not produce the required stale metrics observation",
                            {"observation": observation},
                        )
                    )
                if "websocket_prb_policy_action" in run_ids:
                    invalid = runtime.act(
                        {"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10}
                    )
                    valid = runtime.act(
                        {
                            "type": "SET_PRB_POLICY_RATIO_WS",
                            "plmn": "00101",
                            "sst": 1,
                            "sd": 0xFFFFFF,
                            "min_prb_policy_ratio": 10,
                            "max_prb_policy_ratio": 90,
                            "dedicated_ratio": 0,
                        }
                    )
                    ok = invalid.get("status") == "rejected" and valid.get("accepted") is True
                    results.append(
                        self._result(
                            "websocket_prb_policy_action",
                            RESULT_PASS if ok else RESULT_FAIL,
                            "PRB policy action rejects invalid input locally and accepts valid WebSocket command"
                            if ok
                            else "PRB policy action validation or WebSocket dispatch failed",
                            {"invalid": invalid, "valid": valid},
                        )
                    )
            else:
                stage = str(start.get("stage", "v3_episode"))
                self._append_v3_start_failure(results, run_ids, stage, start)
        finally:
            try:
                cleanup = runtime.cleanup(episode_options.run_id)
                cleanup_success = cleanup.get("status") == "ok"
            except Exception:
                cleanup_success = False
            try:
                if runtime.options is not None:
                    runtime.finalize(
                        unscored_reason=None if start and start.get("status") == "ok" else "v3 conformance setup failed",
                        cleanup_success=cleanup_success,
                    )
            except Exception:
                pass
        return results

    def _append_v3_start_failure(
        self,
        results: list[ConformanceCheckResult],
        run_ids: set[str],
        stage: str,
        start: dict[str, Any],
    ) -> None:
        if "open5gs_core_health" in run_ids:
            status = RESULT_FAIL if stage == "open5gs_core_health" else RESULT_PASS
            summary = start.get("summary", "Open5GS startup failed") if status == RESULT_FAIL else "Open5GS core started"
            results.append(self._result("open5gs_core_health", status, summary, start))
        if "srsue_zmq_attach" in run_ids:
            if stage in {"open5gs_core_health", "ocudu_launch"}:
                results.append(self._blocked_result("srsue_zmq_attach", "gNB/Open5GS startup did not pass"))
            elif stage == "srsue_zmq_attach":
                results.append(self._result("srsue_zmq_attach", RESULT_FAIL, start.get("summary", "srsUE attach failed"), start))
            else:
                results.append(self._result("srsue_zmq_attach", RESULT_PASS, "srsUE attached before later startup failure", start))
        if "ping_traffic_path" in run_ids and stage == "ping_traffic_path":
            results.append(self._result("ping_traffic_path", RESULT_FAIL, start.get("summary", "ping traffic failed"), start))
        for check_id, reason in [
            ("websocket_prb_policy_action", "srsUE/gNB startup did not pass"),
            ("scenario_metrics_staleness_mask", "srsUE/gNB startup did not pass"),
        ]:
            if check_id in run_ids:
                results.append(self._blocked_result(check_id, reason))
        if "ping_traffic_path" in run_ids and stage != "ping_traffic_path":
            results.append(self._blocked_result("ping_traffic_path", "srsUE attach did not pass"))

    def _run_v4_e2_checks(
        self,
        options: ConformanceOptions,
        run_ids: set[str],
        existing: list[ConformanceCheckResult],
    ) -> list[ConformanceCheckResult]:
        results: list[ConformanceCheckResult] = []
        runtime = EpisodeRuntime(self.remote, repo_root=self.repo_root)

        if "flexric_docker_assets" in run_ids:
            if self._all_results_passed(existing, {"remote_tools_ocudu_root", "remote_workspace_artifacts"}):
                assets = self._check_flexric_assets()
                status = RESULT_PASS if assets.get("status") == RESULT_PASS else RESULT_FAIL
                results.append(
                    self._result("flexric_docker_assets", status, assets.get("summary", "FlexRIC image check failed"), assets)
                )
            else:
                results.append(self._blocked_result("flexric_docker_assets", "Remote tools/workspace checks did not pass"))

        if "near_rt_ric_health" in run_ids:
            if self._result_passed(existing + results, "flexric_docker_assets"):
                health = self._check_ric_health(options)
                status = RESULT_PASS if health.get("status") == RESULT_PASS else RESULT_FAIL
                results.append(self._result("near_rt_ric_health", status, health.get("summary", "RIC health failed"), health))
            else:
                results.append(self._blocked_result("near_rt_ric_health", "FlexRIC image check did not pass"))

        if "ocudu_e2_config" in run_ids:
            if self._result_passed(existing + results, "docker_e2e_assets"):
                overlay = self._generate_provider_e2_overlay(options.ws_port)
                compatibility = self._detect_ocudu_kpm_compatibility()
                required = [
                    "enable_du_e2: true",
                    "enable_cu_cp_e2: true",
                    "e2sm_kpm_enabled: true",
                    "e2sm_rc_enabled: true",
                    "e2sm_ccc_enabled: true",
                    "e2ap_enable: true",
                    "remote_control:",
                    "enable_json: true",
                ]
                missing = [item for item in required if item not in overlay]
                compatible = bool(compatibility.get("compatible", True))
                summary = "Generated v4 E2/KPM overlay is valid"
                if missing:
                    summary = "Generated v4 overlay is missing: " + ", ".join(missing)
                elif not compatible:
                    summary = compatibility.get("summary", "OCUDU and FlexRIC KPM versions are incompatible")
                results.append(
                    self._result(
                        "ocudu_e2_config",
                        RESULT_FAIL if missing or not compatible else RESULT_PASS,
                        summary,
                        {"missing": missing, "overlay": overlay, "compatibility": compatibility},
                    )
                )
            else:
                results.append(self._blocked_result("ocudu_e2_config", "Docker e2e assets check did not pass"))

        need_episode = bool(
            run_ids
            & {
                "e2_setup_path",
                "e2_kpm_subscription",
                "e2_pcap_log_oracle",
                "e2_ccc_prb_control_path",
                "e2_rc_du_prb_control_path",
            }
        )
        if not need_episode:
            return results

        combined = existing + results
        provider_ready = self._result_passed(combined, "near_rt_ric_health")
        if not provider_ready or not self._result_passed(combined, "ocudu_e2_config"):
            for check_id in [
                "e2_setup_path",
                "e2_kpm_subscription",
                "e2_pcap_log_oracle",
                "e2_ccc_prb_control_path",
                "e2_rc_du_prb_control_path",
            ]:
                if check_id in run_ids:
                    results.append(self._blocked_result(check_id, "V4 RIC or E2 config check did not pass"))
            return results

        missing_control_tools = self._append_missing_e2_control_tool_results(results, combined, run_ids)
        if missing_control_tools:
            run_ids = set(run_ids) - missing_control_tools

        if "e2_rc_du_prb_control_path" in run_ids and "e2_ccc_prb_control_path" in run_ids:
            episode_task = TASK_E2_CONTROL_API_CONSISTENCY_V1
        elif "e2_rc_du_prb_control_path" in run_ids:
            episode_task = TASK_E2_RC_DU_PRB_POLICY_PING_V1
        elif "e2_ccc_prb_control_path" in run_ids:
            episode_task = TASK_E2_CCC_PRB_POLICY_PING_V1
        else:
            episode_task = TASK_E2_KPM_PRB_PING_V1

        episode_options = EpisodeOptions(
            run_id=f"{options.run_id}-e2",
            task=episode_task,
            duration=0,
            ws_port=options.ws_port or DEFAULT_EPISODE_WS_PORT,
            launch_timeout=max(options.launch_timeout, DEFAULT_EPISODE_LAUNCH_TIMEOUT),
            attach_timeout=DEFAULT_EPISODE_ATTACH_TIMEOUT,
            probe_timeout=max(options.probe_timeout, DEFAULT_EPISODE_PROBE_TIMEOUT),
        )
        cleanup_success = False
        start: dict[str, Any] | None = None
        try:
            start = runtime.start(episode_options)
            if start.get("status") == "ok":
                if "e2_setup_path" in run_ids:
                    results.append(self._result("e2_setup_path", RESULT_PASS, "E2 setup evidence observed", start))
                time.sleep(2.0)
                observation = runtime.observe()
                e2 = observation.get("observation", {}).get("e2", {})
                if "e2_kpm_subscription" in run_ids:
                    has_prb_measurement = bool(e2.get("has_prb_measurement"))
                    ok = int(e2.get("kpm_indications", 0) or 0) >= 3 and has_prb_measurement
                    results.append(
                        self._result(
                            "e2_kpm_subscription",
                            RESULT_PASS if ok else RESULT_FAIL,
                            "FlexRIC KPM path produced decoded v05 PRB records"
                            if ok
                            else "FlexRIC KPM path did not produce enough decoded PRB records",
                            {"observation": observation},
                        )
                    )
                if "e2_ccc_prb_control_path" in run_ids:
                    results.append(
                        self._run_e2_control_action_check(
                            runtime=runtime,
                            check_id="e2_ccc_prb_control_path",
                            action_type=ACTION_SET_PRB_POLICY_RATIO_CCC,
                            summary_prefix="E2SM-CCC PRB control",
                        )
                    )
                if "e2_rc_du_prb_control_path" in run_ids:
                    results.append(
                        self._run_e2_control_action_check(
                            runtime=runtime,
                            check_id="e2_rc_du_prb_control_path",
                            action_type=ACTION_SET_PRB_POLICY_RATIO_RC_DU,
                            summary_prefix="E2SM-RC DU PRB control",
                        )
                    )
            else:
                self._append_v4_start_failure(results, run_ids, str(start.get("stage", "v4_episode")), start)
        finally:
            try:
                cleanup = runtime.cleanup(episode_options.run_id)
                cleanup_success = cleanup.get("status") == "ok"
            except Exception:
                cleanup_success = False
            try:
                if runtime.options is not None:
                    summary = runtime.finalize(
                        unscored_reason=None if start and start.get("status") == "ok" else "v4 conformance setup failed",
                        cleanup_success=cleanup_success,
                    )
                    if start and start.get("status") == "ok" and "e2_pcap_log_oracle" in run_ids:
                        oracle = summary.get("e2_oracle", {})
                        ok = bool(oracle.get("oracle_available")) and int(oracle.get("kpm_indications", 0) or 0) >= 3
                        results.append(
                            self._result(
                                "e2_pcap_log_oracle",
                                RESULT_PASS if ok else RESULT_FAIL,
                                "E2 KPM/log oracle artifacts are available" if ok else "E2 oracle artifacts are missing or incomplete",
                                {"summary": summary},
                            )
                        )
                    if start and start.get("status") == "ok":
                        self._attach_e2_control_oracle_results(results, run_ids, summary)
            except Exception as exc:
                if "e2_pcap_log_oracle" in run_ids:
                    results.append(self._error_result("e2_pcap_log_oracle", str(exc)))
                self._attach_e2_control_oracle_error(results, run_ids, str(exc))
        return results

    def _append_missing_e2_control_tool_results(
        self,
        results: list[ConformanceCheckResult],
        combined: list[ConformanceCheckResult],
        run_ids: set[str],
    ) -> set[str]:
        requirements = {
            "e2_ccc_prb_control_path": E2_CCC_CONTROL_TOOL,
            "e2_rc_du_prb_control_path": E2_RC_DU_CONTROL_TOOL,
        }
        missing: set[str] = set()
        for check_id, tool in requirements.items():
            if check_id not in run_ids:
                continue
            if self._e2_control_tool_available(combined, tool):
                continue
            missing.add(check_id)
            results.append(
                self._result(
                    check_id,
                    RESULT_FAIL,
                    f"FlexRIC image does not expose required E2 control tool {tool}",
                    {"required_tool": tool, "flexric_assets": self._flexric_assets_details(combined)},
                )
            )
        return missing

    def _e2_control_tool_available(self, results: list[ConformanceCheckResult], tool: str) -> bool:
        assets = self._flexric_assets_details(results)
        tools = assets.get("control_tools", {})
        record = tools.get(tool)
        if isinstance(record, dict):
            return bool(record.get("available"))
        return bool(record)

    def _flexric_assets_details(self, results: list[ConformanceCheckResult]) -> dict[str, Any]:
        for result in results:
            if result.id != "flexric_docker_assets":
                continue
            details = result.details or {}
            nested = details.get("details")
            return nested if isinstance(nested, dict) else details
        return {}

    def _detect_ocudu_kpm_compatibility(self) -> dict[str, Any]:
        paths = flexric_workspace_paths(self.remote.config.workspace)
        payload = {"ocudu_root": self.remote.config.ocudu_root, "manifest": paths["manifest"]}
        data = self._remote_json(
            f"""
import json
import pathlib
import re
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
payload["ocudu_root"] = expand_remote_path(payload["ocudu_root"])
payload["manifest"] = expand_remote_path(payload["manifest"])
ocudu_root = pathlib.Path(payload["ocudu_root"])
kpm_asn_paths = [
    ocudu_root / "src" / "ocudu" / "include" / "ocudu" / "asn1" / "e2sm" / "e2sm_kpm_ies.h",
    ocudu_root / "build" / "ocudu" / "include" / "ocudu" / "asn1" / "e2sm" / "e2sm_kpm_ies.h",
]
common_asn_paths = [
    ocudu_root / "src" / "ocudu" / "include" / "ocudu" / "asn1" / "e2sm" / "e2sm_common_ies.h",
    ocudu_root / "build" / "ocudu" / "include" / "ocudu" / "asn1" / "e2sm" / "e2sm_common_ies.h",
]
metric_evidence_paths = [
    ocudu_root / "src" / "ocudu" / "lib" / "e2" / "e2sm" / "e2sm_kpm" / "e2sm_kpm_metric_defs.h",
    ocudu_root / "src" / "ocudu" / "lib" / "e2" / "e2sm" / "e2sm_kpm" / "e2sm_kpm_asn1_packer.cpp",
]
ocudu_version = None
ocudu_version_source = None
ocudu_kpm_asn_version = None
ocudu_kpm_asn_version_source = None
ocudu_kpm_metric_versions = set()
ocudu_kpm_metric_sources = []
for path in kpm_asn_paths:
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    kpm_match = re.search(r"E2SM\\s+v(\\d+\\.\\d+)", text)
    if kpm_match:
        ocudu_kpm_asn_version = kpm_match.group(1)
        ocudu_kpm_asn_version_source = str(path)
        break
for path in common_asn_paths:
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"E2SM\\s+v(\\d+\\.\\d+)", text)
    if match and ocudu_version is None:
        ocudu_version = match.group(1)
        ocudu_version_source = str(path)
        break
for path in metric_evidence_paths:
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"E2SM-KPM-R003-v0?(\\d+\\.\\d+)|E2SM-KPM[^\\n]*v0?(\\d+\\.\\d+)", text):
        version = match.group(1) or match.group(2)
        if version:
            normalized = version if version.startswith("0") else "0" + version
            ocudu_kpm_metric_versions.add(normalized)
            ocudu_kpm_metric_sources.append({{"path": str(path), "text": match.group(0)[:160]}})
manifest = {{}}
manifest_path = pathlib.Path(payload["manifest"])
if manifest_path.is_file():
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        manifest = {{"decode_error": True}}
release = manifest.get("kpm_release") or manifest.get("kpm_version")
flexric_version = None
if release:
    text = str(release)
    if "V5" in text or "v05" in text or "05.00" in text:
        flexric_version = "05.00"
    elif "V3" in text:
        flexric_version = "03.00"
    elif "V2" in text:
        flexric_version = "02.03"
print(json.dumps({{
    "ocudu_e2sm_common_version": ocudu_version,
    "ocudu_e2sm_common_version_source": ocudu_version_source,
    "ocudu_e2sm_kpm_asn_version": ocudu_kpm_asn_version,
    "ocudu_e2sm_kpm_asn_version_source": ocudu_kpm_asn_version_source,
    "ocudu_kpm_metric_definition_versions": sorted(ocudu_kpm_metric_versions),
    "ocudu_kpm_metric_definition_sources": ocudu_kpm_metric_sources[:20],
    "flexric_kpm_version": flexric_version,
    "flexric_manifest": manifest,
    "manifest_path": payload["manifest"],
}}))
"""
        )
        return classify_flexric_kpm_compatibility(data)

    def _check_flexric_assets(self) -> dict[str, Any]:
        paths = flexric_workspace_paths(self.remote.config.workspace)
        payload = {
            "image": FLEXRIC_IMAGE,
            "manifest": paths["manifest"],
            "control_tools": [E2_CCC_CONTROL_TOOL, E2_RC_DU_CONTROL_TOOL],
        }
        data = self._remote_json(
            f"""
import json
import pathlib
import shlex
import subprocess
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
payload["manifest"] = expand_remote_path(payload["manifest"])
image_proc = subprocess.run(["docker", "image", "inspect", payload["image"]], check=False, text=True, capture_output=True)
manifest = {{}}
manifest_path = pathlib.Path(payload["manifest"])
if manifest_path.is_file():
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        manifest = {{"decode_error": True}}
control_tools = {{}}
if image_proc.returncode == 0:
    for tool in payload["control_tools"]:
        proc = subprocess.run(
            ["docker", "run", "--rm", payload["image"], "bash", "-lc", "command -v " + shlex.quote(tool)],
            check=False,
            text=True,
            capture_output=True,
        )
        control_tools[tool] = {{
            "available": proc.returncode == 0,
            "path": proc.stdout.strip(),
            "stderr": proc.stderr.strip()[:500],
        }}
else:
    for tool in payload["control_tools"]:
        control_tools[tool] = {{"available": False, "path": "", "stderr": "image missing"}}
print(json.dumps({{
    "image": payload["image"],
    "image_exists": image_proc.returncode == 0,
    "manifest_path": payload["manifest"],
    "manifest_exists": manifest_path.is_file(),
    "manifest": manifest,
    "control_tools": control_tools,
}}))
"""
        )
        candidates = data.get("manifest", {}).get("kpm_xapp_candidates", [])
        manifest = data.get("manifest", {})
        supports_v05 = (
            bool(manifest.get("supports_e2sm_kpm_v05"))
            and manifest.get("kpm_asn_release") == "E2SM-KPM-R003-v05.00"
            and manifest.get("decoder_source") == "ocudu-generated-asn1-cpp"
            and manifest.get("kpm_indication_decode_per_syntax") == "ATS_UNALIGNED_BASIC_PER"
            and manifest.get("kpm_subscription_encode_per_syntax") == "ATS_ALIGNED_BASIC_PER"
        )
        ok = data.get("image_exists") and data.get("manifest_exists") and bool(candidates) and supports_v05
        return {
            "status": RESULT_PASS if ok else RESULT_FAIL,
            "summary": "FlexRIC Docker image, KPM v05 manifest, and KPM xApp candidates are available"
            if ok
            else "FlexRIC Docker image, KPM v05 manifest, or KPM xApp candidates are missing",
            "details": data,
        }

    def _check_ric_health(self, options: ConformanceOptions) -> dict[str, Any]:
        suffix = container_suffix(f"{options.run_id}-ric-health")
        container = f"skillful-ran-bench-flexric-health-{suffix}"
        paths = {
            "log": f"{self.remote.config.workspace}/runs/{options.run_id}/conformance/logs/ric_health.log",
        }
        payload = {"image": FLEXRIC_IMAGE, "container": container, "paths": paths, "port": RIC_PORT, "timeout": options.launch_timeout}
        data = self._remote_json(
            f"""
import json
import pathlib
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
paths = {{name: expand_remote_path(path) for name, path in payload["paths"].items()}}
pathlib.Path(paths["log"]).parent.mkdir(parents=True, exist_ok=True)
def run(argv):
    return subprocess.run(argv, check=False, text=True, capture_output=True)
def port_listening(port):
    proc = run(["ss", "-ln"])
    token = ":" + str(port)
    return any(any(part.endswith(token) for part in line.split()) for line in proc.stdout.splitlines()[1:])
run(["docker", "rm", "-f", payload["container"]])
start = run([
    "docker", "run", "-d", "--name", payload["container"], "--network", "host",
    "-v", str(pathlib.Path(paths["log"]).parent.parent) + ":/stage",
    payload["image"], "bash", "-lc", "flexric-ric >/stage/logs/ric_health.log 2>&1",
])
ready = False
tail = ""
if start.returncode == 0:
    deadline = time.monotonic() + int(payload["timeout"])
    while time.monotonic() < deadline:
        if port_listening(int(payload["port"])):
            ready = True
            break
        state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["container"]])
        if state.returncode == 0 and state.stdout.strip() == "exited":
            break
        time.sleep(0.5)
log_path = pathlib.Path(paths["log"])
if log_path.exists():
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
run(["docker", "rm", "-f", payload["container"]])
print(json.dumps({{
    "status": "pass" if ready else "fail",
    "container": payload["container"],
    "port": payload["port"],
    "ready": ready,
    "start_returncode": start.returncode,
    "start_stderr": start.stderr,
    "log_tail": tail,
    "log": paths["log"],
}}))
"""
        )
        return {
            "status": RESULT_PASS if data.get("ready") else RESULT_FAIL,
            "summary": "FlexRIC RIC listened on the expected SCTP port" if data.get("ready") else "FlexRIC RIC did not become ready",
            "details": data,
        }

    def _append_v4_start_failure(
        self,
        results: list[ConformanceCheckResult],
        run_ids: set[str],
        stage: str,
        start: dict[str, Any],
    ) -> None:
        if "e2_setup_path" in run_ids:
            if stage in {"near_rt_ric_health", "e2_setup_path"}:
                results.append(self._result("e2_setup_path", RESULT_FAIL, start.get("summary", "E2 setup failed"), start))
            elif stage == "e2_kpm_subscription":
                results.append(self._result("e2_setup_path", RESULT_PASS, "E2 setup evidence observed before KPM failure", start))
            else:
                results.append(self._blocked_result("e2_setup_path", "V4 episode did not reach E2 setup"))
        if "e2_kpm_subscription" in run_ids:
            if stage == "e2_kpm_subscription":
                results.append(self._result("e2_kpm_subscription", RESULT_FAIL, start.get("summary", "KPM subscription failed"), start))
            else:
                results.append(self._blocked_result("e2_kpm_subscription", "E2 setup did not pass"))
        if "e2_pcap_log_oracle" in run_ids:
            results.append(self._blocked_result("e2_pcap_log_oracle", "KPM subscription did not pass"))
        for check_id in V4_E2_CONTROL_CHECKS:
            if check_id in run_ids:
                results.append(self._blocked_result(check_id, "E2 KPM/setup did not pass"))

    def _run_e2_control_action_check(
        self,
        runtime: EpisodeRuntime,
        check_id: str,
        action_type: str,
        summary_prefix: str,
    ) -> ConformanceCheckResult:
        invalid = runtime.act(
            {
                "type": action_type,
                "min_prb_policy_ratio": 90,
                "max_prb_policy_ratio": 10,
            }
        )
        valid_action = fixed_prb_action_for_type(action_type)
        observation = runtime.observe()
        du_ue_id = observation.get("observation", {}).get("e2", {}).get("du_ue_id")
        if action_type == ACTION_SET_PRB_POLICY_RATIO_RC_DU and du_ue_id is not None:
            valid_action["du_ue_id"] = du_ue_id
        valid = runtime.act(valid_action)
        invalid_ok = invalid.get("status") == "rejected" and invalid.get("accepted") is False
        valid_ok = valid.get("accepted") is True
        status = RESULT_PASS if invalid_ok and valid_ok else RESULT_FAIL
        details = {"invalid": invalid, "valid": valid, "observation": observation}
        summary = (
            f"{summary_prefix} rejects invalid input locally and accepts a valid E2 control request"
            if status == RESULT_PASS
            else f"{summary_prefix} validation or dispatch failed"
        )
        return self._result(check_id, status, summary, details)

    def _attach_e2_control_oracle_results(
        self,
        results: list[ConformanceCheckResult],
        run_ids: set[str],
        summary: dict[str, Any],
    ) -> None:
        expected = {
            "e2_ccc_prb_control_path": ACTION_SET_PRB_POLICY_RATIO_CCC,
            "e2_rc_du_prb_control_path": ACTION_SET_PRB_POLICY_RATIO_RC_DU,
        }
        oracle = summary.get("e2_oracle", {})
        control_types = set(oracle.get("control_types", []) if isinstance(oracle, dict) else [])
        oracle_available = bool(oracle.get("control_oracle_available")) if isinstance(oracle, dict) else False
        for index, result in enumerate(results):
            expected_action = expected.get(result.id)
            if expected_action is None or result.id not in run_ids or result.status != RESULT_PASS:
                continue
            details = dict(result.details)
            details["e2_oracle"] = oracle
            if oracle_available and expected_action in control_types:
                results[index] = self._result(
                    result.id,
                    RESULT_PASS,
                    result.summary + " with E2 oracle evidence",
                    details,
                )
            else:
                results[index] = self._result(
                    result.id,
                    RESULT_FAIL,
                    result.summary + " but E2 control oracle evidence is missing",
                    details,
                )

    def _attach_e2_control_oracle_error(
        self,
        results: list[ConformanceCheckResult],
        run_ids: set[str],
        error: str,
    ) -> None:
        for index, result in enumerate(results):
            if result.id not in V4_E2_CONTROL_CHECKS or result.id not in run_ids or result.status != RESULT_PASS:
                continue
            details = dict(result.details)
            details["e2_oracle_error"] = error
            results[index] = self._result(
                result.id,
                RESULT_FAIL,
                result.summary + " but E2 oracle finalization failed",
                details,
            )

    def _generate_provider_e2_overlay(self, ws_port: int) -> str:
        return generate_v4_e2_gnb_overlay(ws_port)

    def _terminate_gnb(self, options: ConformanceOptions) -> None:
        payload = {
            "port": options.ws_port,
            "pid_path": self.artifacts.get("pid", ""),
        }
        script = self._websocket_client_remote_source()
        script += f"""
import json
import os
import signal
import pathlib
import time
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
payload["pid_path"] = expand_remote_path(payload["pid_path"]) if payload["pid_path"] else ""
try:
    with WebSocketClient("127.0.0.1", int(payload["port"]), timeout=2.0) as client:
        client.send_text(json.dumps({{"cmd": "quit"}}))
        try:
            client.recv_text(timeout=1.0)
        except Exception:
            pass
except Exception:
    pass
pid = None
try:
    pid = int(open(payload["pid_path"], encoding="utf-8").read().strip())
except Exception:
    pid = None
if pid:
    for sig, delay in [(signal.SIGTERM, 2.0), (signal.SIGKILL, 0.0)]:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        try:
            os.kill(pid, sig)
        except OSError:
            break
        if delay:
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
print(json.dumps({{"terminated": True, "pid": pid}}))
"""
        try:
            self._remote_json(script)
        except Exception:
            pass

    def _write_result_json(self, options: ConformanceOptions, data: dict[str, Any]) -> None:
        if not self.artifacts.get("result_json"):
            return
        payload = {
            "path": self.artifacts["result_json"],
            "data": data,
        }
        self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
def expand_remote_path(value):
    if value == "~":
        return str(pathlib.Path.home())
    if isinstance(value, str) and value.startswith("~/"):
        return str(pathlib.Path.home() / value[2:])
    return value
path = expand_remote_path(payload["path"])
pathlib.Path(path).write_text(json.dumps(payload["data"], indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({{"written": path}}))
"""
        )

    def _build_result(
        self,
        options: ConformanceOptions,
        results: list[ConformanceCheckResult],
        probe_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        ordered = self._order_results(results)
        status = compute_overall_status(ordered)
        remote_state = (self.remote_check or {}).get("remote", {})
        selected_stages = {
            self.spec_by_id[result.id].stage
            for result in ordered
            if self.spec_by_id.get(result.id)
            and result.status != RESULT_SKIP
        }
        if "v4b" in selected_stages:
            stage = "v4b_conformance"
        elif "v4" in selected_stages:
            stage = "v4_conformance"
        elif "v3" in selected_stages:
            stage = "v3_conformance"
        else:
            stage = "v2_conformance"
        return {
            "status": status,
            "stage": stage,
            "run_id": options.run_id,
            "remote": {
                "ssh": self.remote.config.ssh_target,
                "workspace": self.remote.config.workspace,
                "ocudu_root": self.remote.config.ocudu_root,
                "ocudu_commit": remote_state.get("ocudu_commit", ""),
                "ocudu_branch": remote_state.get("ocudu_branch", ""),
            },
            "options": {
                "checks": sorted(options.checks) if options.checks else None,
                "ws_port": options.ws_port,
                "launch_timeout": options.launch_timeout,
                "probe_timeout": options.probe_timeout,
            },
            "backend_enablement": compute_backend_enablement(ordered),
            "checks": [result.to_dict() for result in ordered],
            "artifacts": self.artifacts,
            "probe": probe_result,
        }

    def _order_results(self, results: list[ConformanceCheckResult]) -> list[ConformanceCheckResult]:
        by_id = {result.id: result for result in results}
        ordered = [by_id[spec.id] for spec in self.specs if spec.id in by_id]
        ordered.extend(result for result in results if result.id not in set(by_id) or result.id not in self.spec_by_id)
        return ordered

    def _skipped_results(self, run_ids: set[str], present_ids: set[str]) -> list[ConformanceCheckResult]:
        skipped = []
        for spec in self.specs:
            if spec.id in present_ids:
                continue
            if spec.id not in run_ids:
                skipped.append(self._result(spec.id, RESULT_SKIP, "Check was not selected for this run", {}))
            elif not spec.executable:
                skipped.append(self._result(spec.id, RESULT_SKIP, "Check is not executable in v2", {}))
        return skipped

    def _probe_to_check_result(self, check_id: str, data: dict[str, Any]) -> ConformanceCheckResult:
        return self._result(check_id, data.get("status", RESULT_ERROR), data.get("summary", "Probe failed"), data)

    def _result(
        self, check_id: str, status: str, summary: str, details: dict[str, Any]
    ) -> ConformanceCheckResult:
        spec = self.spec_by_id.get(
            check_id,
            ConformanceSpec(
                id=check_id,
                name=check_id.replace("_", " "),
                backend="runner",
                stage="v2",
                required_for_scoring=True,
                status="executable",
            ),
        )
        return ConformanceCheckResult(
            id=spec.id,
            name=spec.name,
            backend=spec.backend,
            required_for_scoring=spec.required_for_scoring,
            status=status,
            summary=summary,
            details=details,
        )

    def _blocked_result(self, check_id: str, reason: str) -> ConformanceCheckResult:
        return self._result(check_id, RESULT_BLOCKED, reason, {"reason": reason})

    def _error_result(self, check_id: str, reason: str) -> ConformanceCheckResult:
        return self._result(check_id, RESULT_ERROR, reason, {"reason": reason})

    def _result_passed(self, results: list[ConformanceCheckResult], check_id: str) -> bool:
        return any(result.id == check_id and result.status == RESULT_PASS for result in results)

    def _all_results_passed(self, results: list[ConformanceCheckResult], check_ids: set[str]) -> bool:
        return all(self._result_passed(results, check_id) for check_id in check_ids)

    def _remote_json(self, python_body: str) -> dict[str, Any]:
        command = f"python3 - <<'PY'\n{python_body.rstrip()}\nPY"
        proc = self.remote.exec([command], shell=True)
        if proc.get("status") != "ok":
            raise RemoteCommandError(
                f"Remote command failed with code {proc.get('returncode')}: {proc.get('stderr') or proc.get('stdout')}"
            )
        try:
            return json.loads(proc.get("stdout", "") or "{}")
        except json.JSONDecodeError as exc:
            raise RemoteCommandError(f"Remote command did not return JSON: {proc.get('stdout', '')}") from exc

    def _websocket_client_remote_source(self) -> str:
        parts = [
            "import base64",
            "import hashlib",
            "import os",
            "import socket",
            "import struct",
            "from dataclasses import dataclass",
            inspect.getsource(WebSocketProtocolError),
            inspect.getsource(WebSocketFrame),
            inspect.getsource(WebSocketClient),
        ]
        return "\n\n".join(parts) + "\n"


def parse_checks(value: str | None) -> set[str] | None:
    if value is None or not value.strip():
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def run_conformance(
    remote: RemoteManager,
    repo_root: Path,
    specs_path: Path,
    run_id: str | None = None,
    checks: set[str] | None = None,
    ws_port: int = DEFAULT_WS_PORT,
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
) -> dict[str, Any]:
    runner = ConformanceRunner(remote=remote, repo_root=repo_root, specs_path=specs_path)
    options = ConformanceOptions(
        run_id=run_id or default_run_id(),
        checks=checks,
        ws_port=ws_port,
        launch_timeout=launch_timeout,
        probe_timeout=probe_timeout,
    )
    return runner.run(options)
