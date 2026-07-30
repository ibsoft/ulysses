from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sirina_agent.config.models import MCPServerConfig, UlyssesConfig
from sirina_agent.config.provider_setup import env_path_for_config, update_env_file


@dataclass(frozen=True)
class MCPServerSetup:
    id: str
    enabled: bool
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    environment_variables: tuple[str, ...] = ()
    bearer_token_env: str = ""
    bearer_token: str = ""
    tool_allowlist: tuple[str, ...] = ()
    risk_level: str = "high"
    require_confirmation: bool = True
    timeout_seconds: float = 60.0

    def server_config(self) -> MCPServerConfig:
        return MCPServerConfig(
            id=self.id,
            enabled=self.enabled,
            transport=self.transport,
            command=self.command,
            args=list(self.args),
            url=self.url,
            environment_variables=list(self.environment_variables),
            bearer_token_env=self.bearer_token_env,
            tool_allowlist=list(self.tool_allowlist),
            risk_level=self.risk_level,
            require_confirmation=self.require_confirmation,
            timeout_seconds=self.timeout_seconds,
        )


def apply_mcp_server_setup(config: UlyssesConfig, config_path: Path, setup: MCPServerSetup) -> None:
    candidate = setup.server_config()
    data = config.model_dump(mode="json")
    mcp = data.setdefault("mcp", {})
    mcp["enabled"] = True
    servers = list(mcp.get("servers") or [])
    replacement = candidate.model_dump(mode="json")
    for index, current in enumerate(servers):
        if str(current.get("id") or "") == candidate.id:
            servers[index] = replacement
            break
    else:
        servers.append(replacement)
    mcp["servers"] = servers
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if setup.bearer_token and candidate.bearer_token_env:
        update_env_file(env_path_for_config(config_path), {candidate.bearer_token_env: setup.bearer_token.strip()})
