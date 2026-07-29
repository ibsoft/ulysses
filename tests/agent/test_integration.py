from sirina_agent.config.models import UlyssesConfig
import sirina_agent.core.orchestrator as orchestrator_module
from sirina_agent.core.orchestrator import AgentOrchestrator
from sirina_agent.llm.providers import MockProvider
from sirina_agent.memory.store import FaissMemoryStore, LocalHashEmbeddingProvider
from sirina_agent.security.commands import CommandPolicy, CommandRunner
from sirina_agent.sessions.store import SessionStore
from sirina_agent.skills.registry import SkillRegistry
from sirina_agent.skills.builtin.system_command import SystemCommandSkill
from sirina_agent.skills.base import SkillManifest, SkillResult
import logging
import time


def test_orchestrator_with_mocked_components(tmp_path):
    cfg = UlyssesConfig()
    cfg.memory.top_k = 2
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), SkillRegistry())
    answer = agent.handle_text("hello")
    assert "Ulysses heard" in answer
    assert len(sessions.messages(agent.session_id)) == 2


class HangingMemoryStore:
    def __init__(self):
        self.items = []

    def add(self, *args, **kwargs):
        time.sleep(2)

    def search(self, *args, **kwargs):
        return []

    def erase_all(self):
        self.items.clear()


def test_orchestrator_does_not_block_on_slow_memory_save(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "MEMORY_SAVE_TIMEOUT_SECONDS", 0.01)
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    agent = AgentOrchestrator(cfg, sessions, HangingMemoryStore(), MockProvider(), SkillRegistry())

    started = time.monotonic()
    answer = agent.handle_text("hello")

    assert "Ulysses heard" in answer
    assert time.monotonic() - started < 1
    assert len(sessions.messages(agent.session_id)) == 2


class ToolCallProvider:
    def complete(self, messages, tools=None):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {"function": {"name": "system_command", "arguments": '{"command": "pwd"}'}}
                        ],
                    }
                }
            ]
        }


class RecordingProvider:
    def __init__(self):
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(messages)
        if "Consolidate this session history" in messages[-1]["content"]:
            return {"choices": [{"message": {"role": "assistant", "content": "summary of older context"}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "final answer"}}]}


class SearchThenAnswerProvider:
    def __init__(self):
        self.calls = 0
        self.messages = []

    def complete(self, messages, tools=None):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_search",
                                    "type": "function",
                                    "function": {"name": "internet_search", "arguments": '{"query": "latest security news"}'},
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "A sourced security-news summary."}}]}


class MultiToolThenAnswerProvider:
    def __init__(self):
        self.calls = 0
        self.messages = []

    def complete(self, messages, tools=None):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {"name": "first_tool", "arguments": '{"value": "disk"}'},
                                },
                                {
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {"name": "second_tool", "arguments": '{"value": "filesystem"}'},
                                },
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Combined summary."}}]}


class FailedToolThenAnswerProvider:
    def __init__(self):
        self.calls = 0
        self.messages = []

    def complete(self, messages, tools=None):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_failed",
                                    "type": "function",
                                    "function": {"name": "missing_tool", "arguments": '{"value": "scan"}'},
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Continued with available evidence."}}]}


class StaticSearchSkill:
    manifest = SkillManifest(
        name="internet_search",
        description="Search",
        arguments_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        required_permissions=[],
        risk_level="medium",
    )

    def run(self, arguments, context):
        return SkillResult(True, "1. Security item\nhttps://example.test\nSummary", {"query": arguments["query"]})


class StaticNamedSkill:
    def __init__(self, name, content):
        self.manifest = SkillManifest(
            name=name,
            description=name,
            arguments_schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            required_permissions=[],
            risk_level="low",
        )
        self.content = content

    def run(self, arguments, context):
        return SkillResult(True, f"{self.content}: {arguments['value']}", {"value": arguments["value"]})


class StaticCommandSkill:
    manifest = SkillManifest(
        name="system_command",
        description="Run command",
        arguments_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        required_permissions=[],
        risk_level="low",
    )

    def run(self, arguments, context):
        return SkillResult(True, f"output for {arguments['command']}", {"command": arguments["command"]})


class DefensiveCommandSkill:
    manifest = StaticCommandSkill.manifest

    def run(self, arguments, context):
        command = arguments["command"]
        if command == "journalctl -p warning -n 40 --no-pager":
            return SkillResult(True, "sshd: Failed password for root from 10.0.0.7 port 1 ssh2\n" * 8, {})
        if command.startswith("which "):
            return SkillResult(False, "", {})
        return SkillResult(True, "ok", {})


def test_orchestrator_pending_tool_confirmation(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], require_confirmation=True)
    registry = SkillRegistry()
    registry.register(SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000)))
    agent = AgentOrchestrator(cfg, sessions, memory, ToolCallProvider(), registry)
    prompt = agent.handle_text("run pwd")
    assert "Confirmation token" in prompt
    result = agent.confirm_pending_tool()
    assert str(tmp_path) in result


def test_pending_sudo_tool_requests_secure_password_dialog_even_in_godmode(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], require_confirmation=True, godmode=True)
    registry = SkillRegistry()
    registry.register(SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000)))
    agent = AgentOrchestrator(cfg, sessions, memory, ToolCallProvider(), registry)
    agent._run_skill("system_command", {"command": "sudo nmap -sS example.com"})

    assert agent.pending_tool_requires_sudo_password()


def test_model_supplied_sudo_password_is_discarded(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], godmode=True)
    registry = SkillRegistry()
    registry.register(SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000)))
    agent = AgentOrchestrator(cfg, sessions, memory, ToolCallProvider(), registry)

    result = agent._run_skill_result(
        "system_command",
        {"command": "sudo id", "sudo_password": "must-not-be-used-or-stored"},
    )

    assert result.requires_confirmation
    assert result.data["sudo_password_required"]
    assert "must-not-be-used-or-stored" not in repr(result)


def test_orchestrator_completes_answer_after_tool_call(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticSearchSkill())
    provider = SearchThenAnswerProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, registry)

    answer = agent.handle_text("what is the latest security news?")

    assert answer == "A sourced security-news summary."
    assert provider.calls == 2
    assert provider.messages[-1][-1]["role"] == "tool"
    assert provider.messages[-1][-1]["content"].startswith("1. Security item")
    assert sessions.messages(agent.session_id)[-1].content == answer


def test_orchestrator_runs_multiple_tool_calls_before_final_answer(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticNamedSkill("first_tool", "first result"))
    registry.register(StaticNamedSkill("second_tool", "second result"))
    provider = MultiToolThenAnswerProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, registry)

    answer = agent.handle_text("compare disk and filesystem details")

    assert answer == "Combined summary."
    assert provider.calls == 2
    final_messages = provider.messages[-1]
    tool_messages = [message for message in final_messages if message["role"] == "tool"]
    assert [message["content"] for message in tool_messages] == ["first result: disk", "second result: filesystem"]
    assert sessions.messages(agent.session_id)[-1].content == answer


def test_orchestrator_continues_after_nonfatal_tool_failure(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    provider = FailedToolThenAnswerProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, SkillRegistry())

    answer = agent.handle_text("continue even if a tool fails")

    assert answer == "Continued with available evidence."
    assert provider.calls == 2
    tool_messages = [message for message in sessions.messages(agent.session_id) if message.role == "tool"]
    assert tool_messages[0].metadata["ok"] is False
    assert "missing_tool" in tool_messages[0].content


def test_orchestrator_plans_disk_and_filesystem_commands_then_summarizes(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticCommandSkill())
    provider = RecordingProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, registry)

    answer = agent.handle_text("show me status of disks and filesystems")

    assert answer == "final answer"
    tool_messages = [message for message in sessions.messages(agent.session_id) if message.role == "tool"]
    assert [message.metadata["planned_command"] for message in tool_messages] == ["df -h", "lsblk"]
    final_prompt = provider.calls[-1][-1]["content"]
    assert "$ df -h" in final_prompt
    assert "$ lsblk" in final_prompt


def test_orchestrator_plans_nmap_os_version_then_summarizes(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticCommandSkill())
    provider = RecordingProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, registry)

    answer = agent.handle_text("run nmap to 192.168.7.33 and report OS version")

    assert answer == "final answer"
    tool_messages = [message for message in sessions.messages(agent.session_id) if message.role == "tool"]
    assert [message.metadata["planned_command"] for message in tool_messages] == ["nmap -O 192.168.7.33"]
    assert "$ nmap -O 192.168.7.33" in provider.calls[-1][-1]["content"]


def test_report_from_tool_result_requests_assessment_report_structure(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    provider = RecordingProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, SkillRegistry())

    agent.answer_from_tool_result("make me a report for assessed system", "nmap output")

    prompt = provider.calls[-1][-1]["content"]
    assert "severity-ranked findings table" in prompt
    assert "technical proof of concept" in prompt
    assert "detailed remediation" in prompt
    assert "Do not invent vulnerabilities" in prompt
    assert "Executive Summary" in prompt
    assert "Management Summary" in prompt
    assert "Technical Summary" in prompt
    assert "Exclude missing-tool messages" in prompt


def test_orchestrator_reports_activity_during_tool_call(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticSearchSkill())
    provider = SearchThenAnswerProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, registry)
    activities = []
    agent.set_activity_callback(activities.append)

    agent.handle_text("what is the latest security news?")

    assert "calling my LLM brain" in activities
    assert "running tool: internet_search" in activities
    assert "composing final answer" in activities


def test_direct_system_command_phrase(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    policy = CommandPolicy(["ls"], [], tmp_path, ["PATH"], require_confirmation=True)
    registry = SkillRegistry()
    registry.register(SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000)))
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), registry)
    prompt = agent.handle_text("run ls on system")
    assert "Confirmation token" in prompt
    assert agent.pending_tool


def test_direct_system_command_path_phrase_and_tool_history(tmp_path):
    cfg = UlyssesConfig()
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    policy = CommandPolicy(["ls"], [], tmp_path, ["PATH"], require_confirmation=True)
    registry = SkillRegistry()
    registry.register(SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000)))
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), registry)
    sessions.add_message(agent.session_id, "tool", "old tool output")
    prompt = agent.handle_text("run ls on /")
    assert "Run `ls /`?" in prompt
    assert agent.pending_tool


def test_system_prompt_uses_config_and_prompt_file(tmp_path):
    cfg = UlyssesConfig()
    cfg.prompt.personality = "Dry and precise."
    cfg.prompt.instructions = "Always mention safety."
    cfg.prompt.system_prompt_path = tmp_path / "system.md"
    cfg.prompt.system_prompt_path.write_text("Long prompt block.", encoding="utf-8")
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), SkillRegistry())
    prompt = agent._system_prompt()
    assert "Dry and precise." in prompt
    assert "Always mention safety." in prompt
    assert "Long prompt block." in prompt


def test_session_auto_consolidates_large_context(tmp_path):
    cfg = UlyssesConfig()
    cfg.context.max_messages = 3
    cfg.context.keep_last_messages = 2
    cfg.context.max_chars = 10_000
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    provider = RecordingProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, SkillRegistry())
    for idx in range(4):
        sessions.add_message(agent.session_id, "user", f"older {idx}")
    answer = agent.handle_text("new message")
    assert answer == "final answer"
    metadata = sessions.session_metadata(agent.session_id)
    assert metadata["summary"] == "summary of older context"
    assert sessions.message_count(agent.session_id) <= cfg.context.keep_last_messages + 1
    final_call = provider.calls[-1]
    assert any("Consolidated session context" in message["content"] for message in final_call)


def test_context_usage_and_window_triggered_consolidation(tmp_path):
    cfg = UlyssesConfig()
    cfg.context.max_messages = 100
    cfg.context.max_chars = 1_000_000
    cfg.context.context_window_tokens = 20
    cfg.context.keep_last_messages = 1
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    provider = RecordingProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, SkillRegistry())
    sessions.add_message(agent.session_id, "user", "x" * 500)
    usage = agent.context_usage()
    assert usage["percent"] == 100
    agent.handle_text("trigger")
    assert sessions.session_metadata(agent.session_id)["summary"] == "summary of older context"


def test_autonomous_check_persists_report_to_session_and_memory(tmp_path):
    cfg = UlyssesConfig()
    cfg.autonomous.min_seconds_between_reports = 0
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    provider = RecordingProvider()
    agent = AgentOrchestrator(cfg, sessions, memory, provider, SkillRegistry())
    assert agent.autonomous_check(force=True) is None
    agent.set_autonomous(True)
    note = agent.autonomous_check(force=True)
    assert note == "final answer"
    assert sessions.messages(agent.session_id)[-1].metadata["autonomous"]
    assert sessions.messages(agent.session_id)[-1].metadata["defense"]
    assert memory.items[-1].source == f"autonomous-defense:{agent.session_id}"


def test_autonomous_defense_logs_planned_actions_without_godmode(tmp_path):
    cfg = UlyssesConfig()
    cfg.autonomous.defense_report_min_score = 0
    cfg.skills.command.godmode = False
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(DefensiveCommandSkill())
    agent = AgentOrchestrator(cfg, sessions, memory, RecordingProvider(), registry)
    agent.set_autonomous(True)

    agent.autonomous_check(force=True)

    planned = [message for message in sessions.messages(agent.session_id) if message.metadata.get("planned_only")]
    assert any("sudo ufw deny from 10.0.0.7" in message.content for message in planned)
    assert any("sudo apt-get install -y ufw fail2ban auditd" in message.content for message in planned)


def test_orchestrator_syncs_system_command_policy_from_config(tmp_path):
    cfg = UlyssesConfig()
    cfg.skills.command.allowed_commands = ["pwd"]
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    policy = CommandPolicy(
        cfg.skills.command.allowed_commands,
        cfg.skills.command.denied_commands,
        tmp_path,
        cfg.skills.command.env_allowlist,
        bypass_confirmation_for_allowed_commands=True,
    )
    registry = SkillRegistry()
    registry.register(SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000)))
    agent = AgentOrchestrator(cfg, sessions, memory, RecordingProvider(), registry)
    skill = agent.skills.get("system_command")

    assert not skill.runner.policy.evaluate("nikto -host https://example.com -nointeractive").allowed

    agent.config.skills.command.allowed_commands.extend(["nikto", "nuclei", "sslscan", "katana"])
    agent.config.skills.command.timeout_seconds = 123
    agent.config.skills.command.max_output_chars = 4567
    assert agent.sync_command_policy_from_config()

    decision = skill.runner.policy.evaluate("nikto -host https://example.com -nointeractive")
    assert decision.allowed
    assert skill.runner.policy.allowed == set(agent.config.skills.command.allowed_commands)
    assert skill.runner.policy.evaluate("nuclei -u https://example.com").allowed
    assert skill.runner.policy.evaluate("sslscan example.com:443").allowed
    assert skill.runner.policy.evaluate("katana -u https://example.com").allowed
    assert skill.runner.timeout_seconds == 123
    assert skill.runner.max_output_chars == 4567


def test_explicit_skill_creation_is_directly_routed_from_attachment_preview():
    text = (
        "The user pasted a large text attachment.\n"
        "Saved file: /tmp/paste.txt\n"
        "Characters: 200\n"
        "Preview:\n"
        "Create and activate a complete skill named network_reachability.\n\n"
        "Check authorized network 192.168.1.0/24.\n\n"
        "[The remaining 0 characters are saved in the file above.]"
    )

    routed = AgentOrchestrator._direct_skill_creation(text)

    assert routed == (
        "network_reachability",
        "Create and activate a complete skill named network_reachability.\n\n"
        "Check authorized network 192.168.1.0/24.",
    )


def test_handle_text_bypasses_model_for_explicit_skill_creation():
    calls = []

    class RecordingSessions:
        def add_message(self, *args):
            calls.append(("session", args))

    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.activity_callback = None
    orchestrator.sessions = RecordingSessions()
    orchestrator.session_id = "test-session"
    orchestrator._run_skill = lambda name, arguments, resume_after_confirmation=False: calls.append(
        (name, arguments, resume_after_confirmation)
    ) or "skill routed"

    result = orchestrator.handle_text("Create and activate a complete skill named network_reachability. Check my LAN.")

    assert result == "skill routed"
    assert calls[-1] == (
        "create_skill",
        {
            "name": "network_reachability",
            "request": "Create and activate a complete skill named network_reachability. Check my LAN.",
        },
        True,
    )
