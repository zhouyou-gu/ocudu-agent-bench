"""Multi-model sweep runner for the real-LLM agent harness.

Drives ``benchmark.agents.runner`` over a set of models served one at a time by
vLLM on a remote box. Reachable through an SSH host alias (default ``park-lab``)
and an SSH-forwarded local port (default ``127.0.0.1:8000``).

Scope:

* loops over models; for each, starts vLLM remotely with ``serve-vllm.sh``,
  waits for ``/v1/models`` to return, runs the requested task suites, then
  stops the vLLM process by PID,
* writes one ``<task>.summary.json`` per episode, one
  ``<model>/model_summary.json`` per model, and one ``sweep_matrix.json`` /
  ``sweep_matrix.md`` at the end,
* resumes by skipping any ``<task>.summary.json`` that already exists.

This is a checked-in tool, not a one-off script — the same invocation should
work after a reboot, after a sweep interruption, or against a different
model list.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.agents.backends import build_backend
from benchmark.agents.runner import RealAgentConfig, run_with_real_agent
from benchmark.benchmark_api.task_catalog import load_tasks_for_suite


DEFAULT_SSH_HOST = "park-lab"
DEFAULT_PORT = 8000
DEFAULT_REGISTRY_PATH = "~/skillful-ran-benchmark-workspace/vllm-serve/models.json"
DEFAULT_SERVE_DIR = "~/skillful-ran-benchmark-workspace/vllm-serve"


@dataclass
class SweepConfig:
    models: list[str]
    suites: list[tuple[str, int | None, int]]  # (suite, count, seed)
    output_dir: Path
    ssh_host: str = DEFAULT_SSH_HOST
    local_port: int = DEFAULT_PORT
    remote_port: int = DEFAULT_PORT
    serve_dir: str = DEFAULT_SERVE_DIR
    registry_path: str = DEFAULT_REGISTRY_PATH
    decision_deadline_s: float = 30.0
    step_interval_padding_s: float = 0.5
    request_timeout_s: float = 60.0
    temperature: float = 0.0
    max_tokens: int = 1024
    ready_timeout_s: float = 1800.0
    ready_poll_s: float = 5.0
    stop_timeout_s: float = 120.0
    write_traces: bool = False
    task_ids: list[str] | None = None
    pilot_per_suite: int | None = None
    skip_tier: list[str] = field(default_factory=list)


# ---------- SSH helpers ------------------------------------------------------


def _ssh(host: str, command: str, *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    args = ["ssh", host, command]
    return subprocess.run(args, text=True, check=check, capture_output=capture)


def fetch_registry(cfg: SweepConfig) -> dict[str, Any]:
    """Pull the remote ``models.json`` into a local dict."""
    result = _ssh(cfg.ssh_host, f"cat {cfg.registry_path}")
    return json.loads(result.stdout)


def resolve_models(cfg: SweepConfig, registry: dict[str, Any]) -> list[str]:
    available = list(registry["models"].keys())
    if not cfg.models:
        keys = available
    else:
        keys = []
        for spec in cfg.models:
            if spec.startswith("tier:"):
                tier = spec.split(":", 1)[1].strip().lower()
                keys.extend([k for k, v in registry["models"].items() if v.get("tier") == tier])
            elif spec in registry["models"]:
                keys.append(spec)
            else:
                raise ValueError(f"unknown model key or tier spec: {spec}")
    seen: set[str] = set()
    ordered: list[str] = []
    for k in keys:
        tier = registry["models"][k].get("tier")
        if tier in cfg.skip_tier:
            continue
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def open_tunnel(cfg: SweepConfig) -> None:
    """Add an ``-L`` forward to the existing SSH ControlMaster connection."""
    # Try cancelling any prior forward first; ignore if not present.
    subprocess.run(
        ["ssh", "-O", "cancel", "-L", f"{cfg.local_port}:127.0.0.1:{cfg.remote_port}", cfg.ssh_host],
        text=True, capture_output=True,
    )
    result = subprocess.run(
        ["ssh", "-O", "forward", "-L", f"{cfg.local_port}:127.0.0.1:{cfg.remote_port}", cfg.ssh_host],
        text=True, capture_output=True,
    )
    if result.returncode != 0:
        # Fall back to opening a fresh background tunnel.
        subprocess.run(
            ["ssh", "-fNT", "-L", f"{cfg.local_port}:127.0.0.1:{cfg.remote_port}", cfg.ssh_host],
            text=True, check=True,
        )


def close_tunnel(cfg: SweepConfig) -> None:
    subprocess.run(
        ["ssh", "-O", "cancel", "-L", f"{cfg.local_port}:127.0.0.1:{cfg.remote_port}", cfg.ssh_host],
        text=True, capture_output=True,
    )


# ---------- vLLM lifecycle ---------------------------------------------------


def start_vllm(cfg: SweepConfig, model_key: str) -> int:
    """Start vLLM for ``model_key`` on the remote host. Return the vLLM PID.

    Spawns ``serve-vllm.sh`` via ``nohup`` and then resolves the actual ``vllm
    serve`` PID with ``pgrep -f --newest`` so we have a handle that survives
    after the wrapper shell exits.
    """
    log_path = f"{cfg.serve_dir}/logs/{model_key}.log"
    # Two-step spawn so ssh returns immediately rather than holding a stdio
    # handle open behind a backgrounded subshell:
    #   1. blocking prep (cd, mkdir, truncate prior log) in one ssh call,
    #   2. ``ssh -n -f`` forks ssh itself to the background after auth and
    #      detaches its own stdio, so the local Popen returns immediately
    #      even though the remote vllm keeps running.
    _ssh(
        cfg.ssh_host,
        f"cd {cfg.serve_dir} && mkdir -p logs && : > {log_path}",
    )
    spawn_inner = (
        f"cd {cfg.serve_dir} && "
        f"nohup ./serve-vllm.sh {model_key} > {log_path} 2>&1 < /dev/null"
    )
    # CRITICAL: ssh -n -f daemonizes itself and inherits any subprocess pipes,
    # so it would hold them open indefinitely. Hand it DEVNULL on stdin / stdout
    # / stderr so the background ssh has nothing to keep our Popen waiting on.
    subprocess.run(
        ["ssh", "-n", "-f", cfg.ssh_host, spawn_inner],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    # The vllm serve process appears after the venv activates and the script
    # exec's into the python entry point. Poll until pgrep finds it.
    # The ``[v]llm serve`` trick keeps the literal pattern out of the bash-via-ssh
    # argv so pgrep does not match its own host shell — that footgun is called
    # out explicitly in hosts.local.md.
    pattern = f"[v]llm serve.*--served-model-name {model_key}"
    deadline = time.time() + 60.0
    while time.time() < deadline:
        result = _ssh(
            cfg.ssh_host,
            f"pgrep -f -n {shlex.quote(pattern)} || true",
            check=False,
        )
        pid_text = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if pid_text.isdigit():
            return int(pid_text)
        time.sleep(1.0)
    raise RuntimeError(f"vllm serve for {model_key} did not appear within 60s of spawn")


def wait_for_vllm_ready(cfg: SweepConfig, model_key: str, pid: int) -> None:
    """Block until ``/v1/models`` returns 200 and reports the served name."""
    url = f"http://127.0.0.1:{cfg.local_port}/v1/models"
    started = time.time()
    last_error: str | None = None
    while time.time() - started < cfg.ready_timeout_s:
        if not _remote_pid_alive(cfg, pid):
            tail = _tail_remote_log(cfg, model_key)
            raise RuntimeError(
                f"vLLM PID {pid} for {model_key} exited before becoming ready. "
                f"Last log lines:\n{tail}"
            )
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            served = {m.get("id") for m in data.get("data", [])}
            if model_key in served:
                return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(cfg.ready_poll_s)
    tail = _tail_remote_log(cfg, model_key)
    raise RuntimeError(
        f"vLLM for {model_key} did not become ready within {cfg.ready_timeout_s:.0f}s. "
        f"Last error: {last_error}. Log tail:\n{tail}"
    )


def stop_vllm(cfg: SweepConfig, pid: int) -> None:
    """SIGTERM vLLM by PID; wait for the process and its EngineCore to exit.

    vLLM forwards SIGTERM to its worker subprocesses, so killing the main
    ``vllm serve`` PID is sufficient. After it exits we verify GPU 0 has
    actually released its memory; otherwise we send a targeted SIGKILL to any
    surviving ``VLLM::EngineCore`` whose parent is now ``init``.
    """
    _ssh(cfg.ssh_host, f"kill -TERM {pid} 2>/dev/null || true", check=False)
    started = time.time()
    while time.time() - started < cfg.stop_timeout_s:
        if not _remote_pid_alive(cfg, pid):
            break
        time.sleep(1.0)
    else:
        _ssh(cfg.ssh_host, f"kill -KILL {pid} 2>/dev/null || true", check=False)
    # Reap any orphaned EngineCore (PPID==1) and wait for VRAM to actually free.
    reap_cmd = (
        "for p in $(ps -eo pid,ppid,comm | awk '$2==1 && $3==\"VLLM::EngineCore\" {print $1}'); "
        "do kill -KILL $p 2>/dev/null || true; done"
    )
    _ssh(cfg.ssh_host, reap_cmd, check=False)
    deadline = time.time() + 30.0
    while time.time() < deadline:
        result = _ssh(
            cfg.ssh_host,
            "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1",
            check=False,
        )
        try:
            used_mib = int(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            used_mib = 0
        if used_mib < 1024:  # < 1 GiB means VRAM is effectively free
            return
        time.sleep(2.0)


def _remote_pid_alive(cfg: SweepConfig, pid: int) -> bool:
    result = _ssh(cfg.ssh_host, f"kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD", check=False)
    return "ALIVE" in result.stdout


def _tail_remote_log(cfg: SweepConfig, model_key: str, lines: int = 40) -> str:
    result = _ssh(
        cfg.ssh_host,
        f"tail -n {lines} {cfg.serve_dir}/logs/{shlex.quote(model_key)}.log 2>/dev/null || true",
        check=False,
    )
    return result.stdout


# ---------- sweep core -------------------------------------------------------


def _slim_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Drop heavy nested arrays so the per-task summary stays small."""
    keep = (
        "outcome",
        "failure_category",
        "raw_metrics",
        "component_scores",
        "efficiency",
        "scored",
        "task_id",
        "run_id",
        "stale_metrics_at_decision",
        "evidence_age_at_decision_s",
    )
    return {k: summary[k] for k in keep if k in summary}


def _enumerate_tasks(cfg: SweepConfig) -> list[tuple[str, str, int]]:
    """Return a flat list of (task_id, suite, seed) entries to run, in order."""
    entries: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, int]] = set()
    for suite, count, seed in cfg.suites:
        tasks = load_tasks_for_suite(suite=suite, count=count, seed=seed)
        ordered_ids = sorted(tasks.keys())
        if cfg.pilot_per_suite is not None:
            ordered_ids = ordered_ids[: cfg.pilot_per_suite]
        for task_id in ordered_ids:
            if cfg.task_ids is not None and task_id not in cfg.task_ids:
                continue
            key = (task_id, suite, seed)
            if key in seen:
                continue
            seen.add(key)
            entries.append(key)
    return entries


def run_sweep(cfg: SweepConfig) -> dict[str, Any]:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    registry = fetch_registry(cfg)
    selected_models = resolve_models(cfg, registry)
    task_entries = _enumerate_tasks(cfg)

    (cfg.output_dir / "sweep_config.json").write_text(
        json.dumps(
            {
                "models": selected_models,
                "suites": [{"suite": s, "count": c, "seed": sd} for (s, c, sd) in cfg.suites],
                "ssh_host": cfg.ssh_host,
                "local_port": cfg.local_port,
                "decision_deadline_s": cfg.decision_deadline_s,
                "request_timeout_s": cfg.request_timeout_s,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens,
                "ready_timeout_s": cfg.ready_timeout_s,
                "task_ids": cfg.task_ids,
                "pilot_per_suite": cfg.pilot_per_suite,
                "started_at": time.time(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (cfg.output_dir / "models.snapshot.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(f"[sweep] models={len(selected_models)} tasks_per_model={len(task_entries)} "
          f"total_episodes={len(selected_models) * len(task_entries)}", flush=True)

    open_tunnel(cfg)
    try:
        for model_index, model_key in enumerate(selected_models, start=1):
            model_dir = cfg.output_dir / "per_model" / model_key
            model_dir.mkdir(parents=True, exist_ok=True)
            episodes_remaining = [
                (t, s, sd)
                for (t, s, sd) in task_entries
                if not (model_dir / f"{t}__{s}.summary.json").exists()
            ]
            if not episodes_remaining:
                print(f"[sweep] ({model_index}/{len(selected_models)}) {model_key}: all episodes already complete", flush=True)
                _write_model_aggregate(model_dir, model_key, registry["models"][model_key], task_entries)
                continue
            print(f"[sweep] ({model_index}/{len(selected_models)}) {model_key}: starting vLLM", flush=True)
            t_serve = time.time()
            pid = start_vllm(cfg, model_key)
            try:
                wait_for_vllm_ready(cfg, model_key, pid)
                print(f"[sweep] {model_key}: ready after {time.time() - t_serve:.1f}s (pid={pid})", flush=True)
                _run_model_episodes(cfg, model_key, episodes_remaining, model_dir)
            except Exception as exc:
                err_path = model_dir / "model_error.txt"
                err_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
                print(f"[sweep] {model_key}: ERROR — {exc}", flush=True)
            finally:
                stop_vllm(cfg, pid)
                print(f"[sweep] {model_key}: stopped vLLM ({time.time() - t_serve:.1f}s total)", flush=True)
                _write_model_aggregate(model_dir, model_key, registry["models"][model_key], task_entries)
    finally:
        close_tunnel(cfg)

    matrix = _write_sweep_matrix(cfg, selected_models, task_entries, registry)
    return matrix


def _run_model_episodes(
    cfg: SweepConfig,
    model_key: str,
    episodes: list[tuple[str, str, int]],
    model_dir: Path,
) -> None:
    backend = build_backend(
        provider="custom",
        model=model_key,
        base_url=f"http://127.0.0.1:{cfg.local_port}/v1",
        api_key=None,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        request_timeout_s=cfg.request_timeout_s,
    )
    for ep_index, (task_id, suite, seed) in enumerate(episodes, start=1):
        run_id = f"{model_key}__{task_id}__{suite}__seed{seed}"
        out_path = model_dir / f"{task_id}__{suite}.summary.json"
        trace_dir = model_dir / "traces" if cfg.write_traces else None
        if trace_dir is not None:
            trace_dir.mkdir(parents=True, exist_ok=True)
        episode_config = RealAgentConfig(
            task_id=task_id,
            run_id=run_id,
            seed=seed,
            suite=suite,
            output_dir=trace_dir,
            decision_deadline_s=cfg.decision_deadline_s,
            step_interval_padding_s=cfg.step_interval_padding_s,
        )
        t0 = time.time()
        try:
            result = run_with_real_agent(episode_config, backend)
            summary = _slim_summary(dict(result["summary"]))
            summary.update(
                {
                    "model": model_key,
                    "suite": suite,
                    "seed": seed,
                    "task_id": task_id,
                    "run_id": run_id,
                    "wall_s": round(time.time() - t0, 3),
                }
            )
            out_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
            outcome = summary.get("outcome")
        except Exception as exc:
            out_path.write_text(
                json.dumps(
                    {
                        "model": model_key,
                        "task_id": task_id,
                        "suite": suite,
                        "seed": seed,
                        "outcome": "harness_error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "wall_s": round(time.time() - t0, 3),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            outcome = "harness_error"
        print(f"[sweep]   ({ep_index}/{len(episodes)}) {task_id} [{suite}] "
              f"→ {outcome} in {time.time() - t0:.1f}s", flush=True)


def _load_model_summaries(model_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for path in sorted(model_dir.glob("*.summary.json")):
        try:
            summaries.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return summaries


def _write_model_aggregate(
    model_dir: Path,
    model_key: str,
    model_info: dict[str, Any],
    task_entries: list[tuple[str, str, int]],
) -> None:
    summaries = _load_model_summaries(model_dir)
    by_outcome: dict[str, int] = {}
    total_tokens = 0
    total_latency_s = 0.0
    component_score_means: dict[str, list[float]] = {}
    for summary in summaries:
        outcome = summary.get("outcome", "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        eff = summary.get("efficiency") or {}
        total_tokens += int(eff.get("total_tokens", 0) or 0)
        total_latency_s += float(eff.get("total_decision_latency_s", 0.0) or 0.0)
        for k, v in (summary.get("component_scores") or {}).items():
            try:
                component_score_means.setdefault(k, []).append(float(v))
            except (TypeError, ValueError):
                continue
    aggregate = {
        "model": model_key,
        "tier": model_info.get("tier"),
        "repo": model_info.get("repo"),
        "episode_count": len(summaries),
        "episodes_expected": len(task_entries),
        "by_outcome": by_outcome,
        "success_rate": round(by_outcome.get("success", 0) / max(len(summaries), 1), 4),
        "tokens_total": total_tokens,
        "latency_total_s": round(total_latency_s, 3),
        "component_score_mean": {k: round(sum(v) / len(v), 4) for k, v in component_score_means.items()},
    }
    (model_dir / "model_summary.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8"
    )


def _write_sweep_matrix(
    cfg: SweepConfig,
    models: list[str],
    task_entries: list[tuple[str, str, int]],
    registry: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for model_key in models:
        path = cfg.output_dir / "per_model" / model_key / "model_summary.json"
        if not path.exists():
            continue
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    matrix = {
        "started_at": json.loads((cfg.output_dir / "sweep_config.json").read_text(encoding="utf-8"))["started_at"],
        "ended_at": time.time(),
        "episode_count_per_model": len(task_entries),
        "model_summaries": rows,
    }
    (cfg.output_dir / "sweep_matrix.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    (cfg.output_dir / "sweep_matrix.md").write_text(_render_matrix_md(matrix), encoding="utf-8")
    return matrix


def _render_matrix_md(matrix: dict[str, Any]) -> str:
    lines = ["# Sweep matrix", ""]
    lines.append(f"- episodes per model: {matrix['episode_count_per_model']}")
    lines.append("")
    lines.append("| Model | Tier | Episodes | Success | Success rate | Tokens | Latency (s) |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in matrix["model_summaries"]:
        lines.append(
            "| {model} | {tier} | {n} | {s} | {sr:.3f} | {tok} | {lat:.1f} |".format(
                model=row["model"],
                tier=row.get("tier") or "",
                n=row["episode_count"],
                s=row["by_outcome"].get("success", 0),
                sr=row["success_rate"],
                tok=row["tokens_total"],
                lat=row["latency_total_s"],
            )
        )
    return "\n".join(lines) + "\n"


# ---------- CLI --------------------------------------------------------------


def _parse_suites(specs: list[str]) -> list[tuple[str, int | None, int]]:
    """Parse ``--suite name[:count][:seed]`` repeated specs into (suite, count, seed)."""
    parsed: list[tuple[str, int | None, int]] = []
    for spec in specs:
        parts = spec.split(":")
        name = parts[0]
        count = int(parts[1]) if len(parts) > 1 and parts[1] else None
        seed = int(parts[2]) if len(parts) > 2 and parts[2] else 1
        parsed.append((name, count, seed))
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a multi-model sweep against the real-LLM agent harness.")
    parser.add_argument("--model", action="append", default=[],
                        help="Model key from the registry, or 'tier:<name>'. Repeatable. Default: all.")
    parser.add_argument("--suite", action="append", default=[],
                        help="Suite spec 'name[:count][:seed]'. Repeatable.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    parser.add_argument("--local-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--remote-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--decision-deadline-s", type=float, default=30.0)
    parser.add_argument("--step-interval-padding-s", type=float, default=0.5)
    parser.add_argument("--request-timeout-s", type=float, default=60.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--ready-timeout-s", type=float, default=1800.0)
    parser.add_argument("--write-traces", action="store_true",
                        help="Also persist per-episode trace.json files.")
    parser.add_argument("--task-id", action="append", default=None,
                        help="Restrict to specific task ids. Repeatable.")
    parser.add_argument("--pilot-per-suite", type=int, default=None,
                        help="Limit each suite to its first N alphabetically-sorted tasks.")
    parser.add_argument("--skip-tier", action="append", default=[],
                        help="Skip models whose registry tier matches. Repeatable.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    suites = _parse_suites(args.suite) if args.suite else [("base", None, 1)]
    cfg = SweepConfig(
        models=args.model,
        suites=suites,
        output_dir=Path(args.output_dir),
        ssh_host=args.ssh_host,
        local_port=args.local_port,
        remote_port=args.remote_port,
        decision_deadline_s=args.decision_deadline_s,
        step_interval_padding_s=args.step_interval_padding_s,
        request_timeout_s=args.request_timeout_s,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        ready_timeout_s=args.ready_timeout_s,
        write_traces=args.write_traces,
        task_ids=args.task_id,
        pilot_per_suite=args.pilot_per_suite,
        skip_tier=args.skip_tier,
    )
    matrix = run_sweep(cfg)
    print(json.dumps({"summary_count": len(matrix["model_summaries"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
