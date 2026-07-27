import logging

from sirina_agent.security.commands import CommandPolicy, CommandRunner
from sirina_agent.skills.builtin.create_skill import CreateSkillSkill
from sirina_agent.skills.builtin.duckduckgo_search import normalize_results
from sirina_agent.skills.builtin.system_command import SystemCommandSkill
from sirina_agent.skills.registry import SkillRegistry


def test_duckduckgo_parsing():
    results = normalize_results([{"title": "A", "href": "https://a", "body": "snippet", "date": "2026-01-01"}])
    assert results == [{"title": "A", "url": "https://a", "snippet": "snippet", "timestamp": "2026-01-01"}]


def test_command_confirmation(tmp_path):
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], require_confirmation=True)
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))
    proposal = skill.run({"command": "pwd"}, {})
    assert proposal.requires_confirmation
    assert proposal.confirmation_token
    executed = skill.run({"command": "pwd", "confirmed": True}, {})
    assert executed.ok


def test_sudo_command_requires_token_then_password(tmp_path):
    policy = CommandPolicy(["pwd"], ["rm"], tmp_path, ["PATH"], require_confirmation=True)
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))
    proposal = skill.run({"command": "sudo id"}, {})
    assert proposal.requires_confirmation
    token = proposal.confirmation_token
    needs_password = skill.run({"command": "sudo id", "confirmed": True, "confirmation_text": token}, {})
    assert needs_password.requires_confirmation
    assert needs_password.data["sudo_password_required"]


def test_create_skill_scaffolds_reviewable_skill(tmp_path):
    skill = CreateSkillSkill(tmp_path / "skills")
    proposal = skill.run({"name": "Weather Skill", "request": "Create a weather lookup skill"}, {})
    assert proposal.requires_confirmation
    token = proposal.confirmation_token
    result = skill.run(
        {
            "name": "Weather Skill",
            "request": "Create a weather lookup skill",
            "confirmed": True,
            "confirmation_text": token,
        },
        {},
    )
    assert result.ok
    target = tmp_path / "skills" / "weather_skill"
    assert (target / "manifest.yaml").exists()
    assert (target / "skill.py").exists()
    assert "SkillImpl" in (target / "skill.py").read_text(encoding="utf-8")


def test_registry_loads_external_skill(tmp_path):
    creator = CreateSkillSkill(tmp_path / "skills")
    proposal = creator.run({"name": "Echo", "request": "Echo input"}, {})
    creator.run({"name": "Echo", "request": "Echo input", "confirmed": True, "confirmation_text": proposal.confirmation_token}, {})
    registry = SkillRegistry()
    loaded = registry.load_external(tmp_path / "skills")
    assert loaded == ["echo"]
    assert registry.get("echo").manifest.name == "echo"
