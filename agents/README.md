# Real-LLM Agent Harness

`benchmark/agents/` plugs a real LLM-backed agent into the benchmark while
leaving `benchmark/benchmark_api/` unchanged. The benchmark's agent contract is
a plain `Callable[[dict], dict]`; this package implements one such callable
([`LLMAgent`](llm_agent.py)) that issues exactly one chat completion per step.

Two deployment modes are supported:

| Mode | Backend | Typical use |
| --- | --- | --- |
| **A. Self-hosted LLM** | OpenAI-compatible HTTP at any base URL | One of your own PCs running Ollama / vLLM / llama.cpp / LM Studio, locally or over your LAN |
| **B. Commercial API** | OpenAI Chat Completions, Anthropic Messages, or any OpenAI-compatible aggregator | OpenAI, Anthropic, OpenRouter, Together, Groq, Fireworks, Mistral, DeepSeek |

Both modes go through the same `LLMAgent` and runner — only the backend differs.

## Layout

```text
benchmark/agents/
├── README.md           # this file
├── __init__.py
├── action_schemas.py   # compact per-action cheatsheets used in prompts
├── backends.py         # OpenAICompatibleBackend, AnthropicBackend, EchoBackend
├── llm_agent.py        # the AgentCallable
├── prompt.py           # message construction + response parsing
└── runner.py           # task-clone + episode runner + CLI
```

Tests live alongside the rest of the benchmark suite at
`benchmark/tests/agents/`. They use `EchoBackend` and do not touch the network.

## Why a separate runner

Checked-in task manifests set `U.timing_policy.decision_deadline_s = 0.01s` —
the deterministic baselines in `benchmark/benchmark_api/controller.py` respond
in microseconds. Any real LLM call is orders of magnitude slower, so
[`runner.relax_task_clock`](runner.py) clones the selected task with
`step_interval_s = decision_deadline_s + padding` before scoring. The benchmark
core does not need any changes.

The wrapper still enforces the deadline: if the LLM call exceeds it, the step
is automatically converted to `NO_ACTION` and tagged `telemetry.timed_out = True`.

## Quick start — commercial API (mode B)

```bash
export OPENAI_API_KEY=sk-...
python3 benchmark/agents/runner.py \
    --provider openai \
    --model gpt-4o-mini \
    --api-key-env OPENAI_API_KEY \
    --suite base \
    --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 30 \
    --output-dir /tmp/ocuduagentbench_real \
    --json
```

Anthropic:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 benchmark/agents/runner.py \
    --provider anthropic \
    --model claude-3-7-sonnet-latest \
    --api-key-env ANTHROPIC_API_KEY \
    --suite base \
    --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 30
```

Other OpenAI-compatible aggregators use the same shape — only `--provider`,
`--model`, and `--api-key-env` change. Supported short names: `openai`,
`anthropic`, `openrouter`, `together`, `groq`, `fireworks`, `mistral`,
`deepseek`, `ollama`, `vllm`, `llama_cpp`, `lm_studio`, `custom`.

## Quick start — self-hosted LLM (mode A)

Pick one server below, run it on the PC that will host the model, then point
`--provider` at the matching short name. No API key is required for local
servers, though some accept a placeholder Bearer token.

### Reference serving suggestions

| Server | Install | Start command | Default base URL |
| --- | --- | --- | --- |
| **Ollama** (easiest) | `brew install ollama` / [ollama.com](https://ollama.com) | `ollama serve` then `ollama pull llama3.1:8b` | `http://localhost:11434/v1` |
| **llama.cpp server** | `brew install llama.cpp` or build from source | `llama-server -m model.gguf -c 8192 --port 8080` | `http://localhost:8080/v1` |
| **LM Studio** | [lmstudio.ai](https://lmstudio.ai) | Enable the "Local Server" tab inside the app | `http://localhost:1234/v1` |
| **vLLM** (GPU) | `pip install vllm` | `vllm serve meta-llama/Meta-Llama-3.1-8B-Instruct --port 8000` | `http://localhost:8000/v1` |
| **TabbyAPI** (GPU) | `pip install tabbyapi` | follow the project README; OpenAI-compatible | `http://localhost:5000/v1` |

Recommendation for first try on a developer Mac/PC without a GPU: **Ollama**
with a 7B-class instruct model (`llama3.1:8b`, `qwen2.5:7b-instruct`, or
`mistral-nemo:12b-instruct`). All three respond in ~5–20 s per step on Apple
silicon, which fits well inside the default 30 s decision deadline.

Recommendation for a GPU box (yours or LAN-reachable): **vLLM** with
`Meta-Llama-3.1-8B-Instruct` or `Qwen/Qwen2.5-14B-Instruct`. Throughput is
high enough to run the full 34-task checked-in suite in minutes.

### Running against a local server

Local (same machine):

```bash
ollama serve &
ollama pull llama3.1:8b
python3 benchmark/agents/runner.py \
    --provider ollama \
    --model llama3.1:8b \
    --suite base \
    --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 60 \
    --output-dir /tmp/ocuduagentbench_real
```

Remote LAN server (override the base URL):

```bash
python3 benchmark/agents/runner.py \
    --provider custom \
    --base-url http://10.34.23.184:11434/v1 \
    --model llama3.1:8b \
    --suite base \
    --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 60
```

vLLM with explicit base URL:

```bash
python3 benchmark/agents/runner.py \
    --provider vllm \
    --model meta-llama/Meta-Llama-3.1-8B-Instruct \
    --suite base \
    --task base_prb_slice_congestion_rebalance_v1 \
    --decision-deadline-s 60
```

## CLI reference (`runner.py`)

| Flag | Meaning |
| --- | --- |
| `--task` | Task id to run. |
| `--suite` | `base` / `regression` / `compound` / `all_checked_in` / `generated` / `standard` / `diagnostic` / `stress`. Default `base`. |
| `--seed`, `--count`, `--family` | Forwarded to `task_catalog` for generated suites. |
| `--run-id` | Run id; defaults to `<task>-real-<unix>`. |
| `--output-dir` | If set, writes `<run_id>.trace.json` and `<run_id>.summary.json`. |
| `--provider`, `--model`, `--base-url`, `--api-key-env` | Backend selection. |
| `--temperature`, `--max-tokens`, `--request-timeout-s` | Forwarded to the backend. |
| `--decision-deadline-s` | LLM time budget per step (default 30 s). The wrapper auto-converts overruns into NO_ACTION. |
| `--step-interval-padding-s` | Buffer between decision deadline and step boundary (default 0.5 s). |
| `--json` | Print the full result envelope as JSON. |

Exit code is `0` on `outcome == "success"`, `1` otherwise.

## How a single step works

```text
┌────────────────────────────┐      ┌────────────────────────┐
│ agent_api_wrapper.py       │      │ benchmark/agents       │
│ (benchmark/benchmark_api/) │──┐   │                        │
│                            │  │   │   LLMAgent(payload) ───┼─▶ build_messages
│   request_decision(obs)    │  │   │           │            │       │
│   enforces decision_       │  │   │           ▼            │       ▼
│   deadline_s               │◀─┘   │   backend.complete()   │   chat completion
│                            │      │           │            │
│   returns AgentDecision    │      │           ▼            │
└────────────────────────────┘      │   parse_decision(text) │
                                    │           │            │
                                    │           ▼            │
                                    │   {decision, telemetry}│
                                    └────────────────────────┘
```

Telemetry per step carries `prompt_tokens`, `completion_tokens`,
`reasoning_tokens`, `total_tokens`, `decision_latency_s`, `model`,
`provider`, `parse_status`, `timed_out`, and `malformed`. These are written
into the trace and rolled up into `summary.efficiency` by
`benchmark/benchmark_api/scoring.py`. They do not affect correctness.

## Prompt contract

* **System message**: role, JSON-only output requirement, and the action
  cheatsheet filtered to this task's allowed action types
  ([`action_schemas.py`](action_schemas.py)).
* **User message**: goal, public constraints, observation sources, current
  step id, current observation, previous feedback, decision timeout.
* **Expected reply**: a single JSON object with a top-level `decision` field
  that is either `null` (NO_ACTION) or an action object with a `type`. An
  optional `rationale` string is captured into telemetry, capped at 400 chars.

The parser accepts bare JSON, fenced JSON code blocks, and the literal
`null`. Anything else is treated as `NO_ACTION` with
`telemetry.parse_status` set to one of `empty`, `no_json`, `invalid_json`,
`unrecognized_shape`, or `transport_error`.

## Testing without a network

```bash
python3 -m unittest discover benchmark/tests/agents
```

The smoke test uses `EchoBackend(scripted_text=...)` so it covers the prompt
builder, the parser, and the runner end-to-end without calling any remote
service.

## Boundaries

* **No new dependencies.** `urllib.request` + `json` only.
* **No core changes.** `benchmark/benchmark_api/` is untouched. The runner
  re-implements one episode against a relaxed task clone, so everything else
  flows through the existing modules (observation, action, stimulus,
  scoring, trace).
* **Same privacy envelope.** The agent receives only the payload built by
  `agent_api_wrapper.py`. `M`, `E`, `U`, `J`, oracle records, and stimulus
  schedules stay private.
* **Failure handling.** Backend transport errors and parse failures become
  `NO_ACTION` with descriptive telemetry; the run continues to scoring.
