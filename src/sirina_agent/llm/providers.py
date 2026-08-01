from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import httpx

from .openai_auth import find_codex_cli


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...


class LLMProviderError(RuntimeError):
    pass


class UnconfiguredProvider:
    configured = False

    def __init__(self, reason: str = "No language-model provider is configured.") -> None:
        self.reason = reason

    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "A provider must be configured before I can process requests. Press F7 to open provider setup.",
                    }
                }
            ]
        }


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        return self._post(payload, allow_tool_fallback=bool(tools))

    def complete_with_required_tool(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        tool_name: str,
    ) -> dict[str, Any]:
        selected_tools = [
            tool
            for tool in tools
            if isinstance(tool.get("function"), dict) and tool["function"].get("name") == tool_name
        ]
        if not selected_tools:
            raise LLMProviderError(f"Required tool is not available: {tool_name}")
        return self._post(
            {
                "model": self.model,
                "messages": messages,
                "tools": selected_tools,
                "tool_choice": {"type": "function", "function": {"name": tool_name}},
            },
            allow_tool_fallback=False,
        )

    def _post(self, payload: dict[str, Any], allow_tool_fallback: bool = False) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code == 400 and allow_tool_fallback:
            fallback = dict(payload)
            fallback.pop("tools", None)
            return self._post(fallback, allow_tool_fallback=False)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _error_detail(response)
            raise LLMProviderError(f"LLM request failed: HTTP {response.status_code}: {detail}") from exc
        return response.json()


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        error = data.get("error", data)
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)
    except Exception:
        return response.text[:1000]


class MockProvider:
    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        last = messages[-1]["content"] if messages else ""
        return {"choices": [{"message": {"role": "assistant", "content": f"Ulysses heard: {last}"}}]}


class CodexProvider:
    def __init__(self, model: str, timeout_seconds: float = 60.0) -> None:
        if not model.strip():
            raise LLMProviderError("Codex provider requires a model returned by model/list.")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        codex = find_codex_cli()
        if not codex:
            raise LLMProviderError("Codex provider requires the Codex CLI.")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["content", "tool_calls"],
            "properties": {
                "content": {"type": ["string", "null"]},
                "tool_calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "arguments"],
                        "properties": {
                            "name": {"type": "string"},
                            "arguments": {"type": "string", "description": "JSON object encoded as a string"},
                        },
                    },
                },
            },
        }
        prompt = (
            "Act only as the reasoning backend for Ulysses. Do not execute shell commands or use built-in tools. "
            "Use the supplied conversation to answer. When a listed Ulysses tool is needed, return it in tool_calls "
            "instead of claiming it was executed. Return no tool names that are not listed.\n\n"
            f"Conversation JSON:\n{json.dumps(messages, ensure_ascii=False)}\n\n"
            f"Available Ulysses tools JSON:\n{json.dumps(tools or [], ensure_ascii=False)}"
        )
        with tempfile.TemporaryDirectory(prefix="ulysses-codex-") as directory:
            root = Path(directory)
            schema_path = root / "response-schema.json"
            output_path = root / "response.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            try:
                result = subprocess.run(
                    [
                        codex,
                        "exec",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--model",
                        self.model,
                        "--sandbox",
                        "read-only",
                        "--skip-git-repo-check",
                        "--output-schema",
                        str(schema_path),
                        "--output-last-message",
                        str(output_path),
                        "-",
                    ],
                    input=prompt,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LLMProviderError("Codex request could not be completed.") from exc
            if result.returncode != 0 or not output_path.exists():
                raise LLMProviderError("Codex request failed.")
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise LLMProviderError("Codex returned an invalid response.") from exc
        message: dict[str, Any] = {"role": "assistant", "content": payload.get("content")}
        calls = []
        for call in payload.get("tool_calls") or []:
            if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                continue
            calls.append(
                {
                    "id": f"call_{uuid4().hex}",
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call.get("arguments") if isinstance(call.get("arguments"), str) else "{}",
                    },
                }
            )
        if calls:
            message["tool_calls"] = calls
        return {"choices": [{"message": message}]}


def build_provider(config) -> LLMProvider:
    if config.provider == "mock":
        return MockProvider()
    if config.provider in {"openai", "kimi"}:
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise RuntimeError(f"{config.api_key_env} is required for {config.provider} provider")
        return OpenAICompatibleProvider(config.base_url, config.model, api_key, config.timeout_seconds)
    if config.provider == "openai_chatgpt":
        return CodexProvider(config.model, config.timeout_seconds)
    if config.provider == "ollama":
        api_key = os.getenv(config.api_key_env) or "ollama"
        return OpenAICompatibleProvider(config.base_url, config.model, api_key, config.timeout_seconds)
    raise RuntimeError(f"Unsupported provider: {config.provider}")
