import pytest
from textual.app import App, ComposeResult
from textual.events import Paste

from sirina_agent.tui.textual_app import ComposerInput, UlyssesTextualApp, _set_system_clipboard_text


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
