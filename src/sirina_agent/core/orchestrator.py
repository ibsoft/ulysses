from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import random
import re
from threading import RLock, Thread
from typing import Callable

from .defense import AutonomousDefenseEngine, DefenseCheck
from ..security.commands import CommandPolicy
from ..security.sudo_credentials import SudoCredentialStore
from ..skills.base import SkillResult
from ..skills.builder import SkillBuildError, build_skill_spec


MEMORY_SAVE_TIMEOUT_SECONDS = 2.0


class AgentOrchestrator:
    def __init__(
        self,
        config,
        sessions,
        memory,
        llm,
        skills,
        config_path: str | Path | None = None,
        subagents=None,
        mcp=None,
    ) -> None:
        self.config = config
        self.config_path = Path(config_path).expanduser() if config_path else Path("config/ulysses.yaml")
        self.sudo_credentials = SudoCredentialStore(self.config_path)
        self.sessions = sessions
        self.memory = memory
        self.llm = llm
        self.skills = skills
        self.subagents = subagents
        self.mcp = mcp
        self.pending_tool: dict | None = None
        self.active_skill: str | None = None
        self.skill_resume_name: str | None = None
        self.activity_callback: Callable[[str], None] | None = None
        self.tool_result_callback: Callable[[str, str, dict], None] | None = None
        self._interaction_lock = RLock()
        self.defense = AutonomousDefenseEngine()
        existing = sessions.list_sessions()
        self.session_id = existing[0]["id"] if existing else sessions.create_session("Ulysses")

    def set_activity_callback(self, callback: Callable[[str], None] | None) -> None:
        self.activity_callback = callback

    def set_tool_result_callback(self, callback: Callable[[str, str, dict], None] | None) -> None:
        self.tool_result_callback = callback

    def sync_command_policy_from_config(self, force: bool = True) -> bool:
        try:
            skill = self.skills.get("system_command")
            if not force:
                current_policy = skill.runner.policy
                configured_cwd = self.config.skills.command.working_directory.resolve()
                if current_policy.working_directory != configured_cwd:
                    return False
            skill.runner.policy = CommandPolicy(
                self.config.skills.command.allowed_commands,
                self.config.skills.command.denied_commands,
                self.config.skills.command.working_directory,
                self.config.skills.command.env_allowlist,
                self.config.skills.command.require_confirmation,
                self.config.skills.command.require_typed_confirmation_for_high_risk,
                self.config.skills.command.bypass_confirmation_for_allowed_commands,
                self.config.skills.command.godmode,
            )
            skill.runner.timeout_seconds = self.config.skills.command.timeout_seconds
            skill.runner.max_output_chars = self.config.skills.command.max_output_chars
            return True
        except Exception:
            return False

    def _activity(self, message: str) -> None:
        if self.activity_callback:
            self.activity_callback(message)

    def _record_tool_result(self, name: str, content: str, metadata: dict) -> None:
        self.sessions.add_message(self.session_id, "tool", content, metadata)
        if self.tool_result_callback:
            self.tool_result_callback(name, content, metadata)

    def _save_assistant_message(
        self, content: str, metadata: dict | None = None, importance: float = 0.3, source_prefix: str = "session"
    ) -> None:
        self.sessions.add_message(self.session_id, "assistant", content, metadata)
        self._save_memory_soft(
            content,
            source=f"{source_prefix}:{self.session_id}",
            importance=importance,
            metadata=metadata or {"role": "assistant"},
        )

    def _save_memory_soft(self, text: str, source: str, importance: float, metadata: dict | None = None) -> None:
        error: list[Exception] = []

        def save() -> None:
            try:
                self.memory.add(text, source=source, importance=importance, metadata=metadata or {})
            except Exception as exc:
                error.append(exc)

        worker = Thread(target=save, daemon=True)
        worker.start()
        worker.join(MEMORY_SAVE_TIMEOUT_SECONDS)
        if worker.is_alive():
            self._activity("memory save deferred")
        elif error:
            self._activity(f"memory save skipped: {error[0]}")

    def handle_text(self, text: str) -> str:
        with self._interaction_guard():
            return self._handle_text_locked(text)

    def _interaction_guard(self) -> RLock:
        lock = getattr(self, "_interaction_lock", None)
        if lock is None:
            lock = RLock()
            self._interaction_lock = lock
        return lock

    def _handle_text_locked(self, text: str) -> str:
        self._activity("checking request")
        confirmation = text.strip().lower()
        pending_tool = getattr(self, "pending_tool", None)
        if pending_tool and confirmation in {"yes", "y", "confirm", "confirmed", "proceed", "go ahead"}:
            return self._confirm_pending_tool_locked()
        if pending_tool and confirmation in {"no", "n", "cancel", "stop", "abort"}:
            self.cancel_pending_tool()
            return "Pending command cancelled."
        direct_skill = self._direct_skill_creation(text)
        if direct_skill:
            self.sessions.add_message(self.session_id, "user", text)
            return self._run_skill(
                "create_skill",
                {"name": direct_skill[0], "request": direct_skill[1]},
                resume_after_confirmation=True,
            )
        direct_plan = self._direct_system_command_plan(text)
        if direct_plan:
            self.sessions.add_message(self.session_id, "user", text)
            return self._run_command_plan(text, direct_plan)
        direct_tool = self._direct_system_command(text)
        if direct_tool:
            self.sessions.add_message(self.session_id, "user", text)
            return self._run_skill("system_command", {"command": direct_tool})
        self._activity("saving user message")
        self.sessions.add_message(self.session_id, "user", text)
        self._save_memory_soft(text, source=f"session:{self.session_id}", importance=0.4, metadata={"role": "user"})
        self._activity("checking context")
        self._maybe_consolidate_session()
        self._activity("searching memory")
        memories = (
            self.memory.search(text, top_k=self.config.memory.top_k) if self.config.privacy.retrieve_memory else []
        )
        context = "\n".join(f"- {item.text} ({item.source}, {item.created_at})" for item in memories)
        self._activity("preparing prompt")
        system = self._system_prompt()
        messages = [{"role": "system", "content": system}]
        subagent_reports = self.subagents.completed_reports() if self.subagents else []
        if subagent_reports:
            reports = "\n\n".join(
                f"Sub-agent: {item['agent']}\nJob: {item['id']}\nTask: {item['task']}\n"
                f"Status: {item['status']}\nReport:\n{item.get('response') or item.get('error') or 'No report supplied.'}"
                for item in subagent_reports
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "New subordinate-agent reports are available below. They report to Ulysses, not the user. "
                        "Use relevant results in this answer, verify uncertainty, and decide whether follow-up delegation is needed.\n\n"
                        f"{reports}"
                    ),
                }
            )
        session_summary = self.sessions.session_metadata(self.session_id).get("summary")
        if session_summary:
            messages.append({"role": "system", "content": f"Consolidated session context:\n{session_summary}"})
        if context:
            messages.append({"role": "system", "content": f"Relevant memory:\n{context}"})
        for msg in self.sessions.messages(self.session_id, limit=20):
            if msg.role in {"system", "user", "assistant"}:
                messages.append({"role": msg.role, "content": msg.content})
            elif msg.role == "tool":
                messages.append({"role": "system", "content": f"Previous tool result: {msg.content}"})
        tools = self._tool_schemas()
        command_assignment = self._is_command_assignment(text)
        external_network_task = self._is_external_network_task(text)
        require_tool = getattr(self.llm, "complete_with_required_tool", None)
        require_tool_brief = getattr(self.llm, "complete_with_required_tool_brief", None)
        if external_network_task and callable(require_tool_brief):
            self._activity("asking LLM Brain for one bounded network command plan")
            response = require_tool_brief(
                messages,
                tools,
                "system_command",
                timeout_seconds=self.config.llm.network_planning_timeout_seconds,
            )
        elif command_assignment and callable(require_tool):
            self._activity("asking LLM Brain for one command plan")
            response = require_tool(messages, tools, "system_command")
        else:
            self._activity("calling my LLM brain")
            response = self.llm.complete(messages, tools=tools)
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls and command_assignment:
            self._activity("requesting executable tool call")
            messages.append({"role": "assistant", "content": message.get("content") or ""})
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The user assigned an operational command task, but the previous answer only described it. "
                        "Call system_command now with the concrete command needed to perform the task. Do not print, "
                        "propose, simulate, or narrate a command. Do not claim execution without a tool call. Split "
                        "compound shell expressions into separate system_command calls unless the exact compound "
                        "command is required. The runtime will enforce policy and confirmation."
                    ),
                }
            )
            if callable(require_tool):
                response = require_tool(messages, tools, "system_command")
            else:
                response = self.llm.complete(messages, tools=tools)
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
        if tool_calls:
            content = self._handle_tool_calls(
                messages,
                message,
                tool_calls,
                tools,
            compact_command_completion=external_network_task
            and all((call.get("function") or {}).get("name") == "system_command" for call in tool_calls),
            )
            if subagent_reports:
                self.subagents.mark_reported([item["id"] for item in subagent_reports])
            return content
        self._activity("saving answer")
        content = message.get("content") or ""
        self._save_assistant_message(content)
        if subagent_reports:
            self.subagents.mark_reported([item["id"] for item in subagent_reports])
        return content

    @staticmethod
    def _is_command_assignment(text: str) -> bool:
        request = text.strip().lower()
        if not request or re.search(r"\b(?:how to|how do i|what command|which command|explain)\b", request):
            return False
        if re.search(r"\b(?:show|open|display|view|read|list)\b.*\breports?\b", request):
            return False
        if re.search(r"^(?:give|show|provide)\s+(?:me\s+)?(?:an?\s+)?example\b", request):
            return False
        if re.search(
            r"\b(?:current|live|system)\b.*\b(?:status|state|time|date|identity|resources?|hardware|network)\b"
            r"|\b(?:status|state|time|date|identity|resources?|hardware|network)\b.*\b(?:current|live|system)\b",
            request,
        ):
            return True
        return bool(
            re.search(
                r"^(?:please\s+)?(?:run|execute|install|uninstall|remove|update|upgrade|start|stop|restart|"
                r"enable|disable|scan|check|inspect|list|show|find|create|copy|move|rename|download|"
                r"ping|dig|nslookup|whois|traceroute|tracepath|curl)\b",
                request,
            )
        )

    @staticmethod
    def _is_external_network_task(text: str) -> bool:
        request = text.strip().lower()
        if not AgentOrchestrator._is_command_assignment(text):
            return False
        return bool(
            re.search(
                r"\b(?:network|port|ports|scan|nmap|ping|dns|dig|nslookup|whois|traceroute|tracepath|"
                r"curl|http|https|website|domain|host|tls|ssl|certificate|whatweb|nikto|nuclei)\b",
                request,
            )
            or re.search(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}\b", request)
            or re.search(r"https?://", request)
        )

    def _tool_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": skill.manifest.name,
                    "description": skill.manifest.description,
                    "parameters": skill.manifest.arguments_schema,
                },
            }
            for skill in self.skills.enabled()
        ]

    def _maybe_consolidate_session(self) -> None:
        cfg = self.config.context
        if not cfg.auto_consolidate:
            return
        usage = self.context_usage()
        if usage["percent"] < cfg.rollover_threshold_percent:
            return
        count = self.sessions.message_count(self.session_id)
        at_capacity = True
        messages = self.sessions.messages(self.session_id, limit=max(count, cfg.keep_last_messages))
        old_messages = messages[: max(0, len(messages) - cfg.keep_last_messages)]
        if not old_messages:
            return
        metadata = self.sessions.session_metadata(self.session_id)
        previous_summary = str(metadata.get("summary") or "")
        transcript = "\n".join(f"{msg.role}: {msg.content}" for msg in old_messages)
        prompt = (
            "Consolidate this session history for future context. Preserve user preferences, goals, decisions, "
            "open tasks, important facts, tool outcomes, and safety constraints. Remove repetition and transient chatter. "
            f"Keep it under about {cfg.summary_target_chars} characters."
        )
        if previous_summary:
            prompt += f"\n\nPrevious summary:\n{previous_summary}"
        prompt += f"\n\nMessages to consolidate:\n{transcript}"
        try:
            self._activity("summarizing context with LLM Brain")
            response = self.llm.complete(
                [
                    {"role": "system", "content": "You summarize conversation history for a local AI agent."},
                    {"role": "user", "content": prompt},
                ],
                tools=None,
            )
            summary = response["choices"][0]["message"].get("content") or ""
        except Exception:
            return
        consolidated_summary = summary[: cfg.summary_target_chars * 2]
        metadata["summary"] = consolidated_summary
        metadata["summary_message_count"] = metadata.get("summary_message_count", 0) + len(old_messages)
        metadata["summary_updated_at"] = datetime.now(UTC).isoformat()
        if at_capacity:
            previous_session_id = self.session_id
            recent_messages = messages[-cfg.keep_last_messages :] if cfg.keep_last_messages > 0 else []
            continuation_metadata = {
                "summary": consolidated_summary,
                "summary_message_count": metadata["summary_message_count"],
                "summary_updated_at": metadata["summary_updated_at"],
                "continued_from_session": previous_session_id,
            }
            continuation_id = self.sessions.create_session("Ulysses (continued)", continuation_metadata)
            for message in recent_messages:
                carried_metadata = dict(message.metadata)
                carried_metadata["carried_from_session"] = previous_session_id
                self.sessions.add_message(continuation_id, message.role, message.content, carried_metadata)
            metadata["continued_in_session"] = continuation_id
            self.sessions.update_session_metadata(previous_session_id, metadata)
            self.session_id = continuation_id
            self._activity("context summarized; continued in new session")
            return
        self.sessions.update_session_metadata(self.session_id, metadata)
        self.sessions.prune_messages_keep_last(self.session_id, cfg.keep_last_messages)

    def context_usage(self) -> dict:
        metadata = self.sessions.session_metadata(self.session_id)
        summary = str(metadata.get("summary") or "")
        recent = self.sessions.messages(self.session_id, limit=50)
        text_parts = [self._system_prompt(), summary]
        text_parts.extend(f"{msg.role}: {msg.content}" for msg in recent)
        chars = sum(len(part) for part in text_parts if part)
        estimated_tokens = max(1, chars // 4)
        window = max(1, int(self.config.context.context_window_tokens))
        percent = min(100, int((estimated_tokens / window) * 100))
        return {
            "estimated_tokens": estimated_tokens,
            "context_window_tokens": window,
            "percent": percent,
            "message_count": self.sessions.message_count(self.session_id),
            "summary_present": bool(summary),
        }

    def _system_prompt(self) -> str:
        prompt_parts = [
            f"You are {self.config.agent_name} v{self.config.agent_version}.",
            f"Live runtime fact: Godmode is {'on' if self.config.skills.command.godmode else 'off'}.",
            "Never infer configuration state from chat history; use the live runtime facts in this prompt.",
            f"Personality: {self.config.prompt.personality}",
            self.config.prompt.instructions,
        ]
        prompt_path = self.config.prompt.system_prompt_path
        if prompt_path and prompt_path.exists():
            prompt_parts.append(prompt_path.read_text(encoding="utf-8").strip())
        return "\n\n".join(part for part in prompt_parts if part)

    def collect_subagent_reports(self) -> str | None:
        with self._interaction_guard():
            if not self.subagents:
                return None
            reports = self.subagents.completed_reports()
            if not reports:
                return None
            report_context = "\n\n".join(
                f"Sub-agent: {item['agent']}\nJob: {item['id']}\nTask: {item['task']}\n"
                f"Status: {item['status']}\nReport:\n{item.get('response') or item.get('error') or 'No report supplied.'}"
                for item in reports
            )
            self._activity("reviewing sub-agent reports")
            response = self.llm.complete(
                [
                    {"role": "system", "content": self._system_prompt()},
                    {
                        "role": "system",
                        "content": (
                            "You are supervising completed background work. Give the user one concise progress update using the "
                            "reports below. State outcomes and material uncertainty, do not expose internal paths, and mention any "
                            "follow-up that Ulysses should perform. Do not claim unsupported results.\n\n"
                            f"{report_context}"
                        ),
                    },
                ],
                tools=None,
            )
            content = (response["choices"][0]["message"].get("content") or "").strip()
            if not content:
                return None
            job_ids = [item["id"] for item in reports]
            self._save_assistant_message(
                content,
                {"subagent_reports": job_ids},
                importance=0.4,
                source_prefix="subagent",
            )
            self.subagents.mark_reported(job_ids)
            return content

    def _direct_system_command(self, text: str) -> str | None:
        lowered = text.strip().lower()
        if lowered.startswith("run ") and lowered.endswith(" on system"):
            return text.strip()[4:-10].strip()
        if lowered.startswith("run ") and " on /" in lowered:
            command, path = text.strip()[4:].rsplit(" on ", 1)
            return f"{command.strip()} {path.strip()}"
        if lowered.startswith("run system command "):
            return text.strip()[19:].strip()
        return None

    @staticmethod
    def _direct_skill_creation(text: str) -> tuple[str, str] | None:
        request = text
        if "The user pasted a large text attachment." in text and "Preview:\n" in text:
            request = text.split("Preview:\n", 1)[1].split("\n\n[The remaining", 1)[0].strip()
        match = re.search(
            r"\b(?:create|build|make|generate|implement)\b"
            r"(?:\s+and\s+activate)?(?:\s+(?:a|an|the))?(?:\s+complete)?\s+skill"
            r"(?:\s+(?:named|called))?\s+[`'\"]?([A-Za-z][A-Za-z0-9_-]{1,63})",
            request,
            re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).lower().replace("-", "_"), request

    def _direct_system_command_plan(self, text: str) -> list[str] | None:
        lowered = text.strip().lower()
        if "status" in lowered and "disk" in lowered and "filesystem" in lowered:
            return ["df -h", "lsblk"]
        return None

    def _run_command_plan(self, user_request: str, commands: list[str], *, compact: bool = False) -> str:
        self._activity(f"planned {len(commands)} commands")
        outputs = []
        for index, command in enumerate(commands, start=1):
            self._activity(f"running command {index}/{len(commands)}: {command}")
            result = self._run_skill_result("system_command", {"command": command})
            if result.requires_confirmation:
                self._activity(f"waiting for confirmation: system_command")
                self.pending_tool = {
                    "name": "system_command",
                    "arguments": {"command": command},
                    "token": result.confirmation_token,
                }
                return result.confirmation_prompt or result.content
            self._record_tool_result(
                "system_command",
                result.content,
                {"skill": "system_command", "ok": result.ok, "data": result.data, "planned_command": command},
            )
            outputs.append(f"$ {command}\n{result.content}")
            if not result.ok:
                self._activity(f"command failed: {command}")
                outputs.append("Continuing with remaining planned checks where possible.")
                continue
            self._activity(f"command complete: {command}")
        combined_output = "\n\n".join(outputs)
        if compact:
            self._activity("saving answer")
            self._save_assistant_message(combined_output)
            return combined_output
        return self.answer_from_tool_result(user_request, combined_output)

    def _handle_tool_calls(
        self,
        messages: list[dict],
        message: dict,
        tool_calls: list[dict],
        tools: list[dict],
        compact_command_completion: bool = False,
    ) -> str:
        import json

        last_tool_content = ""
        last_tool_result: SkillResult | None = None
        last_tool_arguments: dict = {}
        compact_outputs: list[str] = []
        if compact_command_completion:
            tool_calls = tool_calls[:1]
        for _ in range(4):
            assistant_tool_calls = []
            tool_results = []
            for index, tool_call in enumerate(tool_calls, start=1):
                function = tool_call.get("function", {})
                name = function.get("name")
                tool_call_id = tool_call.get("id") or f"call_{name}_{index}"
                assistant_tool_calls.append(
                    {
                        "id": tool_call_id,
                        "type": tool_call.get("type", "function"),
                        "function": function,
                    }
                )
                try:
                    raw_arguments = function.get("arguments") or "{}"
                    if isinstance(raw_arguments, dict):
                        arguments = raw_arguments
                    else:
                        text = str(raw_arguments).strip()
                        if text.startswith("```") and text.endswith("```"):
                            text = text[3:-3].strip()
                            if text.lower().startswith("json"):
                                text = text[4:].lstrip()
                        arguments = json.loads(text)
                    if not isinstance(arguments, dict):
                        raise TypeError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, TypeError, ValueError):
                    self._activity(f"correcting tool arguments: {name or 'unknown'}")
                    tool_results.append(
                        (
                            tool_call_id,
                            name or "unknown",
                            (
                                "The tool arguments were invalid. Return a corrected call for this tool using exactly "
                                "one valid JSON object that conforms to its published parameter schema. Do not explain "
                                "the correction and do not repeat already completed tool calls."
                            ),
                        )
                    )
                    continue
                if len(tool_calls) == 1:
                    self._activity(f"running tool: {name}")
                else:
                    self._activity(f"running tool {index}/{len(tool_calls)}: {name}")
                result = self._run_skill_result(name, arguments)
                last_tool_result = result
                last_tool_arguments = arguments
                if result.requires_confirmation:
                    self._activity(f"waiting for confirmation: {name}")
                    user_request = next(
                        (str(item.get("content") or "") for item in reversed(messages) if item.get("role") == "user"),
                        "",
                    )
                    self.pending_tool = {
                        "name": name,
                        "arguments": arguments,
                        "token": result.confirmation_token,
                        "resume_after_confirmation": True,
                        "user_request": user_request,
                        "compact_command_completion": compact_command_completion,
                    }
                    return result.confirmation_prompt or result.content
                self._record_tool_result(name, result.content, {"skill": name, "ok": result.ok, "data": result.data})
                tool_results.append((tool_call_id, name, result.content))
                last_tool_content = result.content
                if compact_command_completion:
                    command = str(arguments.get("command") or "") if name == "system_command" else ""
                    label = f"$ {command}\n" if command else f"{name}:\n"
                    compact_outputs.append(f"{label}{result.content}".strip())
                if not result.ok:
                    self._activity(f"tool failed: {name}")
                    if name == "create_skill":
                        content = (
                            f"Skill creation failed after automated generation and repair attempts: {result.content}"
                        )
                        self._save_assistant_message(content)
                        return content
                    continue
                self._activity(f"tool complete: {name}")

            if compact_command_completion and compact_outputs:
                content = "\n\n".join(compact_outputs)
                self._activity("saving answer")
                self._save_assistant_message(content)
                return content

            messages.append(
                {"role": "assistant", "content": message.get("content") or "", "tool_calls": assistant_tool_calls}
            )
            for tool_call_id, name, content in tool_results:
                messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content})
            self._activity("composing final answer")
            self._activity("calling my LLM brain")
            tools = self._tool_schemas()
            response = self.llm.complete(messages, tools=tools)
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                if compact_command_completion and last_tool_result is not None:
                    content = self._confirmed_command_response(
                        str(last_tool_arguments.get("command") or ""),
                        last_tool_result,
                    )
                    self._save_assistant_message(content)
                    return content
                content = (message.get("content") or "").strip() or last_tool_content
                self._activity("saving answer")
                self._save_assistant_message(content)
                return content

        self._activity("composing final answer")
        response = self.llm.complete(messages, tools=None)
        content = (
            (response["choices"][0]["message"].get("content") or "").strip()
            or last_tool_content
            or "I could not complete the requested tool operation after bounded argument-correction attempts."
        )
        self._activity("saving answer")
        self._save_assistant_message(content)
        return content

    def _run_skill(self, name: str, arguments: dict, resume_after_confirmation: bool = False) -> str:
        self._activity(f"running tool: {name}")
        result = self._run_skill_result(name, arguments)
        if result.requires_confirmation:
            self._activity(f"waiting for confirmation: {name}")
            self.pending_tool = {
                "name": name,
                "arguments": arguments,
                "token": result.confirmation_token,
                "resume_after_confirmation": resume_after_confirmation,
            }
            return result.confirmation_prompt or result.content
        self._record_tool_result(name, result.content, {"skill": name, "ok": result.ok, "data": result.data})
        self._activity(f"tool complete: {name}")
        return result.content

    def _run_skill_result(self, name: str, arguments: dict, *, trusted_sudo_password: bool = False):
        if name == "system_command" and "sudo_password" in arguments and not trusted_sudo_password:
            arguments = dict(arguments)
            arguments.pop("sudo_password", None)
        if name == "system_command":
            self.sync_command_policy_from_config(force=False)
            command = str(arguments.get("command", ""))
            if self.config.skills.command.godmode and re.search(r"\bsudo\b", command):
                cached_password = self.cached_godmode_sudo_password()
                if cached_password:
                    arguments = dict(arguments)
                    arguments["sudo_password"] = cached_password
                    trusted_sudo_password = True
        previous_skill = self.active_skill
        self.active_skill = name
        self._activity(f"using skill {name}")
        try:
            try:
                skill = self.skills.get(name)
            except KeyError:
                safe_arguments = dict(arguments)
                safe_arguments.pop("sudo_password", None)
                return SkillResult(False, f"Tool `{name}` is not available.", {"tool": name, "arguments": safe_arguments})
            activity_label = skill.manifest.activity_label
            if name == "system_command":
                command = str(arguments.get("command", "")).strip()
                activity_label = f"running command: {command}" if command else activity_label
                if re.search(r"\b(?:install|installation)\b", command, re.I):
                    activity_label = f"installing: {command}"
            self._activity(activity_label)
            if name == "create_skill" and not arguments.get("generated_source"):
                prepared = self._prepare_complete_skill(arguments)
                if isinstance(prepared, SkillResult):
                    return prepared
                arguments.update(prepared)
            result = skill.run(arguments, {"session_id": self.session_id})
            if name == "create_skill" and result.ok:
                try:
                    loaded_name = self.skills.load_external_skill(Path(result.data["path"]))
                except Exception as exc:
                    return SkillResult(
                        False,
                        f"Skill files were created but live activation failed: {exc}",
                        result.data,
                    )
                result.content += f" Skill `{loaded_name}` is enabled and available now."
                result.data["loaded"] = loaded_name
            return result
        except Exception as exc:
            safe_arguments = dict(arguments)
            safe_arguments.pop("sudo_password", None)
            return SkillResult(
                False, f"Tool `{name}` failed: {exc}", {"tool": name, "arguments": safe_arguments, "error": str(exc)}
            )
        finally:
            self.active_skill = previous_skill

    def _prepare_complete_skill(self, arguments: dict) -> dict | SkillResult:
        name = str(arguments.get("name") or "").strip()
        request = str(arguments.get("request") or "").strip()
        if not name or not request:
            return SkillResult(False, "Complete skill creation requires a name and request.")
        try:
            search = self.skills.get("internet_search")
        except KeyError:
            return SkillResult(False, "Internet search must be enabled to create a complete skill.")
        self.active_skill = "internet_search"
        self._activity(f"researching skill: {name}")
        research_result = search.run(
            {"query": f"Python implementation documentation and security best practices for {request}", "limit": 5},
            {"session_id": self.session_id},
        )
        self.active_skill = "create_skill"
        if not research_result.ok:
            return SkillResult(False, "Skill research did not return usable results; creation was not attempted.")
        self._activity(f"building skill: {name}")
        try:
            spec = build_skill_spec(self.llm, name, request, research_result.content)
        except SkillBuildError as exc:
            return SkillResult(False, str(exc))
        return {
            "description": spec["description"],
            "arguments_schema": spec["arguments_schema"],
            "required_permissions": spec["required_permissions"],
            "risk_level": spec["risk_level"],
            "generated_source": spec["source"],
            "research": research_result.content,
            "enabled": True,
        }

    def pending_tool_requires_sudo_password(self) -> bool:
        if not self.pending_tool:
            return False
        try:
            skill = self.skills.get(self.pending_tool["name"])
            arguments = dict(self.pending_tool["arguments"])
            command = str(arguments.get("command", ""))
            if command.strip().startswith("sudo "):
                return True
            decision = skill.runner.policy.evaluate(arguments["command"])
            return bool(getattr(decision, "sudo_password_required", False))
        except Exception:
            return False

    def confirm_pending_tool(self, confirmation_text: str | None = None, extra_arguments: dict | None = None) -> str:
        with self._interaction_guard():
            return self._confirm_pending_tool_locked(confirmation_text, extra_arguments)

    def _confirm_pending_tool_locked(
        self,
        confirmation_text: str | None = None,
        extra_arguments: dict | None = None,
    ) -> str:
        if not self.pending_tool:
            return "No pending tool call."
        pending = self.pending_tool
        self.pending_tool = None
        arguments = dict(pending["arguments"])
        arguments["confirmed"] = True
        if extra_arguments:
            arguments.update(extra_arguments)
        if confirmation_text:
            arguments["confirmation_text"] = confirmation_text
        result = self._run_skill_result(
            pending["name"],
            arguments,
            trusted_sudo_password=bool(extra_arguments and "sudo_password" in extra_arguments),
        )
        sudo_password = str((extra_arguments or {}).get("sudo_password") or "")
        if sudo_password:
            result.content = result.content.replace(sudo_password, "<redacted>")
            result.data = self._redact_value(result.data, sudo_password)
            if self.config.skills.command.godmode and result.ok:
                try:
                    self.store_godmode_sudo_password(sudo_password)
                except Exception:
                    pass
        if result.requires_confirmation:
            self.pending_tool = pending
            return result.confirmation_prompt or result.content
        self._record_tool_result(
            pending["name"], result.content, {"skill": pending["name"], "ok": result.ok, "data": result.data}
        )
        if pending["name"] == "system_command" and pending.get("resume_after_confirmation"):
            if not result.ok:
                return self._recover_command_failure(
                    str(pending.get("user_request") or pending.get("arguments", {}).get("command") or "command"),
                    result,
                )
            command = str(pending.get("arguments", {}).get("command") or "")
            response = self._confirmed_command_response(command, result)
            self._save_assistant_message(response)
            return response
        if pending["name"] == "create_skill" and result.ok and pending.get("resume_after_confirmation"):
            self.skill_resume_name = str(result.data.get("loaded") or "") or None
        return result.content

    @staticmethod
    def _redact_value(value, secret: str):
        if isinstance(value, dict):
            return {key: AgentOrchestrator._redact_value(item, secret) for key, item in value.items()}
        if isinstance(value, list):
            return [AgentOrchestrator._redact_value(item, secret) for item in value]
        if isinstance(value, tuple):
            return tuple(AgentOrchestrator._redact_value(item, secret) for item in value)
        if isinstance(value, str):
            return value.replace(secret, "<redacted>")
        return value

    def cached_godmode_sudo_password(self) -> str | None:
        if not self.config.skills.command.godmode:
            return None
        try:
            return self.sudo_credentials.get()
        except Exception:
            return None

    def godmode_credential_readiness(self) -> tuple[bool, str]:
        return self.sudo_credentials.readiness()

    def store_godmode_sudo_password(self, password: str) -> None:
        if not self.config.skills.command.godmode:
            raise RuntimeError("Godmode is off; sudo credentials cannot be cached")
        self.sudo_credentials.set(password)

    def clear_godmode_sudo_password(self) -> None:
        self.sudo_credentials.clear()

    @staticmethod
    def _command_completion_message(result: SkillResult) -> str:
        if result.ok:
            return "Completed successfully"
        lines = [line.strip() for line in result.content.splitlines() if line.strip()]
        detail = lines[-1] if lines else "Command failed."
        if len(detail) > 240:
            detail = detail[:237].rstrip() + "..."
        return f"Failed: {detail}"

    @staticmethod
    def _confirmed_command_response(command: str, result: SkillResult) -> str:
        if not result.ok:
            return AgentOrchestrator._command_completion_message(result)
        if re.search(r"\b(?:install|installation)\b", command, re.I):
            return "Completed successfully"
        output = result.content.strip()
        return output or "Completed successfully"

    def _recover_command_failure(self, user_request: str, result: SkillResult) -> str:
        self._activity("diagnosing command failure")
        tools = self._tool_schemas()
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Operational task:\n{user_request}\n\nCommand failure:\n{result.content}\n\n"
                    "Diagnose the failure and use available tools to resolve it, then complete the original task. "
                    "Do not merely describe commands. Stop if resolution requires unavailable information or unsafe assumptions."
                ),
            },
        ]
        response = self.llm.complete(messages, tools=tools)
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            completion = self._command_completion_message(result)
            self._save_assistant_message(completion)
            return completion
        return self._handle_tool_calls(
            messages,
            message,
            tool_calls,
            tools,
            compact_command_completion=True,
        )

    def consume_skill_resume(self) -> str | None:
        name = self.skill_resume_name
        self.skill_resume_name = None
        return name

    def cancel_pending_tool(self) -> bool:
        if not self.pending_tool:
            return False
        self.pending_tool = None
        return True

    def answer_from_tool_result(self, user_request: str, tool_result: str) -> str:
        self._activity("composing final answer")
        response = self.llm.complete(
            [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{user_request}\n\n"
                        f"Tool output:\n{tool_result}\n\n"
                        "Answer the user's request using the tool output. If they asked for a report, "
                        "format the answer as customer-delivery Markdown. For assessed systems, produce a confidential vulnerability "
                        "assessment report with document control, Executive Summary, Management Summary, Technical Summary, risk profile, "
                        "scope, methodology, severity definitions, severity-ranked findings table, detailed findings, technical proof of "
                        "concept, business and technical impact, detailed remediation, verification steps, evidence appendix, assumptions "
                        "and limitations, and confidentiality notice. Exclude missing-tool messages, allowlist denials, installation output, "
                        "command failures, confirmation prompts, internal paths, and operator diagnostics. Rank findings Critical, High, "
                        "Medium, Low, Informational. "
                        "Do not invent vulnerabilities that are not supported by the tool output."
                    ),
                },
            ],
            tools=None,
        )
        content = (response["choices"][0]["message"].get("content") or "").strip()
        if not content:
            content = tool_result
        self._save_assistant_message(content)
        return content

    def summarize_for_voice(self, text: str, force: bool = False) -> str:
        # Derive speech only from the answer currently displayed. A second LLM
        # call can leak unrelated conversational context into the spoken output.
        from sirina_agent.audio.sirina_io import (
            _clean_spoken_text,
            _clip_sentence,
            _remove_unsafe_speech_content,
            summarize_for_speech,
        )

        cfg = self.config.sirina
        safe_text = _clean_spoken_text(_remove_unsafe_speech_content(text))
        if not cfg.voice_summary_enabled or (not force and len(safe_text) <= cfg.voice_summary_after_chars):
            return summarize_for_speech(text, force=force)
        try:
            from sirina_agent.audio.local_summarizer import get_local_voice_summarizer

            self._activity("loading local voice summarization model")
            summarizer = get_local_voice_summarizer(
                str(cfg.voice_summary_model_path),
                cfg.voice_summary_max_input_tokens,
                cfg.voice_summary_max_chunks,
                cfg.voice_summary_max_output_tokens,
            )
            device = str(getattr(summarizer, "device", "local"))
            self._activity(f"summarizing response locally ({device})")
            summary = summarizer.summarize(_voice_summary_input(safe_text, cfg.voice_summary_max_input_tokens))
            if summary:
                self._activity("preparing summarized voice playback")
                return _clip_sentence("Summary: " + summary, cfg.voice_summary_max_chars)
        except Exception:
            self._activity("local voice summary unavailable; using safe fallback")
            pass
        return summarize_for_speech(text, force=force)

    def startup_greeting(self) -> str:
        metadata = self.sessions.session_metadata(self.session_id)
        previous = str(metadata.get("startup_greeting") or "")
        moment = datetime.now().strftime("%A %H:%M")
        messages = [
            {
                "role": "system",
                "content": (
                    "Speak as Ulysses, a calm senior defensive security operator. Produce one fresh, intelligent "
                    "startup greeting of 12 to 24 words. Convey exceptional technical judgment, confidence, and "
                    "command of security operations through precise language rather than explicit boasting. Make "
                    "it natural and security-minded, not a system status report. Do not use Markdown, clichés, or "
                    "claim that checks were performed. Return only the greeting."
                ),
            },
            {
                "role": "user",
                "content": f"Current local moment: {moment}. Previous greeting to avoid: {previous or 'none'}.",
            },
        ]
        try:
            complete_brief = getattr(self.llm, "complete_brief", None)
            if callable(complete_brief):
                response = complete_brief(
                    messages,
                    max_tokens=64,
                    timeout_seconds=self.config.llm.startup_greeting_timeout_seconds,
                )
            else:
                response = self.llm.complete(messages, tools=None)
            greeting = (response["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            greeting = ""
        fallbacks = [
            "Ready when you are. We will separate signal from noise and turn evidence into decisive security action.",
            "Welcome back. Precise scope, disciplined analysis, and defensible evidence will guide every move.",
            "Ulysses is ready. We will challenge assumptions, expose weak controls, and resolve risk with precision.",
            "Let us begin. Complex security problems become manageable when evidence, judgment, and execution align.",
        ]
        if not greeting or greeting == previous:
            choices = [item for item in fallbacks if item != previous] or fallbacks
            greeting = random.choice(choices)
        metadata["startup_greeting"] = greeting
        self.sessions.update_session_metadata(self.session_id, metadata)
        return greeting

    def erase_user_data(self) -> None:
        self.sessions.erase_all()
        self.memory.erase_all()
        self.session_id = self.sessions.create_session("Ulysses")

    def set_autonomous(self, enabled: bool) -> None:
        metadata = self.sessions.session_metadata(self.session_id)
        metadata["autonomous_enabled"] = enabled
        metadata["autonomous_updated_at"] = datetime.now(UTC).isoformat()
        self.sessions.update_session_metadata(self.session_id, metadata)

    def autonomous_enabled(self) -> bool:
        return bool(self.sessions.session_metadata(self.session_id).get("autonomous_enabled", False))

    def autonomous_check(self, force: bool = False) -> str | None:
        if not self.autonomous_enabled():
            return None
        if getattr(self.config.autonomous, "defense_checks_enabled", True):
            return self._autonomous_defense_check(force)
        return self._autonomous_reflection_check(force)

    def _autonomous_defense_check(self, force: bool = False) -> str | None:
        cfg = self.config.autonomous
        metadata = self.sessions.session_metadata(self.session_id)
        now = datetime.now(UTC)
        last_check_at = metadata.get("last_autonomous_defense_check_at")
        previous_score = int(metadata.get("last_autonomous_defense_score") or 0)
        interval = self._autonomous_defense_interval(previous_score)
        if last_check_at and not force:
            try:
                elapsed = (now - datetime.fromisoformat(last_check_at)).total_seconds()
                if elapsed < interval:
                    return None
            except ValueError:
                pass

        self._activity("autonomous defense: planning checks")
        assessment = self.defense.run(self._run_autonomous_defense_command)
        self.defense.plan_actions(
            assessment,
            bool(getattr(cfg, "auto_block_attackers", True)),
            bool(getattr(cfg, "install_missing_security_apps", True)),
        )
        self._execute_autonomous_defense_actions(assessment)
        metadata["last_autonomous_defense_check_at"] = assessment.checked_at
        metadata["last_autonomous_defense_score"] = assessment.score
        metadata["last_autonomous_defense_severity"] = assessment.highest_severity
        metadata["next_autonomous_defense_interval_seconds"] = self._autonomous_defense_interval(assessment.score)
        self.sessions.update_session_metadata(self.session_id, metadata)

        if not force and assessment.score < cfg.defense_report_min_score:
            return None

        prompt = (
            "Autonomous mode is enabled. You are defending the local host you run on. "
            "Use the collected evidence to produce a concise defensive assessment. "
            "Log what was checked, rank any concerns by severity, and give immediate defensive next steps. "
            "Do not invent compromise indicators unsupported by the evidence. Keep it practical.\n\n"
            f"{assessment.prompt_text()}"
        )
        try:
            self._activity("autonomous defense: calling my LLM brain")
            response = self.llm.complete(
                [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                tools=None,
            )
            note = (response["choices"][0]["message"].get("content") or "").strip()
        except Exception as exc:
            note = (
                f"Autonomous defense check completed, but LLM Brain failed: {exc}\n\n{assessment.prompt_text()[:4000]}"
            )
        if not note:
            note = assessment.prompt_text()
        self.sessions.add_message(
            self.session_id,
            "assistant",
            note,
            {"autonomous": True, "defense": True, "score": assessment.score, "severity": assessment.highest_severity},
        )
        self._save_memory_soft(
            note,
            source=f"autonomous-defense:{self.session_id}",
            importance=0.8 if assessment.score else 0.5,
            metadata={
                "autonomous": True,
                "defense": True,
                "score": assessment.score,
                "severity": assessment.highest_severity,
            },
        )
        metadata = self.sessions.session_metadata(self.session_id)
        metadata["last_autonomous_report_at"] = now.isoformat()
        self.sessions.update_session_metadata(self.session_id, metadata)
        return note

    def _execute_autonomous_defense_actions(self, assessment) -> None:
        if not assessment.planned_actions:
            return
        if not self.config.skills.command.godmode:
            for action in assessment.planned_actions:
                content = f"planned but not executed because godmode is off: {action.command}\nReason: {action.reason}"
                assessment.action_outputs.append(
                    {"name": action.name, "command": action.command, "ok": False, "content": content}
                )
                self.sessions.add_message(
                    self.session_id,
                    "tool",
                    content,
                    {
                        "skill": "system_command",
                        "ok": False,
                        "autonomous": True,
                        "defense_action": action.name,
                        "command": action.command,
                        "planned_only": True,
                    },
                )
            return
        for action in assessment.planned_actions:
            self._activity(f"autonomous defense action: {action.name}")
            result = self._run_skill_result("system_command", {"command": action.command})
            content = result.confirmation_prompt or result.content
            ok = bool(result.ok and not result.requires_confirmation)
            assessment.action_outputs.append(
                {"name": action.name, "command": action.command, "ok": ok, "content": content}
            )
            self.sessions.add_message(
                self.session_id,
                "tool",
                content,
                {
                    "skill": "system_command",
                    "ok": ok,
                    "data": result.data,
                    "autonomous": True,
                    "defense_action": action.name,
                    "command": action.command,
                    "requires_confirmation": result.requires_confirmation,
                },
            )

    def _run_autonomous_defense_command(self, check: DefenseCheck) -> tuple[bool, str]:
        self._activity(f"autonomous defense: {check.name}")
        try:
            result = self._run_skill_result("system_command", {"command": check.command})
        except Exception as exc:
            content = f"check failed before execution: {exc}"
            self.sessions.add_message(
                self.session_id,
                "tool",
                content,
                {
                    "skill": "system_command",
                    "ok": False,
                    "autonomous": True,
                    "defense_check": check.name,
                    "command": check.command,
                },
            )
            return False, content
        if result.requires_confirmation:
            content = result.confirmation_prompt or result.content
            self.sessions.add_message(
                self.session_id,
                "tool",
                content,
                {
                    "skill": "system_command",
                    "ok": False,
                    "autonomous": True,
                    "defense_check": check.name,
                    "command": check.command,
                    "requires_confirmation": True,
                },
            )
            return False, content
        self.sessions.add_message(
            self.session_id,
            "tool",
            result.content,
            {
                "skill": "system_command",
                "ok": result.ok,
                "data": result.data,
                "autonomous": True,
                "defense_check": check.name,
                "command": check.command,
            },
        )
        return result.ok, result.content

    def _autonomous_defense_interval(self, score: int) -> float:
        cfg = self.config.autonomous
        if score >= 6:
            return cfg.defense_critical_interval_seconds
        if score >= 2:
            return cfg.defense_elevated_interval_seconds
        return cfg.check_interval_seconds

    def _autonomous_reflection_check(self, force: bool = False) -> str | None:
        cfg = self.config.autonomous
        metadata = self.sessions.session_metadata(self.session_id)
        now = datetime.now(UTC)
        last_at = metadata.get("last_autonomous_report_at")
        if last_at and not force:
            try:
                elapsed = (now - datetime.fromisoformat(last_at)).total_seconds()
                if elapsed < cfg.min_seconds_between_reports:
                    return None
            except ValueError:
                pass
        if not force and random.random() > cfg.report_probability:
            return None

        recent = self.sessions.messages(self.session_id, limit=cfg.max_recent_messages)
        recent_text = "\n".join(f"{msg.role}: {msg.content}" for msg in recent)
        summary = str(metadata.get("summary") or "")
        prompt = (
            f"You are {self.config.agent_name}. Autonomous mode is enabled. "
            "Check the current mission/session like a thoughtful assistant with a little human warmth. "
            "If there is a useful observation, risk, next step, reminder, or recovery note, write a short report to the user. "
            "Do not pretend to have senses or emotions. Do not be needy. Keep it under 90 words.\n\n"
            f"Consolidated context:\n{summary or '(none)'}\n\nRecent session:\n{recent_text or '(none)'}"
        )
        try:
            response = self.llm.complete(
                [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                tools=None,
            )
            note = (response["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            return None
        if not note:
            return None
        self.sessions.add_message(self.session_id, "assistant", note, {"autonomous": True})
        self._save_memory_soft(
            note, source=f"autonomous:{self.session_id}", importance=0.65, metadata={"autonomous": True}
        )
        metadata = self.sessions.session_metadata(self.session_id)
        metadata["last_autonomous_report_at"] = now.isoformat()
        self.sessions.update_session_metadata(self.session_id, metadata)
        return note


def _voice_summary_input(text: str, max_input_tokens: int) -> str:
    budget = max(1000, int(max_input_tokens) * 4)
    if len(text) <= budget:
        return text
    head = text[: budget // 2].rstrip()
    tail = text[-(budget - len(head)) :].lstrip()
    return f"{head}\n\n[...]\n\n{tail}"
