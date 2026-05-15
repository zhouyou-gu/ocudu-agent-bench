"""Executable conformance checks for the benchmark harness."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.episode import (
    DEFAULT_ATTACH_TIMEOUT as DEFAULT_EPISODE_ATTACH_TIMEOUT,
    DEFAULT_LAUNCH_TIMEOUT as DEFAULT_EPISODE_LAUNCH_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT as DEFAULT_EPISODE_PROBE_TIMEOUT,
    DEFAULT_WS_PORT as DEFAULT_EPISODE_WS_PORT,
    TASK_WS_PRB_PING_V1,
    EpisodeOptions,
    EpisodeRuntime,
)
from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager
from benchmark.benchmark_api.websocket_client import WebSocketClient, WebSocketFrame, WebSocketProtocolError


BASE_ZMQ_CONFIG = "$OCUDU_ROOT/runtime/ocudu_zmq_live/gnb_zmq.yaml"
GNB_BINARY = "$OCUDU_ROOT/install/srsran-project/bin/gnb"
DEFAULT_WS_PORT = 8001
DEFAULT_LAUNCH_TIMEOUT = 20
DEFAULT_PROBE_TIMEOUT = 10

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_BLOCKED = "blocked"
RESULT_SKIP = "skip"
RESULT_ERROR = "error"

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
}
V3_DOCKER_CHECKS = {
    "docker_e2e_assets",
    "open5gs_core_health",
    "srsue_zmq_attach",
    "ping_traffic_path",
    "websocket_prb_policy_action",
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
        "e2_kpm": by_id.get("e2_kpm") == RESULT_PASS,
        "e2_control": by_id.get("e2_rc_ccc") == RESULT_PASS,
        "zmq": by_id.get("zmq_rf_path") == RESULT_PASS or by_id.get("srsue_zmq_attach") == RESULT_PASS,
        "pcap_log": by_id.get("pcap_log_oracle") == RESULT_PASS,
        "docker_e2e": by_id.get("docker_e2e_assets") == RESULT_PASS,
        "ue_traffic": by_id.get("ping_traffic_path") == RESULT_PASS,
        "v3_websocket_prb": by_id.get("websocket_prb_policy_action") == RESULT_PASS,
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

            if run_ids & V3_DOCKER_CHECKS:
                results.extend(self._run_v3_docker_checks(options, run_ids, results))
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
            return {spec.id for spec in self.specs if spec.executable}
        expanded = set(checks)
        changed = True
        while changed:
            changed = False
            for check_id in list(expanded):
                for dependency in CHECK_DEPENDENCIES.get(check_id, set()):
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
        if not remote_state.get("ocudu_exists"):
            failures.append("OCUDU root does not exist")
        if not remote_state.get("ocudu_is_git"):
            failures.append("OCUDU root is not a git work tree")
        status = RESULT_FAIL if failures else RESULT_PASS
        summary = "; ".join(failures) if failures else "Remote tools and OCUDU git root are available"
        return self._result(
            "remote_tools_ocudu_root",
            status,
            summary,
            {
                "tools": all_tools,
                "ocudu_root": self.remote_check.get("ocudu_root"),
                "ocudu_commit": remote_state.get("ocudu_commit", ""),
                "ocudu_branch": remote_state.get("ocudu_branch", ""),
                "ocudu_origin": remote_state.get("ocudu_origin", ""),
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
        overlay = generate_overlay_config(options.ws_port, gnb_log_path)
        payload = {
            "run_id": options.run_id,
            "run_dir": run_dir,
            "conformance_dir": conformance_dir,
            "overlay_path": overlay_path,
            "overlay": overlay,
            "result_path": result_path,
        }
        prep = self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
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
def expand(value):
    return value.replace("$OCUDU_ROOT", os.environ["OCUDU_ROOT"])
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
def expand(value):
    return value.replace("$OCUDU_ROOT", os.environ["OCUDU_ROOT"])
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
paths = payload["paths"]
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

        need_episode = bool(run_ids & {"open5gs_core_health", "srsue_zmq_attach", "ping_traffic_path", "websocket_prb_policy_action"})
        if not need_episode:
            return results

        combined = existing + results
        if not self._result_passed(combined, "docker_e2e_assets"):
            for check_id in ["open5gs_core_health", "srsue_zmq_attach", "ping_traffic_path", "websocket_prb_policy_action"]:
                if check_id in run_ids:
                    results.append(self._blocked_result(check_id, "Docker e2e assets check did not pass"))
            return results

        episode_options = EpisodeOptions(
            run_id=f"{options.run_id}-v3",
            task=TASK_WS_PRB_PING_V1,
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
        ]:
            if check_id in run_ids:
                results.append(self._blocked_result(check_id, reason))
        if "ping_traffic_path" in run_ids and stage != "ping_traffic_path":
            results.append(self._blocked_result("ping_traffic_path", "srsUE attach did not pass"))

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
import time
payload = json.loads({json.dumps(json.dumps(payload))})
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
pathlib.Path(payload["path"]).write_text(json.dumps(payload["data"], indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({{"written": payload["path"]}}))
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
        stage = "v3_conformance" if any(
            self.spec_by_id.get(result.id) and self.spec_by_id[result.id].stage == "v3" for result in ordered
        ) else "v2_conformance"
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
