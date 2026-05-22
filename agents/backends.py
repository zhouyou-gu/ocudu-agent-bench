"""LLM backend adapters.

Three backends are provided, all on the Python standard library only:

* ``OpenAICompatibleBackend`` — talks to any service exposing the OpenAI Chat
  Completions schema. Covers the OpenAI API, OpenRouter, Together, Groq,
  Fireworks, Mistral, DeepSeek, Ollama (``/v1``), vLLM, llama.cpp server,
  LM Studio, and TabbyAPI.
* ``AnthropicBackend`` — talks to the Anthropic Messages API.
* ``EchoBackend`` — offline stub used by unit tests; returns the response
  string pre-loaded at construction time without making any network call.

A backend implements one method, ``complete(messages) -> LLMResponse``. The
``LLMResponse`` carries the model text plus token usage; the agent wrapper
forwards the latter into the benchmark's efficiency metrics.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    model: str
    provider: str
    raw: dict[str, Any] | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens + self.reasoning_tokens


class LLMBackend(Protocol):
    """Protocol every backend implements."""

    name: str
    model: str

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        ...


# ---------- OpenAI-compatible -------------------------------------------------


@dataclass
class OpenAICompatibleBackend:
    """OpenAI Chat Completions client.

    Works with the OpenAI hosted API and with any local server that copies
    the same schema (Ollama, vLLM, llama.cpp server, LM Studio, ...).
    """

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    name: str = "openai_compatible"
    temperature: float = 0.0
    max_tokens: int = 1024
    request_timeout_s: float = 60.0
    extra_headers: dict[str, str] = field(default_factory=dict)

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        api_key = self.api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(self.extra_headers)
        payload = _http_json_request(url, body, headers, self.request_timeout_s)
        text = _extract_openai_text(payload)
        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            reasoning_tokens=int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
            ),
            model=str(payload.get("model", self.model)),
            provider=self.name,
            raw=payload,
        )


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(parts)
    return ""


# ---------- Anthropic Messages ------------------------------------------------


@dataclass
class AnthropicBackend:
    """Anthropic Messages API client.

    Uses the public ``/v1/messages`` endpoint. The ``messages`` argument is the
    same OpenAI-style chat list; the system message is hoisted into the
    Anthropic ``system`` parameter and the remaining messages are translated.
    """

    model: str
    base_url: str = "https://api.anthropic.com/v1"
    api_key: str | None = None
    anthropic_version: str = "2023-06-01"
    name: str = "anthropic"
    temperature: float = 0.0
    max_tokens: int = 1024
    request_timeout_s: float = 60.0
    extra_headers: dict[str, str] = field(default_factory=dict)

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        chat_messages = [m for m in messages if m.get("role") != "system"]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in chat_messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set and no api_key was passed")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": api_key,
            "anthropic-version": self.anthropic_version,
        }
        headers.update(self.extra_headers)
        url = self.base_url.rstrip("/") + "/messages"
        payload = _http_json_request(url, body, headers, self.request_timeout_s)
        text_parts = [
            part.get("text", "")
            for part in payload.get("content", [])
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        usage = payload.get("usage") or {}
        return LLMResponse(
            text="".join(text_parts),
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
            reasoning_tokens=0,
            model=str(payload.get("model", self.model)),
            provider=self.name,
            raw=payload,
        )


# ---------- Offline echo stub -------------------------------------------------


@dataclass
class EchoBackend:
    """Returns a pre-loaded response text without doing any I/O.

    Used by unit tests and offline smoke checks. Construct with the raw model
    output you want the parser to see.
    """

    scripted_text: str
    model: str = "echo"
    name: str = "echo"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    call_log: list[list[dict[str, str]]] = field(default_factory=list)

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        self.call_log.append([dict(m) for m in messages])
        return LLMResponse(
            text=self.scripted_text,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=self.reasoning_tokens,
            model=self.model,
            provider=self.name,
            raw={"echo": True},
        )


# ---------- Shared HTTP helper ------------------------------------------------


class LLMTransportError(RuntimeError):
    pass


def _http_json_request(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout_s: float,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise LLMTransportError(f"HTTP {exc.code} from {url}: {detail[:400]}") from exc
    except urllib.error.URLError as exc:
        raise LLMTransportError(f"Could not reach {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise LLMTransportError(f"Network error to {url} after {time.time() - started:.1f}s: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMTransportError(f"Non-JSON response from {url}: {raw[:400]}") from exc


# ---------- Convenience factory ----------------------------------------------


def build_backend(
    provider: str,
    *,
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    request_timeout_s: float = 60.0,
) -> LLMBackend:
    """Construct a backend by short provider name.

    Supported names (case-insensitive):

    * ``openai``         — OpenAI hosted API
    * ``anthropic``      — Anthropic Messages API
    * ``openrouter``     — OpenRouter (OpenAI-compatible)
    * ``together``, ``groq``, ``fireworks``, ``mistral``, ``deepseek``
                        — public OpenAI-compatible aggregators
    * ``ollama``         — local Ollama at ``http://localhost:11434/v1``
    * ``vllm``           — local vLLM at ``http://localhost:8000/v1``
    * ``llama_cpp``      — local llama.cpp server at ``http://localhost:8080/v1``
    * ``lm_studio``      — local LM Studio at ``http://localhost:1234/v1``
    * ``custom``         — any OpenAI-compatible server; pass ``base_url``
    """

    key = provider.strip().lower()
    if key == "anthropic":
        return AnthropicBackend(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            request_timeout_s=request_timeout_s,
        )
    defaults: dict[str, str] = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "together": "https://api.together.xyz/v1",
        "groq": "https://api.groq.com/openai/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "mistral": "https://api.mistral.ai/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "ollama": "http://localhost:11434/v1",
        "vllm": "http://localhost:8000/v1",
        "llama_cpp": "http://localhost:8080/v1",
        "lm_studio": "http://localhost:1234/v1",
        "custom": "",
    }
    if key not in defaults:
        raise ValueError(f"Unknown provider: {provider!r}")
    resolved_base = base_url or defaults[key]
    if not resolved_base:
        raise ValueError("custom provider requires base_url")
    return OpenAICompatibleBackend(
        model=model,
        base_url=resolved_base,
        api_key=api_key,
        name=key,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout_s=request_timeout_s,
    )
