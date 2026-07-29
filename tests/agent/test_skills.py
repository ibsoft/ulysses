import logging

from sirina_agent.security.commands import CommandPolicy, CommandRunner
from sirina_agent.skills.builtin.create_skill import CreateSkillSkill
import sirina_agent.skills.builtin.duckduckgo_search as search_module
from sirina_agent.skills.builtin.duckduckgo_search import DuckDuckGoSearchSkill, normalize_results
from sirina_agent.skills.builtin.system_command import SystemCommandSkill
from sirina_agent.skills.registry import SkillRegistry


def test_duckduckgo_parsing():
    results = normalize_results([{"title": "A", "href": "https://a", "body": "snippet", "date": "2026-01-01"}])
    assert results == [{"title": "A", "url": "https://a", "snippet": "snippet", "timestamp": "2026-01-01"}]


def test_duckduckgo_empty_results_are_not_blank(monkeypatch):
    def no_results(query, limit):
        return []

    monkeypatch.setattr(search_module, "_search_with_ddgs", no_results)
    monkeypatch.setattr(search_module, "_search_with_duckduckgo_search", no_results)
    monkeypatch.setattr(search_module, "_search_duckduckgo_html", no_results)

    result = DuckDuckGoSearchSkill().run({"query": "nothing here"}, {})

    assert not result.ok
    assert result.content == "No search results found for: nothing here"


def test_duckduckgo_falls_back_to_next_provider(monkeypatch):
    def broken(query, limit):
        raise RuntimeError("primary failed")

    def fallback(query, limit):
        return [{"title": "Security News", "href": "https://example.test", "body": "Patch now."}]

    monkeypatch.setattr(search_module, "_search_with_ddgs", broken)
    monkeypatch.setattr(search_module, "_search_with_duckduckgo_search", fallback)
    monkeypatch.setattr(search_module, "_search_duckduckgo_html", broken)

    result = DuckDuckGoSearchSkill().run({"query": "latest security news"}, {})

    assert result.ok
    assert "Security News" in result.content
    assert "_search_with_ddgs('latest security news'): primary failed" in result.data["errors"]


def test_command_confirmation(tmp_path):
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], require_confirmation=True)
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))
    proposal = skill.run({"command": "pwd"}, {})
    assert proposal.requires_confirmation
    assert proposal.confirmation_token
    executed = skill.run({"command": "pwd", "confirmed": True}, {})
    assert executed.ok


def test_allowed_command_can_bypass_confirmation(tmp_path):
    policy = CommandPolicy(
        ["pwd"],
        [],
        tmp_path,
        ["PATH"],
        require_confirmation=True,
        bypass_confirmation_for_allowed_commands=True,
    )
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))

    result = skill.run({"command": "pwd"}, {})

    assert result.ok
    assert not result.requires_confirmation


def test_godmode_bypasses_allowlist_and_confirmation(tmp_path):
    policy = CommandPolicy(
        [],
        [],
        tmp_path,
        ["PATH"],
        require_confirmation=True,
        require_typed_confirmation_for_high_risk=True,
        godmode=True,
    )
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))

    result = skill.run({"command": "pwd"}, {})

    assert result.ok
    assert not result.requires_confirmation


def test_godmode_allows_shell_control_operators(tmp_path):
    policy = CommandPolicy(
        [],
        [],
        tmp_path,
        ["PATH"],
        require_confirmation=True,
        require_typed_confirmation_for_high_risk=True,
        godmode=True,
    )
    decision = policy.evaluate("printf hello | wc -c")

    assert decision.allowed
    assert decision.argv == ["bash", "-lc", "printf hello | wc -c"]
    assert not decision.requires_confirmation


def test_sudo_command_requires_token_then_password(tmp_path):
    policy = CommandPolicy(["pwd"], ["rm"], tmp_path, ["PATH"], require_confirmation=True)
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))
    proposal = skill.run({"command": "sudo id"}, {})
    assert proposal.requires_confirmation
    token = proposal.confirmation_token
    needs_password = skill.run({"command": "sudo id", "confirmed": True, "confirmation_text": token}, {})
    assert needs_password.requires_confirmation
    assert needs_password.data["sudo_password_required"]


def test_godmode_sudo_skips_token_but_requires_password(tmp_path):
    policy = CommandPolicy(["pwd"], ["rm"], tmp_path, ["PATH"], require_confirmation=True, godmode=True)
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))

    proposal = skill.run({"command": "sudo id"}, {})

    assert proposal.requires_confirmation
    assert "sudo password" in proposal.content.lower() or "sudo password" in (proposal.confirmation_prompt or "").lower()
    assert proposal.confirmation_token


def test_sudo_password_is_not_exposed_to_llm_tool_schema(tmp_path):
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"])
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))

    assert "sudo_password" not in skill.manifest.arguments_schema["properties"]


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
