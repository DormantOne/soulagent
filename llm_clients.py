from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


class LLMError(Exception):
    pass


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-5.5"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.2
    timeout: int = 90


def _split_system(messages: List[Dict[str, str]]) -> tuple[str, List[Dict[str, str]]]:
    system_parts = []
    rest = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content", ""))
        else:
            rest.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    return "\n\n".join(system_parts), rest


def call_llm(messages: List[Dict[str, str]], cfg: LLMConfig) -> str:
    provider = (cfg.provider or "openai").lower().strip()
    if provider == "openai":
        return _call_openai_responses(messages, cfg)
    if provider == "anthropic":
        return _call_anthropic(messages, cfg)
    if provider == "ollama":
        return _call_ollama(messages, cfg)
    if provider in {"openai_compatible", "compatible", "local"}:
        return _call_openai_compatible(messages, cfg)
    raise LLMError(f"Unknown provider: {cfg.provider}")


def _call_openai_responses(messages: List[Dict[str, str]], cfg: LLMConfig) -> str:
    api_key = cfg.api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise LLMError("Missing OpenAI API key. Enter it in the GUI or set OPENAI_API_KEY.")
    url = (cfg.base_url.rstrip("/") if cfg.base_url else "https://api.openai.com/v1") + "/responses"
    # Responses API accepts an input array with role/content objects.
    payload: Dict[str, Any] = {
        "model": cfg.model or os.getenv("OPENAI_MODEL", "gpt-5.5"),
        "input": messages,
        "temperature": cfg.temperature,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=cfg.timeout)
    # Some reasoning models reject temperature. Retry once without it.
    if resp.status_code >= 400 and "temperature" in resp.text.lower():
        payload.pop("temperature", None)
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg.timeout)
    if resp.status_code >= 400:
        raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text[:1000]}")
    data = resp.json()
    if data.get("output_text"):
        return data["output_text"]
    # Fallback extraction for varied Responses output shapes.
    chunks: List[str] = []
    for item in data.get("output", []) or []:
        for c in item.get("content", []) or []:
            if c.get("type") in {"output_text", "text"} and c.get("text"):
                chunks.append(c["text"])
    if chunks:
        return "\n".join(chunks)
    raise LLMError("Could not extract text from OpenAI response: " + json.dumps(data)[:1000])


def _call_anthropic(messages: List[Dict[str, str]], cfg: LLMConfig) -> str:
    api_key = cfg.api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise LLMError("Missing Anthropic API key. Enter it in the GUI or set ANTHROPIC_API_KEY.")
    system, rest = _split_system(messages)
    # Anthropic supports only user/assistant messages; merge consecutive same-role chunks.
    cleaned: List[Dict[str, str]] = []
    for m in rest:
        role = "assistant" if m["role"] == "assistant" else "user"
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1]["content"] += "\n\n" + m["content"]
        else:
            cleaned.append({"role": role, "content": m["content"]})
    payload: Dict[str, Any] = {
        "model": cfg.model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        "max_tokens": 4096,
        "temperature": cfg.temperature,
        "system": system,
        "messages": cleaned or [{"role": "user", "content": "Hello"}],
    }
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=cfg.timeout,
    )
    if resp.status_code >= 400:
        raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text[:1000]}")
    data = resp.json()
    chunks = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
    if chunks:
        return "\n".join(chunks)
    raise LLMError("Could not extract text from Anthropic response: " + json.dumps(data)[:1000])


def _call_ollama(messages: List[Dict[str, str]], cfg: LLMConfig) -> str:
    base = (cfg.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    payload = {
        "model": cfg.model or os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
        "messages": messages,
        "stream": False,
        "options": {"temperature": cfg.temperature},
    }
    resp = requests.post(f"{base}/api/chat", json=payload, timeout=cfg.timeout)
    if resp.status_code >= 400:
        raise LLMError(f"Ollama error {resp.status_code}: {resp.text[:1000]}")
    data = resp.json()
    return data.get("message", {}).get("content", "")


def _call_openai_compatible(messages: List[Dict[str, str]], cfg: LLMConfig) -> str:
    base = (cfg.base_url or "http://localhost:8000/v1").rstrip("/")
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
    }
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    resp = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=cfg.timeout)
    if resp.status_code >= 400:
        raise LLMError(f"OpenAI-compatible API error {resp.status_code}: {resp.text[:1000]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise LLMError("Could not extract chat completion text: " + json.dumps(data)[:1000]) from e
