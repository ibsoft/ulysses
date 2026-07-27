from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table


ULYSSES_LOGO = r'''
╔════════════════════╗
║      ULYSSES       ║
║    ____/^\____     ║
║   /  _     _  \    ║
║  |  / \___/ \  |   ║
║  |  \_/   \_/  |   ║
║   \    ___    /    ║
║    `-._____.-'     ║
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
            try:
                with self.console.status("[bold magenta]Ulysses is thinking...[/bold magenta]", spinner="dots"):
                    answer = self.orchestrator.handle_text(text)
            except Exception as exc:
                self.console.print(Panel(str(exc), title="Ulysses error"))
                continue
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
            self.console.print(Panel(self.orchestrator.confirm_pending_tool(token, extra), title="Tool result"))
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

    def _speak(self, text: str) -> None:
        if not self.voice_io or not self.voice_io.state.enabled or self.voice_io.state.muted:
            return
        try:
            self.voice_io.speak(text)
        except Exception as exc:
            self.console.print(Panel(str(exc), title="TTS error"))
