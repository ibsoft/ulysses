from __future__ import annotations

import threading
import time

import pytest

from sirina_agent.config.models import SubagentConfig, UlyssesConfig
from sirina_agent.core.orchestrator import AgentOrchestrator
from sirina_agent.memory.store import FaissMemoryStore, LocalHashEmbeddingProvider
from sirina_agent.sessions.store import SessionStore
from sirina_agent.skills.base import SkillManifest, SkillResult
from sirina_agent.skills.builtin.subagents import DeleteSubagentSkill, UpdateSubagentSkill
from sirina_agent.skills.registry import SkillRegistry
from sirina_agent.subagents import SubagentCapabilityError, SubagentManager, SubagentSkillBroker


class StaticProvider:
    def complete(self, messages, tools=None):
        return {"choices": [{"message": {"role": "assistant", "content": "Completed analysis for Ulysses."}}]}


class WorkspaceProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "write_1",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_write",
                                        "arguments": '{"path":"notes/result.md","content":"evidence"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Done; wrote notes/result.md."}}]}


class DelegatedSkill:
    manifest = SkillManifest(
        name="evidence_lookup",
        description="Look up bounded evidence.",
        arguments_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        required_permissions=["network"],
        risk_level="medium",
    )

    def __init__(self, result: SkillResult | None = None):
        self.calls = []
        self.result = result or SkillResult(True, "confirmed evidence")

    def run(self, arguments, context):
        self.calls.append((arguments, context))
        return self.result


class DelegatedSkillProvider:
    def __init__(self):
        self.calls = 0
        self.tools = []
        self.messages = []

    def complete(self, messages, tools=None):
        self.calls += 1
        self.tools = tools or []
        self.messages = messages
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "lookup_1",
                                    "type": "function",
                                    "function": {
                                        "name": "evidence_lookup",
                                        "arguments": '{"query":"private search value"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "Evidence review complete."}}]}


class BlockingDelegatedSkill(DelegatedSkill):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, arguments, context):
        self.started.set()
        self.release.wait(timeout=2)
        return super().run(arguments, context)


def wait_for_job(manager, job_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next(item for item in manager.list_jobs() if item["id"] == job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    raise AssertionError("sub-agent job did not finish")


def test_persistent_subagent_creation_and_background_completion(tmp_path):
    manager = SubagentManager(SubagentConfig(root_dir=tmp_path / "agents"), StaticProvider)

    created = manager.create("tls_specialist", "Review TLS evidence", "Analyze TLS configuration rigorously.")
    job = manager.delegate("tls_specialist", "Review the supplied certificate evidence.", "Target is authorized.")
    completed = wait_for_job(manager, job["id"])

    assert created["name"] == "tls_specialist"
    assert completed["status"] == "completed"
    assert (tmp_path / "agents/tls_specialist/prompt.md").is_file()
    assert (tmp_path / f"agents/tls_specialist/tasks/{job['id']}/response.md").read_text().startswith("Completed")
    assert manager.completed_reports()[0]["id"] == job["id"]
    manager.mark_reported([job["id"]])
    assert manager.completed_reports() == []

    status = manager.status_detail()
    assert "Delegated jobs" in status
    assert "[completed] tls_specialist" in status
    assert "Review the supplied certificate evidence." in status


def test_subagent_status_prioritizes_active_jobs_and_truncates_tasks(tmp_path):
    manager = SubagentManager(SubagentConfig(root_dir=tmp_path / "agents"), StaticProvider)
    manager.create("status_agent", "Status testing", "Report status.")
    job_dir = tmp_path / "agents/status_agent/tasks/job_20260730121212_1234abcd"
    job_dir.mkdir(parents=True)
    manager._write_json(
        job_dir / "job.json",
        {
            "id": job_dir.name,
            "agent": "status_agent",
            "task": "This deliberately long delegated task should be shortened in the narrow sidebar",
            "status": "running",
            "created_at": "2026-07-30T12:12:12+00:00",
            "updated_at": "2026-07-30T12:12:12+00:00",
            "reported": False,
        },
    )

    status = manager.status_detail(max_jobs=1, max_task_chars=24)

    assert "1 agents / 1 active" in status
    assert "[running] status_agent" in status
    assert "This deliberately lon..." in status


def test_subagent_workspace_tools_are_confined(tmp_path):
    provider = WorkspaceProvider()
    manager = SubagentManager(SubagentConfig(root_dir=tmp_path / "agents"), lambda: provider)
    manager.create("writer_agent", "Create bounded files", "Write useful notes.")

    job = manager.delegate("writer_agent", "Create the evidence note.")
    completed = wait_for_job(manager, job["id"])

    assert completed["status"] == "completed"
    assert (tmp_path / "agents/writer_agent/workspace/notes/result.md").read_text() == "evidence"
    with pytest.raises(ValueError, match="escapes"):
        manager._workspace_tool(
            tmp_path / "agents/writer_agent/workspace", "workspace_write", {"path": "../../escape", "content": "no"}
        )


def test_subagent_name_validation_and_persistence(tmp_path):
    config = SubagentConfig(root_dir=tmp_path / "agents")
    manager = SubagentManager(config, StaticProvider)
    with pytest.raises(ValueError):
        manager.create("../outside", "bad", "bad")
    manager.create("persistent_agent", "Persistent role", "Persistent prompt")

    reloaded = SubagentManager(config, StaticProvider)
    assert reloaded.list_agents()[0]["name"] == "persistent_agent"


def test_subagent_executes_only_persisted_and_job_granted_skills(tmp_path):
    registry = SkillRegistry()
    delegated = DelegatedSkill()
    registry.register(delegated)
    provider = DelegatedSkillProvider()
    config = SubagentConfig(
        root_dir=tmp_path / "agents",
        delegable_skills=["evidence_lookup"],
    )
    manager = SubagentManager(config, lambda: provider, registry)
    created = manager.create(
        "research_agent",
        "Research evidence",
        "Use delegated evidence tools.",
        ["evidence_lookup"],
    )

    job = manager.delegate("research_agent", "Find evidence.", skills=["evidence_lookup"])
    completed = wait_for_job(manager, job["id"])
    audit_path = tmp_path / f"agents/research_agent/tasks/{job['id']}/skill-calls.jsonl"
    audit = audit_path.read_text(encoding="utf-8")

    assert created["allowed_skills"] == ["evidence_lookup"]
    assert completed["granted_skills"] == ["evidence_lookup"]
    assert completed["skill_calls"] == 1
    assert delegated.calls == [
        (
            {"query": "private search value"},
            {"actor": "subagent", "agent": "research_agent", "job_id": job["id"]},
        )
    ]
    assert any(tool["function"]["name"] == "evidence_lookup" for tool in provider.tools)
    assert "argument_keys" not in audit
    assert "private search value" not in audit
    assert '"skill": "evidence_lookup"' in audit
    assert "Skills: evidence_lookup" in manager.status_detail()

    reloaded = SubagentManager(config, StaticProvider, registry)
    assert reloaded.list_agents()[0]["allowed_skills"] == ["evidence_lookup"]
    assert reloaded.list_jobs()[0]["granted_skills"] == ["evidence_lookup"]


def test_subagent_job_cannot_expand_persistent_skill_grants(tmp_path):
    registry = SkillRegistry()
    registry.register(DelegatedSkill())
    manager = SubagentManager(
        SubagentConfig(root_dir=tmp_path / "agents", delegable_skills=["evidence_lookup"]),
        StaticProvider,
        registry,
    )
    manager.create("workspace_agent", "Workspace only", "Use workspace tools.")

    with pytest.raises(ValueError, match="outside the sub-agent policy"):
        manager.delegate("workspace_agent", "Escalate capabilities.", skills=["evidence_lookup"])


def test_subagent_update_changes_future_grants_without_changing_identity(tmp_path):
    registry = SkillRegistry()
    registry.register(DelegatedSkill())
    manager = SubagentManager(
        SubagentConfig(root_dir=tmp_path / "agents", delegable_skills=["evidence_lookup"]),
        StaticProvider,
        registry,
    )
    manager.create("existing_agent", "Original purpose", "Original prompt.")
    skill = UpdateSubagentSkill(manager)

    result = skill.run(
        {
            "name": "existing_agent",
            "purpose": "Evidence research",
            "allowed_skills": ["evidence_lookup"],
        },
        {},
    )
    job = manager.delegate("existing_agent", "Use the updated policy.")

    assert result.ok
    assert result.data["purpose"] == "Evidence research"
    assert result.data["allowed_skills"] == ["evidence_lookup"]
    assert job["granted_skills"] == ["evidence_lookup"]


def test_subagent_status_shows_active_delegated_skill(tmp_path):
    registry = SkillRegistry()
    delegated = BlockingDelegatedSkill()
    registry.register(delegated)
    provider = DelegatedSkillProvider()
    manager = SubagentManager(
        SubagentConfig(root_dir=tmp_path / "agents", delegable_skills=["evidence_lookup"]),
        lambda: provider,
        registry,
    )
    manager.create("visible_agent", "Show active tools", "Use the granted skill.", ["evidence_lookup"])

    job = manager.delegate("visible_agent", "Run a visible lookup.")
    assert delegated.started.wait(timeout=1)

    assert "Using: evidence_lookup" in manager.status_detail()

    delegated.release.set()
    completed = wait_for_job(manager, job["id"])
    assert completed["active_skill"] is None


def test_subagent_broker_rejects_supervisor_and_confirmation_skills(tmp_path):
    registry = SkillRegistry()
    confirmation = DelegatedSkill(
        SkillResult(
            False,
            "approval needed",
            requires_confirmation=True,
            confirmation_token="secret-token",
        )
    )
    registry.register(confirmation)
    config = SubagentConfig(
        root_dir=tmp_path / "agents",
        delegable_skills=["evidence_lookup", "system_command"],
    )
    broker = SubagentSkillBroker(config, registry)

    with pytest.raises(SubagentCapabilityError, match="supervisor-only"):
        broker.validate_grants(["system_command"])
    with pytest.raises(SubagentCapabilityError, match="cannot be self-approved"):
        broker.execute(
            "evidence_lookup",
            {},
            agent="research_agent",
            job_id="job_20260730121212_1234abcd",
            granted_skills=["evidence_lookup"],
        )


def test_subagent_confirmation_request_is_audited_without_token(tmp_path):
    registry = SkillRegistry()
    registry.register(
        DelegatedSkill(
            SkillResult(
                False,
                "approval needed",
                requires_confirmation=True,
                confirmation_token="secret-token",
            )
        )
    )
    provider = DelegatedSkillProvider()
    manager = SubagentManager(
        SubagentConfig(root_dir=tmp_path / "agents", delegable_skills=["evidence_lookup"]),
        lambda: provider,
        registry,
    )
    manager.create("approval_agent", "Test approval boundary", "Never self-approve.", ["evidence_lookup"])

    job = manager.delegate("approval_agent", "Request bounded evidence.")
    completed = wait_for_job(manager, job["id"])
    audit = (tmp_path / f"agents/approval_agent/tasks/{job['id']}/skill-calls.jsonl").read_text(encoding="utf-8")

    assert completed["status"] == "completed"
    assert completed["skill_calls"] == 1
    assert "cannot be self-approved" in audit
    assert "secret-token" not in audit


def test_subagent_broker_caps_skill_output(tmp_path):
    registry = SkillRegistry()
    registry.register(DelegatedSkill(SkillResult(True, "x" * 2_000)))
    config = SubagentConfig(
        root_dir=tmp_path / "agents",
        delegable_skills=["evidence_lookup"],
        max_skill_output_chars=1_000,
    )
    broker = SubagentSkillBroker(config, registry)

    content, metadata = broker.execute(
        "evidence_lookup",
        {},
        agent="research_agent",
        job_id="job_20260730121212_1234abcd",
        granted_skills=["evidence_lookup"],
    )

    assert content.endswith("[Skill output truncated by sub-agent policy.]")
    assert metadata["truncated"] is True


def test_subagent_delete_requires_confirmation_and_removes_workspace(tmp_path):
    manager = SubagentManager(SubagentConfig(root_dir=tmp_path / "agents"), StaticProvider)
    manager.create("retired_agent", "Temporary role", "Do temporary work.")
    skill = DeleteSubagentSkill(manager)

    pending = skill.run({"name": "retired_agent"}, {})
    assert pending.requires_confirmation
    result = skill.run(
        {"name": "retired_agent", "confirmed": True, "confirmation_text": pending.confirmation_token},
        {},
    )

    assert result.ok
    assert manager.list_agents() == []


class ReportManager:
    def __init__(self):
        self.marked = []

    def completed_reports(self):
        return [
            {
                "id": "job_20260730120000_1234abcd",
                "agent": "tls_specialist",
                "task": "Review TLS evidence",
                "status": "completed",
                "response": "TLS 1.0 remains enabled with high confidence.",
            }
        ]

    def mark_reported(self, job_ids):
        self.marked.extend(job_ids)


class ReportAwareProvider:
    def __init__(self):
        self.messages = []

    def complete(self, messages, tools=None):
        self.messages = messages
        return {"choices": [{"message": {"role": "assistant", "content": "The TLS review is complete."}}]}


def test_orchestrator_injects_and_acknowledges_completed_subagent_reports(tmp_path):
    config = UlyssesConfig()
    config.prompt.system_prompt_path = None
    sessions = SessionStore(tmp_path / "sessions.sqlite3")
    memory = FaissMemoryStore(
        tmp_path / "memory.faiss",
        tmp_path / "memory.jsonl",
        LocalHashEmbeddingProvider(64),
    )
    reports = ReportManager()
    provider = ReportAwareProvider()
    orchestrator = AgentOrchestrator(
        config,
        sessions,
        memory,
        provider,
        SkillRegistry(),
        subagents=reports,
    )

    answer = orchestrator.handle_text("Any completed specialist work?")

    assert answer == "The TLS review is complete."
    assert any("TLS 1.0 remains enabled" in message["content"] for message in provider.messages)
    assert reports.marked == ["job_20260730120000_1234abcd"]


def test_orchestrator_collects_background_report_without_user_turn(tmp_path):
    config = UlyssesConfig()
    config.prompt.system_prompt_path = None
    sessions = SessionStore(tmp_path / "sessions.sqlite3")
    memory = FaissMemoryStore(
        tmp_path / "memory.faiss",
        tmp_path / "memory.jsonl",
        LocalHashEmbeddingProvider(64),
    )
    reports = ReportManager()
    provider = ReportAwareProvider()
    orchestrator = AgentOrchestrator(
        config,
        sessions,
        memory,
        provider,
        SkillRegistry(),
        subagents=reports,
    )

    note = orchestrator.collect_subagent_reports()

    assert note == "The TLS review is complete."
    assert reports.marked == ["job_20260730120000_1234abcd"]
    saved = sessions.messages(orchestrator.session_id)
    assert saved[-1].metadata["subagent_reports"] == ["job_20260730120000_1234abcd"]
