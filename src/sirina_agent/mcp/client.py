from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


class MCPClientError(RuntimeError):
    pass


class SDKMCPClient:
    def __init__(self, allowed_stdio_commands: list[str]) -> None:
        self.allowed_stdio_commands = set(allowed_stdio_commands)

    def discover(self, server) -> list[dict[str, Any]]:
        return asyncio.run(self._discover(server))

    def call(self, server, tool_name: str, arguments: dict[str, Any]):
        return asyncio.run(self._call(server, tool_name, arguments))

    async def _discover(self, server) -> list[dict[str, Any]]:
        async with self._session(server) as session:
            response = await session.list_tools()
            return [tool.model_dump(by_alias=True, mode="json") for tool in response.tools]

    async def _call(self, server, tool_name: str, arguments: dict[str, Any]):
        async with self._session(server) as session:
            return await session.call_tool(
                tool_name,
                arguments,
                read_timeout_seconds=timedelta(seconds=server.timeout_seconds),
            )

    @asynccontextmanager
    async def _session(self, server):
        timeout = timedelta(seconds=server.timeout_seconds)
        if server.transport == "stdio":
            command = self._allowed_stdio_command(server.command)
            environment = self._stdio_environment(server.environment_variables)
            parameters = StdioServerParameters(command=command, args=server.args, env=environment)
            async with stdio_client(parameters) as (read, write):  # noqa: SIM117
                async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
                    await session.initialize()
                    yield session
            return

        self._validate_http_url(server.url)
        headers = {}
        if server.bearer_token_env:
            token = os.environ.get(server.bearer_token_env, "").strip()
            if not token:
                raise MCPClientError(f"MCP credential environment variable `{server.bearer_token_env}` is not set")
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(  # noqa: SIM117
            headers=headers,
            timeout=server.timeout_seconds,
            follow_redirects=False,
        ) as client:
            async with streamable_http_client(server.url, http_client=client) as (read, write, _):
                async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
                    await session.initialize()
                    yield session

    @staticmethod
    def _stdio_environment(names: list[str]) -> dict[str, str]:
        permitted = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", *names}
        return {name: value for name in permitted if (value := os.environ.get(name)) is not None}

    def _allowed_stdio_command(self, configured_command: str) -> str:
        configured = Path(configured_command).expanduser()
        basename = configured.name
        explicit_paths = {
            str(Path(command).expanduser().resolve()) for command in self.allowed_stdio_commands if "/" in command
        }
        if "/" in configured_command:
            resolved = configured.resolve()
            path_match = str(resolved) in explicit_paths
            discovered = shutil.which(basename)
            system_match = (
                basename in self.allowed_stdio_commands
                and discovered is not None
                and Path(discovered).resolve() == resolved
            )
            if not path_match and not system_match:
                raise MCPClientError(f"stdio command `{configured_command}` is not allowed by MCP policy")
            return str(resolved)
        if configured_command not in self.allowed_stdio_commands:
            raise MCPClientError(f"stdio command `{configured_command}` is not allowed by MCP policy")
        resolved = shutil.which(configured_command)
        if not resolved:
            raise MCPClientError(f"stdio command `{configured_command}` was not found on PATH")
        return resolved

    @staticmethod
    def _validate_http_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.username or parsed.password or parsed.fragment:
            raise MCPClientError("MCP URLs cannot contain credentials or fragments")
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise MCPClientError("Streamable HTTP MCP URLs require HTTPS except for loopback servers")
        if not parsed.hostname:
            raise MCPClientError("MCP URL must include a hostname")
