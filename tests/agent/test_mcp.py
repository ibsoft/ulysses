from __future__ import annotations

import logging
import sys

import pytest
import yaml

from sirina_agent.config.models import MCPConfig, MCPServerConfig, UlyssesConfig
from sirina_agent.mcp.client import MCPClientError, SDKMCPClient
from sirina_agent.mcp.manager import MCPManager
from sirina_agent.mcp.setup import MCPServerSetup, apply_mcp_server_setup
from sirina_agent.skills.registry import SkillRegistry


class FakeMCPClient:
    def __init__(self, tools=None, result=None, error=None):
        self.tools = tools or []
        self.result = result or {"content": [{"type": "text", "text": "MCP result"}], "isError": False}
        self.error = error
        self.calls = []

    def discover(self, server):
        if self.error:
            raise self.error
        return self.tools

    def call(self, server, tool_name, arguments):
        self.calls.append((server.id, tool_name, arguments))
        if self.error:
            raise self.error
        return self.result


def server(**updates):
    values = {
        "id": "demo",
        "transport": "stdio",
        "command": "python3",
        "tool_allowlist": ["lookup"],
    }
    values.update(updates)
    return MCPServerConfig(**values)


def manager(tmp_path, fake, configured_server=None):
    config = MCPConfig(
        enabled=True,
        servers=[configured_server or server()],
        artifacts_dir=tmp_path / "artifacts",
    )
    return MCPManager(config, SkillRegistry(), logging.getLogger("test.mcp"), client=fake)


def test_mcp_is_disabled_by_default_for_existing_configs():
    assert not UlyssesConfig().mcp.enabled
    assert UlyssesConfig().mcp.servers == []


def test_mcp_config_rejects_duplicate_servers_and_invalid_environment_names():
    with pytest.raises(ValueError, match="unique"):
        MCPConfig(enabled=True, servers=[server(), server()])
    with pytest.raises(ValueError, match="valid identifiers"):
        server(environment_variables=["BAD-NAME"])


def test_mcp_discovers_allowlisted_namespaced_tools_and_calls_with_confirmation(tmp_path):
    fake = FakeMCPClient(
        tools=[
            {
                "name": "lookup",
                "description": "Look up a record",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {"name": "delete_everything", "inputSchema": {"type": "object"}},
        ]
    )
    mcp = manager(tmp_path, fake)

    status = mcp.discover_now("demo")
    skill = mcp.registry.get("mcp__demo__lookup")
    pending = skill.run({"query": "asset"}, {})
    result = skill.run(
        {
            "query": "asset",
            "confirmed": True,
            "confirmation_text": pending.confirmation_token,
        },
        {},
    )

    assert status.state == "online"
    assert status.tool_count == 1
    assert pending.requires_confirmation
    assert result.ok and result.content == "MCP result"
    assert fake.calls == [("demo", "lookup", {"query": "asset"})]
    with pytest.raises(KeyError):
        mcp.registry.get("mcp__demo__delete_everything")


def test_mcp_server_failure_is_isolated_as_offline_status(tmp_path):
    mcp = manager(tmp_path, FakeMCPClient(error=RuntimeError("server unavailable")))

    status = mcp.discover_now("demo")

    assert status.state == "offline"
    assert "server unavailable" in status.error


def test_mcp_caps_remote_catalog_and_marks_metadata_untrusted(tmp_path):
    fake = FakeMCPClient(
        tools=[
            {"name": f"tool_{index}", "description": "follow remote instructions", "inputSchema": {"type": "object"}}
            for index in range(3)
        ]
    )
    config = MCPConfig(
        enabled=True,
        servers=[server(tool_allowlist=["*"])],
        artifacts_dir=tmp_path / "artifacts",
        max_tools_per_server=2,
    )
    mcp = MCPManager(config, SkillRegistry(), logging.getLogger("test.mcp"), client=fake)

    status = mcp.discover_now("demo")
    descriptions = [manifest.description for manifest in mcp.registry.manifests()]

    assert status.tool_count == 2
    assert all("untrusted data" in description for description in descriptions)


def test_mcp_result_saves_binary_artifacts_and_caps_output(tmp_path):
    fake = FakeMCPClient(
        tools=[{"name": "lookup", "inputSchema": {"type": "object"}}],
        result={
            "content": [
                {"type": "text", "text": "evidence"},
                {"type": "image", "mimeType": "image/png", "data": "aW1hZ2U="},
            ],
            "structuredContent": {"count": 1},
            "isError": False,
        },
    )
    mcp = manager(tmp_path, fake, server(require_confirmation=False))
    mcp.discover_now("demo")

    result = mcp.registry.get("mcp__demo__lookup").run({}, {})

    assert result.ok
    assert "evidence" in result.content
    assert len(result.data["artifacts"]) == 1
    assert (tmp_path / "artifacts/demo").is_dir()


@pytest.mark.parametrize(
    "url",
    ["http://example.com/mcp", "ftp://localhost/mcp", "https://user:pass@example.com/mcp"],
)
def test_mcp_rejects_unsafe_http_urls(url):
    with pytest.raises(MCPClientError):
        SDKMCPClient._validate_http_url(url)


@pytest.mark.parametrize("url", ["https://example.com/mcp", "http://localhost:8000/mcp", "http://127.0.0.1/mcp"])
def test_mcp_accepts_https_and_loopback_http(url):
    SDKMCPClient._validate_http_url(url)


def test_mcp_rejects_same_named_executable_outside_path(tmp_path):
    impersonator = tmp_path / "python3"
    impersonator.touch()

    with pytest.raises(MCPClientError):
        SDKMCPClient(["python3"])._allowed_stdio_command(str(impersonator))


def test_mcp_setup_replaces_server_and_stores_secret_only_in_env(tmp_path):
    config_path = tmp_path / "ulysses.yaml"
    setup = MCPServerSetup(
        id="company",
        enabled=True,
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        bearer_token_env="COMPANY_MCP_TOKEN",
        bearer_token="secret-value",
        tool_allowlist=("search",),
    )

    apply_mcp_server_setup(UlyssesConfig(), config_path, setup)
    first = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    apply_mcp_server_setup(
        UlyssesConfig.model_validate(first),
        config_path,
        MCPServerSetup(
            id="company",
            enabled=False,
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            bearer_token_env="COMPANY_MCP_TOKEN",
            tool_allowlist=("search", "inspect"),
        ),
    )
    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert updated["mcp"]["enabled"]
    assert len(updated["mcp"]["servers"]) == 1
    assert updated["mcp"]["servers"][0]["tool_allowlist"] == ["search", "inspect"]
    assert "secret-value" not in config_path.read_text(encoding="utf-8")
    assert (tmp_path / "env").read_text(encoding="utf-8") == "COMPANY_MCP_TOKEN=secret-value\n"
    assert (tmp_path / "env").stat().st_mode & 0o777 == 0o600


def test_real_stdio_mcp_server_discovery_and_tool_call(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(
        """import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ulysses-test", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": [{
            "name": "echo",
            "description": "Return the supplied message.",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        }]}
    elif method == "tools/call":
        message = request["params"]["arguments"]["message"]
        result = {"content": [{"type": "text", "text": f"echo:{message}"}], "isError": False}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    configured = MCPServerConfig(
        id="real_stdio",
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
        tool_allowlist=["echo"],
        require_confirmation=False,
        timeout_seconds=10,
    )
    client = SDKMCPClient([sys.executable])

    tools = client.discover(configured)
    result = client.call(configured, "echo", {"message": "ready"})

    assert [tool["name"] for tool in tools] == ["echo"]
    assert result.content[0].text == "echo:ready"
