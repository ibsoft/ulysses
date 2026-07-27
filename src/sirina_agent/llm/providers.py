from __future__ import annotations

import os
from typing import Any, Protocol

import httpx


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...


class LLMProviderError(RuntimeError):
    pass


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


def build_provider(config) -> LLMProvider:
    if config.provider == "mock":
        return MockProvider()
    if config.provider in {"openai", "kimi"}:
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise RuntimeError(f"{config.api_key_env} is required for {config.provider} provider")
        return OpenAICompatibleProvider(config.base_url, config.model, api_key, config.timeout_seconds)
    if config.provider == "ollama":
        api_key = os.getenv(config.api_key_env) or "ollama"
        return OpenAICompatibleProvider(config.base_url, config.model, api_key, config.timeout_seconds)
    token = os.getenv(config.oauth_token_env or "")
    if not token and config.oauth_keyring_service and config.oauth_keyring_username:
        import keyring  # type: ignore

        token = keyring.get_password(config.oauth_keyring_service, config.oauth_keyring_username)
    if not token:
        raise RuntimeError("OAuth-compatible provider requires a configured token env var or keyring entry")
    return OpenAICompatibleProvider(config.base_url, config.model, token, config.timeout_seconds)
