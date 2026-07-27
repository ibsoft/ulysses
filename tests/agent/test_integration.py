from sirina_agent.config.models import UlyssesConfig
from sirina_agent.core.orchestrator import AgentOrchestrator
from sirina_agent.llm.providers import MockProvider
from sirina_agent.memory.store import FaissMemoryStore, LocalHashEmbeddingProvider
from sirina_agent.security.commands import CommandPolicy, CommandRunner
from sirina_agent.sessions.store import SessionStore
from sirina_agent.skills.registry import SkillRegistry
from sirina_agent.skills.builtin.system_command import SystemCommandSkill
from sirina_agent.skills.base import SkillManifest, SkillResult
import logging


def test_orchestrator_with_mocked_components(tmp_path):
    cfg = UlyssesConfig()
    cfg.memory.top_k = 2
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), SkillRegistry())
    answer = agent.handle_text("hello")
    assert "Ulysses heard" in answer
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
    assert memory.items[-1].source == f"autonomous:{agent.session_id}"
