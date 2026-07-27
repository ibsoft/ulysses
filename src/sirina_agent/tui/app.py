from __future__ import annotations

from threading import Thread

from sirina_agent.core.artifacts import ArtifactManager, attachment_prompt, is_report_request, should_store_large_paste
from sirina_agent.config import load_config
from sirina_agent.config.provider_setup import (
    ProviderSetup,
    apply_provider_setup,
    default_for,
    env_path_for_config,
    load_env_file,
    provider_labels,
)
from sirina_agent.llm.providers import build_provider

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table


ULYSSES_LOGO = r'''
╔════════════════════╗
║    U L Y S S E S   ║
║   CYBER SENTINEL   ║
║       .-^^-.       ║
║    .-/  /\  \-.    ║
║   / /  /==\  \ \   ║
║  | |  | () |  | |  ║
║   \ \  \==/  / /   ║
║    `-\__\/__/-'    ║
║     <_//||\\_>     ║
╚════════════════════╝
'''


def create_tui(orchestrator, voice_io=None):
    try:
        from .textual_app import UlyssesTextualApp

        return UlyssesTextualApp(orchestrator, voice_io)
    except Exception:
        return RichTUI(orchestrator, voice_io)


class RichTUI:
    def __init__(self, orchestrator, voice_io=None) -> None:
        self.orchestrator = orchestrator
        self.voice_io = voice_io
        self.console = Console()
        self.artifacts = ArtifactManager.from_config(orchestrator.config)
        self._last_user_text = ""
        self._last_response_wants_report = False

    def run(self) -> None:
        self.console.print(
            Panel(
                f"{ULYSSES_LOGO}\n"
                f"{self.orchestrator.config.agent_name} v{self.orchestrator.config.agent_version}\n"
                "Type /status, /skills, /memory, /confirm, /new, /voice on, /voice off, /autonomous on, /quit."
            )
        )
        while True:
            text = Prompt.ask("[bold cyan]you[/bold cyan]")
            if not text:
                continue
            if text.startswith("/"):
                if self._command(text):
                    break
                continue
            wants_report = is_report_request(text)
            self._last_user_text = text
            self._last_response_wants_report = wants_report
            prompt_text = text
            if should_store_large_paste(text, self.orchestrator.config.context.max_chars):
                artifact = self.artifacts.save_text_attachment(self.orchestrator.session_id, text)
                self.console.print(f"Large paste saved as text file: {artifact.path}")
                prompt_text = attachment_prompt(text, artifact)
            try:
                with self.console.status("[bold magenta]Ulysses is thinking...[/bold magenta]", spinner="dots"):
                    answer = self.orchestrator.handle_text(prompt_text)
            except Exception as exc:
                self.console.print(Panel(str(exc), title="Ulysses error"))
                continue
            if wants_report and self.orchestrator.pending_tool is None:
                artifact = self.artifacts.save_markdown_report(self.orchestrator.session_id, answer)
                answer = f"{answer}\n\nReport saved as Markdown:\n{artifact.path}"
                self._last_response_wants_report = False
            self.console.print(Panel(answer, title="Ulysses"))
            self._speak(answer)

    def _command(self, text: str) -> bool:
        parts = text.split()
        cmd = parts[0]
        if cmd == "/quit":
            return True
        if cmd == "/new":
            self.orchestrator.session_id = self.orchestrator.sessions.create_session("Ulysses")
            self.console.print("Created a new session.")
        elif cmd == "/sessions":
            table = Table("id", "title", "updated")
            for row in self.orchestrator.sessions.list_sessions():
                table.add_row(row["id"], row["title"], row["updated_at"])
            self.console.print(table)
        elif cmd == "/downloads":
            files = self.artifacts.list_downloads()
            self.console.print("\n".join(str(path) for path in files) or "No report or attachment files.")
        elif cmd == "/switch" and len(parts) > 1:
            self.orchestrator.session_id = parts[1]
        elif cmd == "/skills":
            table = Table("name", "risk", "enabled", "permissions")
            for manifest in self.orchestrator.skills.manifests():
                table.add_row(manifest.name, manifest.risk_level, str(manifest.enabled), ", ".join(manifest.required_permissions))
            self.console.print(table)
        elif cmd == "/memory":
            for item in self.orchestrator.memory.items[-20:]:
                self.console.print(f"{item.id}: {item.text[:120]}")
        elif cmd == "/forget" and len(parts) > 1:
            if parts[1] == "all" and Confirm.ask("Erase all Ulysses user data?"):
                self.orchestrator.erase_user_data()
            else:
                self.console.print("forgot" if self.orchestrator.memory.forget(parts[1]) else "not found")
        elif cmd == "/confirm":
            token = parts[1] if len(parts) > 1 else None
            extra = None
            if self.orchestrator.pending_tool_requires_sudo_password():
                extra = {"sudo_password": Prompt.ask("sudo password", password=True)}
            tool_result = self.orchestrator.confirm_pending_tool(token, extra)
            self.console.print(Panel(tool_result, title="Tool result"))
            if self._last_response_wants_report:
                try:
                    with self.console.status("[bold magenta]Ulysses is writing report...[/bold magenta]", spinner="dots"):
                        answer = self.orchestrator.answer_from_tool_result(self._last_user_text, tool_result)
                    artifact = self.artifacts.save_markdown_report(self.orchestrator.session_id, answer)
                    answer = f"{answer}\n\nReport saved as Markdown:\n{artifact.path}"
                    self._last_response_wants_report = False
                    self.console.print(Panel(answer, title="Ulysses"))
                except Exception as exc:
                    self.console.print(Panel(str(exc), title="Report failed"))
        elif cmd == "/run" and len(parts) > 1:
            self.console.print(Panel(self.orchestrator._run_skill("system_command", {"command": " ".join(parts[1:])}), title="Tool proposal"))
        elif cmd == "/create-skill" and len(parts) > 2:
            name = parts[1]
            request = " ".join(parts[2:])
            self.console.print(
                Panel(self.orchestrator._run_skill("create_skill", {"name": name, "request": request}), title="Skill proposal")
            )
        elif cmd in {"/autonomous", "/***autonomous"}:
            if len(parts) > 1 and parts[1].lower() in {"on", "off"}:
                enabled = parts[1].lower() == "on"
                self.orchestrator.set_autonomous(enabled)
                self.console.print(f"Autonomous mode: {'on' if enabled else 'off'}")
            elif len(parts) > 1 and parts[1].lower() == "now":
                note = self.orchestrator.autonomous_check(force=True)
                self.console.print(Panel(note or "No autonomous report.", title="Autonomous"))
            else:
                self.console.print(f"Autonomous mode: {'on' if self.orchestrator.autonomous_enabled() else 'off'}")
        elif cmd in {"/status", "/config"}:
            self.console.print_json(data=self.orchestrator.config.model_dump_safe())
        elif cmd == "/setup":
            self._setup_provider()
        elif cmd == "/context":
            self.console.print_json(data=self.orchestrator.context_usage())
        elif cmd == "/voice":
            if not self.voice_io:
                self.console.print("Voice I/O is not active. Start without --text-only to enable Sirina voice mode.")
            elif len(parts) == 1:
                self.console.print_json(data=self.voice_io.state.__dict__)
            elif parts[1].lower() == "on":
                self.voice_io.state.enabled = True
                self.console.print("Voice responses: on")
            elif parts[1].lower() == "off":
                self.voice_io.state.enabled = False
                self.voice_io.interrupt()
                self.console.print("Voice responses: off")
            else:
                self.console.print("Usage: /voice, /voice on, or /voice off")
        elif cmd == "/mute":
            if not self.voice_io:
                self.console.print("Voice I/O is not active. Start without --text-only to enable Sirina voice mode.")
            else:
                self.voice_io.state.muted = not self.voice_io.state.muted
                self.console.print(f"Muted: {self.voice_io.state.muted}")
        elif cmd == "/say":
            if not self.voice_io:
                self.console.print("Voice I/O is not active. Start without --text-only to enable Sirina voice mode.")
            elif len(parts) < 2:
                self.console.print("Usage: /say text to speak")
            else:
                self._speak(" ".join(parts[1:]))
        elif cmd == "/export":
            self.console.print_json(data={"sessions": self.orchestrator.sessions.list_sessions(), "memory": [item.__dict__ for item in self.orchestrator.memory.items]})
        else:
            self.console.print("Unknown command.")
        return False

    def _setup_provider(self) -> None:
        labels = {str(index): provider for index, (provider, label) in enumerate(provider_labels(), 1)}
        for index, (provider, label) in enumerate(provider_labels(), 1):
            self.console.print(f"{index}. {label} ({provider})")
        choice = Prompt.ask("provider", choices=list(labels), default="1")
        provider = labels[choice]
        model = Prompt.ask("model", default=default_for(provider, "model"))
        base_url = Prompt.ask("base URL", default=default_for(provider, "base_url"))
        api_key_env = Prompt.ask("API key env", default=default_for(provider, "api_key_env") or self.orchestrator.config.llm.api_key_env)
        api_key = ""
        oauth_token_env = default_for(provider, "oauth_token_env")
        oauth_token = ""
        if provider == "oauth_compatible":
            oauth_token_env = Prompt.ask("OAuth token env", default=oauth_token_env)
            oauth_token = Prompt.ask("OAuth token blank keeps existing", password=True, default="")
        elif provider != "ollama":
            api_key = Prompt.ask("API key blank keeps existing", password=True, default="")
        setup = ProviderSetup(provider, model, base_url, api_key_env, api_key, oauth_token_env, oauth_token)
        try:
            config_path = self.orchestrator.config_path
            apply_provider_setup(self.orchestrator.config, config_path, setup)
            load_env_file(env_path_for_config(config_path))
            self.orchestrator.config = load_config(config_path)
        except Exception as exc:
            self.console.print(Panel(str(exc), title="Provider setup failed"))
            return
        try:
            self.orchestrator.llm = build_provider(self.orchestrator.config.llm)
        except Exception as exc:
            self.console.print(Panel(str(exc), title="Provider saved, but activation failed"))
            return
        self.console.print(
            f"Provider saved and activated: {self.orchestrator.config.llm.provider} / "
            f"{self.orchestrator.config.llm.model}"
        )

    def _speak(self, text: str) -> None:
        if not self.voice_io or not self.voice_io.state.enabled or self.voice_io.state.muted:
            return
        self.console.print("[bold magenta]Ulysses speaking...[/bold magenta]")
        Thread(target=self._speak_in_thread, args=(text,), daemon=True).start()

    def _speak_in_thread(self, text: str) -> None:
        try:
            self.voice_io.speak(text)
        except Exception as exc:
            self.console.print(Panel(str(exc), title="TTS error"))
