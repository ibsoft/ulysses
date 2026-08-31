from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from threading import Event, Thread

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from sirina_agent.config import load_config
from sirina_agent.config.security_settings import persist_godmode
from sirina_agent.config.provider_setup import (
    ProviderSetup,
    apply_provider_setup,
    complete_name_onboarding,
    default_for,
    env_path_for_config,
    load_env_file,
    provider_labels,
)
from sirina_agent.connectors.registry import ConnectorManager, connector_definitions
from sirina_agent.connectors.setup import TelegramSetup, apply_telegram_setup
from sirina_agent.connectors.telegram import TelegramConnector
from sirina_agent.core.artifacts import (
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
    should_store_large_paste,
)
from sirina_agent.core.assessment import (
    AssessmentToolOption,
    assessment_check_for_tool_option,
    AssessmentResult,
    assessment_checks,
    assessment_tool_options,
    missing_tool_installer_script,
    missing_tool_packages,
    render_assessment_report,
    render_assessment_tool_selection,
    select_assessment_tool_options,
)
from sirina_agent.core.tasks import TaskStore, format_tasks, parse_recurring_prompt
from sirina_agent.llm.openai_auth import OpenAIBrowserLogin, OpenAIBrowserLoginError
from sirina_agent.llm.providers import build_provider
from sirina_agent.mcp.client import SDKMCPClient
from sirina_agent.mcp.setup import MCPServerSetup, apply_mcp_server_setup
from sirina_agent.tui.boot import spoken_startup_brief, startup_brief
from sirina_agent.tui.branding import ULYSSES_LOGO
from sirina_agent.updates import UpdateManager


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
        self.tasks = TaskStore(self.artifacts.runtime_dir / "tasks.json")
        self._task_stop = Event()
        self._last_user_text = ""
        self.last_assistant_text = ""
        self._last_response_wants_report = False
        self._assessment_project: AssessmentProject | None = None
        self._assessment_install_attempted = False
        self._assessment_pending_tool_options: list[AssessmentToolOption] = []
        self._assessment_pending_tool_target: str | None = None
        self._queued_input: str | None = None
        self.connectors = ConnectorManager.from_config(
            orchestrator.config,
            self._handle_connector_message,
            self.console.print,
        )
        self.updates = UpdateManager(orchestrator.config.updates)

    def run(self) -> None:
        Thread(target=self._task_loop, daemon=True).start()
        boot_message = startup_brief(self.orchestrator, self.voice_io)
        self.console.print(Panel(f"{ULYSSES_LOGO}\n{boot_message}"))
        if not bool(getattr(self.orchestrator.llm, "configured", True)):
            guidance = (
                "No AI provider is configured. I will open provider setup now. Choose a provider and enter its "
                "connection details or API key."
            )
            self.console.print(Panel(guidance, title="Provider setup required"))
            self._speak(guidance)
            self._setup_provider()
        else:
            with self.console.status(
                "[bold magenta]Calling LLM Brain for startup greeting...[/bold magenta]",
                spinner="dots",
            ):
                greeting = self.orchestrator.startup_greeting()
            self.console.print(Panel(greeting, title="Ulysses"))
            self._speak(f"{spoken_startup_brief(self.orchestrator, self.voice_io)} {greeting}")
        self.connectors.start_all()
        if self.orchestrator.config.updates.enabled and self.orchestrator.config.updates.check_on_startup:
            status = self.updates.check()
            if status.state == "available":
                self.console.print(f"Ulysses update available: {status.summary()}. Run /update install to apply it.")
        while True:
            if self._queued_input is None:
                text = Prompt.ask("[bold cyan]you[/bold cyan]")
            else:
                text = self._queued_input
                self._queued_input = None
                self.console.print(f"[bold cyan]you[/bold cyan] {text}")
            if not text:
                continue
            if text.startswith("/"):
                if self._command(text):
                    break
                continue
            if re.fullmatch(
                r"(?:please\s+)?summari[sz]e\s+(?:the\s+)?(?:last|previous)\s+(?:response|answer|message)",
                text.strip(),
                re.IGNORECASE,
            ):
                if not self.last_assistant_text:
                    self.console.print(Panel("No previous assistant response is available.", title="Ulysses"))
                else:
                    with self.console.status("Summarizing previous response locally...", spinner="dots"):
                        summary = self.orchestrator.summarize_for_voice(self.last_assistant_text, force=True)
                    self.last_assistant_text = summary
                    self.console.print(Panel(summary, title="Ulysses"))
                    self._speak(summary)
                continue
            recurring = parse_recurring_prompt(text)
            if recurring:
                try:
                    task = self.tasks.add(*recurring)
                    self.console.print(f"Recurring task created: {task.id}; next run: {task.next_run_at}")
                except ValueError as exc:
                    self.console.print(Panel(str(exc), title="Task error"))
                continue
            if re.search(r"\b(?:show|open|display|view|read|list)\b.*\breports?\b", text, re.I):
                report_path, guidance = self.artifacts.resolve_report(text, self._assessment_project)
                if report_path is None:
                    self.console.print(Panel(guidance, title="Ulysses"))
                else:
                    try:
                        report = report_path.read_text(encoding="utf-8")
                        self.console.print(Panel(f"{report}\n\nLoaded report:\n{report_path}", title="Ulysses"))
                    except OSError as exc:
                        self.console.print(Panel(f"Report could not be opened: {exc}", title="Ulysses error"))
                continue
            if self._assessment_pending_tool_options:
                selected, error = select_assessment_tool_options(text, self._assessment_pending_tool_options)
                if error:
                    self.console.print(Panel(error, title="Ulysses"))
                    continue
                target = self._assessment_pending_tool_target
                self._assessment_pending_tool_options = []
                self._assessment_pending_tool_target = None
                if not self._assessment_project or not target:
                    self.console.print(Panel("No active assessment target is available. Start a new assessment request.", title="Ulysses"))
                    continue
                self._last_user_text = text
                self._last_response_wants_report = True
                with self.console.status("[bold magenta]Ulysses is collecting assessment evidence...[/bold magenta]", spinner="dots"):
                    answer = self._run_complete_assessment(target, selected_options=selected)
                self.last_assistant_text = answer
                self.console.print(Panel(answer, title="Ulysses"))
                if self._should_speak_response(answer):
                    self._speak(answer)
                continue
            new_assessment = is_assessment_request(text)
            assessment_request = new_assessment or (
                self._assessment_project is not None and is_assessment_continuation(text)
            )
            wants_report = assessment_request or (is_report_request(text) and not is_skill_creation_request(text))
            self._last_user_text = text
            self._last_response_wants_report = wants_report
            if new_assessment:
                self._assessment_project = self.artifacts.create_assessment_project(self.orchestrator.session_id, text)
                self._assessment_install_attempted = False
                self._assessment_pending_tool_options = []
                self._assessment_pending_tool_target = None
                target = assessment_target(text)
                if target:
                    direct_command = assessment_command_for_text(text, text)
                    options = assessment_tool_options(target, direct_command)
                    self._assessment_pending_tool_options = options
                    self._assessment_pending_tool_target = target
                    self._set_project_result_capture(self._assessment_project)
                    self.console.print(f"Assessment project created: {self._assessment_project.path}")
                    self.console.print(Panel(render_assessment_tool_selection(target, options), title="Ulysses"))
                    continue
            self._set_project_result_capture(self._assessment_project)
            prompt_text = text
            if should_store_large_paste(text, self.orchestrator.config.context.max_chars):
                artifact = self.artifacts.save_text_attachment(self.orchestrator.session_id, text)
                self.console.print(f"Large paste saved as text file: {artifact.path}")
                prompt_text = attachment_prompt(text, artifact)
            direct_command = None
            target = None
            if self._assessment_project:
                if new_assessment:
                    self.console.print(f"Assessment project created: {self._assessment_project.path}")
                direct_command = assessment_command_for_text(text, _project_request(self._assessment_project))
                target = assessment_target(text) or assessment_target(_project_request(self._assessment_project))
                if direct_command and not new_assessment:
                    prompt_text = None
                elif assessment_request and target:
                    prompt_text = "__complete_assessment__"
                elif assessment_request:
                    prompt_text = self._assessment_prompt(prompt_text, self._assessment_project)
            try:
                with self.console.status("[bold magenta]Ulysses is thinking...[/bold magenta]", spinner="dots"):
                    if prompt_text == "__complete_assessment__":
                        answer = self._run_complete_assessment(target, direct_command)
                    elif prompt_text is None:
                        answer = self.orchestrator._run_skill("system_command", {"command": direct_command})
                    else:
                        answer = self.orchestrator.handle_text(prompt_text)
            except Exception as exc:
                self.console.print(Panel(str(exc), title="Ulysses error"))
                continue
            should_speak = self._should_speak_response(answer)
            save_final_assessment = self._assessment_project is not None and (
                is_final_assessment_report(answer) or is_report_request(self._last_user_text)
            )
            save_standalone_report = self._assessment_project is None and wants_report
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
            self.last_assistant_text = answer
            self.console.print(Panel(answer, title="Ulysses"))
            if should_speak:
                self._speak(answer)
        self.connectors.stop_all()
        self._task_stop.set()
        if self.orchestrator.mcp:
            self.orchestrator.mcp.stop()

    def _run_complete_assessment(
        self,
        target: str,
        preferred_command: str | None = None,
        selected_options: list[AssessmentToolOption] | None = None,
    ) -> str:
        assert self._assessment_project is not None
        results = []
        if selected_options is None:
            options = assessment_tool_options(target, preferred_command)
            self._assessment_pending_tool_options = options
            self._assessment_pending_tool_target = target
            return render_assessment_tool_selection(target, options)
        for check in [assessment_check_for_tool_option(option) for option in selected_options]:
            self.orchestrator.sync_command_policy_from_config(force=True)
            result = self.orchestrator._run_skill_result("system_command", {"command": check.command})
            output = result.confirmation_prompt or result.content
            if result.requires_confirmation:
                self.orchestrator.pending_tool = {
                    "name": "system_command",
                    "arguments": {"command": check.command},
                    "token": result.confirmation_token,
                }
            else:
                self.orchestrator._record_tool_result(
                    "system_command",
                    result.content,
                    {"skill": "system_command", "ok": result.ok, "data": result.data, "planned_command": check.command},
                )
            self.artifacts.save_project_result(self._assessment_project, check.id, output)
            results.append(AssessmentResult(check, output, bool(result.ok)))
            if result.requires_confirmation:
                break
        packages = missing_tool_packages(results)
        if (
            packages
            and self.orchestrator.config.skills.command.install_missing_assessment_tools
            and not self._assessment_install_attempted
            and self.orchestrator.pending_tool is None
        ):
            self._assessment_install_attempted = True
            installer = self.artifacts.save_project_script(
                self._assessment_project,
                "install-missing-tools",
                missing_tool_installer_script(),
            )
            install_command = f"sudo python3 {installer.path} {' '.join(packages)}"
            proposal = self.orchestrator._run_skill_result("system_command", {"command": install_command})
            if proposal.requires_confirmation:
                self.orchestrator.pending_tool = {
                    "name": "system_command",
                    "arguments": {"command": install_command},
                    "token": proposal.confirmation_token,
                }
                password = Prompt.ask("sudo password", password=True)
                install_output = self.orchestrator.confirm_pending_tool(
                    proposal.confirmation_token,
                    {"sudo_password": password},
                )
            else:
                install_output = proposal.content
            self.artifacts.save_project_result(self._assessment_project, "install-missing-tools", install_output)
            return self._run_complete_assessment(target, preferred_command)
        report = render_assessment_report(target, results)
        artifact = self.artifacts.save_project_markdown_report(self._assessment_project, report)
        return f"{report}\n\nReport saved as Markdown:\n{artifact.path}"

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
            table = Table("name", "risk", "enabled", "scope", "permissions", "status")
            for manifest in self.orchestrator.skills.manifests():
                scope = (
                    "Ulysses + sub-agents"
                    if self.orchestrator.subagents
                    and self.orchestrator.subagents.capabilities.is_delegable(manifest.name)
                    else "Ulysses only"
                )
                table.add_row(
                    manifest.name,
                    manifest.risk_level,
                    str(manifest.enabled),
                    scope,
                    ", ".join(manifest.required_permissions),
                    "ready",
                )
            for name, manifest, error in self.orchestrator.skills.load_failures():
                table.add_row(
                    name,
                    manifest.risk_level if manifest else "unknown",
                    str(manifest.enabled) if manifest else "unknown",
                    "unavailable",
                    ", ".join(manifest.required_permissions) if manifest else "",
                    f"load_failed: {error}",
                )
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
            resume_skill = self.orchestrator.consume_skill_resume()
            if resume_skill:
                with self.console.status(f"[bold magenta]Using {resume_skill}...[/bold magenta]", spinner="dots"):
                    answer = self.orchestrator.handle_text(
                        f"Skill `{resume_skill}` is now active. Continue the prior request and use `{resume_skill}` now "
                        f"when applicable. Do not recreate it.\n\nPrior request:\n{self._last_user_text}"
                    )
                self.console.print(Panel(answer, title="Ulysses"))
            elif self._last_response_wants_report:
                try:
                    with self.console.status(
                        "[bold magenta]Ulysses is writing report...[/bold magenta]", spinner="dots"
                    ):
                        answer = self.orchestrator.answer_from_tool_result(self._last_user_text, tool_result)
                    if self._assessment_project:
                        artifact = self.artifacts.save_project_markdown_report(self._assessment_project, answer)
                    else:
                        artifact = self.artifacts.save_markdown_report(self.orchestrator.session_id, answer)
                    answer = f"{answer}\n\nReport saved as Markdown:\n{artifact.path}"
                    self._last_response_wants_report = False
                    self._set_project_result_capture(self._assessment_project)
                    self.console.print(Panel(answer, title="Ulysses"))
                except Exception as exc:
                    self.console.print(Panel(str(exc), title="Report failed"))
        elif cmd == "/run" and len(parts) > 1:
            self.console.print(
                Panel(
                    self.orchestrator._run_skill("system_command", {"command": " ".join(parts[1:])}),
                    title="Tool proposal",
                )
            )
        elif cmd == "/create-skill" and len(parts) > 2:
            name = parts[1]
            request = " ".join(parts[2:])
            with self.console.status("[bold cyan]Researching and building skill...[/bold cyan]", spinner="dots"):
                result = self.orchestrator._run_skill("create_skill", {"name": name, "request": request})
            self.console.print(Panel(result, title="Skill proposal"))
        elif cmd == "/autonomous":
            if len(parts) > 1 and parts[1].lower() in {"on", "off"}:
                enabled = parts[1].lower() == "on"
                self.orchestrator.set_autonomous(enabled)
                self.console.print(
                    "Autonomous defense: on. Periodic host checks, evidence logging, adaptive frequency, and defensive reports."
                    if enabled
                    else "Autonomous defense: off."
                )
            elif len(parts) > 1 and parts[1].lower() == "now":
                note = self.orchestrator.autonomous_check(force=True)
                self.console.print(Panel(note or "No autonomous report.", title="Autonomous"))
                if note:
                    self._speak(note)
            else:
                self.console.print(f"Autonomous mode: {'on' if self.orchestrator.autonomous_enabled() else 'off'}")
        elif cmd in {"/task", "/tasks"}:
            self._task_command(text, parts)
        elif cmd in {"/status", "/config"}:
            self.console.print_json(data=self.orchestrator.config.model_dump_safe())
            self.console.print(f"Update: {self.updates.status.summary()}")
        elif cmd == "/godmode":
            current = self.orchestrator.config.skills.command.godmode
            if len(parts) == 1 or parts[1].lower() == "status":
                self.console.print(f"Godmode: {'on' if current else 'off'}")
            elif parts[1].lower() == "off":
                credential_error = None
                try:
                    self.orchestrator.clear_godmode_sudo_password()
                except Exception as exc:
                    credential_error = str(exc)
                persist_godmode(self.orchestrator.config_path, False)
                self.orchestrator.config.skills.command.godmode = False
                self.orchestrator.sync_command_policy_from_config(force=True)
                self.console.print("Godmode: off (saved and active)")
                if credential_error:
                    self.console.print(f"Credential-vault cleanup could not be verified: {credential_error}")
            elif (
                parts[1].lower() == "on"
                and " ".join(parts[2:]) == "I ACCEPT UNRESTRICTED COMMAND EXECUTION"
            ):
                ready, guidance = self.orchestrator.godmode_credential_readiness()
                if not ready:
                    self.console.print(Panel(guidance, title="Godmode was not enabled"))
                    return False
                persist_godmode(self.orchestrator.config_path, True)
                self.orchestrator.config.skills.command.godmode = True
                self.orchestrator.sync_command_policy_from_config(force=True)
                self.console.print("Godmode: on (saved and active)")
            else:
                self.console.print(
                    "Usage: /godmode, /godmode off, or /godmode on I ACCEPT UNRESTRICTED COMMAND EXECUTION"
                )
        elif cmd == "/update":
            if len(parts) > 1 and parts[1].lower() == "install":
                with self.console.status("[bold cyan]Installing update from GitHub main...[/bold cyan]", spinner="dots"):
                    message = self.updates.install()
                self.console.print(Panel(message, title="Update"))
            elif len(parts) == 1 or parts[1].lower() == "check":
                with self.console.status("[bold cyan]Checking GitHub main...[/bold cyan]", spinner="dots"):
                    status = self.updates.check()
                self.console.print(Panel(status.error or status.summary(), title="Update"))
            else:
                self.console.print("Usage: /update or /update install")
        elif cmd == "/reload":
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
                self.console.print(Panel(str(exc), title="Config reload failed"))
            else:
                self.console.print(
                    f"Config reloaded from {self.orchestrator.config_path}; "
                    f"external skills: {', '.join(loaded) or 'none'}; "
                    f"command allowlist synchronized: "
                    f"{len(set(self.orchestrator.config.skills.command.allowed_commands))} commands"
                )
        elif cmd == "/mcp":
            action = parts[1].lower() if len(parts) > 1 else "servers"
            if action in {"servers", "status"}:
                self.console.print(self.orchestrator.mcp.status_detail())
            elif action == "tools":
                names = [
                    manifest.name
                    for manifest in self.orchestrator.skills.manifests()
                    if manifest.name.startswith("mcp__")
                ]
                self.console.print("\n".join(names) or "No MCP tools are registered.")
            elif action == "reconnect" and len(parts) > 2:
                try:
                    self.orchestrator.mcp.discover(parts[2])
                    self.console.print(f"MCP reconnection started: {parts[2]}")
                except KeyError as exc:
                    self.console.print(str(exc))
            else:
                self.console.print("Usage: /mcp servers, /mcp tools, or /mcp reconnect <server>")
        elif cmd == "/setup":
            if len(parts) > 1 and parts[1].lower() in {"provider", "providers"}:
                self._setup_provider()
            elif len(parts) > 1 and parts[1].lower() in {"connector", "connectors"}:
                self._setup_connectors()
            elif len(parts) > 1 and parts[1].lower() == "mcp":
                self._setup_mcp()
            else:
                self.console.print("Usage: /setup providers, /setup connectors, or /setup mcp")
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
        elif cmd == "/talk":
            if not self.voice_io:
                self.console.print("Voice input is not active. Start without --text-only to use push to talk.")
            else:
                if self.voice_io.state.tts == "speaking":
                    self.voice_io.interrupt()
                try:
                    with self.console.status("[bold cyan]Listening...[/bold cyan]", spinner="dots"):
                        transcript = self.voice_io.listen_once().strip()
                except Exception as exc:
                    self.console.print(Panel(str(exc), title="Voice input failed"))
                else:
                    if transcript:
                        self._queued_input = transcript
                    else:
                        self.console.print("No speech detected.")
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
            self.console.print_json(
                data={
                    "sessions": self.orchestrator.sessions.list_sessions(),
                    "memory": [item.__dict__ for item in self.orchestrator.memory.items],
                }
            )
        else:
            self.console.print("Unknown command.")
        return False

    def _task_command(self, text: str, parts: list[str]) -> None:
        action = parts[1].lower() if len(parts) > 1 and parts[0] == "/task" else "list"
        if action in {"list", "status"}:
            self.console.print(format_tasks(self.tasks.list()))
        elif action == "add":
            specification = text.split("add", 1)[1].strip()
            if "::" not in specification:
                self.console.print("Usage: /task add <schedule> :: <prompt>")
                return
            schedule, prompt = (part.strip() for part in specification.split("::", 1))
            try:
                task = self.tasks.add(schedule, prompt)
                self.console.print(f"Recurring task created: {task.id}; next run: {task.next_run_at}")
            except ValueError as exc:
                self.console.print(f"Task error: {exc}")
        elif len(parts) >= 3 and action in {"pause", "resume", "delete", "run"}:
            task_id = parts[2]
            if action == "delete":
                self.console.print(f"Deleted {task_id}." if self.tasks.delete(task_id) else "Task not found.")
            elif action in {"pause", "resume"}:
                task = self.tasks.update_enabled(task_id, action == "resume")
                self.console.print(f"{action.title()}d {task_id}." if task else "Task not found.")
            else:
                task = next((item for item in self.tasks.list() if item.id == task_id), None)
                self.console.print(self._run_task(task) if task else "Task not found.")
        else:
            self.console.print("Usage: /task add|list|pause|resume|run|delete")

    def _task_loop(self) -> None:
        while not self._task_stop.is_set():
            due = self.tasks.due()
            if due:
                result = self._run_task(due[0])
                self.console.print(Panel(result, title=f"Recurring task {due[0].id}"))
            self._task_stop.wait(1.0)

    def _run_task(self, task) -> str:
        self.tasks.mark_started(task.id)
        try:
            result = self.orchestrator.handle_text(task.prompt)
        except Exception as exc:
            result = f"Failed: {exc}"
        self.tasks.mark_finished(task.id, result)
        return result

    def _setup_provider(self) -> None:
        labels = {str(index): provider for index, (provider, label) in enumerate(provider_labels(), 1)}
        for index, (provider, label) in enumerate(provider_labels(), 1):
            self.console.print(f"{index}. {label} ({provider})")
        choice = Prompt.ask("provider", choices=list(labels), default="1")
        provider = labels[choice]
        if provider == "openai_chatgpt":
            model = ""
            base_url = ""
            api_key_env = ""
        else:
            model = Prompt.ask("model", default=default_for(provider, "model"))
            base_url = Prompt.ask("base URL", default=default_for(provider, "base_url"))
            api_key_env = Prompt.ask(
                "API key env",
                default=default_for(provider, "api_key_env") or self.orchestrator.config.llm.api_key_env,
            )
        api_key = ""
        if provider not in {"ollama", "openai_chatgpt"}:
            api_key = Prompt.ask("API key blank keeps existing", password=True, default="")
        setup = ProviderSetup(provider, model, base_url, api_key_env, api_key)
        if provider == "openai_chatgpt":
            login = OpenAIBrowserLogin()
            try:
                with self.console.status("Preparing OpenAI-Codex login...", spinner="dots"):
                    login.start()
                self.console.print("Open this login link in your browser:")
                self.console.print(login.auth_url, markup=False, soft_wrap=True)
                callback_url = Prompt.ask("Paste the localhost return URL", password=True)
                with self.console.status("Completing OpenAI-Codex login...", spinner="dots"):
                    model = login.complete(callback_url)
                setup = replace(setup, model=model, base_url="", api_key_env="", api_key="")
            except OpenAIBrowserLoginError as exc:
                login.close()
                self.console.print(Panel(str(exc), title="Provider setup failed"))
                return
        try:
            config_path = self.orchestrator.config_path
            apply_provider_setup(self.orchestrator.config, config_path, setup)
            load_env_file(env_path_for_config(config_path))
            self.orchestrator.config = load_config(config_path)
            self.orchestrator.sync_command_policy_from_config()
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
        first_run_name_prompt = (
            not self.orchestrator.config.tui.name_prompt_completed
            and self.orchestrator.sessions.message_count(self.orchestrator.session_id) == 0
        )
        if not self.orchestrator.config.tui.name_prompt_completed:
            try:
                complete_name_onboarding(self.orchestrator.config, config_path)
            except Exception as exc:
                self.console.print(Panel(str(exc), title="Could not save onboarding state"))
        if first_run_name_prompt:
            question = "Provider setup is complete. How would you like me to address you?"
            self.console.print(Panel(question, title="Ulysses"))
            self._speak(question)

    def _setup_connectors(self) -> None:
        definitions = connector_definitions()
        choices = {str(index): definition.id for index, definition in enumerate(definitions, 1)}
        for index, definition in enumerate(definitions, 1):
            self.console.print(f"{index}. {definition.label} - {definition.description}")
        choice = Prompt.ask("connector", choices=list(choices), default="1")
        connector_id = choices[choice]
        if connector_id == "telegram":
            self._setup_telegram()
        else:
            self.console.print(f"Connector setup is not available: {connector_id}")

    def _setup_mcp(self) -> None:
        current = {server.id: server for server in self.orchestrator.config.mcp.servers}
        choices = ["new", *current]
        for index, server_id in enumerate(choices, 1):
            self.console.print(f"{index}. {server_id}")
        selected = choices[int(Prompt.ask("MCP server", choices=[str(i) for i in range(1, len(choices) + 1)])) - 1]
        existing = current.get(selected)
        server_id = existing.id if existing else Prompt.ask("server id")
        enabled = Confirm.ask("enabled", default=existing.enabled if existing else True)
        transport = Prompt.ask(
            "transport",
            choices=["stdio", "streamable_http"],
            default=existing.transport if existing else "stdio",
        )
        command = Prompt.ask("command", default=existing.command if existing else "") if transport == "stdio" else ""
        args_text = Prompt.ask(
            "arguments as JSON array",
            default=json.dumps(existing.args if existing else []),
        )
        try:
            args = json.loads(args_text)
            if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
                raise ValueError("arguments must be a JSON array of strings")
        except ValueError as exc:
            self.console.print(Panel(str(exc), title="MCP setup failed"))
            return
        url = Prompt.ask("MCP URL", default=existing.url if existing else "") if transport == "streamable_http" else ""
        env_names = Prompt.ask(
            "environment variable names, comma separated",
            default=", ".join(existing.environment_variables) if existing else "",
        )
        token_env = Prompt.ask(
            "bearer token environment variable",
            default=existing.bearer_token_env if existing else "",
        )
        token = Prompt.ask("bearer token blank keeps existing", password=True, default="") if token_env else ""
        tools = Prompt.ask(
            "allowed tool names, comma separated",
            default=", ".join(existing.tool_allowlist) if existing else "",
        )
        risk = Prompt.ask(
            "risk",
            choices=["high", "medium", "low"],
            default=existing.risk_level if existing else "high",
        )
        confirmation = Confirm.ask(
            "require confirmation for every tool call",
            default=existing.require_confirmation if existing else True,
        )
        setup = MCPServerSetup(
            server_id,
            enabled,
            transport,
            command,
            tuple(args),
            url,
            tuple(_csv(env_names)),
            token_env,
            token,
            tuple(_csv(tools)),
            risk,
            confirmation,
            existing.timeout_seconds if existing else 60,
        )
        server = setup.server_config()
        previous_token = os.environ.get(token_env) if token_env else None
        if token and token_env:
            os.environ[token_env] = token
        try:
            with self.console.status("Validating MCP server...", spinner="dots"):
                discovered = (
                    SDKMCPClient(self.orchestrator.config.mcp.allowed_stdio_commands).discover(server)
                    if enabled
                    else []
                )
            apply_mcp_server_setup(self.orchestrator.config, self.orchestrator.config_path, setup)
            load_env_file(env_path_for_config(self.orchestrator.config_path))
            self.orchestrator.config = load_config(self.orchestrator.config_path)
            self.orchestrator.mcp.reconfigure(self.orchestrator.config.mcp, start=False)
            if enabled:
                self.orchestrator.mcp.discover_now(server.id)
        except Exception as exc:
            if token_env:
                if previous_token is None:
                    os.environ.pop(token_env, None)
                else:
                    os.environ[token_env] = previous_token
            self.console.print(Panel(str(exc), title="MCP validation failed; not saved"))
            return
        status = self.orchestrator.mcp.status(server.id)
        self.console.print(
            Panel(
                f"Status: {status.state}\nAdvertised tools: "
                f"{', '.join(str(tool.get('name') or '') for tool in discovered) or 'none'}\n"
                f"Allowed tools registered: {status.tool_count}",
                title=f"MCP {server.id}",
            )
        )

    def _setup_telegram(self) -> None:
        if self.orchestrator.config.connectors.telegram.enabled and not Confirm.ask(
            "Keep Telegram enabled?", default=True
        ):
            self.connectors.remove("telegram")
            apply_telegram_setup(self.orchestrator.config, self.orchestrator.config_path, TelegramSetup(False))
            self.orchestrator.config = load_config(self.orchestrator.config_path)
            self.console.print("Telegram connector disabled.")
            return
        token = Prompt.ask("BotFather token; blank keeps existing", password=True, default="")
        telegram_config = self.orchestrator.config.connectors.telegram.model_copy(update={"enabled": True})
        candidate = TelegramConnector(
            telegram_config,
            self._handle_connector_message,
            self.console.print,
            token=token.strip() or os.environ.get(telegram_config.token_env, ""),
        )
        try:
            with self.console.status("[bold cyan]Verifying Telegram bot...[/bold cyan]", spinner="dots"):
                candidate.validate()
            apply_telegram_setup(self.orchestrator.config, self.orchestrator.config_path, TelegramSetup(True, token))
            load_env_file(env_path_for_config(self.orchestrator.config_path))
            self.orchestrator.config = load_config(self.orchestrator.config_path)
            code = candidate.begin_pairing()
            candidate.start()
        except Exception as exc:
            candidate.stop()
            self.console.print(Panel(str(exc), title="Telegram connector setup failed"))
            return
        self.connectors.replace(candidate)
        self.console.print(
            Panel(
                f"Send [bold]/verify {code}[/bold] to the bot within "
                f"{candidate.config.pairing_code_ttl_seconds // 60} minutes.",
                title="Telegram connector",
            )
        )

    def _handle_connector_message(self, connector_id: str, chat_id: int, text: str) -> str:
        self.console.print(f"[bold cyan]{connector_id.title()} {chat_id}[/bold cyan] {text}")
        lowered = text.strip().lower()
        if lowered.startswith("/confirm"):
            if self.orchestrator.pending_tool_requires_sudo_password():
                return "This command requires local sudo authentication. Confirm it in the local Ulysses console."
            parts = text.split(maxsplit=1)
            return self.orchestrator.confirm_pending_tool(parts[1].strip() if len(parts) == 2 else None)
        if lowered == "/cancel":
            return "Pending command cancelled." if self.orchestrator.cancel_pending_tool() else "No command is pending."
        return self.orchestrator.handle_text(text)

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

    def _speak(self, text: str) -> None:
        if not self.voice_io or not self.voice_io.state.enabled or self.voice_io.state.muted:
            return
        self.console.print("[bold magenta]Ulysses speaking...[/bold magenta]")
        Thread(target=self._speak_in_thread, args=(text,), daemon=True).start()

    def _speak_in_thread(self, text: str) -> None:
        try:
            self.voice_io.speak(self.orchestrator.summarize_for_voice(text))
        except Exception as exc:
            self.console.print(Panel(str(exc), title="TTS error"))


def _project_request(project: AssessmentProject) -> str:
    try:
        return (project.artifacts_dir / "request.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "current assessment"


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
