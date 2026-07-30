import json

import pytest

from sirina_agent.llm.openai_auth import (
    OpenAIBrowserLoginError,
    _callback_origin_from_auth_url,
    _open_system_browser,
    _select_provider_model,
    _validate_callback_url,
    find_codex_cli,
)


def test_detects_codex_chatgpt_tokens(tmp_path):
    from sirina_agent.llm.openai_auth import codex_chatgpt_authenticated

    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": None, "tokens": {"access_token": "token"}}),
        encoding="utf-8",
    )

    assert codex_chatgpt_authenticated(tmp_path)


def test_extracts_loopback_callback_from_authorization_url():
    auth_url = (
        "https://auth.openai.com/oauth/authorize?"
        "redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback&state=xyz"
    )

    assert _callback_origin_from_auth_url(auth_url) == ("localhost", 1455, "/auth/callback", "xyz")


def test_callback_requires_exact_loopback_origin_and_oauth_values():
    expected = ("localhost", 1455, "/auth/callback", "xyz")
    _validate_callback_url("http://localhost:1455/auth/callback?code=abc&state=xyz", expected)

    unsafe = [
        "https://localhost:1455/auth/callback?code=abc&state=xyz",
        "http://example.com:1455/auth/callback?code=abc&state=xyz",
        "http://localhost:9999/auth/callback?code=abc&state=xyz",
        "http://localhost:1455/other?code=abc&state=xyz",
        "http://localhost:1455/auth/callback?code=abc",
        "http://localhost:1455/auth/callback?code=abc&state=wrong",
    ]
    for callback_url in unsafe:
        with pytest.raises(OpenAIBrowserLoginError):
            _validate_callback_url(callback_url, expected)


def test_browser_launcher_falls_back_after_failed_desktop_handler(monkeypatch):
    launched = []

    class Process:
        def __init__(self, return_code):
            self.return_code = return_code

        def wait(self, timeout):
            return self.return_code

    paths = {"xdg-open": "/usr/bin/xdg-open", "firefox": "/usr/bin/firefox"}
    monkeypatch.setattr("sirina_agent.llm.openai_auth.shutil.which", paths.get)

    def popen(command, **kwargs):
        launched.append(command)
        return Process(1 if command[0].endswith("xdg-open") else 0)

    monkeypatch.setattr("sirina_agent.llm.openai_auth.subprocess.Popen", popen)

    assert _open_system_browser("https://auth.openai.com/example")
    assert [command[0] for command in launched] == ["/usr/bin/xdg-open", "/usr/bin/firefox"]


def test_selects_default_visible_model_from_provider_catalog():
    models = [
        {"model": "hidden-codex", "hidden": True, "isDefault": True},
        {"model": "gpt-5.2-codex", "hidden": False, "isDefault": False},
        {"model": "gpt-5.3-codex", "hidden": False, "isDefault": True},
    ]

    assert _select_provider_model(models) == "gpt-5.3-codex"


def test_uses_configured_codex_executable(monkeypatch):
    monkeypatch.setenv("ULYSSES_CODEX_BIN", "/discovered/codex")
    monkeypatch.setattr("sirina_agent.llm.openai_auth.shutil.which", lambda name: name)

    assert find_codex_cli() == "/discovered/codex"
