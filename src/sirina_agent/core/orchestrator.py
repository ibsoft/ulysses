from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import random
from typing import Callable


class AgentOrchestrator:
    def __init__(self, config, sessions, memory, llm, skills, config_path: str | Path | None = None) -> None:
        self.config = config
        self.config_path = Path(config_path).expanduser() if config_path else Path("config/ulysses.yaml")
        self.sessions = sessions
        self.memory = memory
        self.llm = llm
        self.skills = skills
        self.pending_tool: dict | None = None
        self.activity_callback: Callable[[str], None] | None = None
        existing = sessions.list_sessions()
        self.session_id = existing[0]["id"] if existing else sessions.create_session("Ulysses")

    def set_activity_callback(self, callback: Callable[[str], None] | None) -> None:
        self.activity_callback = callback

    def _activity(self, message: str) -> None:
        if self.activity_callback:
            self.activity_callback(message)

    def handle_text(self, text: str) -> str:
        self._activity("checking request")
        direct_tool = self._direct_system_command(text)
        if direct_tool:
            self.sessions.add_message(self.session_id, "user", text)
            return self._run_skill("system_command", {"command": direct_tool})
        self._activity("saving user message")
        self.sessions.add_message(self.session_id, "user", text)
        self.memory.add(text, source=f"session:{self.session_id}", importance=0.4, metadata={"role": "user"})
        self._activity("checking context")
        self._maybe_consolidate_session()
        self._activity("searching memory")
        memories = self.memory.search(text, top_k=self.config.memory.top_k) if self.config.privacy.retrieve_memory else []
        context = "\n".join(f"- {item.text} ({item.source}, {item.created_at})" for item in memories)
        self._activity("preparing prompt")
        system = self._system_prompt()
        messages = [{"role": "system", "content": system}]
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
        tools = [
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
        self._activity("calling my LLM brain")
        response = self.llm.complete(messages, tools=tools)
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            return self._handle_tool_call(messages, message, tool_calls[0])
        self._activity("saving answer")
        content = message.get("content") or ""
        self.sessions.add_message(self.session_id, "assistant", content)
        self.memory.add(content, source=f"session:{self.session_id}", importance=0.3, metadata={"role": "assistant"})
        return content

    def _maybe_consolidate_session(self) -> None:
        cfg = self.config.context
        if not cfg.auto_consolidate:
            return
        count = self.sessions.message_count(self.session_id)
        chars = self.sessions.total_message_chars(self.session_id)
        usage = self.context_usage()
        if count <= cfg.max_messages and chars <= cfg.max_chars and usage["percent"] < 100:
            return
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
        metadata["summary"] = summary[: cfg.summary_target_chars * 2]
        metadata["summary_message_count"] = metadata.get("summary_message_count", 0) + len(old_messages)
        metadata["summary_updated_at"] = datetime.now(UTC).isoformat()
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
            f"Personality: {self.config.prompt.personality}",
            self.config.prompt.instructions,
        ]
        prompt_path = self.config.prompt.system_prompt_path
        if prompt_path and prompt_path.exists():
            prompt_parts.append(prompt_path.read_text(encoding="utf-8").strip())
        return "\n\n".join(part for part in prompt_parts if part)

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

    def _handle_tool_call(self, messages: list[dict], message: dict, tool_call: dict) -> str:
        import json

        function = tool_call.get("function", {})
        name = function.get("name")
        arguments = json.loads(function.get("arguments") or "{}")
        self._activity(f"running tool: {name}")
        result = self._run_skill_result(name, arguments)
        if result.requires_confirmation:
            self._activity(f"waiting for confirmation: {name}")
            self.pending_tool = {"name": name, "arguments": arguments, "token": result.confirmation_token}
            return result.confirmation_prompt or result.content
        self.sessions.add_message(self.session_id, "tool", result.content, {"skill": name, "ok": result.ok, "data": result.data})
        if not result.ok:
            self._activity(f"tool failed: {name}")
            return result.content

        self._activity(f"tool complete: {name}")
        tool_call_id = tool_call.get("id") or f"call_{name}"
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": tool_call.get("type", "function"),
                        "function": function,
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": result.content})
        self._activity("composing final answer")
        response = self.llm.complete(messages, tools=None)
        content = (response["choices"][0]["message"].get("content") or "").strip()
        if not content:
            content = result.content
        self._activity("saving answer")
        self.sessions.add_message(self.session_id, "assistant", content)
        self.memory.add(content, source=f"session:{self.session_id}", importance=0.3, metadata={"role": "assistant"})
        return content

    def _run_skill(self, name: str, arguments: dict) -> str:
        self._activity(f"running tool: {name}")
        result = self._run_skill_result(name, arguments)
        if result.requires_confirmation:
            self._activity(f"waiting for confirmation: {name}")
            self.pending_tool = {"name": name, "arguments": arguments, "token": result.confirmation_token}
            return result.confirmation_prompt or result.content
        self.sessions.add_message(self.session_id, "tool", result.content, {"skill": name, "ok": result.ok, "data": result.data})
        self._activity(f"tool complete: {name}")
        return result.content

    def _run_skill_result(self, name: str, arguments: dict):
        skill = self.skills.get(name)
        return skill.run(arguments, {"session_id": self.session_id})

    def pending_tool_requires_sudo_password(self) -> bool:
        if not self.pending_tool:
            return False
        try:
            skill = self.skills.get(self.pending_tool["name"])
            arguments = dict(self.pending_tool["arguments"])
            decision = skill.runner.policy.evaluate(arguments["command"])
            return bool(getattr(decision, "sudo_password_required", False))
        except Exception:
            return False

    def confirm_pending_tool(self, confirmation_text: str | None = None, extra_arguments: dict | None = None) -> str:
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
        result = self.skills.get(pending["name"]).run(arguments, {"session_id": self.session_id})
        if result.requires_confirmation:
            self.pending_tool = pending
            return result.confirmation_prompt or result.content
        self.sessions.add_message(
            self.session_id,
            "tool",
            result.content,
            {"skill": pending["name"], "ok": result.ok, "data": result.data},
        )
        return result.content

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
        self.memory.add(note, source=f"autonomous:{self.session_id}", importance=0.65, metadata={"autonomous": True})
        metadata = self.sessions.session_metadata(self.session_id)
        metadata["last_autonomous_report_at"] = now.isoformat()
        self.sessions.update_session_metadata(self.session_id, metadata)
        return note
