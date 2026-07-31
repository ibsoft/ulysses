import json
import time

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.events import Paste
from textual.widgets import Label, Static

from sirina_agent.config.models import UlyssesConfig
from sirina_agent.core.orchestrator import AgentOrchestrator
from sirina_agent.llm.providers import MockProvider
from sirina_agent.memory.store import FaissMemoryStore, LocalHashEmbeddingProvider
from sirina_agent.sessions.store import SessionStore
from sirina_agent.skills.registry import SkillRegistry
from sirina_agent.tui.textual_app import (
    ComposerInput,
    UlyssesTextualApp,
    _boot_progress,
    _set_system_clipboard_text,
    _system_clipboard_backend,
    _system_clipboard_text,
)
from sirina_agent.updates import UpdateStatus


@pytest.fixture
def anyio_backend():
    return "asyncio"


class PasteHarness(App):
    def __init__(self):
        super().__init__()
        self.pasted = ""
        self.clipboard_paste_calls = 0

    def compose(self) -> ComposeResult:
        yield ComposerInput(id="composer")

    def _handle_composer_paste(self, text: str) -> None:
        self.pasted = text

    def action_paste_clipboard(self) -> None:
        self.clipboard_paste_calls += 1


def test_textual_tui_binds_f4_to_push_to_talk():
    bindings = {(binding.key, binding.action) for binding in UlyssesTextualApp.BINDINGS}

    assert ("f4", "push_to_talk") in bindings


def test_textual_tui_binds_ctrl_v_to_clipboard_paste():
    bindings = {(binding.key, binding.action) for binding in UlyssesTextualApp.BINDINGS}

    assert ("ctrl+v", "paste_clipboard") in bindings


def test_system_clipboard_uses_discovered_native_writer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sirina_agent.tui.textual_app.shutil.which",
        lambda name: "/dynamic/clipboard" if name == "xclip" else None,
    )

    class Result:
        returncode = 0

    def run(command, **kwargs):
        calls.append((command, kwargs["input"]))
        return Result()

    monkeypatch.setattr("sirina_agent.tui.textual_app.subprocess.run", run)

    assert _set_system_clipboard_text("login-url")
    assert calls == [(["/dynamic/clipboard", "-selection", "clipboard"], "login-url")]


def test_system_clipboard_reads_from_discovered_native_backend(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sirina_agent.tui.textual_app.shutil.which",
        lambda name: "/dynamic/clipboard" if name == "xclip" else None,
    )

    class Result:
        returncode = 0
        stdout = "first line\nsecond line\n"

    def run(command, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr("sirina_agent.tui.textual_app.subprocess.run", run)

    assert _system_clipboard_text() == "first line\nsecond line"
    assert calls == [["/dynamic/clipboard", "-selection", "clipboard", "-o"]]


def test_system_clipboard_backend_reports_missing_tools(monkeypatch):
    monkeypatch.setattr("sirina_agent.tui.textual_app.shutil.which", lambda name: None)

    assert _system_clipboard_backend() is None


def test_boot_progress_resolves_checks_without_repeating_log_entries():
    message = (
        "Ulysses Cyber Sentinel initializing\n\n"
        "Brain: up (provider / model)\nMemory: up (2 memories)\nSkills: up (one)\n"
        "Prompt: up (profile)\nVoice: inactive"
    )

    first = _boot_progress(message, 0, "/")
    middle = _boot_progress(message, 2, "-")
    final = _boot_progress(message, 5, "|")

    assert "Brain:" in first and "/[/cyan]" in first and "checking..." in first
    assert "Memory:" in first and "waiting" in first
    assert "Brain: up (provider / model)" in middle
    assert "Skills:" in middle and "-[/cyan]" in middle and "checking..." in middle
    assert "Voice: inactive" in final
    assert "checking" not in final


@pytest.mark.anyio
async def test_sidebar_scrolls_without_hiding_lower_sections(tmp_path):
    config = UlyssesConfig()
    config.memory.sqlite_path = tmp_path / "sessions.sqlite3"
    config.memory.faiss_path = tmp_path / "memory.faiss"
    config.memory.metadata_path = tmp_path / "memory.jsonl"
    sessions = SessionStore(config.memory.sqlite_path)
    memory = FaissMemoryStore(
        config.memory.faiss_path,
        config.memory.metadata_path,
        LocalHashEmbeddingProvider(64),
    )
    orchestrator = AgentOrchestrator(config, sessions, memory, MockProvider(), SkillRegistry())
    app = UlyssesTextualApp(orchestrator)

    async with app.run_test(size=(80, 30)) as pilot:
        sidebar = app.query_one("#sidebar", VerticalScroll)
        assert not any("\nTheme: " in entry for entry in app.transcript_plain)
        app._boot_started_at = time.monotonic() - 3
        app._tick_boot_sequence()
        assert not app.query_one("#boot-status").display
        assert sum("ULYSSES CYBER SENTINEL" in entry for entry in app.transcript_plain) == 1
        assert sidebar.max_scroll_y > 0
        sidebar.scroll_end(animate=False)
        await pilot.pause()
        assert sidebar.scroll_y > 0


@pytest.mark.anyio
async def test_sidebar_shows_release_version_once_below_logo(tmp_path):
    config = UlyssesConfig()
    config.memory.sqlite_path = tmp_path / "sessions.sqlite3"
    config.memory.faiss_path = tmp_path / "memory.faiss"
    config.memory.metadata_path = tmp_path / "memory.jsonl"
    config.updates.metadata_path = tmp_path / ".ulysses-build.json"
    config.updates.metadata_path.write_text(json.dumps({"source_branch": "v_2.0.15"}), encoding="utf-8")
    sessions = SessionStore(config.memory.sqlite_path)
    memory = FaissMemoryStore(
        config.memory.faiss_path,
        config.memory.metadata_path,
        LocalHashEmbeddingProvider(64),
    )
    app = UlyssesTextualApp(AgentOrchestrator(config, sessions, memory, MockProvider(), SkillRegistry()))
    async with app.run_test(size=(80, 30)):
        app.updates.status = UpdateStatus("available", latest_branch="v_2.0.16")
        app._finish_update_check(app.updates.status, False)
        brand = str(app.query_one("#brand", Label).render())
        status = str(app.query_one("#status", Static).render())

        assert brand == "Ulysses v_2.0.15 (update)"
        assert "Version\n" not in status
        assert "Latest branch\n" not in status
        assert status.count("v_2.0.15") == 0
        assert app.title == "Ulysses v_2.0.15 (update)"

        app.action_status()
        report = app.transcript_plain[-1]
        assert "◆  ULYSSES SYSTEM STATUS" in report
        assert "CORE" in report
        assert "CAPABILITIES" in report
        assert "SECURITY" in report
        assert "Connector:" in report
        assert "Active skill:" in report and "idle" in report
        assert "Godmode:" in report and "off" in report
        assert "Delegated jobs" not in report
        assert "Config path" not in report


def test_top_header_includes_locally_installed_version(tmp_path):
    config = UlyssesConfig()
    config.memory.sqlite_path = tmp_path / "sessions.sqlite3"
    config.memory.faiss_path = tmp_path / "memory.faiss"
    config.memory.metadata_path = tmp_path / "memory.jsonl"
    config.updates.metadata_path = tmp_path / ".ulysses-build.json"
    config.updates.metadata_path.write_text(json.dumps({"source_branch": "v_2.0.14"}), encoding="utf-8")
    sessions = SessionStore(config.memory.sqlite_path)
    memory = FaissMemoryStore(
        config.memory.faiss_path,
        config.memory.metadata_path,
        LocalHashEmbeddingProvider(64),
    )

    app = UlyssesTextualApp(AgentOrchestrator(config, sessions, memory, MockProvider(), SkillRegistry()))

    assert app.title == "Ulysses v_2.0.14"
    assert app.sub_title == "local-first AI voice agent"


def test_textual_tui_escape_stops_voice_with_priority():
    binding = next(binding for binding in UlyssesTextualApp.BINDINGS if binding.key == "escape")

    assert binding.action == "stop_speaking"
    assert binding.priority


@pytest.mark.anyio
async def test_composer_intercepts_multiline_paste_before_input_truncates_it():
    app = PasteHarness()
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", ComposerInput)
        composer.focus()
        composer.post_message(Paste("first line\nsecond line\nthird line"))
        await pilot.pause()

        assert app.pasted == "first line\nsecond line\nthird line"
        assert composer.value == ""


@pytest.mark.anyio
async def test_composer_ctrl_v_routes_to_full_clipboard_handler():
    app = PasteHarness()
    async with app.run_test() as pilot:
        app.query_one("#composer", ComposerInput).focus()
        await pilot.press("ctrl+v")

        assert app.clipboard_paste_calls == 1


@pytest.mark.anyio
async def test_composer_up_down_navigates_history_and_restores_draft(tmp_path):
    config = UlyssesConfig()
    config.memory.sqlite_path = tmp_path / "sessions.sqlite3"
    config.memory.faiss_path = tmp_path / "memory.faiss"
    config.memory.metadata_path = tmp_path / "memory.jsonl"
    sessions = SessionStore(config.memory.sqlite_path)
    memory = FaissMemoryStore(
        config.memory.faiss_path,
        config.memory.metadata_path,
        LocalHashEmbeddingProvider(64),
    )
    app = UlyssesTextualApp(AgentOrchestrator(config, sessions, memory, MockProvider(), SkillRegistry()))
    app._remember_command("first command")
    app._remember_command("second command")

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", ComposerInput)
        composer.focus()
        composer.value = "unfinished draft"

        await pilot.press("up")
        assert composer.value == "second command"
        await pilot.press("up")
        assert composer.value == "first command"
        await pilot.press("up")
        assert composer.value == "first command"
        await pilot.press("down")
        assert composer.value == "second command"
        await pilot.press("down")
        assert composer.value == "unfinished draft"
        if app._boot_timer is not None:
            app._boot_timer.pause()


def test_command_history_is_bounded_and_avoids_consecutive_duplicates():
    app = object.__new__(UlyssesTextualApp)
    app._command_history = []
    app._command_history_index = None
    app._command_history_draft = ""

    for index in range(205):
        app._remember_command(f"command {index}")
    app._remember_command("command 204")

    assert len(app._command_history) == 200
    assert app._command_history[0] == "command 5"
    assert app._command_history[-1] == "command 204"
