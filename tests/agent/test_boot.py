from sirina_agent.config.models import UlyssesConfig
from sirina_agent.core.orchestrator import AgentOrchestrator
from sirina_agent.llm.providers import MockProvider
from sirina_agent.memory.store import FaissMemoryStore, LocalHashEmbeddingProvider
from sirina_agent.sessions.store import SessionStore
from sirina_agent.skills.base import SkillManifest, SkillResult
from sirina_agent.skills.registry import SkillRegistry
from sirina_agent.tui.boot import spoken_startup_brief, startup_brief


class StaticSkill:
    manifest = SkillManifest(
        name="system_command",
        description="Run command",
        arguments_schema={"type": "object", "properties": {}},
        required_permissions=[],
        risk_level="low",
    )

    def run(self, arguments, context):
        return SkillResult(True, "ok")


def test_startup_brief_reports_core_systems(tmp_path):
    cfg = UlyssesConfig()
    cfg.llm.provider = "mock"
    cfg.prompt.personality = "Kali Linux vulnerability assessor"
    cfg.prompt.instructions = "You are a vulnerability assessor."
    cfg.prompt.system_prompt_path = None
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticSkill())
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), registry)

    brief = startup_brief(agent)

    assert "VAPT / PenTest / Vulnerability Assessment console initialized." in brief
    assert "LLM Brain: up" in brief
    assert "Memory: up" in brief
    assert "Skills: up" in brief
    assert "Prompt: up" in brief
    assert "All systems ready and operational." in brief


def test_spoken_startup_brief_is_short_status_only(tmp_path):
    cfg = UlyssesConfig()
    cfg.llm.provider = "mock"
    cfg.prompt.system_prompt_path = None
    sessions = SessionStore(tmp_path / "s.sqlite3")
    memory = FaissMemoryStore(tmp_path / "m.faiss", tmp_path / "m.jsonl", LocalHashEmbeddingProvider(64))
    registry = SkillRegistry()
    registry.register(StaticSkill())
    agent = AgentOrchestrator(cfg, sessions, memory, MockProvider(), registry)

    spoken = spoken_startup_brief(agent)

    assert "LLM Brain up." in spoken
    assert "Memory up." in spoken
    assert "Skills up." in spoken
    assert "All systems ready and operational." in spoken
    assert "gpt-" not in spoken
    assert "memories indexed" not in spoken
