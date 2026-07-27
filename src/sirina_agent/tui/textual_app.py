from __future__ import annotations

from datetime import datetime
from itertools import cycle
from threading import Thread

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static


ULYSSES_HEAD = r'''
╔══════════════════╗
║     ULYSSES      ║
║   ____/^\____    ║
║  /  _     _  \   ║
║ |  / \___/ \  |  ║
║ |  \_/   \_/  |  ║
║  \    ___    /   ║
║   `-._____.-'    ║
╚══════════════════╝
'''


class SudoPasswordScreen(ModalScreen[str | None]):
    CSS = """
    SudoPasswordScreen {
        align: center middle;
    }

    #sudo-dialog {
        width: 58;
        height: auto;
        border: thick $error;
        background: $panel;
        padding: 1 2;
    }

    #sudo-password {
        margin-top: 1;
    }

    #sudo-actions {
        height: 3;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="sudo-dialog"):
            yield Label("Sudo permission required")
            yield Static("Enter your sudo password to run the confirmed command. It will not be stored.")
            yield Input(password=True, placeholder="sudo password", id="sudo-password")
            with Horizontal(id="sudo-actions"):
                yield Button("Run", variant="error", id="run")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#sudo-password", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.dismiss(self.query_one("#sudo-password", Input).value)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class UlyssesTextualApp(App):
    TITLE = "Ulysses"
    SUB_TITLE = "local-first AI voice agent"

    CSS = """
    Screen {
        background: $surface;
    }

    #shell {
        height: 1fr;
    }

    #sidebar {
        width: 32;
        min-width: 28;
        background: $panel;
        border: solid $primary;
        padding: 1 1;
    }

    #logo {
        width: 100%;
        content-align: center middle;
        text-align: center;
        color: $accent;
    }

    #brand {
        width: 100%;
        content-align: center middle;
        text-align: center;
    }

    #main {
        width: 1fr;
    }

    #transcript {
        height: 1fr;
        border: solid $accent;
        background: $boost;
        padding: 1 2;
    }

    #composer {
        height: 3;
        border: solid $primary;
        margin-top: 1;
    }

    .section-title {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }

    .muted {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+l", "clear_transcript", "Clear"),
        Binding("ctrl+t", "cycle_theme", "Theme"),
        Binding("ctrl+u", "voice_toggle", "Voice"),
        Binding("ctrl+m", "mute_toggle", "Mute"),
        Binding("ctrl+y", "copy_last", "Copy last"),
        Binding("ctrl+shift+y", "copy_transcript", "Copy all"),
        Binding("ctrl+s", "selection_mode", "Select"),
        Binding("f2", "cycle_theme", "Theme"),
        Binding("f5", "status", "Status"),
        Binding("f6", "skills", "Skills"),
    ]

    THEMES = ("ulysses_dark", "ulysses_light", "terminal")
    THEME_ALIASES = {
        "ulysses_dark": "textual-dark",
        "ulysses_light": "textual-light",
        # "terminal" means minimal colors, but still maps to a known Textual theme.
        "terminal": "textual-dark",
    }
    SPINNER_FRAMES = ("|", "/", "-", "\\")

    def __init__(self, orchestrator, voice_io=None) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.voice_io = voice_io
        self.last_assistant_text = ""
        self.transcript_plain: list[str] = []
        self.theme_name = getattr(orchestrator.config.tui, "theme", "ulysses_dark")
        self._waiting = False
        self._spinner = cycle(self.SPINNER_FRAMES)
        self.selection_mode = False
        self._autonomous_running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with Vertical(id="sidebar"):
                yield Static(ULYSSES_HEAD, id="logo")
                yield Label(
                    f"{self.orchestrator.config.agent_name} v{self.orchestrator.config.agent_version}",
                    id="brand",
                    classes="section-title",
                )
                yield Static(id="status")
                yield Label("Shortcuts", classes="section-title")
                yield Static(
                    "Ctrl+U voice on/off\n"
                    "Ctrl+M mute\n"
                    "Ctrl+Y copy last\n"
                    "Ctrl+Shift+Y copy all\n"
                    "Ctrl+S select mode\n"
                    "Ctrl+N new session\n"
                    "Ctrl+L clear\n"
                    "F2 theme\n"
                    "F5 status\n"
                    "F6 skills\n"
                    "Ctrl+Q quit",
                    classes="muted",
                )
                yield Label("Slash", classes="section-title")
                yield Static(
                    "/voice on|off\n"
                    "/mute\n"
                    "/run <cmd>\n"
                    "/create-skill <name> <request>\n"
                    "/autonomous on|off\n"
                    "/***autonomous on|off\n"
                    "/confirm [token]\n"
                    "/memory\n"
                    "/context\n"
                    "/sessions\n"
                    "/theme [name]\n"
                    "/copy [all]\n"
                    "/select on|off\n"
                    "/quit",
                    classes="muted",
                )
            with Vertical(id="main"):
                yield RichLog(id="transcript", wrap=True, highlight=True, markup=True)
                yield Static("", id="spinner", classes="muted")
                yield Input(placeholder="Ask Ulysses, paste text, or type /command ...", id="composer")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_theme(self.theme_name)
        self._write_system(
            f"{self.orchestrator.config.agent_name} v{self.orchestrator.config.agent_version} ready. "
            "Paste normally into the input. Use /voice on to speak every response. "
            "Use /select on if you want terminal-native mouse selection."
        )
        self._refresh_status()
        self.set_interval(0.12, self._tick_spinner)
        self.set_interval(2.0, self._refresh_status)
        self.set_interval(self.orchestrator.config.autonomous.check_interval_seconds, self._maybe_autonomous)
        self.query_one("#composer", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._command(text)
            return
        self._write_user(text)
        self._refresh_status()
        self._start_waiting()
        Thread(target=self._answer_in_thread, args=(text,), daemon=True).start()

    def _answer_in_thread(self, text: str) -> None:
        try:
            answer = self.orchestrator.handle_text(text)
        except Exception as exc:
            self.call_from_thread(self._finish_error, str(exc))
            return
        self.call_from_thread(self._finish_answer, answer)

    def _finish_answer(self, answer: str) -> None:
        self._stop_waiting()
        self._write_assistant(answer)
        self._refresh_status()
        self._speak(answer)

    def _finish_error(self, error: str) -> None:
        self._stop_waiting()
        self._write_error(error)
        self._refresh_status()

    def _command(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0]
        if cmd == "/quit":
            self.exit()
        elif cmd == "/new":
            self.orchestrator.session_id = self.orchestrator.sessions.create_session("Ulysses")
            self._write_system("Created a new session.")
        elif cmd == "/sessions":
            rows = self.orchestrator.sessions.list_sessions()
            self._write_system("\n".join(f"{row['id']}  {row['title']}  {row['updated_at']}" for row in rows) or "No sessions.")
        elif cmd == "/switch" and len(parts) > 1:
            self.orchestrator.session_id = parts[1]
            self._write_system(f"Switched to {parts[1]}.")
        elif cmd == "/skills":
            self.action_skills()
        elif cmd == "/memory":
            memories = self.orchestrator.memory.items[-20:]
            self._write_system("\n".join(f"{item.id}: {item.text[:120]}" for item in memories) or "No memory.")
        elif cmd == "/forget" and len(parts) > 1:
            if parts[1] == "all":
                self.orchestrator.erase_user_data()
                self._write_system("Erased all sessions and memory.")
            else:
                self._write_system("Forgot memory." if self.orchestrator.memory.forget(parts[1]) else "Memory not found.")
        elif cmd == "/confirm":
            token = parts[1] if len(parts) > 1 else None
            if self.orchestrator.pending_tool_requires_sudo_password():
                self.push_screen(SudoPasswordScreen(), lambda password: self._confirm_with_sudo_password(token, password))
            else:
                self._write_tool(self.orchestrator.confirm_pending_tool(token))
        elif cmd == "/run" and len(parts) > 1:
            self._write_tool(self.orchestrator._run_skill("system_command", {"command": " ".join(parts[1:])}))
        elif cmd == "/create-skill" and len(parts) > 2:
            self._write_tool(self.orchestrator._run_skill("create_skill", {"name": parts[1], "request": " ".join(parts[2:])}))
        elif cmd in {"/autonomous", "/***autonomous"}:
            self._autonomous_command(parts)
        elif cmd in {"/status", "/config"}:
            self.action_status()
        elif cmd == "/context":
            self._write_system(str(self.orchestrator.context_usage()))
        elif cmd == "/voice":
            self._voice_command(parts)
        elif cmd == "/mute":
            self.action_mute_toggle()
        elif cmd == "/say" and len(parts) > 1:
            self._speak(" ".join(parts[1:]))
        elif cmd == "/theme":
            if len(parts) > 1 and parts[1] == "list":
                self._write_system("Themes: " + ", ".join(self.THEMES))
            elif len(parts) > 1:
                self._apply_theme(parts[1])
            else:
                self.action_cycle_theme()
        elif cmd == "/copy":
            if len(parts) > 1 and parts[1] == "all":
                self.action_copy_transcript()
            else:
                self.action_copy_last()
        elif cmd == "/select":
            if len(parts) > 1 and parts[1].lower() in {"on", "off"}:
                self._set_selection_mode(parts[1].lower() == "on")
            else:
                self.action_selection_mode()
        elif cmd == "/export":
            self._write_system(str({"sessions": self.orchestrator.sessions.list_sessions(), "memory": [item.__dict__ for item in self.orchestrator.memory.items]}))
        else:
            self._write_error("Unknown command.")
        self._refresh_status()

    def _confirm_with_sudo_password(self, token: str | None, password: str | None) -> None:
        if password is None:
            self._write_system("Sudo command cancelled.")
            return
        self._write_tool(self.orchestrator.confirm_pending_tool(token, {"sudo_password": password}))
        self._refresh_status()

    def _voice_command(self, parts: list[str]) -> None:
        if not self.voice_io:
            self._write_system("Voice I/O is not active. Start without --text-only to enable Sirina voice mode.")
            return
        if len(parts) == 1:
            self._write_system(str(self.voice_io.state.__dict__))
        elif parts[1].lower() == "on":
            self.voice_io.state.enabled = True
            self._write_system("Voice responses: on")
        elif parts[1].lower() == "off":
            self.voice_io.state.enabled = False
            self.voice_io.interrupt()
            self._write_system("Voice responses: off")
        else:
            self._write_system("Usage: /voice, /voice on, or /voice off")

    def _autonomous_command(self, parts: list[str]) -> None:
        if len(parts) == 1:
            self._write_system(f"Autonomous mode: {'on' if self.orchestrator.autonomous_enabled() else 'off'}")
            return
        mode = parts[1].lower()
        if mode == "on":
            self.orchestrator.set_autonomous(True)
            self._write_system("Autonomous mode: on. I will periodically check the mission and report when I have something useful.")
        elif mode == "off":
            self.orchestrator.set_autonomous(False)
            self._write_system("Autonomous mode: off.")
        elif mode == "now":
            self._start_autonomous_check(force=True)
        else:
            self._write_system("Usage: /autonomous on, /autonomous off, /autonomous now")
        self._refresh_status()

    def action_new_session(self) -> None:
        self._command("/new")

    def action_clear_transcript(self) -> None:
        self.query_one("#transcript", RichLog).clear()
        self.transcript_plain.clear()
        self._write_system("Transcript cleared.")

    def action_status(self) -> None:
        cfg = self.orchestrator.config
        self._write_system(
            f"Session: {self.orchestrator.session_id}\n"
            f"Provider: {cfg.llm.provider} / {cfg.llm.model}\n"
            f"Version: {cfg.agent_version}\n"
            f"Voice: {getattr(self.voice_io, 'state', None).__dict__ if self.voice_io else 'inactive'}\n"
            f"Autonomous: {self.orchestrator.autonomous_enabled()}\n"
            f"Godmode: {cfg.skills.command.godmode}"
        )

    def action_skills(self) -> None:
        lines = []
        for manifest in self.orchestrator.skills.manifests():
            lines.append(f"{manifest.name}  risk={manifest.risk_level}  enabled={manifest.enabled}")
        self._write_system("\n".join(lines) or "No skills registered.")

    def action_voice_toggle(self) -> None:
        if not self.voice_io:
            self._write_system("Voice I/O is not active.")
            return
        self.voice_io.state.enabled = not self.voice_io.state.enabled
        if not self.voice_io.state.enabled:
            self.voice_io.interrupt()
        self._write_system(f"Voice responses: {'on' if self.voice_io.state.enabled else 'off'}")
        self._refresh_status()

    def action_mute_toggle(self) -> None:
        if not self.voice_io:
            self._write_system("Voice I/O is not active.")
            return
        self.voice_io.state.muted = not self.voice_io.state.muted
        self._write_system(f"Muted: {self.voice_io.state.muted}")
        self._refresh_status()

    def action_copy_last(self) -> None:
        if not self.last_assistant_text:
            self._write_system("No assistant response to copy.")
            return
        try:
            self.copy_to_clipboard(self.last_assistant_text)
            self._write_system("Copied last assistant response.")
        except Exception as exc:
            self._write_error(f"Clipboard unavailable: {exc}")

    def action_copy_transcript(self) -> None:
        if not self.transcript_plain:
            self._write_system("No transcript to copy.")
            return
        try:
            self.copy_to_clipboard("\n".join(self.transcript_plain))
            self._write_system("Copied transcript.")
        except Exception as exc:
            self._write_error(f"Clipboard unavailable: {exc}")

    def action_selection_mode(self) -> None:
        self._set_selection_mode(not self.selection_mode)

    def _set_selection_mode(self, enabled: bool) -> None:
        self.selection_mode = enabled
        if enabled:
            self.query_one("#composer", Input).blur()
            self._write_system(
                "Selection mode: on. Drag-select with the mouse using your terminal's native selection; "
                "many terminals copy on selection or with Ctrl+Shift+C."
            )
        else:
            self.query_one("#composer", Input).focus()
            self._write_system("Selection mode: off.")

    def action_cycle_theme(self) -> None:
        current = self.THEMES.index(self.theme_name) if self.theme_name in self.THEMES else 0
        self._apply_theme(self.THEMES[(current + 1) % len(self.THEMES)])

    def _apply_theme(self, name: str) -> None:
        requested = name
        theme = self.THEME_ALIASES.get(name)
        if theme is None:
            theme = self.THEME_ALIASES["ulysses_dark"]
            name = "ulysses_dark"
        try:
            self.theme = theme
        except Exception as exc:
            self.theme = "textual-dark"
            name = "ulysses_dark"
            if self.is_mounted:
                self._write_error(f"Theme `{requested}` failed, reverted to ulysses_dark: {exc}")
        self.theme_name = name
        if self.is_mounted:
            self._write_system(f"Theme: {name}")
            self._refresh_status()

    def _refresh_status(self) -> None:
        voice = "inactive"
        if self.voice_io:
            state = self.voice_io.state
            voice = f"{'on' if state.enabled else 'off'} / muted={state.muted} / tts={state.tts}"
        autonomous = "on" if self.orchestrator.autonomous_enabled() else "off"
        context = self.orchestrator.context_usage()
        gauge = _gauge(context["percent"])
        self.query_one("#status", Static).update(
            f"Session\n{self.orchestrator.session_id}\n\n"
            f"Version\n{self.orchestrator.config.agent_version}\n\n"
            f"Provider\n{self.orchestrator.config.llm.provider}\n\n"
            f"Context\n{gauge} {context['percent']}%\n"
            f"{context['estimated_tokens']}/{context['context_window_tokens']} tok\n\n"
            f"Voice\n{voice}\n\n"
            f"Autonomous\n{autonomous}\n\n"
            f"Theme\n{self.theme_name}"
        )

    def _maybe_autonomous(self) -> None:
        self._start_autonomous_check(force=False)

    def _start_autonomous_check(self, force: bool = False) -> None:
        if self._waiting or self._autonomous_running or not self.orchestrator.autonomous_enabled():
            return
        self._autonomous_running = True
        Thread(target=self._autonomous_in_thread, args=(force,), daemon=True).start()

    def _autonomous_in_thread(self, force: bool) -> None:
        note = self.orchestrator.autonomous_check(force=force)
        self.call_from_thread(self._finish_autonomous_check, note)

    def _finish_autonomous_check(self, note: str | None) -> None:
        self._autonomous_running = False
        if note:
            self._write_assistant(note)
            self._speak(note)
        self._refresh_status()

    def _start_waiting(self) -> None:
        self._waiting = True
        self.query_one("#composer", Input).disabled = True
        self.query_one("#spinner", Static).update("| Ulysses is thinking...")

    def _stop_waiting(self) -> None:
        self._waiting = False
        self.query_one("#spinner", Static).update("")
        composer = self.query_one("#composer", Input)
        composer.disabled = False
        composer.focus()

    def _tick_spinner(self) -> None:
        if self._waiting:
            self.query_one("#spinner", Static).update(f"{next(self._spinner)} Ulysses is thinking...")

    def _write_user(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(f"[bold cyan]you[/bold cyan] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("you", text)

    def _write_assistant(self, text: str) -> None:
        self.last_assistant_text = text
        self.query_one("#transcript", RichLog).write(f"[bold magenta]Ulysses[/bold magenta] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("Ulysses", text)

    def _write_tool(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(f"[bold yellow]tool[/bold yellow] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("tool", text)

    def _write_system(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(f"[bold green]system[/bold green] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("system", text)

    def _write_error(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(f"[bold red]error[/bold red] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("error", text)

    def _append_plain(self, role: str, text: str) -> None:
        self.transcript_plain.append(f"{role} {_time()}\n{text}")

    def _speak(self, text: str) -> None:
        if not self.voice_io or not self.voice_io.state.enabled or self.voice_io.state.muted:
            return
        try:
            self.voice_io.speak(text)
        except Exception as exc:
            self._write_error(f"TTS error: {exc}")


def _time() -> str:
    return datetime.now().strftime("%H:%M")


def _gauge(percent: int, width: int = 14) -> str:
    filled = min(width, max(0, round((percent / 100) * width)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"
