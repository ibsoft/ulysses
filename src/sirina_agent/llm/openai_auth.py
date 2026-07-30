from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


class OpenAIBrowserLoginError(RuntimeError):
    pass


def find_codex_cli() -> str | None:
    override = os.environ.get("ULYSSES_CODEX_BIN", "").strip()
    return shutil.which(override) if override else shutil.which("codex")


def codex_chatgpt_authenticated(codex_home: str | Path | None = None) -> bool:
    root = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    try:
        data = json.loads((root / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    tokens = data.get("tokens")
    return isinstance(tokens, dict) and any(
        isinstance(tokens.get(name), str) and bool(tokens[name].strip())
        for name in ("access_token", "refresh_token", "id_token")
    )


class OpenAIBrowserLogin:
    """Drive Codex-managed ChatGPT login without handling OAuth tokens directly."""

    def __init__(self, timeout_seconds: float = 180.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.auth_url = ""
        self.login_id = ""
        self.codex_home: Path | None = None
        self._callback_origin: tuple[str, int, str, str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._messages: Queue[dict[str, Any]] = Queue()
        self._request_id = 0

    def start(self, open_browser: bool = False) -> str:
        codex = find_codex_cli()
        if not codex:
            raise OpenAIBrowserLoginError("OpenAI browser login requires the Codex CLI.")
        try:
            self._process = subprocess.Popen(
                [codex, "app-server", "--stdio", "-c", 'cli_auth_credentials_store="file"'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise OpenAIBrowserLoginError("Could not start OpenAI browser login.") from exc
        Thread(target=self._read_messages, daemon=True).start()
        initialized = self._request(
            "initialize",
            {"clientInfo": {"name": "ulysses", "title": "Ulysses", "version": "1.0"}, "capabilities": {}},
        )
        self.codex_home = Path(initialized["codexHome"])
        self._send({"method": "initialized"})
        login = self._request(
            "account/login/start",
            {"type": "chatgpt", "appBrand": "codex", "useHostedLoginSuccessPage": True},
        )
        if login.get("type") != "chatgpt":
            self.close()
            raise OpenAIBrowserLoginError("OpenAI browser login returned an unsupported authentication method.")
        self.auth_url = str(login["authUrl"])
        self.login_id = str(login["loginId"])
        self._callback_origin = _callback_origin_from_auth_url(self.auth_url)
        if open_browser and not _open_system_browser(self.auth_url):
            self.close()
            raise OpenAIBrowserLoginError("Could not open the system browser.")
        return self.auth_url

    def complete(self, callback_url: str) -> str:
        if not self._process or not self.login_id or not self._callback_origin:
            raise OpenAIBrowserLoginError("No OpenAI browser login is pending.")
        _validate_callback_url(callback_url, self._callback_origin)
        if self._wait_for_completion(0):
            return self._finish_login()
        try:
            response = httpx.get(callback_url.strip(), follow_redirects=False, timeout=10.0)
        except httpx.HTTPError as exc:
            if self._wait_for_completion(2):
                return self._finish_login()
            self.close()
            raise OpenAIBrowserLoginError("The local OpenAI login callback could not be delivered.") from exc
        if response.status_code >= 400:
            if self._wait_for_completion(2):
                return self._finish_login()
            self.close()
            raise OpenAIBrowserLoginError("OpenAI rejected the local login callback.")
        if self._wait_for_completion(self.timeout_seconds):
            return self._finish_login()
        self.close()
        raise OpenAIBrowserLoginError("OpenAI browser login timed out.")

    def _finish_login(self) -> str:
        try:
            response = self._request("model/list", {"includeHidden": False, "limit": 100})
            return _select_provider_model(response.get("data"))
        finally:
            self.close()

    def _wait_for_completion(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        first = True
        while first or time.monotonic() < deadline:
            first = False
            try:
                message = self._messages.get_nowait() if timeout == 0 else self._messages.get(
                    timeout=max(0.01, deadline - time.monotonic())
                )
            except Empty:
                return False
            if message.get("method") != "account/login/completed":
                continue
            params = message.get("params") or {}
            if params.get("loginId") not in {None, self.login_id}:
                continue
            if not params.get("success"):
                self.close()
                raise OpenAIBrowserLoginError("OpenAI browser login was not completed.")
            return True
        return False

    def close(self) -> None:
        process, self._process = self._process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            message = self._next_message(deadline - time.monotonic())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                self.close()
                raise OpenAIBrowserLoginError("OpenAI browser login service rejected the request.")
            return dict(message.get("result") or {})
        self.close()
        raise OpenAIBrowserLoginError("OpenAI browser login service timed out.")

    def _send(self, message: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise OpenAIBrowserLoginError("OpenAI browser login service is not running.")
        try:
            self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise OpenAIBrowserLoginError("OpenAI browser login service stopped unexpectedly.") from exc

    def _read_messages(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                self._messages.put(message)

    def _next_message(self, timeout: float) -> dict[str, Any]:
        try:
            return self._messages.get(timeout=max(0.01, timeout))
        except Empty as exc:
            raise OpenAIBrowserLoginError("OpenAI browser login service timed out.") from exc


def _callback_origin_from_auth_url(auth_url: str) -> tuple[str, int, str, str]:
    auth_values = parse_qs(urlparse(auth_url).query)
    redirect_uri = (auth_values.get("redirect_uri") or [""])[0]
    state = (auth_values.get("state") or [""])[0]
    parsed = urlparse(redirect_uri)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not parsed.port
        or not state
    ):
        raise OpenAIBrowserLoginError("OpenAI returned an invalid local callback address.")
    return parsed.hostname, parsed.port, parsed.path or "/auth/callback", state


def _validate_callback_url(callback_url: str, expected: tuple[str, int, str, str]) -> None:
    parsed = urlparse(callback_url.strip())
    host, port, path, state = expected
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != "http"
        or parsed.hostname != host
        or parsed.port != port
        or parsed.path != path
        or not query.get("code")
        or query.get("state") != [state]
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OpenAIBrowserLoginError("Paste the complete localhost return URL from the OpenAI browser login.")


def _select_provider_model(models: Any) -> str:
    if not isinstance(models, list):
        raise OpenAIBrowserLoginError("Codex did not return its supported model catalog.")
    visible = [model for model in models if isinstance(model, dict) and not model.get("hidden")]
    selected = next((model for model in visible if model.get("isDefault")), visible[0] if visible else None)
    model_name = selected.get("model") if selected else None
    if not isinstance(model_name, str) or not model_name.strip():
        raise OpenAIBrowserLoginError("Codex did not return a supported default model.")
    return model_name.strip()


def _open_system_browser(url: str) -> bool:
    commands: list[list[str]] = []
    for executable, arguments in (
        ("xdg-open", []),
        ("gio", ["open"]),
        ("sensible-browser", []),
        ("firefox", ["--new-tab"]),
        ("google-chrome", ["--new-tab"]),
        ("google-chrome-stable", ["--new-tab"]),
        ("chromium", ["--new-tab"]),
        ("chromium-browser", ["--new-tab"]),
    ):
        path = shutil.which(executable)
        if path:
            commands.append([path, *arguments, url])
    for command in commands:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            continue
        try:
            return_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return True
        if return_code == 0:
            return True
    try:
        return bool(webbrowser.open(url, new=1))
    except webbrowser.Error:
        return False
