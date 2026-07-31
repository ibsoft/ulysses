from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LLMConfig(BaseModel):
    provider: Literal["openai", "openai_chatgpt", "kimi", "ollama", "mock"] = "openai"
    model: str = "gpt-4.1-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: float = 60.0


class AudioConfig(BaseModel):
    enabled: bool = True
    text_only: bool = False
    input_device: str | int | None = "auto"
    output_device: str | int | None = "auto"
    language: Literal["en", "el", "auto"] = "auto"
    vad_threshold: float | None = None
    silence_seconds: float = 1.1
    max_utterance_seconds: float = 20.0
    push_to_talk_key: str = "f4"


class WakeWordConfig(BaseModel):
    enabled: bool = True
    models: list[str] = Field(default_factory=lambda: ["hey_ulysses"])
    threshold: float = 0.55
    inference_framework: str = "onnx"


class SirinaConfig(BaseModel):
    stt_engine: str = "tdt"
    tts_voice: str = "am_michael"
    normalize_tts_text: bool = True
    isolate_tts_process: bool = True


class MemoryConfig(BaseModel):
    sqlite_path: Path = Path("var/ulysses/sessions.sqlite3")
    faiss_path: Path = Path("var/ulysses/memory.faiss")
    metadata_path: Path = Path("var/ulysses/memory.jsonl")
    embedding_provider: str = "local_hash"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimension: int = 384
    top_k: int = 6
    retention_days: int = 365
    max_items: int = 5000


class ContextConfig(BaseModel):
    auto_consolidate: bool = True
    context_window_tokens: int = 128_000
    max_messages: int = 40
    max_chars: int = 24_000
    keep_last_messages: int = 12
    summary_target_chars: int = 3_000


class SubagentConfig(BaseModel):
    enabled: bool = True
    root_dir: Path = Path("var/ulysses/subagents")
    max_agents: int = 16
    max_concurrent_jobs: int = 4
    max_tool_rounds: int = 6
    max_file_chars: int = 200_000
    delegable_skills: list[str] = Field(default_factory=lambda: ["internet_search"])
    denied_skills: list[str] = Field(
        default_factory=lambda: [
            "system_command",
            "create_skill",
            "subagent_create",
            "subagent_update",
            "subagent_delegate",
            "subagent_jobs",
            "subagent_delete",
        ]
    )
    allow_mcp: bool = False
    allowed_risk_levels: list[Literal["low", "medium", "high"]] = Field(default_factory=lambda: ["low", "medium"])
    max_skill_calls_per_job: int = Field(default=10, ge=1, le=100)
    max_skill_output_chars: int = Field(default=20_000, ge=1_000, le=200_000)


class AutonomousConfig(BaseModel):
    check_interval_seconds: float = 90.0
    report_probability: float = 0.35
    min_seconds_between_reports: float = 180.0
    max_recent_messages: int = 8
    defense_checks_enabled: bool = True
    defense_elevated_interval_seconds: float = 45.0
    defense_critical_interval_seconds: float = 15.0
    defense_report_min_score: int = 0
    auto_block_attackers: bool = True
    install_missing_security_apps: bool = True


class LoggingConfig(BaseModel):
    directory: Path = Path("var/ulysses/logs")
    level: str = "INFO"
    max_bytes: int = 2_000_000
    backups: int = 5


class TUIConfig(BaseModel):
    theme: Literal["ulysses_dark", "ulysses_light", "terminal"] = "ulysses_dark"


class TelegramConnectorConfig(BaseModel):
    enabled: bool = False
    token_env: str = "TELEGRAM_BOT_TOKEN"
    state_path: Path = Path("var/ulysses/connectors/telegram.json")
    polling_timeout_seconds: float = 20.0
    pairing_code_ttl_seconds: int = 600
    max_message_chars: int = 3500


class ConnectorConfig(BaseModel):
    telegram: TelegramConnectorConfig = Field(default_factory=TelegramConnectorConfig)


class MCPServerConfig(BaseModel):
    id: str
    enabled: bool = True
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    environment_variables: list[str] = Field(default_factory=list)
    bearer_token_env: str = ""
    tool_allowlist: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "high"
    require_confirmation: bool = True
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if not normalized or len(normalized) > 40 or not normalized[0].isalpha():
            raise ValueError("MCP server id must start with a letter and contain at most 40 characters")
        if any(not (character.isalnum() or character == "_") for character in normalized):
            raise ValueError("MCP server id may contain only letters, numbers, and underscores")
        return normalized

    @field_validator("environment_variables")
    @classmethod
    def valid_environment_variables(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or not (value[0].isalpha() or value[0] == "_"):
                raise ValueError("MCP environment-variable names must be valid identifiers")
            if any(not (character.isalnum() or character == "_") for character in value):
                raise ValueError("MCP environment-variable names must be valid identifiers")
        return list(dict.fromkeys(values))

    @field_validator("bearer_token_env")
    @classmethod
    def valid_bearer_token_environment(cls, value: str) -> str:
        if not value:
            return value
        return cls.valid_environment_variables([value])[0]

    @model_validator(mode="after")
    def transport_target(self):
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError("stdio MCP servers require a command")
        if self.transport == "streamable_http" and not self.url.strip():
            raise ValueError("Streamable HTTP MCP servers require a URL")
        return self


class MCPConfig(BaseModel):
    enabled: bool = False
    servers: list[MCPServerConfig] = Field(default_factory=list)
    allowed_stdio_commands: list[str] = Field(
        default_factory=lambda: ["python", "python3", "uv", "uvx", "node", "npx", "docker"]
    )
    artifacts_dir: Path = Path("var/ulysses/mcp/artifacts")
    max_output_chars: int = Field(default=50_000, ge=1_000, le=1_000_000)
    max_tools_per_server: int = Field(default=50, ge=1, le=200)
    max_description_chars: int = Field(default=500, ge=80, le=2_000)

    @model_validator(mode="after")
    def unique_server_ids(self):
        ids = [server.id for server in self.servers]
        if len(ids) != len(set(ids)):
            raise ValueError("MCP server ids must be unique")
        return self


class PromptConfig(BaseModel):
    personality: str = (
        "Pragmatic, calm, technically rigorous, concise, and security-aware. Speak like a capable local Linux operator."
    )
    instructions: str = (
        "You are a local-first Linux voice agent. Use concise answers. "
        "Ask for confirmation before risky skills. Respect privacy and never expose secrets."
    )
    system_prompt_path: Path | None = Path("prompts/ulysses_system.md")


class CommandSkillConfig(BaseModel):
    enabled: bool = True
    godmode: bool = False
    bypass_confirmation_for_allowed_commands: bool = True
    require_confirmation: bool = True
    require_typed_confirmation_for_high_risk: bool = True
    install_missing_assessment_tools: bool = True
    allowed_commands: list[str] = Field(
        default_factory=lambda: [
            "pwd",
            "ls",
            "cat",
            "sed",
            "rg",
            "git",
            "python",
            "python3",
            "apt",
            "apt-get",
            "apt-cache",
            "dpkg",
            "pip",
            "pip3",
            "pipx",
            "curl",
            "wget",
            "df",
            "lsblk",
            "fdisk",
            "nmap",
            "rustscan",
            "masscan",
            "naabu",
            "whatweb",
            "nikto",
            "nuclei",
            "gobuster",
            "feroxbuster",
            "ffuf",
            "dirsearch",
            "httpx",
            "katana",
            "subfinder",
            "amass",
            "dnsx",
            "dig",
            "host",
            "nslookup",
            "whois",
            "sslscan",
            "testssl",
            "testssl.sh",
            "wafw00f",
            "enum4linux",
            "enum4linux-ng",
            "smbclient",
            "smbmap",
            "ldapsearch",
            "snmpwalk",
            "onesixtyone",
            "sqlmap",
            "hydra",
            "medusa",
            "patator",
            "john",
            "hashcat",
            "wpscan",
            "crackmapexec",
            "netexec",
            "msfconsole",
            "msfvenom",
            "aircrack-ng",
            "uname",
            "uptime",
            "who",
            "last",
            "ss",
            "ps",
            "ip",
            "journalctl",
            "which",
        ]
    )
    denied_commands: list[str] = Field(
        default_factory=lambda: ["rm", "sudo", "su", "chmod", "chown", "mkfs", "mount", "umount"]
    )
    working_directory: Path = Path(".")
    timeout_seconds: float = 300.0
    max_output_chars: int = 50_000
    sandbox_mode: Literal["none", "container"] = "none"
    env_allowlist: list[str] = Field(default_factory=lambda: ["PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH"])


class SkillConfig(BaseModel):
    enabled: bool = True
    skills_dir: Path = Path("skills")
    internet_search_enabled: bool = True
    command: CommandSkillConfig = Field(default_factory=CommandSkillConfig)


class PrivacyConfig(BaseModel):
    privacy_mode: bool = False
    redact_logs: bool = True
    retrieve_memory: bool = True


class UlyssesConfig(BaseModel):
    agent_name: str = "Ulysses"
    agent_version: str = "1.0"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    sirina: SirinaConfig = Field(default_factory=SirinaConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
    autonomous: AutonomousConfig = Field(default_factory=AutonomousConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tui: TUIConfig = Field(default_factory=TUIConfig)
    connectors: ConnectorConfig = Field(default_factory=ConnectorConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)

    @field_validator("agent_name")
    @classmethod
    def non_empty_agent_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent_name cannot be empty")
        return value.strip()

    def model_dump_safe(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
