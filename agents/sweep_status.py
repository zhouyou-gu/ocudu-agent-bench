"""Probe the live state of a sweep run from its output directory.

Reads ``sweep_config.json`` plus each ``<task>__<suite>.summary.json`` and
``model_summary.json`` and prints a compact status snapshot:

* sweep elapsed time and configured scope
* per-model status: pending / running / done, with running success-rate so far
* aggregate progress: episodes completed / total, total tokens, mean wall

Run repeatedly to track a long sweep. No filesystem mutation, no SSH.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read_sweep_state(output_dir: Path) -> dict[str, Any]:
    config_path = output_dir / "sweep_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"no sweep_config.json under {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    models: list[str] = list(config.get("models") or [])
    suites: list[dict[str, Any]] = list(config.get("suites") or [])
    started_at: float | None = config.get("started_at")

    per_model_dir = output_dir / "per_model"
    model_states: list[dict[str, Any]] = []
    aggregate_tokens = 0
    aggregate_episodes = 0
    aggregate_success = 0
    aggregate_wall = 0.0

    for model in models:
        model_dir = per_model_dir / model
        summaries = _load_summaries(model_dir)
        aggregate_summary = _load_aggregate(model_dir)
        error_text = _load_text(model_dir / "model_error.txt")
        status = _classify_status(model_dir, summaries, error_text)
        success = sum(1 for s in summaries if s.get("outcome") == "success")
        failed = sum(1 for s in summaries if s.get("outcome") == "agent_failure")
        harness_errors = sum(1 for s in summaries if s.get("outcome") == "harness_error")
        other = len(summaries) - success - failed - harness_errors
        tokens = sum(int((s.get("efficiency") or {}).get("total_tokens", 0) or 0) for s in summaries)
        wall = sum(float(s.get("wall_s", 0.0) or 0.0) for s in summaries)
        last_summary = summaries[-1] if summaries else None
        model_states.append(
            {
                "model": model,
                "status": status,
                "episodes_done": len(summaries),
                "success": success,
                "agent_failure": failed,
                "harness_error": harness_errors,
                "other": other,
                "tokens_total": tokens,
                "wall_total_s": round(wall, 1),
                "last_task": last_summary.get("task_id") if last_summary else None,
                "last_outcome": last_summary.get("outcome") if last_summary else None,
                "model_error": error_text,
                "aggregate_present": aggregate_summary is not None,
            }
        )
        aggregate_tokens += tokens
        aggregate_episodes += len(summaries)
        aggregate_success += success
        aggregate_wall += wall

    matrix_path = output_dir / "sweep_matrix.json"
    finished = matrix_path.exists()
    elapsed_s = (time.time() - started_at) if started_at else None
    return {
        "output_dir": str(output_dir),
        "config": config,
        "models": model_states,
        "suites": suites,
        "started_at": started_at,
        "elapsed_s": elapsed_s,
        "finished": finished,
        "matrix_path": str(matrix_path) if finished else None,
        "aggregate": {
            "models_total": len(models),
            "models_done": sum(1 for m in model_states if m["status"] == "done"),
            "models_running": sum(1 for m in model_states if m["status"] == "running"),
            "models_pending": sum(1 for m in model_states if m["status"] == "pending"),
            "episodes_done": aggregate_episodes,
            "episodes_success": aggregate_success,
            "tokens_total": aggregate_tokens,
            "wall_total_s": round(aggregate_wall, 1),
        },
    }


def _classify_status(model_dir: Path, summaries: list[dict[str, Any]], error_text: str | None) -> str:
    if not model_dir.exists():
        return "pending"
    if error_text:
        return "error"
    if (model_dir / "model_summary.json").exists() and summaries:
        return "done"
    if summaries:
        return "running"
    return "pending"


def _load_summaries(model_dir: Path) -> list[dict[str, Any]]:
    if not model_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(model_dir.glob("*.summary.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def _load_aggregate(model_dir: Path) -> dict[str, Any] | None:
    path = model_dir / "model_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def render_text(state: dict[str, Any]) -> str:
    lines: list[str] = []
    elapsed = state["elapsed_s"]
    elapsed_str = _fmt_duration(elapsed) if elapsed is not None else "?"
    finished_str = "FINISHED" if state["finished"] else "RUNNING"
    agg = state["aggregate"]
    suites = ", ".join(
        f"{s['suite']}({s.get('count') or 'default'})" for s in state.get("suites", [])
    ) or "(none)"
    lines.append(f"sweep status: {finished_str}  elapsed: {elapsed_str}  output: {state['output_dir']}")
    lines.append(f"suites: {suites}")
    lines.append(
        f"models: {agg['models_done']} done / {agg['models_running']} running / {agg['models_pending']} pending"
        f" (total {agg['models_total']})"
    )
    lines.append(
        f"episodes: {agg['episodes_done']} done, {agg['episodes_success']} success"
        f" | tokens: {agg['tokens_total']:,} | wall: {_fmt_duration(agg['wall_total_s'])}"
    )
    lines.append("")
    lines.append(f"{'model':<32} {'status':<8} {'eps':>5} {'ok':>4} {'fail':>5} {'err':>4} {'tok':>10} {'last task / outcome'}")
    for m in state["models"]:
        lines.append(
            f"{m['model']:<32} {m['status']:<8} {m['episodes_done']:>5} {m['success']:>4} "
            f"{m['agent_failure']:>5} {m['harness_error']:>4} {m['tokens_total']:>10,} "
            f"{m.get('last_task') or '-'} / {m.get('last_outcome') or '-'}"
        )
        if m.get("model_error"):
            short = m["model_error"].splitlines()[0][:140]
            lines.append(f"  ! error: {short}")
    if state["finished"]:
        lines.append("")
        lines.append(f"matrix written: {state['matrix_path']}")
    return "\n".join(lines) + "\n"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m{int(sec):02d}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h{int(minutes):02d}m"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe a sweep output directory for live status.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true", help="Emit the raw JSON state instead of the table.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    state = read_sweep_state(Path(args.output_dir))
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True, default=str))
    else:
        sys.stdout.write(render_text(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
