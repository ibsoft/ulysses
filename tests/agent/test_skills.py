import json
import logging
from types import SimpleNamespace

from sirina_agent.core.orchestrator import AgentOrchestrator
from sirina_agent.security.commands import CommandPolicy, CommandRunner
from sirina_agent.skills.base import SkillManifest, SkillResult
from sirina_agent.skills.builder import SkillBuildError, build_skill_spec, validate_generated_skill_source
from sirina_agent.skills.builtin.create_skill import CreateSkillSkill
import sirina_agent.skills.builtin.duckduckgo_search as search_module
from sirina_agent.skills.builtin.duckduckgo_search import DuckDuckGoSearchSkill, normalize_results
from sirina_agent.skills.builtin.system_command import SystemCommandSkill
from sirina_agent.skills.registry import SkillRegistry


def test_internet_search_schema_is_openai_function_compatible():
    schema = DuckDuckGoSearchSkill.manifest.arguments_schema

    assert schema["type"] == "object"
    assert not ({"oneOf", "anyOf", "allOf", "enum", "const", "not"} & schema.keys())


def test_duckduckgo_parsing():
    results = normalize_results([{"title": "A", "href": "https://a", "body": "snippet", "date": "2026-01-01"}])
    assert results == [{"title": "A", "url": "https://a", "snippet": "snippet", "timestamp": "2026-01-01"}]


def test_search_result_normalization_decodes_redirects_and_drops_relative_urls():
    results = normalize_results(
        [
            {"title": "invalid", "href": "/relative"},
            {
                "title": "valid",
                "href": "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Ffinding",
            },
        ]
    )

    assert results == [
        {"title": "valid", "url": "https://example.test/finding", "snippet": "", "timestamp": ""}
    ]


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


def test_internet_search_groups_multiple_queries(monkeypatch):
    def search(query, limit):
        return [{"title": query, "href": f"https://example.test/{len(query)}", "body": "Source result."}]

    monkeypatch.setattr(search_module, "_search_with_ddgs", search)
    monkeypatch.setattr(search_module, "_search_with_duckduckgo_search", search)
    monkeypatch.setattr(search_module, "_search_duckduckgo_html", search)

    result = DuckDuckGoSearchSkill().run(
        {"queries": ["security example.com", "security example.org"], "limit": 3},
        {},
    )

    assert result.ok
    assert "Search: security example.com" in result.content
    assert "Search: security example.org" in result.content
    assert result.data["queries"] == ["security example.com", "security example.org"]
    assert {item["query"] for item in result.data["results"]} == {
        "security example.com",
        "security example.org",
    }


def test_internet_search_expands_domain_discovery_queries(monkeypatch):
    attempted = []

    def search(query, limit):
        attempted.append(query)
        return []

    monkeypatch.setattr(search_module, "_search_with_ddgs", search)
    monkeypatch.setattr(search_module, "_search_with_duckduckgo_search", search)
    monkeypatch.setattr(search_module, "_search_duckduckgo_html", search)

    DuckDuckGoSearchSkill().run(
        {"query": "find subdomains and IP addresses of example.com and example.org"},
        {},
    )

    assert "site:example.com -www" in attempted
    assert 'site:crt.sh "example.com"' in attempted
    assert "site:example.org -www" in attempted
    assert 'site:crt.sh "example.org"' in attempted


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
    assert (
        "sudo password" in proposal.content.lower() or "sudo password" in (proposal.confirmation_prompt or "").lower()
    )
    assert proposal.confirmation_token


def test_godmode_compound_sudo_requires_secure_password(tmp_path):
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], godmode=True)
    skill = SystemCommandSkill(CommandRunner(policy, logging.getLogger("test"), 2, 1000))

    proposal = skill.run({"command": "sudo apt update && sudo apt upgrade -y"}, {})

    assert proposal.requires_confirmation
    assert proposal.data["sudo_password_required"]


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
    creator.run(
        {"name": "Echo", "request": "Echo input", "confirmed": True, "confirmation_text": proposal.confirmation_token},
        {},
    )
    registry = SkillRegistry()
    loaded = registry.load_external(tmp_path / "skills")
    assert loaded == ["echo"]
    assert registry.get("echo").manifest.name == "echo"
    assert not registry.get("echo").manifest.enabled


def test_registry_reports_external_skill_load_failure(tmp_path):
    skill_dir = tmp_path / "skills" / "network_reachability"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        """name: network_reachability
description: Check local network reachability.
arguments_schema:
  type: object
  properties: {}
required_permissions: []
risk_level: low
enabled: true
""",
        encoding="utf-8",
    )
    (skill_dir / "skill.py").write_text(
        """from sirina_agent.skills.base import SkillManifest

class SkillImpl:
    manifest = SkillManifest(
        name="network_reachability",
        description="Broken generated skill",
        unsupported_schema={},
        required_permissions=[],
        risk_level="low",
    )
""",
        encoding="utf-8",
    )
    registry = SkillRegistry()

    assert registry.load_external(tmp_path / "skills") == []
    failures = registry.load_failures()

    assert len(failures) == 1
    name, manifest, error = failures[0]
    assert name == "network_reachability"
    assert manifest is not None
    assert manifest.enabled
    assert "unsupported_schema" in error


def test_legacy_generated_skill_contract_is_supported():
    manifest = SkillManifest(
        name="legacy",
        description="Legacy generated skill",
        parameters_schema={"type": "object", "properties": {"target": {"type": "string"}}},
        required_permissions=["network"],
        risk_level="low",
    )
    successful = SkillResult(success=True, result={"summary": "Reachable", "target": "gateway"})
    failed = SkillResult(success=False, error="Unreachable")

    assert "target" in manifest.arguments_schema["properties"]
    assert successful.ok
    assert successful.content == "Reachable"
    assert successful.data["target"] == "gateway"
    assert not failed.ok
    assert failed.content == "Unreachable"
    assert failed.data["error"] == "Unreachable"


class StaticResearchSkill:
    manifest = SkillManifest(
        name="internet_search",
        description="Search implementation documentation",
        arguments_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        required_permissions=["network"],
        risk_level="medium",
    )

    def __init__(self):
        self.queries = []

    def run(self, arguments, context):
        self.queries.append(arguments["query"])
        return SkillResult(True, "Python documentation: validate input and return structured results.")


class GeneratedSkillProvider:
    def __init__(self):
        self.messages = []

    def complete(self, messages, tools=None):
        self.messages.append(messages)
        prompt = messages[-1]["content"]
        source = """from __future__ import annotations
from typing import Any
from sirina_agent.skills.base import SkillManifest, SkillResult

class SkillImpl:
    manifest = SkillManifest(
        name="echo_complete",
        description="Echo validated text.",
        arguments_schema={"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]},
        required_permissions=[],
        risk_level="low",
        enabled=True,
    )

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        value = str(arguments.get("input", "")).strip()
        if not value:
            return SkillResult(False, "Input is required.")
        return SkillResult(True, f"Echo: {value}", {"input": value})
"""
        if "Design metadata" in prompt:
            content = json.dumps(
                {
                    "description": "Echo validated text.",
                    "arguments_schema": {
                        "type": "object",
                        "properties": {"input": {"type": "string"}},
                        "required": ["input"],
                    },
                    "required_permissions": [],
                    "risk_level": "low",
                }
            )
        else:
            content = f"```python\n{source}\n```"
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class InvalidThenValidSkillProvider(GeneratedSkillProvider):
    def complete(self, messages, tools=None):
        response = super().complete(messages, tools)
        if len(self.messages) == 2:
            response["choices"][0]["message"]["content"] = response["choices"][0]["message"]["content"].replace(
                "arguments_schema=", "parameters_schema=", 1
            )
        return response


class InvalidSyntaxThenValidSkillProvider(GeneratedSkillProvider):
    def complete(self, messages, tools=None):
        response = super().complete(messages, tools)
        if len(self.messages) == 2:
            response["choices"][0]["message"]["content"] = response["choices"][0]["message"]["content"].replace(
                'value = str(arguments.get("input", "")).strip()',
                'value = str(arguments.get("input", "").strip()',
                1,
            )
        return response


def test_orchestrator_researches_builds_activates_and_loads_skill(tmp_path):
    registry = SkillRegistry()
    research = StaticResearchSkill()
    registry.register(research)
    registry.register(CreateSkillSkill(tmp_path / "skills"))
    provider = GeneratedSkillProvider()
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.skills = registry
    orchestrator.llm = provider
    orchestrator.session_id = "test-session"
    orchestrator.active_skill = None
    orchestrator.skill_resume_name = None
    activities = []
    orchestrator.activity_callback = lambda message: activities.append((message, orchestrator.active_skill))
    orchestrator._record_tool_result = lambda *args, **kwargs: None
    orchestrator.config = SimpleNamespace(skills=SimpleNamespace(skills_dir=tmp_path / "skills"))
    arguments = {"name": "echo_complete", "request": "Create a robust echo skill"}

    proposal = orchestrator._run_skill_result("create_skill", arguments)

    assert proposal.requires_confirmation
    assert arguments["generated_source"]
    assert research.queries
    assert "Python documentation" in provider.messages[0][1]["content"]
    assert "Python documentation" in provider.messages[1][1]["content"]
    assert ("researching skill: echo_complete", "internet_search") in activities
    assert ("building skill: echo_complete", "create_skill") in activities
    assert ("using skill create_skill", "create_skill") in activities

    orchestrator.pending_tool = {
        "name": "create_skill",
        "arguments": arguments,
        "token": proposal.confirmation_token,
        "resume_after_confirmation": True,
    }
    created = orchestrator.confirm_pending_tool(proposal.confirmation_token)

    assert "enabled and available now" in created
    assert registry.get("echo_complete").manifest.enabled
    assert registry.get("echo_complete").run({"input": "hello"}, {}).content == "Echo: hello"
    assert "echo_complete" in {item["function"]["name"] for item in orchestrator._tool_schemas()}
    assert orchestrator.consume_skill_resume() == "echo_complete"
    assert orchestrator.consume_skill_resume() is None
    assert orchestrator.active_skill is None


def test_generated_skill_rejects_unsafe_imports():
    source = """
import subprocess
class SkillImpl:
    def run(self, arguments, context):
        return None
"""

    try:
        validate_generated_skill_source(source)
    except SkillBuildError as exc:
        assert "blocked module" in str(exc)
    else:
        raise AssertionError("expected unsafe generated source to be rejected")


def test_generated_skill_rejects_invalid_manifest_field():
    source = """
from sirina_agent.skills.base import SkillManifest, SkillResult
class SkillImpl:
    manifest = SkillManifest(
        name="bad",
        description="Bad manifest",
        parameters_schema={"type": "object", "properties": {}},
        required_permissions=[],
        risk_level="low",
        enabled=True,
    )
    def run(self, arguments, context):
        return SkillResult(True, "ok")
"""

    try:
        validate_generated_skill_source(source)
    except SkillBuildError as exc:
        assert "invalid field: parameters_schema" in str(exc)
    else:
        raise AssertionError("expected invalid manifest field to be rejected")


def test_generated_skill_rejects_invalid_skill_result_fields():
    source = """
from sirina_agent.skills.base import SkillManifest, SkillResult
class SkillImpl:
    manifest = SkillManifest(
        name="bad_result",
        description="Bad result",
        arguments_schema={"type": "object", "properties": {}},
        required_permissions=[],
        risk_level="low",
        enabled=True,
    )
    def run(self, arguments, context):
        return SkillResult(success=True, result={"status": "ok"})
"""

    try:
        validate_generated_skill_source(source)
    except SkillBuildError as exc:
        assert "invalid field" in str(exc)
    else:
        raise AssertionError("expected invalid SkillResult fields to be rejected")


def test_skill_builder_retries_invalid_manifest_field():
    provider = InvalidThenValidSkillProvider()

    spec = build_skill_spec(provider, "echo_complete", "Create an echo skill", "Python documentation")

    assert len(provider.messages) == 3
    assert "arguments_schema=" in spec["source"]
    assert "parameters_schema=" not in spec["source"]
    assert "invalid field: parameters_schema" in provider.messages[2][1]["content"]


def test_skill_builder_repairs_python_syntax_error():
    provider = InvalidSyntaxThenValidSkillProvider()

    spec = build_skill_spec(provider, "echo_complete", "Create an echo skill", "Python documentation")

    assert len(provider.messages) == 3
    assert "invalid Python syntax" in provider.messages[2][1]["content"]
    validate_generated_skill_source(spec["source"])


def test_create_skill_can_replace_broken_existing_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    target = skills_dir / "repair_me"
    target.mkdir(parents=True)
    (target / "skill.py").write_text("broken source", encoding="utf-8")
    creator = CreateSkillSkill(skills_dir)
    proposal = creator.run({"name": "repair_me", "request": "Create a working replacement"}, {})

    assert proposal.requires_confirmation
    assert "Replace existing" in (proposal.confirmation_prompt or "")

    result = creator.run(
        {
            "name": "repair_me",
            "request": "Create a working replacement",
            "confirmed": True,
            "confirmation_text": proposal.confirmation_token,
        },
        {},
    )

    assert result.ok
    assert result.data["replaced"]
    assert "SkillImpl" in (target / "skill.py").read_text(encoding="utf-8")
    assert any((skills_dir / ".backups").iterdir())
