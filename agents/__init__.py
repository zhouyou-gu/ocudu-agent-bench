"""Real-LLM agent harness for OCUDUAgentBench.

This package implements an `AgentCallable` (see `benchmark/agents/README.md`) on
top of either an OpenAI-compatible HTTP endpoint (commercial API or a local
server such as Ollama / vLLM / llama.cpp) or the Anthropic Messages API.

The benchmark core is unchanged. The runner under `runner.py` clones the
selected task with a relaxed `U.timing_policy` so LLM-scale decision latency
fits inside the per-step budget.
"""

from agents.backends import (
    AnthropicBackend,
    EchoBackend,
    LLMBackend,
    LLMResponse,
    OpenAICompatibleBackend,
)
from agents.llm_agent import LLMAgent
from agents.runner import RealAgentConfig, run_agent_episode

__all__ = [
    "AnthropicBackend",
    "EchoBackend",
    "LLMAgent",
    "LLMBackend",
    "LLMResponse",
    "OpenAICompatibleBackend",
    "RealAgentConfig",
    "run_agent_episode",
]
