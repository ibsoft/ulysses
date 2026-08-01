from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import replace
from datetime import datetime
from itertools import cycle
from threading import Thread

from rich.console import Group
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key, Paste
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RichLog, Select, Static

from sirina_agent.config import load_config
from sirina_agent.config.provider_setup import (
    ProviderSetup,
    apply_provider_setup,
    default_for,
    env_path_for_config,
    load_env_file,
    provider_labels,
)
from sirina_agent.connectors.registry import ConnectorManager, connector_definitions
from sirina_agent.connectors.setup import TelegramSetup, apply_telegram_setup
from sirina_agent.connectors.telegram import TelegramConnector
from sirina_agent.core.artifacts import (
    Artifact,
    ArtifactManager,
    AssessmentProject,
    assessment_command_for_text,
    assessment_needs_voice,
    assessment_target,
    attachment_prompt,
    is_assessment_continuation,
    is_assessment_request,
    is_final_assessment_report,
    is_report_request,
    is_skill_creation_request,
    should_attach_clipboard_text,
    should_store_large_paste,
)
from sirina_agent.core.assessment import (
    AssessmentCheck,
    AssessmentResult,
    assessment_checks,
    missing_tool_installer_script,
    missing_tool_packages,
    render_assessment_report,
)
from sirina_agent.llm.openai_auth import OpenAIBrowserLogin, OpenAIBrowserLoginError
from sirina_agent.llm.providers import build_provider
from sirina_agent.mcp.client import SDKMCPClient
from sirina_agent.mcp.setup import MCPServerSetup, apply_mcp_server_setup
from sirina_agent.tui.boot import spoken_startup_brief, startup_brief
from sirina_agent.tui.branding import ULYSSES_SIDEBAR_LOGO, ULYSSES_SPEAKING_LOGOS
from sirina_agent.updates import UpdateManager


class TranscriptLog(RichLog):
    def get_selection(self, selection) -> tuple[str, str] | None:
        text = "\n".join(line.text.rstrip() for line in self.lines)
        if not text:
            return None
        return selection.extract(text), "\n"


def _formatted_transcript_content(text: str):
    markdown = bool(
        re.search(r"(?m)^#{1,6}\s+\S", text)
        or re.search(r"(?m)^```", text)
        or re.search(r"(?m)^\s*[-*+]\s+\S", text)
        or re.search(r"(?m)^\s*\d+\.\s+\S", text)
        or re.search(r"(?m)^\s*\|.*\|\s*$\n^\s*\|?\s*:?-{3,}", text)
    )
    return Markdown(text) if markdown else Text(text)


class ComposerInput(Input):
    def _on_paste(self, event: Paste) -> None:
        event.prevent_default()
        event.stop()
        if event.text:
            self.app._handle_composer_paste(event.text)

    def action_paste(self) -> None:
        self.app.action_paste_clipboard()

    def on_key(self, event: Key) -> None:
        if event.key not in {"up", "down"}:
            return
        navigate = getattr(self.app, "_navigate_command_history", None)
        if navigate and navigate(event.key):
            event.prevent_default()
            event.stop()


class SudoPasswordScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("enter", "submit_password", "Run", show=False, priority=True)]

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

    def action_submit_password(self) -> None:
        self.dismiss(self.query_one("#sudo-password", Input).value)


class ProviderSetupScreen(ModalScreen[ProviderSetup | None]):
    CSS = """
    ProviderSetupScreen {
        align: center middle;
    }

    #setup-dialog {
        width: 76;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }

    #setup-provider-buttons {
        height: 3;
        margin: 1 0;
    }

    #setup-actions {
        height: 3;
        margin-top: 1;
    }

    .setup-input {
        margin-bottom: 1;
    }
    """

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.provider = config.llm.provider

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Label("Provider setup")
            yield Static("OpenAI browser login opens your browser and stores no OAuth token in Ulysses.")
            with Horizontal(id="setup-provider-buttons"):
                for provider, label in provider_labels():
                    yield Button(label, id=f"setup-provider-{provider}")
            yield Label("Model", id="setup-model-label")
            yield Input(
                value=self.config.llm.model,
                id="setup-model",
                classes="setup-input",
                disabled=self.provider == "openai_chatgpt",
            )
            yield Label("Base URL", id="setup-base-url-label")
            yield Input(
                value=self.config.llm.base_url,
                id="setup-base-url",
                classes="setup-input",
                disabled=self.provider == "openai_chatgpt",
            )
            yield Label("API key environment variable", id="setup-api-env-label")
            yield Input(
                value=self.config.llm.api_key_env,
                id="setup-api-env",
                classes="setup-input",
                disabled=self.provider == "openai_chatgpt",
            )
            yield Label("API key", id="setup-api-key-label")
            yield Input(
                password=True,
                placeholder="leave blank to keep existing key",
                id="setup-api-key",
                classes="setup-input",
                disabled=self.provider == "openai_chatgpt",
            )
            with Horizontal(id="setup-actions"):
                yield Button("Save", variant="primary", id="setup-save")
                yield Button("Cancel", id="setup-cancel")

    def on_mount(self) -> None:
        self._toggle_provider_fields()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "setup-save":
            self.dismiss(
                ProviderSetup(
                    provider=self.provider,
                    model=self.query_one("#setup-model", Input).value,
                    base_url=self.query_one("#setup-base-url", Input).value,
                    api_key_env=self.query_one("#setup-api-env", Input).value,
                    api_key=self.query_one("#setup-api-key", Input).value,
                )
            )
            return
        if button_id == "setup-cancel":
            self.dismiss(None)
            return
        prefix = "setup-provider-"
        if button_id.startswith(prefix):
            self._select_provider(button_id.removeprefix(prefix))

    def _select_provider(self, provider: str) -> None:
        self.provider = provider
        self.query_one("#setup-model", Input).value = default_for(provider, "model")
        self.query_one("#setup-base-url", Input).value = default_for(provider, "base_url")
        self.query_one("#setup-api-env", Input).value = default_for(provider, "api_key_env")
        disabled = provider == "openai_chatgpt"
        for selector in ("#setup-model", "#setup-base-url", "#setup-api-env", "#setup-api-key"):
            self.query_one(selector, Input).disabled = disabled
        self._toggle_provider_fields()

    def _toggle_provider_fields(self) -> None:
        visible = self.provider != "openai_chatgpt"
        for selector in (
            "#setup-model-label",
            "#setup-model",
            "#setup-base-url-label",
            "#setup-base-url",
            "#setup-api-env-label",
            "#setup-api-env",
            "#setup-api-key-label",
            "#setup-api-key",
        ):
            self.query_one(selector).display = visible


class OpenAICallbackScreen(ModalScreen[str | None]):
    CSS = """
    OpenAICallbackScreen { align: center middle; }
    #openai-callback-dialog {
        width: 100;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    #openai-login-actions, #openai-callback-actions { height: 3; margin-top: 1; }
    """

    def __init__(self, auth_url: str) -> None:
        super().__init__()
        self.auth_url = auth_url

    def compose(self) -> ComposeResult:
        with Vertical(id="openai-callback-dialog"):
            yield Label("OpenAI browser login")
            yield Static("Copy the login link, open it in your browser, and sign in to OpenAI.")
            yield Input(value=self.auth_url, select_on_focus=True, id="openai-login-url")
            with Horizontal(id="openai-login-actions"):
                yield Button("Copy login link", variant="primary", id="openai-login-copy")
            yield Static("Then paste the complete localhost return URL below. It is masked and not saved.")
            yield Input(password=True, placeholder="http://localhost:.../auth/callback?...", id="openai-callback")
            with Horizontal(id="openai-callback-actions"):
                yield Button("Continue", variant="primary", id="openai-callback-submit")
                yield Button("Cancel", id="openai-callback-cancel")

    def on_mount(self) -> None:
        self.query_one("#openai-callback", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "openai-login-copy":
            try:
                self.app.copy_to_clipboard(self.auth_url)
            except Exception as exc:
                self.notify(f"Clipboard unavailable: {exc}", severity="error")
                return
            if _set_system_clipboard_text(self.auth_url):
                self.notify("OpenAI login link copied.")
            else:
                login_input = self.query_one("#openai-login-url", Input)
                login_input.focus()
                self.notify("System clipboard unavailable. The login link is selected.", severity="warning")
        elif event.button.id == "openai-callback-submit":
            self.dismiss(self.query_one("#openai-callback", Input).value)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class TelegramSetupScreen(ModalScreen[TelegramSetup | None]):
    CSS = """
    TelegramSetupScreen { align: center middle; }
    #connector-dialog {
        width: 68;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    #connector-token { margin: 1 0; }
    #connector-actions { height: 3; }
    """

    def __init__(self, enabled: bool) -> None:
        super().__init__()
        self.enabled = enabled

    def compose(self) -> ComposeResult:
        with Vertical(id="connector-dialog"):
            yield Label("Telegram connector")
            yield Static("Enter the BotFather token. It is stored only in the protected environment file.")
            yield Input(password=True, placeholder="bot token; blank keeps existing token", id="connector-token")
            with Horizontal(id="connector-actions"):
                yield Button("Verify & connect", variant="primary", id="connector-connect")
                if self.enabled:
                    yield Button("Disable", variant="error", id="connector-disable")
                yield Button("Cancel", id="connector-cancel")

    def on_mount(self) -> None:
        self.query_one("#connector-token", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "connector-connect":
            self.dismiss(TelegramSetup(True, self.query_one("#connector-token", Input).value))
        elif event.button.id == "connector-disable":
            self.dismiss(TelegramSetup(False))
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(TelegramSetup(True, event.value))


class ConnectorSelectionScreen(ModalScreen[str | None]):
    CSS = """
    ConnectorSelectionScreen { align: center middle; }
    #connector-selection {
        width: 62;
        height: auto;
        border: thick $primary;
        background: $panel;
        padding: 1 2;
    }
    .connector-choice { width: 100%; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="connector-selection"):
            yield Label("Connector setup")
            for definition in connector_definitions():
                yield Static(definition.description)
                yield Button(
                    definition.label,
                    id=f"connector-choice-{definition.id}",
                    classes="connector-choice",
                )
            yield Button("Cancel", id="connector-choice-cancel", classes="connector-choice")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "connector-choice-cancel":
            self.dismiss(None)
        else:
            self.dismiss(button_id.removeprefix("connector-choice-"))


class MCPSelectionScreen(ModalScreen[str | None]):
    CSS = """
    MCPSelectionScreen { align: center middle; }
    #mcp-selection { width: 68; height: auto; max-height: 80%; border: thick $primary; background: $panel; padding: 1 2; }
    .mcp-choice { width: 100%; margin-top: 1; }
    """

    def __init__(self, servers) -> None:
        super().__init__()
        self.servers = servers

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="mcp-selection"):
            yield Label("MCP servers")
            yield Static("Select a server to edit or add a new isolated MCP connection.")
            for server in self.servers:
                state = "enabled" if server.enabled else "disabled"
                yield Button(
                    f"{server.id}  ({server.transport}, {state})", id=f"mcp-edit-{server.id}", classes="mcp-choice"
                )
            yield Button("Add MCP server", variant="primary", id="mcp-add", classes="mcp-choice")
            yield Button("Cancel", id="mcp-cancel", classes="mcp-choice")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "mcp-add":
            self.dismiss("")
        elif button_id == "mcp-cancel":
            self.dismiss(None)
        else:
            self.dismiss(button_id.removeprefix("mcp-edit-"))


class MCPSetupScreen(ModalScreen[MCPServerSetup | None]):
    CSS = """
    MCPSetupScreen { align: center middle; }
    #mcp-dialog { width: 88; height: 90%; border: thick $primary; background: $panel; padding: 1 2; }
    #mcp-fields { height: 1fr; }
    .mcp-input { margin-bottom: 1; }
    .mcp-select { margin-bottom: 1; }
    #mcp-actions { height: 3; margin-top: 1; }
    """

    def __init__(self, server=None) -> None:
        super().__init__()
        self.server = server

    def compose(self) -> ComposeResult:
        server = self.server
        with Vertical(id="mcp-dialog"):
            yield Label("MCP server setup")
            yield Static(
                "Tools remain unavailable until this connection is validated. Secrets are stored only in the protected env file."
            )
            with VerticalScroll(id="mcp-fields"):
                yield Label("Server ID")
                yield Input(
                    value=server.id if server else "",
                    placeholder="company_tools",
                    disabled=bool(server),
                    id="mcp-id",
                    classes="mcp-input",
                )
                yield Label("Transport")
                yield Select(
                    [("stdio", "stdio"), ("Streamable HTTP", "streamable_http")],
                    value=server.transport if server else "stdio",
                    allow_blank=False,
                    id="mcp-transport",
                    classes="mcp-select",
                )
                yield Label("Command (stdio)")
                yield Input(
                    value=server.command if server else "", placeholder="python3", id="mcp-command", classes="mcp-input"
                )
                yield Label("Arguments as JSON array (stdio)")
                yield Input(
                    value=json.dumps(server.args if server else []),
                    placeholder='["server.py"]',
                    id="mcp-args",
                    classes="mcp-input",
                )
                yield Label("MCP URL (Streamable HTTP)")
                yield Input(
                    value=server.url if server else "",
                    placeholder="https://example.com/mcp",
                    id="mcp-url",
                    classes="mcp-input",
                )
                yield Label("Environment variable names, comma separated")
                yield Input(
                    value=", ".join(server.environment_variables) if server else "", id="mcp-env", classes="mcp-input"
                )
                yield Label("Bearer token environment variable")
                yield Input(
                    value=server.bearer_token_env if server else "",
                    placeholder="MCP_ACCESS_TOKEN",
                    id="mcp-token-env",
                    classes="mcp-input",
                )
                yield Label("Bearer token (blank keeps existing)")
                yield Input(password=True, id="mcp-token", classes="mcp-input")
                yield Label("Allowed tool names, comma separated; use * only when all server tools are trusted")
                yield Input(
                    value=", ".join(server.tool_allowlist) if server else "", id="mcp-tools", classes="mcp-input"
                )
                yield Label("Risk")
                yield Select(
                    [("High", "high"), ("Medium", "medium"), ("Low", "low")],
                    value=server.risk_level if server else "high",
                    allow_blank=False,
                    id="mcp-risk",
                    classes="mcp-select",
                )
                yield Label("Timeout seconds")
                yield Input(value=str(server.timeout_seconds if server else 60), id="mcp-timeout", classes="mcp-input")
                yield Checkbox("Enabled", value=server.enabled if server else True, id="mcp-enabled")
                yield Checkbox(
                    "Require confirmation for every MCP tool call",
                    value=server.require_confirmation if server else True,
                    id="mcp-confirm",
                )
            with Horizontal(id="mcp-actions"):
                yield Button("Validate & save", variant="primary", id="mcp-save")
                yield Button("Cancel", id="mcp-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "mcp-save":
            self.dismiss(None)
            return
        try:
            args = json.loads(self.query_one("#mcp-args", Input).value or "[]")
            if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
                raise ValueError("Arguments must be a JSON array of strings.")
            setup = MCPServerSetup(
                id=self.query_one("#mcp-id", Input).value,
                enabled=self.query_one("#mcp-enabled", Checkbox).value,
                transport=str(self.query_one("#mcp-transport", Select).value),
                command=self.query_one("#mcp-command", Input).value,
                args=tuple(args),
                url=self.query_one("#mcp-url", Input).value,
                environment_variables=tuple(_csv(self.query_one("#mcp-env", Input).value)),
                bearer_token_env=self.query_one("#mcp-token-env", Input).value.strip(),
                bearer_token=self.query_one("#mcp-token", Input).value,
                tool_allowlist=tuple(_csv(self.query_one("#mcp-tools", Input).value)),
                risk_level=str(self.query_one("#mcp-risk", Select).value),
                require_confirmation=self.query_one("#mcp-confirm", Checkbox).value,
                timeout_seconds=float(self.query_one("#mcp-timeout", Input).value),
            )
            setup.server_config()
        except (ValueError, TypeError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.dismiss(setup)


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
        height: 100%;
        background: $panel;
        border: solid $primary;
        padding: 1 1;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
        scrollbar-color: $accent;
        scrollbar-background: $panel;
    }

    #logo {
        width: 24;
        height: 12;
        min-height: 12;
        max-height: 12;
        align-horizontal: center;
        content-align: center middle;
        text-align: center;
        color: $accent;
        margin: 0 1;
    }

    #brand {
        width: 100%;
        content-align: center middle;
        text-align: center;
        margin-bottom: 1;
    }

    #status {
        width: 100%;
        height: auto;
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

    #spinner {
        width: 100%;
        height: 1;
        background: transparent;
        color: $text;
        text-style: bold;
        content-align: center middle;
        text-align: center;
        padding: 0;
    }

    #boot-status {
        display: none;
        width: 100%;
        height: auto;
        min-height: 10;
        border: solid $primary;
        background: $boost;
        color: $text;
        padding: 1 2;
        margin-bottom: 1;
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
        Binding("ctrl+v", "paste_clipboard", "Paste", show=False),
        Binding("ctrl+y", "copy_selected_or_last", "Copy"),
        Binding("ctrl+shift+y", "copy_transcript", "Copy all"),
        Binding("ctrl+s", "selection_mode", "Select"),
        Binding("f2", "cycle_theme", "Theme"),
        Binding("f4", "push_to_talk", "Talk"),
        Binding("f5", "status", "Status"),
        Binding("f6", "skills", "Skills"),
        Binding("f7", "setup", "Setup"),
        Binding("escape", "stop_speaking", "Stop voice", show=False, priority=True),
    ]

    THEMES = ("ulysses_dark", "ulysses_light", "terminal")
    THEME_ALIASES = {
        "ulysses_dark": "textual-dark",
        "ulysses_light": "textual-light",
        # "terminal" means minimal colors, but still maps to a known Textual theme.
        "terminal": "textual-dark",
    }
    SPINNER_FRAMES = ("|", "/", "-", "\\")
    ACTIVITY_FRAMES = (
        "[█░░░░░]",
        "[██░░░░]",
        "[███░░░]",
        "[████░░]",
        "[█████░]",
        "[██████]",
    )

    def __init__(self, orchestrator, voice_io=None) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.voice_io = voice_io
        self.artifacts = ArtifactManager.from_config(orchestrator.config)
        self.last_assistant_text = ""
        self.transcript_plain: list[str] = []
        self._last_user_text = ""
        self._command_history: list[str] = []
        self._command_history_index: int | None = None
        self._command_history_draft = ""
        self._last_response_wants_report = False
        self._assessment_project: AssessmentProject | None = None
        self._assessment_install_attempted = False
        self._assessment_resume_target: str | None = None
        self._assessment_pending_check: AssessmentCheck | None = None
        self._assessment_results: list[AssessmentResult] = []
        self._assessment_completed_commands: set[str] = set()
        self._pending_paste: tuple[str, Artifact, str] | None = None
        self.theme_name = getattr(orchestrator.config.tui, "theme", "ulysses_dark")
        self._waiting = False
        self._speaking = False
        self._listening = False
        self._listen_cancel_requested = False
        self._speech_id = 0
        self._activity_text = "thinking"
        self._spinner = cycle(self.ACTIVITY_FRAMES)
        self.selection_mode = False
        self._autonomous_running = False
        self._subagent_collection_running = False
        self._boot_started_at = 0.0
        self._boot_message = ""
        self._boot_spoken_message = ""
        self._boot_frame_index = 0
        self._boot_timer = None
        self._boot_complete = False
        self._logo_frame_index = 0
        self._provider_onboarding = not bool(getattr(orchestrator.llm, "configured", True))
        self.updates = UpdateManager(orchestrator.config.updates)
        self.title = self._local_version_text()
        self.connectors = ConnectorManager.from_config(
            orchestrator.config,
            self._handle_connector_message,
            self._connector_event_from_worker,
        )
        if getattr(self.orchestrator, "mcp", None):
            self.orchestrator.mcp.event_callback = self._mcp_event_from_worker
        if hasattr(self.orchestrator, "set_activity_callback"):
            self.orchestrator.set_activity_callback(self._activity_from_worker)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with VerticalScroll(id="sidebar"):
                yield Static(ULYSSES_SIDEBAR_LOGO, id="logo")
                yield Label(
                    self._brand_version_text(),
                    id="brand",
                    classes="section-title",
                )
                yield Static(id="status")
                yield Label("Shortcuts", classes="section-title")
                yield Static(
                    "Ctrl+U voice on/off\n"
                    "Ctrl+M mute\n"
                    "Ctrl+V paste\n"
                    "Ctrl+Y copy selected/last\n"
                    "Ctrl+Shift+Y copy all\n"
                    "Esc stop voice\n"
                    "Ctrl+S select mode\n"
                    "Ctrl+N new session\n"
                    "Ctrl+L clear\n"
                    "F2 theme\n"
                    "F4 push to talk\n"
                    "F5 status\n"
                    "F6 skills\n"
                    "F7 setup\n"
                    "Ctrl+Q quit",
                    classes="muted",
                )
                yield Label("Slash", classes="section-title")
                yield Static(
                    "/voice on|off\n"
                    "/talk\n"
                    "/mute\n"
                    "/run <cmd>\n"
                    "/create-skill <name> <request>\n"
                    "/autonomous on|off\n"
                    "/***autonomous on|off\n"
                    "/confirm [token]\n"
                    "/memory\n"
                    "/context\n"
                    "/reload\n"
                    "/update [install]\n"
                    "/sessions\n"
                    "/downloads\n"
                    "/theme [name]\n"
                    "/setup providers\n"
                    "/setup connectors\n"
                    "/setup mcp\n"
                    "/mcp [servers|tools|reconnect]\n"
                    "/copy [selected|all]\n"
                    "/select on|off\n"
                    "/quit",
                    classes="muted",
                )
            with Vertical(id="main"):
                yield Static("", id="boot-status")
                yield TranscriptLog(id="transcript", wrap=True, highlight=True, markup=True)
                yield Static("", id="spinner", classes="muted")
                yield ComposerInput(placeholder="Ask Ulysses, paste text, or type /command ...", id="composer")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_theme(self.theme_name, announce=False)
        talk_key = str(getattr(self.orchestrator.config.audio, "push_to_talk_key", "f4")).strip().lower()
        if talk_key and talk_key != "f4":
            self.bind(talk_key, "push_to_talk", description="Talk", show=True)
        boot_message = startup_brief(self.orchestrator, self.voice_io)
        self._start_boot_sequence(boot_message, spoken_startup_brief(self.orchestrator, self.voice_io))
        self._refresh_status()
        self.set_interval(0.12, self._tick_spinner)
        self.set_interval(2.0, self._refresh_status)
        self.set_interval(1.0, self._maybe_collect_subagent_reports)
        self.set_interval(self._autonomous_timer_seconds(), self._maybe_autonomous)
        self.query_one("#composer", Input).focus()
        if self.orchestrator.config.updates.enabled and self.orchestrator.config.updates.check_on_startup:
            Thread(target=self._check_update_in_thread, args=(False,), daemon=True).start()

    def _start_boot_sequence(self, message: str, spoken_message: str) -> None:
        self._boot_started_at = time.monotonic()
        self._boot_message = message
        self._boot_spoken_message = spoken_message
        self._boot_frame_index = 0
        self._boot_complete = False
        widget = self.query_one("#boot-status", Static)
        widget.display = True
        widget.update(_boot_progress(message, 0, self.SPINNER_FRAMES[0]))
        self._boot_timer = self.set_interval(0.12, self._tick_boot_sequence)

    def _tick_boot_sequence(self) -> None:
        if self._boot_complete:
            return
        elapsed = time.monotonic() - self._boot_started_at
        if elapsed >= 2.3:
            self._boot_complete = True
            if self._boot_timer is not None:
                self._boot_timer.pause()
            self.query_one("#boot-status", Static).display = False
            self._write_system(self._boot_message)
            self.connectors.start_all()
            if self._provider_onboarding:
                guidance = (
                    "No AI provider is configured. Press F7 and choose Provider Setup, then select a provider and "
                    "enter its connection details or API key. I have opened provider setup for you."
                )
                self._write_assistant(guidance)
                self._speak(guidance)
                self.set_timer(0.4, self.action_setup)
            else:
                Thread(target=self._startup_greeting_in_thread, daemon=True).start()
            return
        completed = min(5, int(elapsed / 0.36))
        self._boot_frame_index = (self._boot_frame_index + 1) % len(self.SPINNER_FRAMES)
        frame = self.SPINNER_FRAMES[self._boot_frame_index]
        self.query_one("#boot-status", Static).update(_boot_progress(self._boot_message, completed, frame))

    def _startup_greeting_in_thread(self) -> None:
        greeting = self.orchestrator.startup_greeting()
        self.call_from_thread(self._deliver_startup_greeting, greeting)

    def _deliver_startup_greeting(self, greeting: str) -> None:
        self._write_assistant(greeting)
        self._speak(f"{self._boot_spoken_message} {greeting}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self._remember_command(text)
        event.input.value = ""
        self._submit_text(text)

    def _remember_command(self, text: str) -> None:
        if text and (not self._command_history or self._command_history[-1] != text):
            self._command_history.append(text)
            del self._command_history[:-200]
        self._command_history_index = None
        self._command_history_draft = ""

    def _navigate_command_history(self, direction: str) -> bool:
        if not self._command_history:
            return False
        composer = self.query_one("#composer", Input)
        if direction == "up":
            if self._command_history_index is None:
                self._command_history_draft = composer.value
                self._command_history_index = len(self._command_history) - 1
            elif self._command_history_index > 0:
                self._command_history_index -= 1
        elif direction == "down":
            if self._command_history_index is None:
                return False
            if self._command_history_index < len(self._command_history) - 1:
                self._command_history_index += 1
            else:
                self._command_history_index = None
                composer.value = self._command_history_draft
                composer.cursor_position = len(composer.value)
                return True
        else:
            return False
        composer.value = self._command_history[self._command_history_index]
        composer.cursor_position = len(composer.value)
        return True

    def _submit_text(self, text: str) -> None:
        pending_paste = self._pending_paste
        pasted_artifact: Artifact | None = None
        display_text = text
        if pending_paste is not None:
            pasted_text, pasted_artifact, marker = pending_paste
            typed_text = text.replace(marker, "").strip()
            original_text = f"{typed_text}\n\n{pasted_text}".strip() if typed_text else pasted_text
            display_text = f"{typed_text}\n{marker}".strip()
            self._pending_paste = None
        else:
            original_text = text
        if not original_text:
            return
        if pending_paste is None and original_text.startswith("/"):
            self._command(original_text)
            return
        if self.orchestrator.pending_tool_requires_sudo_password():
            pending = self.orchestrator.pending_tool or {}
            self._open_sudo_password_dialog(pending.get("token"))
            return
        new_assessment = is_assessment_request(original_text)
        assessment_request = new_assessment or (
            self._assessment_project is not None and is_assessment_continuation(original_text)
        )
        if (
            assessment_request
            and self.orchestrator.pending_tool is not None
            and not self.orchestrator.pending_tool_requires_sudo_password()
        ):
            self.orchestrator.pending_tool = None
        if new_assessment:
            self._assessment_project = self.artifacts.create_assessment_project(
                self.orchestrator.session_id, original_text
            )
            self._assessment_install_attempted = False
            self._assessment_resume_target = None
            self._assessment_pending_check = None
            self._assessment_results = []
            self._assessment_completed_commands = set()
        self._set_project_result_capture(self._assessment_project)
        text = (
            attachment_prompt(original_text, pasted_artifact)
            if pasted_artifact is not None
            else self._submission_text_with_attachments(original_text)
        )
        if self._assessment_project:
            direct_command = assessment_command_for_text(original_text, _project_request(self._assessment_project))
            assessment_turn = assessment_request or direct_command is not None
            if direct_command and not new_assessment:
                self._last_user_text = original_text
                self._last_response_wants_report = True
                self._write_user(display_text)
                if new_assessment:
                    self._write_system(f"Assessment project created:\n{self._assessment_project.path}")
                self._refresh_status()
                self._start_waiting()
                target = (
                    assessment_target(original_text)
                    or assessment_target(_project_request(self._assessment_project))
                    or "target"
                )
                Thread(
                    target=self._assessment_command_in_thread,
                    args=(direct_command, self._assessment_project, target),
                    daemon=True,
                ).start()
                return
            target = assessment_target(original_text) or assessment_target(_project_request(self._assessment_project))
            if assessment_turn and target:
                self._last_user_text = original_text
                self._last_response_wants_report = True
                self._write_user(display_text)
                if new_assessment:
                    self._write_system(f"Assessment project created:\n{self._assessment_project.path}")
                self._refresh_status()
                self._start_waiting()
                Thread(
                    target=self._assessment_baseline_in_thread,
                    args=(self._assessment_project, target, direct_command),
                    daemon=True,
                ).start()
                return
            if assessment_turn:
                text = self._assessment_prompt(text, self._assessment_project)
                if new_assessment:
                    self._write_system(f"Assessment project created:\n{self._assessment_project.path}")
        self._last_user_text = original_text
        self._last_response_wants_report = assessment_request or (
            is_report_request(original_text) and not is_skill_creation_request(original_text)
        )
        self._write_user(display_text)
        self._refresh_status()
        self._start_waiting()
        Thread(target=self._answer_in_thread, args=(text,), daemon=True).start()

    def on_paste(self, event: Paste) -> None:
        if getattr(self.focused, "id", None) != "composer":
            return
        text = event.text
        if not text:
            return
        event.prevent_default()
        event.stop()
        self._handle_composer_paste(text)

    def action_paste_clipboard(self) -> None:
        if getattr(self.focused, "id", None) != "composer":
            self.query_one("#composer", Input).focus()
        text = self._clipboard_text()
        if not text:
            if _system_clipboard_backend() is None:
                self._write_system(
                    "System clipboard access is unavailable. Use Ctrl+Shift+V for terminal paste, "
                    "or install xclip on X11 / wl-clipboard on Wayland to enable Ctrl+V."
                )
            else:
                self._write_system("Clipboard is empty.")
            return
        self._handle_composer_paste(text)

    def _handle_composer_paste(self, text: str) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not should_attach_clipboard_text(text, self.orchestrator.config.context.max_chars):
            self._insert_composer_text(text)
            return
        composer = self.query_one("#composer", Input)
        prefix = composer.value
        if self._pending_paste is not None:
            previous_text, _, previous_marker = self._pending_paste
            text = f"{previous_text}\n{text}"
            prefix = prefix.replace(previous_marker, "").strip()
        artifact = self.artifacts.save_text_attachment(self.orchestrator.session_id, text)
        marker = f"[Pasted text attached: {artifact.path.name}, {artifact.chars} chars]"
        self._pending_paste = (text, artifact, marker)
        composer.value = f"{prefix} {marker}".strip()
        composer.cursor_position = len(composer.value)
        self._write_system(f"Clipboard text saved as attachment:\n{artifact.path}")

    def _insert_composer_text(self, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
        self.query_one("#composer", Input).insert_text_at_cursor(normalized)

    def _clipboard_text(self) -> str:
        app_clipboard = str(self.clipboard or "")
        system_clipboard = _system_clipboard_text()
        return system_clipboard or app_clipboard

    def _submission_text_with_attachments(self, text: str) -> str:
        if should_store_large_paste(text, self.orchestrator.config.context.max_chars):
            artifact = self.artifacts.save_text_attachment(self.orchestrator.session_id, text)
            self._write_system(f"Large paste saved as text file:\n{artifact.path}")
            return attachment_prompt(text, artifact)
        return text

    def _answer_in_thread(self, text: str) -> None:
        try:
            answer = self.orchestrator.handle_text(text)
        except Exception as exc:
            self.call_from_thread(self._finish_error, str(exc))
            return
        self.call_from_thread(self._finish_answer, answer)

    def _assessment_command_in_thread(
        self, command: str, project: AssessmentProject | None = None, target: str | None = None
    ) -> None:
        try:
            answer, ok = _run_system_command_capture(self.orchestrator, command)
        except Exception as exc:
            self.call_from_thread(self._finish_error, str(exc))
            return
        if project is not None and target is not None and self.orchestrator.pending_tool is None:
            try:
                self.artifacts.save_project_result(project, command, answer)
                report = _assessment_report_markdown(target, [command], [(command, answer, ok)])
                artifact = self.artifacts.save_project_markdown_report(project, report)
                answer = f"{report}\n\nReport saved as Markdown:\n{artifact.path}"
            except Exception as exc:
                answer = f"{answer}\n\nReport save failed: {exc}"
            self.call_from_thread(self._finish_assessment_baseline, answer)
            return
        self.call_from_thread(self._finish_answer, answer)

    def _assessment_baseline_in_thread(
        self,
        project: AssessmentProject,
        target: str,
        preferred_command: str | None = None,
    ) -> None:
        checks = assessment_checks(target, preferred_command)
        results = list(self._assessment_results)
        for check in checks:
            if check.command in self._assessment_completed_commands:
                continue
            self.call_from_thread(self._set_activity, f"assessment: {check.id}")
            try:
                output, ok = _run_system_command_capture(self.orchestrator, check.command)
            except Exception as exc:
                output = f"Command failed before execution: {exc}"
                ok = False
            try:
                self.artifacts.save_project_result(project, check.id, output)
            except Exception:
                pass
            if self.orchestrator.pending_tool is not None:
                results.append(AssessmentResult(check, output, ok))
                self._assessment_pending_check = check
                self._assessment_resume_target = target
                break
            completed = AssessmentResult(check, output, ok)
            results.append(completed)
            self._assessment_results.append(completed)
            self._assessment_completed_commands.add(check.command)
        packages = missing_tool_packages(results)
        if (
            packages
            and self.orchestrator.config.skills.command.install_missing_assessment_tools
            and not self._assessment_install_attempted
            and self.orchestrator.pending_tool is None
        ):
            self._assessment_install_attempted = True
            missing_commands = {
                result.check.command
                for result in self._assessment_results
                if "command not found:" in result.output.lower()
            }
            self._assessment_results = [
                result for result in self._assessment_results if result.check.command not in missing_commands
            ]
            self._assessment_completed_commands.difference_update(missing_commands)
            installer = self.artifacts.save_project_script(
                project,
                "install-missing-tools",
                missing_tool_installer_script(),
            )
            install_check = AssessmentCheck(
                "install-missing-tools",
                "Recovery",
                f"sudo python3 {installer.path} {' '.join(packages)}",
            )
            install_output, install_ok = _run_system_command_capture(self.orchestrator, install_check.command)
            self.artifacts.save_project_result(project, install_check.id, install_output)
            results.append(AssessmentResult(install_check, install_output, install_ok))
            if self.orchestrator.pending_tool is not None:
                self._assessment_pending_check = install_check
                self._assessment_resume_target = target
        report = render_assessment_report(target, results)
        try:
            artifact = self.artifacts.save_project_markdown_report(project, report)
            report = f"{report}\n\nReport saved as Markdown:\n{artifact.path}"
        except Exception as exc:
            report = f"{report}\n\nReport save failed: {exc}"
        self.call_from_thread(self._finish_assessment_baseline, report)

    def _finish_answer(self, answer: str) -> None:
        self._stop_waiting()
        should_speak = self._should_speak_response(answer)
        save_final_assessment = self._assessment_project is not None and (
            is_final_assessment_report(answer) or is_report_request(self._last_user_text)
        )
        save_standalone_report = self._assessment_project is None and self._last_response_wants_report
        if (save_final_assessment or save_standalone_report) and self.orchestrator.pending_tool is None:
            try:
                if self._assessment_project:
                    artifact = self.artifacts.save_project_markdown_report(self._assessment_project, answer)
                else:
                    artifact = self.artifacts.save_markdown_report(self.orchestrator.session_id, answer)
                answer = f"{answer}\n\nReport saved as Markdown:\n{artifact.path}"
            except Exception as exc:
                answer = f"{answer}\n\nReport save failed: {exc}"
            self._last_response_wants_report = False
            if save_final_assessment:
                self._set_project_result_capture(self._assessment_project)
        self._write_assistant(answer)
        self._refresh_status()
        if self.orchestrator.pending_tool_requires_sudo_password():
            pending = self.orchestrator.pending_tool or {}
            token = pending.get("token")
            self._open_sudo_password_dialog(token)
        if should_speak:
            self._speak(answer)

    def _finish_assessment_baseline(self, answer: str) -> None:
        self._stop_waiting()
        self._write_assistant(answer)
        if self.orchestrator.pending_tool is None:
            self._last_response_wants_report = False
        self._refresh_status()
        if self.orchestrator.pending_tool_requires_sudo_password():
            pending = self.orchestrator.pending_tool or {}
            token = pending.get("token")
            self._open_sudo_password_dialog(token)

    def _finish_confirmed_tool(self, tool_result: str, *, as_assistant: bool = False) -> None:
        self._stop_waiting()
        if as_assistant:
            self._write_assistant(tool_result)
            self._speak(tool_result)
        else:
            self._write_tool(tool_result)
        if self.orchestrator.pending_tool is not None:
            self._refresh_status()
            if self.orchestrator.pending_tool_requires_sudo_password():
                pending = self.orchestrator.pending_tool or {}
                self._open_sudo_password_dialog(pending.get("token"))
            return
        resume_skill = self.orchestrator.consume_skill_resume()
        if resume_skill:
            self._start_waiting()
            Thread(
                target=self._resume_created_skill_in_thread,
                args=(resume_skill, self._last_user_text),
                daemon=True,
            ).start()
            return
        if self._assessment_resume_target and self._assessment_project and self._assessment_pending_check:
            target = self._assessment_resume_target
            check = self._assessment_pending_check
            self._assessment_resume_target = None
            self._assessment_pending_check = None
            lowered = tool_result.lower()
            ok = not any(
                marker in lowered for marker in ("failed", "error", "incorrect password", "not found", "timed out")
            )
            completed = AssessmentResult(check, tool_result, ok)
            self._assessment_results.append(completed)
            self._assessment_completed_commands.add(check.command)
            try:
                self.artifacts.save_project_result(self._assessment_project, check.id, tool_result)
            except Exception:
                pass
            self._start_waiting()
            Thread(
                target=self._assessment_baseline_in_thread,
                args=(self._assessment_project, target),
                daemon=True,
            ).start()
            return
        if not self._last_response_wants_report:
            self._refresh_status()
            return
        self._start_waiting()
        Thread(target=self._report_from_tool_in_thread, args=(self._last_user_text, tool_result), daemon=True).start()

    def _resume_created_skill_in_thread(self, skill_name: str, original_request: str) -> None:
        prompt = (
            f"Skill `{skill_name}` is now active. Continue the prior request below and use `{skill_name}` now when its "
            f"capability is applicable. Do not recreate the skill.\n\nPrior request:\n{original_request}"
        )
        try:
            answer = self.orchestrator.handle_text(prompt)
        except Exception as exc:
            self.call_from_thread(self._finish_error, str(exc))
            return
        self.call_from_thread(self._finish_answer, answer)

    def _report_from_tool_in_thread(self, user_request: str, tool_result: str) -> None:
        try:
            answer = self.orchestrator.answer_from_tool_result(user_request, tool_result)
        except Exception as exc:
            self.call_from_thread(self._finish_error, str(exc))
            return
        self.call_from_thread(self._finish_answer, answer)

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
            self._write_system(
                "\n".join(f"{row['id']}  {row['title']}  {row['updated_at']}" for row in rows) or "No sessions."
            )
        elif cmd == "/downloads":
            self._write_downloads()
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
                self._write_system(
                    "Forgot memory." if self.orchestrator.memory.forget(parts[1]) else "Memory not found."
                )
        elif cmd == "/confirm":
            token = parts[1] if len(parts) > 1 else None
            if self.orchestrator.pending_tool_requires_sudo_password():
                self._open_sudo_password_dialog(token)
            else:
                pending = self.orchestrator.pending_tool or {}
                as_assistant = pending.get("name") == "system_command" and bool(
                    pending.get("resume_after_confirmation")
                )
                result = self.orchestrator.confirm_pending_tool(token)
                self._finish_confirmed_tool(result, as_assistant=as_assistant)
        elif cmd == "/run" and len(parts) > 1:
            self._write_tool(self.orchestrator._run_skill("system_command", {"command": " ".join(parts[1:])}))
        elif cmd == "/create-skill" and len(parts) > 2:
            self._start_waiting()
            Thread(
                target=self._create_skill_in_thread,
                args=(parts[1], " ".join(parts[2:])),
                daemon=True,
            ).start()
            return
        elif cmd in {"/autonomous", "/***autonomous"}:
            self._autonomous_command(parts)
        elif cmd in {"/status", "/config"}:
            self.action_status()
        elif cmd == "/reload":
            self.action_reload_config()
        elif cmd == "/update":
            if len(parts) > 1 and parts[1].lower() == "install":
                self._start_waiting()
                Thread(target=self._install_update_in_thread, daemon=True).start()
            elif len(parts) == 1 or parts[1].lower() == "check":
                self._write_system("Checking GitHub main for updates...")
                Thread(target=self._check_update_in_thread, args=(True,), daemon=True).start()
            else:
                self._write_system("Usage: /update or /update install")
        elif cmd == "/mcp":
            self._mcp_command(parts)
        elif cmd == "/setup":
            if len(parts) > 1 and parts[1].lower() in {"provider", "providers"}:
                self.action_setup()
            elif len(parts) > 1 and parts[1].lower() in {"connector", "connectors"}:
                self.action_connector_setup()
            elif len(parts) > 1 and parts[1].lower() == "mcp":
                self.action_mcp_setup()
            else:
                self._write_system("Usage: /setup providers, /setup connectors, or /setup mcp")
        elif cmd == "/context":
            self._write_system(str(self.orchestrator.context_usage()))
        elif cmd == "/voice":
            self._voice_command(parts)

        elif cmd == "/talk":
            self.action_push_to_talk()
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
            elif len(parts) > 1 and parts[1] == "selected":
                self.action_copy_selected()
            else:
                self.action_copy_selected_or_last()
        elif cmd == "/select":
            if len(parts) > 1 and parts[1].lower() in {"on", "off"}:
                self._set_selection_mode(parts[1].lower() == "on")
            else:
                self.action_selection_mode()
        elif cmd == "/export":
            self._write_system(
                str(
                    {
                        "sessions": self.orchestrator.sessions.list_sessions(),
                        "memory": [item.__dict__ for item in self.orchestrator.memory.items],
                    }
                )
            )
        else:
            self._write_error("Unknown command.")
        self._refresh_status()

    def _mcp_command(self, parts: list[str]) -> None:
        manager = self.orchestrator.mcp
        if not manager:
            self._write_system("MCP is unavailable.")
            return
        action = parts[1].lower() if len(parts) > 1 else "servers"
        if action in {"servers", "status"}:
            self._write_system(manager.status_detail())
        elif action == "tools":
            names = [
                manifest.name for manifest in self.orchestrator.skills.manifests() if manifest.name.startswith("mcp__")
            ]
            self._write_system("\n".join(names) or "No MCP tools are registered.")
        elif action == "reconnect" and len(parts) > 2:
            try:
                manager.discover(parts[2])
            except KeyError as exc:
                self._write_error(str(exc))
                return
            self._write_system(f"MCP reconnection started: {parts[2]}")
        else:
            self._write_system("Usage: /mcp servers, /mcp tools, or /mcp reconnect <server>")

    def _confirm_with_sudo_password(self, token: str | None, password: str | None) -> None:
        composer = self.query_one("#composer", Input)
        if password is None:
            composer.disabled = False
            self.orchestrator.cancel_pending_tool()
            self._assessment_resume_target = None
            self._assessment_pending_check = None
            self._write_system("Sudo command cancelled.")
            composer.focus()
            return
        pending = self.orchestrator.pending_tool or {}
        as_assistant = pending.get("name") == "system_command" and bool(pending.get("resume_after_confirmation"))
        command = str(pending.get("arguments", {}).get("command", ""))
        activity = "installing" if re.search(r"\b(?:install|installation)\b", command, re.I) else "running command"
        self._waiting = True
        self._activity_text = activity
        composer.disabled = True
        self.query_one("#spinner", Static).update(self._activity_renderable(next(self._spinner), f"Ulysses: {activity}"))
        self._refresh_status()
        Thread(
            target=self._confirm_pending_tool_in_thread,
            args=(token, password, as_assistant),
            daemon=True,
        ).start()

    def _confirm_pending_tool_in_thread(
        self,
        token: str | None,
        password: str,
        as_assistant: bool,
    ) -> None:
        try:
            result = self.orchestrator.confirm_pending_tool(token, {"sudo_password": password})
        except Exception:
            result = "Failed: command execution could not be completed"
        self.call_from_thread(self._finish_confirmed_tool, result, as_assistant=as_assistant)
        self._refresh_status()

    def _open_sudo_password_dialog(self, token: str | None) -> None:
        composer = self.query_one("#composer", Input)
        composer.value = ""
        composer.disabled = True
        self.push_screen(SudoPasswordScreen(), lambda password: self._confirm_with_sudo_password(token, password))

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
            self._speech_id += 1
            self.voice_io.interrupt()
            self._stop_speaking_ui()
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
            self._write_system(
                "Autonomous defense: on. I will run periodic host checks, log the evidence, adapt check frequency when risk rises, and report defensive actions."
            )
        elif mode == "off":
            self.orchestrator.set_autonomous(False)
            self._write_system("Autonomous defense: off.")
        elif mode == "now":
            self._start_autonomous_check(force=True)
        else:
            self._write_system("Usage: /autonomous on, /autonomous off, /autonomous now")
        self._refresh_status()

    def action_new_session(self) -> None:
        self._command("/new")

    def action_clear_transcript(self) -> None:
        self.query_one("#transcript", TranscriptLog).clear()
        self.transcript_plain.clear()
        self._write_system("Transcript cleared.")

    def action_status(self) -> None:
        cfg = self.orchestrator.config
        active = _active_command_policy(self.orchestrator)
        active_allowed = active.allowed if active is not None else set()
        configured_allowed = set(cfg.skills.command.allowed_commands)
        policy_ok = active_allowed == configured_allowed
        voice = "off"
        if self.voice_io:
            state = self.voice_io.state
            voice = f"{'on' if state.enabled else 'off'} / muted={'yes' if state.muted else 'no'} / STT={state.stt} / TTS={state.tts}"
        connector_on = any(status.configured for status in self.connectors.statuses())
        local_release = self.updates.installed_branch or f"v{cfg.agent_version}"
        update_state = self.updates.status.state
        update_ok = update_state == "current"
        update_icon = "[green]✓[/green]" if update_ok else "[yellow]![/yellow]"
        subagents = self.orchestrator.subagents.summary() if self.orchestrator.subagents else "disabled"
        mcp = self.orchestrator.mcp.summary() if self.orchestrator.mcp else "disabled"
        active_skill = getattr(self.orchestrator, "active_skill", None) or "idle"
        lines = [
            "[bold cyan]◆  ULYSSES SYSTEM STATUS[/bold cyan]",
            "[dim]Local runtime and capability overview[/dim]",
            "",
            "[bold]CORE[/bold]",
            f"[green]●[/green]  Session:    {escape(self.orchestrator.session_id)}",
            f"[green]●[/green]  Provider:   {escape(cfg.llm.provider)} / {escape(cfg.llm.model)}",
            f"[green]●[/green]  Release:    {escape(local_release)}",
            f"[cyan]◆[/cyan]  Latest:     {escape(self.updates.status.latest_branch or 'unknown')}",
            f"{update_icon}  Update:     {escape(self.updates.status.summary())}",
            "",
            "[bold]CAPABILITIES[/bold]",
            _dashboard_line("Voice", voice, "ok" if voice != "off" else "off"),
            _dashboard_line("Connector", "on" if connector_on else "off", "ok" if connector_on else "off"),
            _dashboard_line("Active skill", active_skill, "ok" if active_skill != "idle" else "off"),
            _dashboard_line("Sub-agents", subagents, "ok" if self.orchestrator.subagents else "off"),
            _dashboard_line("MCP", mcp, "ok" if self.orchestrator.mcp and mcp != "disabled" else "off"),
            _dashboard_line(
                "Autonomous",
                "on" if self.orchestrator.autonomous_enabled() else "off",
                "ok" if self.orchestrator.autonomous_enabled() else "off",
            ),
            "",
            "[bold]SECURITY[/bold]",
            _dashboard_line("Policy", "synchronized" if policy_ok else "mismatch", "ok" if policy_ok else "warning"),
            f"[cyan]◆[/cyan]  Commands:   {len(active_allowed)} active / {len(configured_allowed)} configured",
            _dashboard_line("Godmode", "on" if cfg.skills.command.godmode else "off", "warning" if cfg.skills.command.godmode else "ok"),
        ]
        self._write_system("\n".join(lines))

    def action_reload_config(self) -> None:
        try:
            self.orchestrator.config = load_config(self.orchestrator.config_path)
            if not self.orchestrator.sync_command_policy_from_config(force=True):
                raise RuntimeError("command policy synchronization failed")
            loaded = self.orchestrator.skills.load_external(self.orchestrator.config.skills.skills_dir)
            if self.orchestrator.subagents:
                self.orchestrator.subagents.reconfigure(self.orchestrator.config.subagents)
            if self.orchestrator.mcp:
                self.orchestrator.mcp.reconfigure(self.orchestrator.config.mcp)
            self.artifacts = ArtifactManager.from_config(self.orchestrator.config)
        except Exception as exc:
            self._write_error(f"Config reload failed: {exc}")
            return
        self._write_system(
            f"Config reloaded from {self.orchestrator.config_path}\n"
            f"External skills loaded: {', '.join(loaded) or 'none'}\n"
            f"Command allowlist synchronized: {len(set(self.orchestrator.config.skills.command.allowed_commands))} commands"
        )
        self._refresh_status()

    def action_skills(self) -> None:
        lines = []
        for manifest in self.orchestrator.skills.manifests():
            scope = (
                "Ulysses + sub-agents"
                if self.orchestrator.subagents and self.orchestrator.subagents.capabilities.is_delegable(manifest.name)
                else "Ulysses only"
            )
            lines.append(
                f"{manifest.name}  risk={manifest.risk_level}  enabled={manifest.enabled}  scope={scope}  status=ready"
            )
        for name, manifest, error in self.orchestrator.skills.load_failures():
            details = f"risk={manifest.risk_level}  enabled={manifest.enabled}" if manifest else "manifest=unavailable"
            lines.append(f"{name}  {details}  status=load_failed: {error}")
        self._write_system("\n".join(lines) or "No skills registered.")

    def _write_downloads(self) -> None:
        files = self.artifacts.list_downloads()
        self._write_system("\n".join(str(path) for path in files) or "No report or attachment files.")

    def _assessment_prompt(self, text: str, project: AssessmentProject) -> str:
        request = _project_request(project)
        return (
            f"{text}\n\n"
            "Assessment project workspace has been created. Use it to organize this assessment:\n"
            f"- Initial request: {request}\n"
            f"- Project root: {project.path}\n"
            f"- Scripts: {project.scripts_dir}\n"
            f"- Artifacts: {project.artifacts_dir}\n"
            f"- Results: {project.results_dir}\n"
            f"- Reports: {project.reports_dir}\n\n"
            "Save purpose-built helper scripts under `scripts/`, raw/intermediate outputs under `results/`, "
            "supporting files under `artifacts/`, and make your final response a Markdown report based on those materials. "
            "The application will save that final report under `reports/`. "
            "Proceed with safe baseline checks using the concrete target and current project context. "
            "Ask only for required authorization, credentials, destructive/intrusive actions, or unclear target identity. "
            "If this turn approves sudo or installation, call `system_command` with the concrete command now."
        )

    def _should_speak_response(self, text: str) -> bool:
        if not self._assessment_project:
            return True
        return assessment_needs_voice(text, self.orchestrator.pending_tool is not None)

    def _set_project_result_capture(self, project: AssessmentProject | None) -> None:
        if not hasattr(self.orchestrator, "set_tool_result_callback"):
            return
        if project is None:
            self.orchestrator.set_tool_result_callback(None)
            return

        def save_result(name: str, content: str, metadata: dict) -> None:
            label = str(metadata.get("planned_command") or name)
            self.artifacts.save_project_result(project, label, content)

        self.orchestrator.set_tool_result_callback(save_result)

    def action_setup(self) -> None:
        self.push_screen(ProviderSetupScreen(self.orchestrator.config), self._finish_provider_setup)

    def action_connector_setup(self) -> None:
        self.push_screen(ConnectorSelectionScreen(), self._open_connector_setup)

    def action_mcp_setup(self) -> None:
        self.push_screen(MCPSelectionScreen(self.orchestrator.config.mcp.servers), self._open_mcp_setup)

    def _open_mcp_setup(self, server_id: str | None) -> None:
        if server_id is None:
            self._write_system("MCP setup cancelled.")
            return
        server = next((item for item in self.orchestrator.config.mcp.servers if item.id == server_id), None)
        self.push_screen(MCPSetupScreen(server), self._finish_mcp_setup)

    def _finish_mcp_setup(self, setup: MCPServerSetup | None) -> None:
        if setup is None:
            self._write_system("MCP setup cancelled.")
            return
        self._start_waiting()
        Thread(target=self._mcp_setup_in_thread, args=(setup,), daemon=True).start()

    def _mcp_setup_in_thread(self, setup: MCPServerSetup) -> None:
        server = setup.server_config()
        previous_token = os.environ.get(server.bearer_token_env) if server.bearer_token_env else None
        if setup.bearer_token and server.bearer_token_env:
            os.environ[server.bearer_token_env] = setup.bearer_token.strip()
        try:
            tools = (
                SDKMCPClient(self.orchestrator.config.mcp.allowed_stdio_commands).discover(server)
                if server.enabled
                else []
            )
            apply_mcp_server_setup(self.orchestrator.config, self.orchestrator.config_path, setup)
            load_env_file(env_path_for_config(self.orchestrator.config_path))
            self.orchestrator.config = load_config(self.orchestrator.config_path)
            self.orchestrator.mcp.reconfigure(self.orchestrator.config.mcp, start=False)
            if server.enabled:
                self.orchestrator.mcp.discover_now(server.id)
        except Exception as exc:
            if server.bearer_token_env:
                if previous_token is None:
                    os.environ.pop(server.bearer_token_env, None)
                else:
                    os.environ[server.bearer_token_env] = previous_token
            self.call_from_thread(self._finish_mcp_setup_error, str(exc))
            return
        names = [str(tool.get("name") or "") for tool in tools]
        self.call_from_thread(self._activate_mcp_setup, server.id, names)

    def _activate_mcp_setup(self, server_id: str, tools: list[str]) -> None:
        self._stop_waiting()
        status = self.orchestrator.mcp.status(server_id)
        self._write_system(
            f"MCP server saved: {server_id}\n"
            f"Status: {status.state}\n"
            f"Advertised tools: {', '.join(tools) or 'none'}\n"
            f"Allowed tools registered: {status.tool_count}"
        )
        self._refresh_status()

    def _finish_mcp_setup_error(self, error: str) -> None:
        self._stop_waiting()
        self._write_error(f"MCP server validation failed; configuration was not saved: {error}")
        self._refresh_status()

    def _open_connector_setup(self, connector_id: str | None) -> None:
        if connector_id is None:
            self._write_system("Connector setup cancelled.")
            return
        if connector_id == "telegram":
            enabled = self.orchestrator.config.connectors.telegram.enabled
            self.push_screen(TelegramSetupScreen(enabled), self._finish_telegram_setup)
            return
        self._write_error(f"Connector setup is not available: {connector_id}")

    def _finish_telegram_setup(self, setup: TelegramSetup | None) -> None:
        if setup is None:
            self._write_system("Connector setup cancelled.")
            return
        if not setup.enabled:
            self.connectors.remove("telegram")
            apply_telegram_setup(self.orchestrator.config, self.orchestrator.config_path, setup)
            self.orchestrator.config = load_config(self.orchestrator.config_path)
            self._write_system("Telegram connector disabled. Previously verified chats are retained locally.")
            self._refresh_status()
            return
        self._start_waiting()
        Thread(target=self._telegram_setup_in_thread, args=(setup,), daemon=True).start()

    def _telegram_setup_in_thread(self, setup: TelegramSetup) -> None:
        config_path = self.orchestrator.config_path
        telegram_config = self.orchestrator.config.connectors.telegram.model_copy(update={"enabled": True})
        token = setup.token.strip() or os.environ.get(telegram_config.token_env, "")
        candidate = TelegramConnector(
            telegram_config,
            self._handle_connector_message,
            self._connector_event_from_worker,
            token=token,
        )
        try:
            username = candidate.validate()
            apply_telegram_setup(self.orchestrator.config, config_path, TelegramSetup(True, setup.token))
            load_env_file(env_path_for_config(config_path))
            self.orchestrator.config = load_config(config_path)
            code = candidate.begin_pairing()
            candidate.start()
        except Exception as exc:
            candidate.stop()
            self.call_from_thread(self._finish_telegram_setup_error, str(exc))
            return
        self.call_from_thread(self._activate_telegram_connector, candidate, username, code)

    def _activate_telegram_connector(self, connector: TelegramConnector, _username: str, code: str) -> None:
        self.connectors.replace(connector)
        self._stop_waiting()
        self._write_system(
            "Telegram connector verified.\n"
            f"Open the bot and send: /verify {code}\n"
            f"Pairing code expires in {connector.config.pairing_code_ttl_seconds // 60} minutes."
        )
        self._refresh_status()

    def _finish_telegram_setup_error(self, error: str) -> None:
        self._stop_waiting()
        self._write_error(f"Telegram connector setup failed: {error}")
        self._refresh_status()

    def _connector_event_from_worker(self, message: str) -> None:
        try:
            self.call_from_thread(self._write_system, message)
            self.call_from_thread(self._refresh_status)
        except RuntimeError:
            pass

    def _handle_connector_message(self, connector_id: str, chat_id: int, text: str) -> str:
        source = f"{connector_id.title()} {chat_id}"
        try:
            self.call_from_thread(self._write_user, f"[{source}] {text}")
        except RuntimeError:
            pass
        lowered = text.strip().lower()
        if lowered.startswith("/confirm"):
            if self.orchestrator.pending_tool_requires_sudo_password():
                response = "This command requires local sudo authentication. Confirm it in the local Ulysses console."
            else:
                parts = text.split(maxsplit=1)
                token = parts[1].strip() if len(parts) == 2 else None
                response = self.orchestrator.confirm_pending_tool(token)
        elif lowered == "/cancel":
            response = (
                "Pending command cancelled." if self.orchestrator.cancel_pending_tool() else "No command is pending."
            )
        else:
            response = self.orchestrator.handle_text(text)
        try:
            self.call_from_thread(self._write_assistant, f"[{source}] {response}")
            self.call_from_thread(self._refresh_status)
        except RuntimeError:
            pass
        return response

    def on_unmount(self) -> None:
        self.connectors.stop_all()
        if self.orchestrator.mcp:
            self.orchestrator.mcp.stop()
        _stop_system_clipboard_owner()

    def _mcp_event_from_worker(self, message: str) -> None:
        try:
            self.call_from_thread(self._write_system, message)
            self.call_from_thread(self._refresh_status)
        except RuntimeError:
            pass

    def _finish_provider_setup(self, setup: ProviderSetup | None) -> None:
        if setup is None:
            self._write_system("Provider setup cancelled.")
            return
        if setup.provider == "openai_chatgpt":
            self._start_waiting()
            Thread(target=self._start_openai_login, args=(setup,), daemon=True).start()
            return
        self._activate_provider_setup(setup)

    def _start_openai_login(self, setup: ProviderSetup) -> None:
        login = OpenAIBrowserLogin()
        try:
            login.start()
        except OpenAIBrowserLoginError as exc:
            self.call_from_thread(self._provider_setup_error, str(exc))
            return
        self.call_from_thread(self._show_openai_callback, login, setup)

    def _show_openai_callback(self, login: OpenAIBrowserLogin, setup: ProviderSetup) -> None:
        self._stop_waiting()
        self.push_screen(
            OpenAICallbackScreen(login.auth_url), lambda value: self._finish_openai_callback(value, login, setup)
        )

    def _finish_openai_callback(
        self, callback_url: str | None, login: OpenAIBrowserLogin, setup: ProviderSetup
    ) -> None:
        if not callback_url:
            login.close()
            self._write_system("OpenAI browser login cancelled.")
            return
        self._start_waiting()
        Thread(target=self._complete_openai_login, args=(callback_url, login, setup), daemon=True).start()

    def _complete_openai_login(self, callback_url: str, login: OpenAIBrowserLogin, setup: ProviderSetup) -> None:
        try:
            model = login.complete(callback_url)
        except OpenAIBrowserLoginError as exc:
            self.call_from_thread(self._provider_setup_error, str(exc))
            return
        resolved = replace(setup, model=model, base_url="", api_key_env="", api_key="")
        self.call_from_thread(self._activate_provider_setup, resolved)

    def _provider_setup_error(self, error: str) -> None:
        self._stop_waiting()
        self._write_error(f"Provider setup failed: {error}")
        self._refresh_status()

    def _activate_provider_setup(self, setup: ProviderSetup) -> None:
        self._stop_waiting()
        config_path = self.orchestrator.config_path
        try:
            apply_provider_setup(self.orchestrator.config, config_path, setup)
            load_env_file(env_path_for_config(config_path))
            self.orchestrator.config = load_config(config_path)
            self.orchestrator.sync_command_policy_from_config()
        except Exception as exc:
            self._write_error(f"Provider setup failed: {exc}")
            self._refresh_status()
            return
        try:
            self.orchestrator.llm = build_provider(self.orchestrator.config.llm)
        except Exception as exc:
            self._write_error(f"Provider saved, but activation failed: {exc}")
            self._refresh_status()
            return
        self.artifacts = ArtifactManager.from_config(self.orchestrator.config)
        self._provider_onboarding = False
        self._write_system(
            "Provider saved and activated:\n"
            f"{self.orchestrator.config.llm.provider} / {self.orchestrator.config.llm.model}\n"
            f"{self.orchestrator.config.llm.base_url}"
        )
        question = "Provider setup is complete. How would you like me to address you?"
        self._write_assistant(question)
        self._speak(question)
        self._refresh_status()

    def action_voice_toggle(self) -> None:
        if not self.voice_io:
            self._write_system("Voice I/O is not active.")
            return
        self.voice_io.state.enabled = not self.voice_io.state.enabled
        if not self.voice_io.state.enabled:
            self._speech_id += 1
            self.voice_io.interrupt()
            self._stop_speaking_ui()
        self._write_system(f"Voice responses: {'on' if self.voice_io.state.enabled else 'off'}")
        self._refresh_status()

    def action_mute_toggle(self) -> None:
        if not self.voice_io:
            self._write_system("Voice I/O is not active.")
            return
        self.voice_io.state.muted = not self.voice_io.state.muted
        if self.voice_io.state.muted:
            self._speech_id += 1
            self.voice_io.interrupt()
            self._stop_speaking_ui()
        self._write_system(f"Muted: {self.voice_io.state.muted}")
        self._refresh_status()

    def action_stop_speaking(self) -> None:
        if not self.voice_io:
            return
        if self._listening:
            self._listen_cancel_requested = True
            self.voice_io.cancel_listen()
            self._set_activity("stopping microphone")
            return
        state = self.voice_io.state
        if not self._speaking and state.tts != "speaking":
            return
        self._speech_id += 1
        self.voice_io.interrupt()
        self._stop_speaking_ui()
        self._write_system("Stopped speaking.")
        self._refresh_status()

    def action_push_to_talk(self) -> None:
        if not self.voice_io:
            self._write_system("Voice input is not active. Start without --text-only to use push to talk.")
            return
        if self._listening:
            self._listen_cancel_requested = True
            self.voice_io.cancel_listen()
            self._set_activity("stopping microphone")
            return
        if self._waiting:
            self._write_system("Wait for the current operation to finish before recording.")
            return
        if self.orchestrator.pending_tool_requires_sudo_password():
            self._write_system("Complete or cancel the secure sudo prompt before recording.")
            return
        if self._speaking or self.voice_io.state.tts == "speaking":
            self._speech_id += 1
            self.voice_io.interrupt()
            self._stop_speaking_ui()
        self._listening = True
        self._listen_cancel_requested = False
        self._activity_text = "listening"
        composer = self.query_one("#composer", Input)
        composer.disabled = True
        self.query_one("#spinner", Static).update(
            self._activity_renderable(next(self._spinner), "Ulysses: listening")
        )
        self._refresh_status()
        Thread(target=self._listen_in_thread, daemon=True).start()

    def _listen_in_thread(self) -> None:
        try:
            transcript = self.voice_io.listen_once()
        except Exception as exc:
            self.call_from_thread(self._finish_listening, "", str(exc))
            return
        self.call_from_thread(self._finish_listening, transcript, None)

    def _finish_listening(self, transcript: str, error: str | None) -> None:
        cancelled = self._listen_cancel_requested
        self._listening = False
        self._listen_cancel_requested = False
        self._activity_text = "idle"
        self.query_one("#spinner", Static).update("")
        composer = self.query_one("#composer", Input)
        composer.disabled = False
        composer.focus()
        self._refresh_status()
        if error:
            self._write_error(f"Voice input failed: {error}")
        elif cancelled:
            self._write_system("Voice input cancelled.")
        elif transcript.strip():
            self._submit_text(transcript.strip())
        else:
            self._write_system("No speech detected.")

    def action_copy_selected_or_last(self) -> None:
        selected = self._selected_text()
        if selected:
            self._copy_text(selected, "Copied selected text.")
            return
        self.action_copy_last()

    def action_copy_selected(self) -> None:
        selected = self._selected_text()
        if not selected:
            self._write_system("No selected text to copy.")
            return
        self._copy_text(selected, "Copied selected text.")

    def action_copy_last(self) -> None:
        if not self.last_assistant_text:
            self._write_system("No assistant response to copy.")
            return
        self._copy_text(self.last_assistant_text, "Copied last assistant response.")

    def action_copy_transcript(self) -> None:
        if not self.transcript_plain:
            self._write_system("No transcript to copy.")
            return
        self._copy_text("\n".join(self.transcript_plain), "Copied transcript.")

    def _copy_text(self, text: str, success_message: str) -> None:
        try:
            self.copy_to_clipboard(text)
        except Exception as exc:
            self._write_error(f"Clipboard unavailable: {exc}")
            return
        if _set_system_clipboard_text(text):
            self._write_system(success_message)
        else:
            self._write_error("System clipboard backend is unavailable.")

    def _selected_text(self) -> str:
        try:
            return (self.screen.get_selected_text() or "").strip()
        except Exception:
            return ""

    def action_selection_mode(self) -> None:
        self._set_selection_mode(not self.selection_mode)

    def _set_selection_mode(self, enabled: bool) -> None:
        self.selection_mode = enabled
        if enabled:
            self.query_one("#composer", Input).blur()
            _set_terminal_mouse_capture(self, False)
            self._write_system(
                "Selection mode: on. Mouse selection is released to the terminal. "
                "Drag-select text, then copy with Ctrl+Shift+C or your terminal shortcut."
            )
        else:
            _set_terminal_mouse_capture(self, True)
            self.query_one("#composer", Input).focus()
            self._write_system("Selection mode: off.")

    def action_cycle_theme(self) -> None:
        current = self.THEMES.index(self.theme_name) if self.theme_name in self.THEMES else 0
        self._apply_theme(self.THEMES[(current + 1) % len(self.THEMES)])

    def _apply_theme(self, name: str, announce: bool = True) -> None:
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
            if announce:
                self._write_system(f"Theme: {name}")
            self._refresh_status()

    def _refresh_status(self) -> None:
        if self._speaking and not self._voice_allows_speech():
            self._stop_speaking_ui()
        voice = "inactive"
        if self.voice_io:
            state = self.voice_io.state
            voice = f"{'on' if state.enabled else 'off'} / muted={state.muted} / stt={state.stt} / tts={state.tts}"
        connector_state = "on" if any(status.configured for status in self.connectors.statuses()) else "off"
        autonomous = "on" if self.orchestrator.autonomous_enabled() else "off"
        active_skill = getattr(self.orchestrator, "active_skill", None) or "idle"
        context = self.orchestrator.context_usage()
        gauge = _gauge(context["percent"])
        self.query_one("#status", Static).update(
            f"Session\n{self.orchestrator.session_id}\n\n"
            f"Provider\n{self.orchestrator.config.llm.provider}\n\n"
            f"Context\n{gauge} {context['percent']}%\n"
            f"{context['estimated_tokens']}/{context['context_window_tokens']} tok\n\n"
            f"Voice\n{voice}\n\n"
            f"Connector: {connector_state}\n\n"
            f"Activity\n{self._activity_text if self._waiting or self._speaking or self._listening else 'idle'}\n\n"
            f"Active skill\n{active_skill}\n\n"
            f"Sub-agents\n{self.orchestrator.subagents.summary() if self.orchestrator.subagents else 'disabled'}\n\n"
            f"MCP\n{self.orchestrator.mcp.status_detail() if self.orchestrator.mcp else 'disabled'}\n\n"
            f"Autonomous\n{autonomous}\n\n"
            f"Update\n{self.updates.status.state}\n\n"
            f"Theme\n{self.theme_name}"
        )

    def _brand_version_text(self) -> str:
        return self._local_version_text()

    def _local_version_text(self) -> str:
        version = self.updates.installed_branch or f"v{self.orchestrator.config.agent_version}"
        update_label = " (update)" if self.updates.status.state in {"available", "staged"} else ""
        return f"{self.orchestrator.config.agent_name} {version}{update_label}"

    def _refresh_version_labels(self) -> None:
        self.title = self._local_version_text()
        if self.is_mounted:
            self.query_one("#brand", Label).update(self._brand_version_text())

    def _check_update_in_thread(self, announce: bool) -> None:
        status = self.updates.check()
        self.call_from_thread(self._finish_update_check, status, announce)

    def _finish_update_check(self, status, announce: bool) -> None:
        self._refresh_version_labels()
        self._refresh_status()
        if status.state == "available":
            self._write_system(f"Ulysses update available from GitHub main: {status.summary()}\nRun /update install to apply it.")
        elif announce:
            self._write_system(status.error or f"Ulysses update status: {status.summary()}")

    def _install_update_in_thread(self) -> None:
        message = self.updates.install()
        self.call_from_thread(self._finish_update_install, message)

    def _finish_update_install(self, message: str) -> None:
        self._stop_waiting()
        self._refresh_version_labels()
        if self.updates.status.error:
            self._write_error(message)
        else:
            self._write_system(message)
        self._refresh_status()

    def _maybe_autonomous(self) -> None:
        self._start_autonomous_check(force=False)

    def _maybe_collect_subagent_reports(self) -> None:
        manager = getattr(self.orchestrator, "subagents", None)
        if self._subagent_collection_running or not manager or not manager.completed_reports():
            return
        self._subagent_collection_running = True
        Thread(target=self._collect_subagent_reports_in_thread, daemon=True).start()

    def _collect_subagent_reports_in_thread(self) -> None:
        try:
            note = self.orchestrator.collect_subagent_reports()
        except Exception as exc:
            self.call_from_thread(self._finish_subagent_collection, None, str(exc))
            return
        self.call_from_thread(self._finish_subagent_collection, note, None)

    def _finish_subagent_collection(self, note: str | None, error: str | None) -> None:
        self._subagent_collection_running = False
        if error:
            self._write_error(f"Sub-agent report collection failed: {error}")
        elif note:
            self._write_assistant(note)
            if self._should_speak_response(note):
                self._speak(note)
        self._refresh_status()

    def _autonomous_timer_seconds(self) -> float:
        cfg = self.orchestrator.config.autonomous
        if getattr(cfg, "defense_checks_enabled", True):
            return max(5.0, min(cfg.check_interval_seconds, cfg.defense_critical_interval_seconds))
        return cfg.check_interval_seconds

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
        self._activity_text = "starting"
        self.query_one("#composer", Input).disabled = True
        self.query_one("#spinner", Static).update(self._activity_renderable(next(self._spinner), "Ulysses: starting"))

    def _stop_waiting(self) -> None:
        self._waiting = False
        if not self._speaking:
            self._activity_text = "idle"
            self.query_one("#spinner", Static).update("")
        composer = self.query_one("#composer", Input)
        composer.disabled = False
        composer.focus()

    def _tick_spinner(self) -> None:
        if self._speaking and not self._voice_allows_speech():
            self._stop_speaking_ui()
            self._refresh_status()
            return
        if self._speaking:
            self._logo_frame_index = (self._logo_frame_index + 1) % len(ULYSSES_SPEAKING_LOGOS)
            self.query_one("#logo", Static).update(ULYSSES_SPEAKING_LOGOS[self._logo_frame_index])
        active = self._waiting or self._speaking or self._listening
        if active:
            frame = next(self._spinner)
            label = self._activity_label()
            message = self._activity_renderable(frame, label)
            self.query_one("#spinner", Static).update(message)

    def _activity_label(self) -> str:
        return f"Ulysses: {self._activity_text}"

    @staticmethod
    def _activity_renderable(frame: str, label: str, gap: str = " ") -> Text:
        return Text.assemble((frame, "bold #ff8c00"), gap, label, "...")

    def _activity_from_worker(self, message: str) -> None:
        try:
            self.call_from_thread(self._set_activity, message)
        except RuntimeError:
            self._activity_text = message

    def _set_activity(self, message: str) -> None:
        self._activity_text = message
        if self._waiting or self._speaking or self._listening:
            self.query_one("#spinner", Static).update(
                self._activity_renderable(next(self._spinner), self._activity_label())
            )
        self._refresh_status()

    def _create_skill_in_thread(self, name: str, request: str) -> None:
        try:
            result = self.orchestrator._run_skill("create_skill", {"name": name, "request": request})
        except Exception as exc:
            self.call_from_thread(self._finish_error, str(exc))
            return
        self.call_from_thread(self._finish_created_skill, result)

    def _finish_created_skill(self, result: str) -> None:
        self._stop_waiting()
        self._write_tool(result)
        self._refresh_status()

    def _write_user(self, text: str) -> None:
        self.query_one("#transcript", TranscriptLog).write(f"[bold cyan]you[/bold cyan] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("you", text)

    def _write_assistant(self, text: str) -> None:
        self.last_assistant_text = text
        self.query_one("#transcript", TranscriptLog).write(
            Group(
                Text.from_markup(f"[bold magenta]Ulysses[/bold magenta] [dim]{_time()}[/dim]"),
                _formatted_transcript_content(text),
                Text(""),
            )
        )
        self._append_plain("Ulysses", text)

    def _write_tool(self, text: str) -> None:
        self.query_one("#transcript", TranscriptLog).write(
            Group(
                Text.from_markup(f"[bold yellow]tool[/bold yellow] [dim]{_time()}[/dim]"),
                _formatted_transcript_content(text),
                Text(""),
            )
        )
        self._append_plain("tool", text)

    def _write_system(self, text: str) -> None:
        self.query_one("#transcript", TranscriptLog).write(
            f"[bold green]system[/bold green] [dim]{_time()}[/dim]\n{text}\n"
        )
        self._append_plain("system", text)

    def _write_error(self, text: str) -> None:
        self.query_one("#transcript", TranscriptLog).write(f"[bold red]error[/bold red] [dim]{_time()}[/dim]\n{text}\n")
        self._append_plain("error", text)

    def _append_plain(self, role: str, text: str) -> None:
        self.transcript_plain.append(f"{role} {_time()}\n{text}")

    def _speak(self, text: str) -> None:
        if not self._voice_allows_speech():
            return
        self._speech_id += 1
        speech_id = self._speech_id
        self.voice_io.interrupt()
        self._speaking = False
        self._activity_text = "preparing voice"
        self.query_one("#spinner", Static).update(
            self._activity_renderable(next(self._spinner), "Ulysses: preparing voice")
        )
        self._refresh_status()
        Thread(target=self._speak_in_thread, args=(text, speech_id), daemon=True).start()

    def _voice_allows_speech(self) -> bool:
        return bool(self.voice_io and self.voice_io.state.enabled and not self.voice_io.state.muted)

    def _speak_in_thread(self, text: str, speech_id: int) -> None:
        try:
            spoken_text = self.orchestrator.summarize_for_voice(text)
            self.voice_io.speak(
                spoken_text,
                on_playback_start=lambda: self.call_from_thread(self._start_speaking_ui, speech_id),
            )
        except Exception as exc:
            self.call_from_thread(self._finish_speaking, speech_id, str(exc))
            return
        self.call_from_thread(self._finish_speaking, speech_id, None)

    def _start_speaking_ui(self, speech_id: int) -> None:
        if speech_id != self._speech_id:
            return
        self._speaking = True
        self._logo_frame_index = 0
        self.query_one("#logo", Static).update(ULYSSES_SPEAKING_LOGOS[0])
        self._activity_text = "speaking"
        self.query_one("#spinner", Static).update(
            self._activity_renderable(next(self._spinner), "Ulysses: speaking")
        )
        self._refresh_status()

    def _finish_speaking(self, speech_id: int, error: str | None) -> None:
        if speech_id != self._speech_id:
            return
        self._stop_speaking_ui()
        if error:
            self._write_error(f"TTS error: {error}")
        self._refresh_status()

    def _stop_speaking_ui(self) -> None:
        self._speaking = False
        self._logo_frame_index = 0
        self.query_one("#logo", Static).update(ULYSSES_SIDEBAR_LOGO)
        if not self._waiting:
            self._activity_text = "idle"
            self.query_one("#spinner", Static).update("")


def _time() -> str:
    return datetime.now().strftime("%H:%M")


def _dashboard_line(label: str, value: object, state: str = "ok") -> str:
    icons = {
        "ok": "[green]✓[/green]",
        "off": "[dim]○[/dim]",
        "warning": "[yellow]![/yellow]",
    }
    return f"{icons.get(state, icons['off'])}  {label + ':':<12} {escape(str(value))}"


def _boot_progress(message: str, completed: int, frame: str) -> str:
    labels = ("Brain", "Memory", "Skills", "Prompt", "Voice")
    final_lines = {label: line for line in message.splitlines() for label in labels if f"{label}:" in line}
    message_lines = message.splitlines()
    heading = message_lines[0] if message_lines else "[bold cyan]◆  ULYSSES CYBER SENTINEL[/bold cyan]"
    subtitle = message_lines[1] if len(message_lines) > 1 else ""
    lines = [heading, subtitle, "", "[bold]SYSTEM READINESS[/bold]"]
    for index, label in enumerate(labels):
        if index < completed:
            lines.append(final_lines.get(label, f"[green]✓[/green]  {label + ':':<8} ready"))
        elif index == completed:
            lines.append(f"[cyan]{frame}[/cyan]  {label + ':':<8} checking...")
        else:
            lines.append(f"[dim]○  {label + ':':<8} waiting[/dim]")
    return "\n".join(lines)


def _gauge(percent: int, width: int = 14) -> str:
    filled = min(width, max(0, round((percent / 100) * width)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _system_clipboard_text() -> str:
    commands = (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
    )
    for command in commands:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            result = subprocess.run(
                [executable, *command[1:]],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout:
            return result.stdout.rstrip("\r\n")
    return ""


def _system_clipboard_backend() -> str | None:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    candidates = (
        ("wl-paste", "wayland"),
        ("xclip", "x11"),
        ("xsel", "x11"),
        ("powershell.exe", "windows"),
    )
    for command, backend in candidates:
        if backend == "wayland" and session_type == "x11":
            continue
        if backend == "x11" and session_type == "wayland" and not os.environ.get("DISPLAY"):
            continue
        if shutil.which(command):
            return command
    return None


_clipboard_owner_process: subprocess.Popen[str] | None = None


def _set_system_clipboard_text(text: str) -> bool:
    global _clipboard_owner_process
    commands = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard"],
    ]
    for command in commands:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            result = subprocess.run(
                [executable, *command[1:]],
                input=text,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True

    python = shutil.which("python3")
    if python is None or not os.environ.get("DISPLAY"):
        return False
    owner = (
        "import sys; from PyQt5.QtCore import QTimer; from PyQt5.QtWidgets import QApplication; "
        "app=QApplication([]); clipboard=app.clipboard(); clipboard.setText(sys.stdin.read()); "
        "timer=QTimer(); timer.timeout.connect(lambda: app.quit() if not clipboard.ownsClipboard() else None); "
        "timer.start(1000); app.exec()"
    )
    if _clipboard_owner_process and _clipboard_owner_process.poll() is None:
        _clipboard_owner_process.terminate()
    try:
        process = subprocess.Popen(
            [python, "-c", owner],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        if process.stdin is None:
            process.terminate()
            return False
        process.stdin.write(text)
        process.stdin.close()
    except OSError:
        return False
    time.sleep(0.2)
    if process.poll() is not None:
        return False
    _clipboard_owner_process = process
    return True


def _stop_system_clipboard_owner() -> None:
    global _clipboard_owner_process
    process, _clipboard_owner_process = _clipboard_owner_process, None
    if process and process.poll() is None:
        process.terminate()


def _set_terminal_mouse_capture(app, enabled: bool) -> None:
    driver = getattr(app, "_driver", None)
    method_name = "_enable_mouse_support" if enabled else "_disable_mouse_support"
    method = getattr(driver, method_name, None)
    if callable(method):
        try:
            method()
        except Exception:
            pass


def _active_command_policy(orchestrator):
    try:
        return orchestrator.skills.get("system_command").runner.policy
    except Exception:
        return None


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _run_system_command_capture(orchestrator, command: str) -> tuple[str, bool]:
    orchestrator.sync_command_policy_from_config(force=True)
    result = orchestrator._run_skill_result("system_command", {"command": command})
    if result.requires_confirmation:
        orchestrator.pending_tool = {
            "name": "system_command",
            "arguments": {"command": command},
            "token": result.confirmation_token,
        }
        return result.confirmation_prompt or result.content, False
    orchestrator._record_tool_result(
        "system_command",
        result.content,
        {"skill": "system_command", "ok": result.ok, "data": result.data, "planned_command": command},
    )
    return result.content, bool(result.ok)


def _assessment_report_markdown(target: str, commands: list[str], sections: list[tuple]) -> str:
    checks_by_command = {check.command: check for check in assessment_checks(target)}
    results = []
    for index, section in enumerate(sections):
        command, output, ok = _normalize_assessment_section(section)
        check = checks_by_command.get(command)
        if check is None:
            check = AssessmentCheck(f"requested-check-{index + 1}", "Requested", command)
        results.append(AssessmentResult(check, output, ok))
    return render_assessment_report(target, results)


def _normalize_assessment_section(section: tuple) -> tuple[str, str, bool]:
    if len(section) >= 3:
        return str(section[0]), str(section[1]), bool(section[2])
    command, output = section
    output_text = str(output)
    lowered = output_text.lower()
    ok = not any(
        marker in lowered
        for marker in (
            "not in the allowlist",
            "not allowed",
            "command not found:",
            "command timed out",
            "command failed before execution",
        )
    )
    return str(command), output_text, ok


def _project_request(project: AssessmentProject) -> str:
    try:
        return (project.artifacts_dir / "request.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "current assessment"
