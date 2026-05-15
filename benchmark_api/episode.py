"""V3 WebSocket PRB-control episode runtime."""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.benchmark_api.remote import RemoteCommandError, RemoteManager
from benchmark.benchmark_api.websocket_client import WebSocketClient, WebSocketFrame, WebSocketProtocolError


TASK_WS_PRB_PING_V1 = "ws_prb_ping_v1"
DEFAULT_EPISODE_DURATION = 30
DEFAULT_WS_PORT = 8001
DEFAULT_ATTACH_TIMEOUT = 90
DEFAULT_LAUNCH_TIMEOUT = 60
DEFAULT_PROBE_TIMEOUT = 5
OPEN5GS_COMPOSE = "/home/zhouyou/skillful-ran/skills/ocudu-open5gs-core/assets/compose/docker-compose.open5gs.yml"
E2E_CONFIG_DIR = "/home/zhouyou/skillful-ran/skills/ocudu-zmq-open5gs-e2e/assets/config"
GNB_IMAGE = "skillful-ran/srsran-project-build:release_25_10"
SRSUE_IMAGE = "skillful-ran/srsran-4g-ue-build:release_23_11"
PING_TARGET = "10.45.1.1"


@dataclass(frozen=True)
class EpisodeOptions:
    run_id: str
    task: str = TASK_WS_PRB_PING_V1
    duration: int = DEFAULT_EPISODE_DURATION
    ws_port: int = DEFAULT_WS_PORT
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT
    attach_timeout: int = DEFAULT_ATTACH_TIMEOUT
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT


def default_episode_run_id() -> str:
    return f"ep-{int(time.time())}"


def safe_run_id(value: str) -> str:
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        raise ValueError(f"Invalid episode run_id: {value!r}")
    return value


def container_suffix(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).replace("_", "-").replace(".", "-")


def episode_paths(workspace: str, run_id: str) -> dict[str, str]:
    run_dir = f"{workspace}/runs/{run_id}"
    episode_dir = f"{run_dir}/episode"
    return {
        "run_dir": run_dir,
        "episode_dir": episode_dir,
        "configs_dir": f"{episode_dir}/configs",
        "logs_dir": f"{episode_dir}/logs",
        "gnb_config": f"{episode_dir}/configs/gnb_zmq.yaml",
        "gnb_overlay": f"{episode_dir}/configs/gnb_v3_overlay.yaml",
        "ue_config": f"{episode_dir}/configs/ue_zmq.conf",
        "containers": f"{episode_dir}/pids_or_containers.json",
        "actions": f"{episode_dir}/actions.jsonl",
        "observations": f"{episode_dir}/observations.jsonl",
        "metrics_raw": f"{episode_dir}/metrics_raw.jsonl",
        "summary": f"{episode_dir}/summary.json",
        "cleanup": f"{episode_dir}/cleanup.json",
        "gnb_log": f"{episode_dir}/logs/gnb.log",
        "ue_log": f"{episode_dir}/logs/ue.log",
        "ping_log": f"{episode_dir}/logs/ping.log",
        "core_log": f"{episode_dir}/logs/core.log",
    }


def generate_v3_gnb_overlay(ws_port: int) -> str:
    return (
        "metrics:\n"
        "  enable_json: true\n"
        "  autostart_stdout_metrics: false\n"
        "remote_control:\n"
        "  enabled: true\n"
        "  bind_addr: 127.0.0.1\n"
        f"  port: {ws_port}\n"
        "log:\n"
        "  filename: /stage/logs/gnb_internal.log\n"
        "  all_level: info\n"
    )


def parse_ping_log(text: str) -> dict[str, Any]:
    replies = len(re.findall(r"bytes from ", text))
    transmitted = replies
    received = replies
    loss_percent: float | None = None
    summary = re.search(r"(\d+) packets transmitted, (\d+) received,.*?(\d+(?:\.\d+)?)% packet loss", text)
    if summary:
        transmitted = int(summary.group(1))
        received = int(summary.group(2))
        loss_percent = float(summary.group(3))
    return {
        "packets_transmitted": transmitted,
        "packets_received": received,
        "reply_lines": replies,
        "loss_percent": loss_percent,
        "success_ratio": (received / transmitted) if transmitted else (1.0 if replies else 0.0),
    }


def normalize_metrics_frame(frame: Any) -> dict[str, Any]:
    if not isinstance(frame, dict):
        return {"present": False, "component_keys": [], "timestamp": None, "raw": frame}
    component_keys = sorted(key for key in frame if key != "timestamp")
    return {
        "present": bool(component_keys),
        "component_keys": component_keys,
        "timestamp": frame.get("timestamp"),
        "raw": frame,
    }


def validate_prb_action(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {"valid": False, "reason": "action must be a dictionary", "normalized": None, "request": None}
    if action.get("type") != "SET_PRB_POLICY_RATIO_WS":
        return {"valid": False, "reason": "unsupported action type for v3", "normalized": None, "request": None}

    normalized: dict[str, Any] = {
        "type": "SET_PRB_POLICY_RATIO_WS",
        "plmn": str(action.get("plmn", "00101")),
        "sst": action.get("sst", 1),
        "sd": action.get("sd"),
        "min_prb_policy_ratio": action.get("min_prb_policy_ratio"),
        "max_prb_policy_ratio": action.get("max_prb_policy_ratio"),
        "dedicated_ratio": action.get("dedicated_ratio"),
    }
    for field in ["sst", "min_prb_policy_ratio", "max_prb_policy_ratio"]:
        value = normalized[field]
        if isinstance(value, bool) or not isinstance(value, int):
            return {"valid": False, "reason": f"{field} must be an integer", "normalized": normalized, "request": None}
    for field in ["min_prb_policy_ratio", "max_prb_policy_ratio"]:
        value = normalized[field]
        if value < 0 or value > 100:
            return {"valid": False, "reason": f"{field} must be in [0, 100]", "normalized": normalized, "request": None}
    if normalized["min_prb_policy_ratio"] > normalized["max_prb_policy_ratio"]:
        return {
            "valid": False,
            "reason": "min_prb_policy_ratio must be <= max_prb_policy_ratio",
            "normalized": normalized,
            "request": None,
        }
    if normalized["dedicated_ratio"] is not None:
        value = normalized["dedicated_ratio"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
            return {"valid": False, "reason": "dedicated_ratio must be an integer in [0, 100]", "normalized": normalized, "request": None}
    if normalized["sd"] is not None:
        value = normalized["sd"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 0xFFFFFF:
            return {"valid": False, "reason": "sd must be an integer in [0, 16777215]", "normalized": normalized, "request": None}

    request = build_prb_request(normalized)
    return {"valid": True, "reason": "valid", "normalized": normalized, "request": request}


def build_prb_request(action: dict[str, Any]) -> dict[str, Any]:
    member = {"plmn": action["plmn"], "sst": action["sst"]}
    if action.get("sd") is not None:
        member["sd"] = action["sd"]
    policies = {
        "resourceType": "PRB",
        "rRMPolicyMemberList": [member],
        "min_prb_policy_ratio": action["min_prb_policy_ratio"],
        "max_prb_policy_ratio": action["max_prb_policy_ratio"],
    }
    if action.get("dedicated_ratio") is not None:
        policies["dedicated_ratio"] = action["dedicated_ratio"]
    return {"cmd": "rrm_policy_ratio_set", "policies": policies}


def score_episode(
    ping: dict[str, Any],
    actions: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    cleanup_success: bool,
    unscored_reason: str | None = None,
) -> dict[str, Any]:
    valid_actions = [item for item in actions if item.get("validation", {}).get("valid")]
    accepted_valid = [item for item in valid_actions if item.get("accepted")]
    invalid_actions = [item for item in actions if not item.get("validation", {}).get("valid")]
    rejected_invalid = [item for item in invalid_actions if not item.get("dispatched")]
    metrics_frames = [
        item
        for item in observations
        if item.get("observation", {}).get("metrics", {}).get("present") or item.get("metrics", {}).get("present")
    ]
    setup_failed = bool(unscored_reason)
    scored = not setup_failed and bool(valid_actions) and ping.get("packets_received", 0) > 0 and bool(metrics_frames)
    if not cleanup_success:
        scored = False
        unscored_reason = unscored_reason or "cleanup failed"
    elif not scored:
        if unscored_reason is None and not valid_actions:
            unscored_reason = "no valid actions"
        elif unscored_reason is None and ping.get("packets_received", 0) <= 0:
            unscored_reason = "no successful ping replies"
        elif unscored_reason is None and not metrics_frames:
            unscored_reason = "no metrics observations"
    return {
        "scored": scored,
        "unscored_reason": None if scored else unscored_reason,
        "scores": {
            "valid_action_accepted_rate": (len(accepted_valid) / len(valid_actions)) if valid_actions else 0.0,
            "invalid_local_rejection_correctness": (len(rejected_invalid) / len(invalid_actions)) if invalid_actions else 1.0,
            "ping_success_ratio": ping.get("success_ratio", 0.0),
            "metrics_continuity": len(metrics_frames),
            "clean_teardown": cleanup_success,
        },
        "counts": {
            "actions": len(actions),
            "valid_actions": len(valid_actions),
            "accepted_valid_actions": len(accepted_valid),
            "invalid_actions": len(invalid_actions),
            "locally_rejected_invalid_actions": len(rejected_invalid),
            "observations": len(observations),
            "metrics_frames": len(metrics_frames),
        },
        "ping": ping,
    }


def episode_exit_code(result: dict[str, Any]) -> int:
    if result.get("status") != "ok":
        return 1
    summary = result.get("summary", result)
    return 0 if summary.get("scored") else 1


class EpisodeRuntime:
    def __init__(self, remote: RemoteManager, repo_root: Path | None = None) -> None:
        self.remote = remote
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.options: EpisodeOptions | None = None
        self.paths: dict[str, str] = {}

    def check_docker_assets(self) -> dict[str, Any]:
        payload = {
            "compose": OPEN5GS_COMPOSE,
            "config_dir": E2E_CONFIG_DIR,
            "ocudu_root": self.remote.config.ocudu_root,
            "images": [GNB_IMAGE, SRSUE_IMAGE, "skillful-ran/open5gs:v2.7.0"],
        }
        data = self._remote_json(
            f"""
import json
import pathlib
import shutil
import subprocess
payload = json.loads({json.dumps(json.dumps(payload))})
images = {{}}
for image in payload["images"]:
    proc = subprocess.run(["docker", "image", "inspect", image], check=False, text=True, capture_output=True)
    images[image] = proc.returncode == 0
compose_proc = subprocess.run(["docker", "compose", "version"], check=False, text=True, capture_output=True)
files = {{
    "compose": pathlib.Path(payload["compose"]).is_file(),
    "gnb_config": (pathlib.Path(payload["config_dir"]) / "gnb_zmq.yaml").is_file(),
    "ue_config": (pathlib.Path(payload["config_dir"]) / "ue_zmq.conf").is_file(),
    "gnb_install": (pathlib.Path(payload["ocudu_root"]) / "install" / "srsran-project").is_dir(),
    "ue_install": (pathlib.Path(payload["ocudu_root"]) / "install" / "srsran-4g").is_dir(),
}}
print(json.dumps({{
    "docker": shutil.which("docker") or "",
    "docker_compose": compose_proc.returncode == 0,
    "docker_compose_stdout": compose_proc.stdout.strip(),
    "docker_compose_stderr": compose_proc.stderr.strip(),
    "images": images,
    "files": files,
}}))
"""
        )
        missing = []
        if not data.get("docker"):
            missing.append("docker")
        if not data.get("docker_compose"):
            missing.append("docker compose")
        missing.extend(name for name, ok in data.get("images", {}).items() if not ok)
        missing.extend(name for name, ok in data.get("files", {}).items() if not ok)
        return {
            "status": "pass" if not missing else "fail",
            "summary": "Docker e2e assets are available" if not missing else "Missing Docker e2e assets: " + ", ".join(missing),
            "details": data,
        }

    def start(self, options: EpisodeOptions) -> dict[str, Any]:
        if options.task != TASK_WS_PRB_PING_V1:
            raise ValueError(f"Unsupported v3 task: {options.task}")
        safe_run_id(options.run_id)
        self.options = options
        self.paths = episode_paths(self.remote.config.workspace, options.run_id)
        suffix = container_suffix(options.run_id)
        payload = {
            "run_id": options.run_id,
            "task": options.task,
            "paths": self.paths,
            "compose": OPEN5GS_COMPOSE,
            "config_dir": E2E_CONFIG_DIR,
            "ocudu_root": self.remote.config.ocudu_root,
            "gnb_image": GNB_IMAGE,
            "ue_image": SRSUE_IMAGE,
            "gnb_container": f"skillful-ran-bench-gnb-{suffix}",
            "ue_container": f"skillful-ran-bench-ue-{suffix}",
            "ws_port": options.ws_port,
            "launch_timeout": options.launch_timeout,
            "attach_timeout": options.attach_timeout,
            "overlay": generate_v3_gnb_overlay(options.ws_port),
        }
        data = self._remote_json(
            f"""
import json
import pathlib
import shutil
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})
paths = payload["paths"]

def run(argv, check=False):
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "command failed: " + " ".join(argv))
    return proc

def fail(stage, summary, details=None):
    print(json.dumps({{"status": "error", "stage": stage, "summary": summary, "details": details or {{}}, "paths": paths}}))
    raise SystemExit(0)

def port_listening(port):
    proc = run(["ss", "-ltn"])
    token = ":" + str(port)
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3].endswith(token):
            return True
    return False

episode_dir = pathlib.Path(paths["episode_dir"])
for key in ["configs_dir", "logs_dir"]:
    pathlib.Path(paths[key]).mkdir(parents=True, exist_ok=True)
for path_key in ["actions", "observations", "metrics_raw"]:
    pathlib.Path(paths[path_key]).write_text("", encoding="utf-8")
shutil.copy2(pathlib.Path(payload["config_dir"]) / "gnb_zmq.yaml", paths["gnb_config"])
shutil.copy2(pathlib.Path(payload["config_dir"]) / "ue_zmq.conf", paths["ue_config"])
pathlib.Path(paths["gnb_overlay"]).write_text(payload["overlay"], encoding="utf-8")

run(["docker", "rm", "-f", payload["gnb_container"], payload["ue_container"]])

core = run(["docker", "compose", "-f", payload["compose"], "up", "-d"])
pathlib.Path(paths["core_log"]).write_text(core.stdout + core.stderr, encoding="utf-8")
if core.returncode != 0:
    fail("open5gs_core_health", "Open5GS compose up failed", {{"stderr": core.stderr, "stdout": core.stdout}})

deadline = time.monotonic() + int(payload["launch_timeout"])
core_healthy = False
while time.monotonic() < deadline:
    proc = run(["docker", "inspect", "-f", "{{{{.State.Health.Status}}}}", "skillful_ran_5gc"])
    if proc.returncode == 0 and proc.stdout.strip() == "healthy":
        core_healthy = True
        break
    proc_state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", "skillful_ran_5gc"])
    if proc_state.returncode == 0 and proc_state.stdout.strip() == "exited":
        fail("open5gs_core_health", "Open5GS container exited before health", {{"inspect": proc.stdout, "state": proc_state.stdout}})
    time.sleep(1)
if not core_healthy:
    fail("open5gs_core_health", "Open5GS healthcheck timed out")

if port_listening(int(payload["ws_port"])):
    fail("ocudu_launch", f"port {{payload['ws_port']}} is already listening")

gnb_cmd = (
    "export PATH=/install/bin:$PATH; "
    "export LD_LIBRARY_PATH=/install/lib:${{LD_LIBRARY_PATH:-}}; "
    "gnb -c /config/gnb_zmq.yaml -c /config/gnb_v3_overlay.yaml >/stage/logs/gnb.log 2>&1"
)
gnb = run([
    "docker", "run", "-d", "--name", payload["gnb_container"], "--network", "host",
    "-v", str(pathlib.Path(payload["ocudu_root"]) / "install" / "srsran-project") + ":/install:ro",
    "-v", str(pathlib.Path(paths["configs_dir"])) + ":/config:ro",
    "-v", str(episode_dir) + ":/stage",
    payload["gnb_image"], "bash", "-lc", gnb_cmd,
])
if gnb.returncode != 0:
    fail("ocudu_launch", "gNB container failed to start", {{"stderr": gnb.stderr, "stdout": gnb.stdout}})

deadline = time.monotonic() + int(payload["launch_timeout"])
while time.monotonic() < deadline:
    if port_listening(int(payload["ws_port"])):
        break
    state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["gnb_container"]])
    if state.returncode == 0 and state.stdout.strip() == "exited":
        tail = pathlib.Path(paths["gnb_log"]).read_text(encoding="utf-8", errors="replace")[-4000:] if pathlib.Path(paths["gnb_log"]).exists() else ""
        fail("ocudu_launch", "gNB container exited before WebSocket readiness", {{"tail": tail}})
    time.sleep(0.5)
else:
    fail("ocudu_launch", "gNB WebSocket readiness timed out")

ue_cmd = (
    "mkdir -p /run/netns; ip netns add ue1 2>/dev/null || true; "
    "export PATH=/install/bin:$PATH; "
    "export LD_LIBRARY_PATH=/install/lib:${{LD_LIBRARY_PATH:-}}; "
    "srsue /config/ue_zmq.conf >/stage/logs/ue.log 2>&1"
)
ue = run([
    "docker", "run", "-d", "--name", payload["ue_container"], "--network", "host", "--privileged",
    "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
    "-v", str(pathlib.Path(payload["ocudu_root"]) / "install" / "srsran-4g") + ":/install:ro",
    "-v", str(pathlib.Path(paths["configs_dir"])) + ":/config:ro",
    "-v", str(episode_dir) + ":/stage",
    payload["ue_image"], "bash", "-lc", ue_cmd,
])
if ue.returncode != 0:
    fail("srsue_zmq_attach", "srsUE container failed to start", {{"stderr": ue.stderr, "stdout": ue.stdout}})

deadline = time.monotonic() + int(payload["attach_timeout"])
attached = False
while time.monotonic() < deadline:
    log_text = pathlib.Path(paths["ue_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ue_log"]).exists() else ""
    if "PDU Session Establishment successful" in log_text or "RRC NR reconfiguration successful" in log_text:
        attached = True
        break
    state = run(["docker", "inspect", "-f", "{{{{.State.Status}}}}", payload["ue_container"]])
    if state.returncode == 0 and state.stdout.strip() == "exited":
        fail("srsue_zmq_attach", "srsUE exited before attach", {{"tail": log_text[-4000:]}})
    time.sleep(1)
if not attached:
    log_text = pathlib.Path(paths["ue_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ue_log"]).exists() else ""
    fail("srsue_zmq_attach", "srsUE attach timed out", {{"tail": log_text[-4000:]}})

ping = run([
    "docker", "exec", "-d", payload["ue_container"], "bash", "-lc",
    "ip netns exec ue1 ping -D -i 0.2 10.45.1.1 >/stage/logs/ping.log 2>&1",
])
if ping.returncode != 0:
    fail("ping_traffic_path", "ping command failed to start", {{"stderr": ping.stderr, "stdout": ping.stdout}})

containers = {{
    "open5gs_container": "skillful_ran_5gc",
    "gnb_container": payload["gnb_container"],
    "ue_container": payload["ue_container"],
    "ws_port": payload["ws_port"],
    "started_at": time.time(),
}}
pathlib.Path(paths["containers"]).write_text(json.dumps(containers, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({{
    "status": "ok",
    "stage": "v3_episode",
    "summary": "Docker e2e episode is running",
    "containers": containers,
    "paths": paths,
}}))
"""
        )
        if data.get("status") != "ok":
            self.cleanup(options.run_id)
        return data

    def observe(self) -> dict[str, Any]:
        options = self._require_options()
        payload = {
            "run_id": options.run_id,
            "paths": self.paths,
            "ws_port": options.ws_port,
            "timeout": options.probe_timeout,
        }
        script = self._websocket_client_remote_source()
        script += f"""
import json
import pathlib
import time
payload = json.loads({json.dumps(json.dumps(payload))})
paths = payload["paths"]

def parse_ping(text):
    import re
    replies = len(re.findall(r"bytes from ", text))
    transmitted = replies
    received = replies
    loss = None
    match = re.search(r"(\\d+) packets transmitted, (\\d+) received,.*?(\\d+(?:\\.\\d+)?)% packet loss", text)
    if match:
        transmitted = int(match.group(1))
        received = int(match.group(2))
        loss = float(match.group(3))
    return {{
        "packets_transmitted": transmitted,
        "packets_received": received,
        "reply_lines": replies,
        "loss_percent": loss,
        "success_ratio": (received / transmitted) if transmitted else (1.0 if replies else 0.0),
    }}

ping_text = pathlib.Path(paths["ping_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ping_log"]).exists() else ""
metric = None
metric_error = None
try:
    with WebSocketClient("127.0.0.1", int(payload["ws_port"]), timeout=float(payload["timeout"])) as client:
        client.send_text(json.dumps({{"cmd": "metrics_subscribe"}}))
        deadline = time.monotonic() + float(payload["timeout"])
        while time.monotonic() < deadline:
            text = client.recv_text(timeout=max(0.1, deadline - time.monotonic()))
            if text is None:
                break
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if decoded.get("cmd") == "metrics_subscribe":
                continue
            metric = decoded
            with open(paths["metrics_raw"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps(decoded, sort_keys=True) + "\\n")
            break
except Exception as exc:
    metric_error = str(exc)

last_action = None
actions_path = pathlib.Path(paths["actions"])
if actions_path.exists():
    lines = [line for line in actions_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if lines:
        try:
            last_action = json.loads(lines[-1])
        except json.JSONDecodeError:
            last_action = {{"decode_error": lines[-1]}}

metrics = {{
    "present": isinstance(metric, dict),
    "component_keys": sorted([key for key in metric.keys() if key != "timestamp"]) if isinstance(metric, dict) else [],
    "timestamp": metric.get("timestamp") if isinstance(metric, dict) else None,
    "raw": metric,
    "error": metric_error,
}}
observation = {{
    "type": "ws_prb_ping_v1",
    "timestamp": time.time(),
    "ping": parse_ping(ping_text),
    "metrics": metrics,
    "last_action": last_action,
    "backend": {{"websocket": metric_error is None, "ping": bool(ping_text)}},
}}
record = {{"run_id": payload["run_id"], "state": "running", "observation": observation}}
with open(paths["observations"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
print(json.dumps({{"status": "ok", "stage": "v3_episode", "run_id": payload["run_id"], "state": "running", "observation": observation}}))
"""
        return self._remote_json(script)

    def act(self, action: Any) -> dict[str, Any]:
        options = self._require_options()
        validation = validate_prb_action(action)
        record = {
            "timestamp": time.time(),
            "action": action,
            "validation": validation,
            "dispatched": False,
            "accepted": False,
            "request": validation.get("request"),
            "response": None,
            "reason": validation["reason"],
        }
        if not validation["valid"]:
            self._append_jsonl(self.paths["actions"], record)
            return {
                "status": "rejected",
                "stage": "v3_episode",
                "run_id": options.run_id,
                "accepted": False,
                "reason": validation["reason"],
                "validation": validation,
            }
        payload = {
            "run_id": options.run_id,
            "paths": self.paths,
            "ws_port": options.ws_port,
            "timeout": options.probe_timeout,
            "record": record,
        }
        script = self._websocket_client_remote_source()
        script += f"""
import json
import time
payload = json.loads({json.dumps(json.dumps(payload))})
record = payload["record"]
try:
    with WebSocketClient("127.0.0.1", int(payload["ws_port"]), timeout=float(payload["timeout"])) as client:
        client.send_text(json.dumps(record["request"]))
        raw = client.recv_text(timeout=float(payload["timeout"]))
        response = json.loads(raw) if raw else None
        record["response"] = response
        record["raw_response"] = raw
        record["dispatched"] = True
        record["accepted"] = isinstance(response, dict) and "error" not in response
        record["reason"] = "accepted" if record["accepted"] else (response.get("error") if isinstance(response, dict) else "empty response")
except Exception as exc:
    record["reason"] = str(exc)
record["completed_at"] = time.time()
with open(payload["paths"]["actions"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")
print(json.dumps({{
    "status": "ok" if record["accepted"] else "rejected",
    "stage": "v3_episode",
    "run_id": payload["run_id"],
    "accepted": record["accepted"],
    "reason": record["reason"],
    "request": record["request"],
    "response": record["response"],
    "record": record,
}}))
"""
        return self._remote_json(script)

    def cleanup(self, run_id: str | None = None) -> dict[str, Any]:
        run_id = safe_run_id(run_id or self._require_options().run_id)
        paths = self.paths or episode_paths(self.remote.config.workspace, run_id)
        suffix = container_suffix(run_id)
        payload = {
            "run_id": run_id,
            "paths": paths,
            "compose": OPEN5GS_COMPOSE,
            "gnb_container": f"skillful-ran-bench-gnb-{suffix}",
            "ue_container": f"skillful-ran-bench-ue-{suffix}",
            "ws_port": self.options.ws_port if self.options is not None else DEFAULT_WS_PORT,
        }
        return self._remote_json(
            f"""
import json
import pathlib
import subprocess
import time
payload = json.loads({json.dumps(json.dumps(payload))})
paths = payload["paths"]
commands = []
def run(argv):
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    commands.append({{"argv": argv, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}})
    return proc
def port_listening(port):
    proc = run(["ss", "-ltn"])
    if proc.returncode != 0:
        return None
    token = ":" + str(port)
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3].endswith(token):
            return True
    return False
run(["docker", "exec", payload["ue_container"], "bash", "-lc", "pkill -f 'ping.*10.45.1.1' || true"])
time.sleep(0.5)
run(["docker", "rm", "-f", payload["gnb_container"], payload["ue_container"]])
run(["docker", "compose", "-f", payload["compose"], "down"])
ps = run(["docker", "ps", "-a", "--format", "{{{{.Names}}}}"])
leftover = []
if ps.returncode == 0:
    names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    wanted = {{payload["gnb_container"], payload["ue_container"], "skillful_ran_5gc"}}
    leftover = [name for name in names if name in wanted]
port_open = port_listening(int(payload.get("ws_port", 8001)))
errors = []
if ps.returncode != 0:
    errors.append("unable to inspect docker containers")
if port_open is None:
    errors.append("unable to inspect listening ports")
if leftover:
    errors.append("leftover containers: " + ", ".join(leftover))
if port_open:
    errors.append("WebSocket port is still listening")
status = "error" if errors else "ok"
result = {{
    "status": status,
    "run_id": payload["run_id"],
    "commands": commands,
    "leftover_containers": leftover,
    "ws_port_open": port_open,
    "errors": errors,
    "completed_at": time.time(),
}}
pathlib.Path(paths["episode_dir"]).mkdir(parents=True, exist_ok=True)
pathlib.Path(paths["cleanup"]).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(result))
"""
        )

    def finalize(self, unscored_reason: str | None = None, cleanup_success: bool = True) -> dict[str, Any]:
        options = self._require_options()
        payload = {
            "run_id": options.run_id,
            "paths": self.paths,
            "unscored_reason": unscored_reason,
            "cleanup_success": cleanup_success,
        }
        return self._remote_json(
            f"""
import json
import pathlib
import re
payload = json.loads({json.dumps(json.dumps(payload))})
paths = payload["paths"]
def read_jsonl(path):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    result = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result
def parse_ping(text):
    replies = len(re.findall(r"bytes from ", text))
    transmitted = replies
    received = replies
    loss = None
    match = re.search(r"(\\d+) packets transmitted, (\\d+) received,.*?(\\d+(?:\\.\\d+)?)% packet loss", text)
    if match:
        transmitted = int(match.group(1))
        received = int(match.group(2))
        loss = float(match.group(3))
    return {{
        "packets_transmitted": transmitted,
        "packets_received": received,
        "reply_lines": replies,
        "loss_percent": loss,
        "success_ratio": (received / transmitted) if transmitted else (1.0 if replies else 0.0),
    }}
ping_text = pathlib.Path(paths["ping_log"]).read_text(encoding="utf-8", errors="replace") if pathlib.Path(paths["ping_log"]).exists() else ""
actions = read_jsonl(paths["actions"])
observations = read_jsonl(paths["observations"])
valid_actions = [item for item in actions if item.get("validation", {{}}).get("valid")]
accepted_valid = [item for item in valid_actions if item.get("accepted")]
invalid_actions = [item for item in actions if not item.get("validation", {{}}).get("valid")]
rejected_invalid = [item for item in invalid_actions if not item.get("dispatched")]
metrics_frames = [
    item for item in observations
    if item.get("observation", {{}}).get("metrics", {{}}).get("present") or item.get("metrics", {{}}).get("present")
]
ping = parse_ping(ping_text)
unscored_reason = payload["unscored_reason"]
cleanup_success = bool(payload["cleanup_success"])
scored = (not unscored_reason) and bool(valid_actions) and ping.get("packets_received", 0) > 0 and bool(metrics_frames) and cleanup_success
if not scored and unscored_reason is None:
    if not cleanup_success:
        unscored_reason = "cleanup failed"
    elif not valid_actions:
        unscored_reason = "no valid actions"
    elif ping.get("packets_received", 0) <= 0:
        unscored_reason = "no successful ping replies"
    elif not metrics_frames:
        unscored_reason = "no metrics observations"
summary = {{
    "status": "ok",
    "stage": "v3_episode",
    "task": "ws_prb_ping_v1",
    "run_id": payload["run_id"],
    "scored": scored,
    "unscored_reason": None if scored else unscored_reason,
    "scores": {{
        "valid_action_accepted_rate": (len(accepted_valid) / len(valid_actions)) if valid_actions else 0.0,
        "invalid_local_rejection_correctness": (len(rejected_invalid) / len(invalid_actions)) if invalid_actions else 1.0,
        "ping_success_ratio": ping.get("success_ratio", 0.0),
        "metrics_continuity": len(metrics_frames),
        "clean_teardown": cleanup_success,
    }},
    "counts": {{
        "actions": len(actions),
        "valid_actions": len(valid_actions),
        "accepted_valid_actions": len(accepted_valid),
        "invalid_actions": len(invalid_actions),
        "locally_rejected_invalid_actions": len(rejected_invalid),
        "observations": len(observations),
        "metrics_frames": len(metrics_frames),
    }},
    "ping": ping,
    "artifacts": paths,
}}
pathlib.Path(paths["summary"]).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary))
"""
        )

    def run(
        self,
        options: EpisodeOptions,
        action: dict[str, Any] | None = None,
        unscored_reason: str | None = None,
    ) -> dict[str, Any]:
        action = action or {
            "type": "SET_PRB_POLICY_RATIO_WS",
            "plmn": "00101",
            "sst": 1,
            "sd": 0xFFFFFF,
            "min_prb_policy_ratio": 10,
            "max_prb_policy_ratio": 90,
            "dedicated_ratio": 0,
        }
        try:
            start = self.start(options)
        except Exception as exc:
            self.options = options
            self.paths = episode_paths(self.remote.config.workspace, options.run_id)
            cleanup = self._cleanup_after_error(options.run_id)
            summary = self._finalize_after_error(
                reason=str(exc),
                cleanup_success=cleanup.get("status") == "ok",
            )
            return {"status": "error", "run_id": options.run_id, "error": str(exc), "cleanup": cleanup, "summary": summary}
        if start.get("status") != "ok":
            self.options = options
            self.paths = episode_paths(self.remote.config.workspace, options.run_id)
            cleanup = self._cleanup_after_error(options.run_id)
            summary = self._finalize_after_error(
                reason=start.get("summary", "episode start failed"),
                cleanup_success=cleanup.get("status") == "ok",
            )
            return {"status": "error", "run_id": options.run_id, "start": start, "cleanup": cleanup, "summary": summary}

        observations: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        episode_error: str | None = None
        try:
            observations.append(self.observe())
            actions.append(self.act({"type": "SET_PRB_POLICY_RATIO_WS", "min_prb_policy_ratio": 90, "max_prb_policy_ratio": 10}))
            actions.append(self.act(action))
            deadline = time.monotonic() + max(0, options.duration)
            while time.monotonic() < deadline:
                observations.append(self.observe())
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        except Exception as exc:
            episode_error = str(exc)
        cleanup = self._cleanup_after_error(options.run_id)
        final_unscored_reason = unscored_reason or episode_error
        summary = self._finalize_after_error(
            reason=final_unscored_reason,
            cleanup_success=cleanup.get("status") == "ok",
        )
        if episode_error is not None:
            return {
                "status": "error",
                "run_id": options.run_id,
                "start": start,
                "error": episode_error,
                "actions": actions,
                "observations": observations,
                "cleanup": cleanup,
                "summary": summary,
            }
        return {
            "status": "ok" if summary.get("scored") else "error",
            "run_id": options.run_id,
            "start": start,
            "actions": actions,
            "observations": observations,
            "cleanup": cleanup,
            "summary": summary,
        }

    def _cleanup_after_error(self, run_id: str) -> dict[str, Any]:
        try:
            return self.cleanup(run_id)
        except Exception as exc:
            return {"status": "error", "run_id": run_id, "errors": [str(exc)], "commands": []}

    def _finalize_after_error(self, reason: str | None, cleanup_success: bool) -> dict[str, Any]:
        try:
            return self.finalize(unscored_reason=reason, cleanup_success=cleanup_success)
        except Exception as exc:
            options = self._require_options()
            return {
                "status": "error",
                "stage": "v3_episode",
                "run_id": options.run_id,
                "scored": False,
                "unscored_reason": reason or str(exc),
                "finalize_error": str(exc),
            }

    def _append_jsonl(self, path: str, record: dict[str, Any]) -> None:
        payload = {"path": path, "record": record}
        self._remote_json(
            f"""
import json
import pathlib
payload = json.loads({json.dumps(json.dumps(payload))})
pathlib.Path(payload["path"]).parent.mkdir(parents=True, exist_ok=True)
with open(payload["path"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload["record"], sort_keys=True) + "\\n")
print(json.dumps({{"status": "ok"}}))
"""
        )

    def _require_options(self) -> EpisodeOptions:
        if self.options is None:
            raise RuntimeError("episode runtime has not been started")
        return self.options

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


def run_episode(
    remote: RemoteManager,
    run_id: str | None = None,
    task: str = TASK_WS_PRB_PING_V1,
    duration: int = DEFAULT_EPISODE_DURATION,
    ws_port: int = DEFAULT_WS_PORT,
    launch_timeout: int = DEFAULT_LAUNCH_TIMEOUT,
    attach_timeout: int = DEFAULT_ATTACH_TIMEOUT,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
    unscored_reason: str | None = None,
) -> dict[str, Any]:
    runtime = EpisodeRuntime(remote)
    options = EpisodeOptions(
        run_id=safe_run_id(run_id or default_episode_run_id()),
        task=task,
        duration=duration,
        ws_port=ws_port,
        launch_timeout=launch_timeout,
        attach_timeout=attach_timeout,
        probe_timeout=probe_timeout,
    )
    return runtime.run(options, unscored_reason=unscored_reason)


def cleanup_episode(remote: RemoteManager, run_id: str) -> dict[str, Any]:
    runtime = EpisodeRuntime(remote)
    return runtime.cleanup(safe_run_id(run_id))
