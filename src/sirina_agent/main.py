from __future__ import annotations

import argparse
import os
from pathlib import Path

from .audio.sirina_io import SirinaSpeechIO
from .config import load_config
from .core.orchestrator import AgentOrchestrator
from .llm.providers import UnconfiguredProvider, build_provider
from .mcp import MCPManager
from .memory.store import FaissMemoryStore, LocalHashEmbeddingProvider
from .security.commands import CommandPolicy, CommandRunner
from .sessions.store import SessionStore
from .skills.builtin.subagents import (
    CreateSubagentSkill,
    DelegateSubagentSkill,
    DeleteSubagentSkill,
    SubagentJobsSkill,
    UpdateSubagentSkill,
)
from .skills.builtin.system_command import SystemCommandSkill
from .skills.registry import default_registry
from .subagents import SubagentManager
from .tui.app import create_tui
from .utils.logging import audit_logger, configure_logging


def build_agent(config_path: str | Path | None = None) -> tuple[AgentOrchestrator, SirinaSpeechIO]:
    onnx_device_override = os.environ.get("ULYSSES_ONNX_DEVICE")
    config = load_config(config_path)
    os.environ["ULYSSES_ONNX_DEVICE"] = onnx_device_override or config.sirina.onnx_device
    configure_logging(config.logging.directory, config.logging.level, config.logging.max_bytes, config.logging.backups)
    sessions = SessionStore(config.memory.sqlite_path)
    embeddings = LocalHashEmbeddingProvider(config.memory.dimension)
    memory = FaissMemoryStore(
        config.memory.faiss_path, config.memory.metadata_path, embeddings, config.memory.max_items
    )
    policy = CommandPolicy(
        config.skills.command.allowed_commands,
        config.skills.command.denied_commands,
        config.skills.command.working_directory,
        config.skills.command.env_allowlist,
        config.skills.command.require_confirmation,
        config.skills.command.require_typed_confirmation_for_high_risk,
        config.skills.command.bypass_confirmation_for_allowed_commands,
        config.skills.command.godmode,
    )
    runner = CommandRunner(
        policy,
        audit_logger(config.logging.directory),
        config.skills.command.timeout_seconds,
        config.skills.command.max_output_chars,
    )
    skills = default_registry(
        SystemCommandSkill(runner),
        config.skills.skills_dir,
        include_search=config.skills.internet_search_enabled,
    )
    subagents = None
    if config.subagents.enabled:
        subagents = SubagentManager(
            config.subagents,
            lambda: build_provider(config.llm),
            skills,
            audit_logger(config.logging.directory),
        )
        skills.register(CreateSubagentSkill(subagents))
        skills.register(UpdateSubagentSkill(subagents))
        skills.register(DelegateSubagentSkill(subagents))
        skills.register(SubagentJobsSkill(subagents))
        skills.register(DeleteSubagentSkill(subagents))
    mcp = MCPManager(config.mcp, skills, audit_logger(config.logging.directory))
    try:
        provider = build_provider(config.llm)
    except RuntimeError as exc:
        provider = UnconfiguredProvider(str(exc))
    orchestrator = AgentOrchestrator(
        config,
        sessions,
        memory,
        provider,
        skills,
        config_path,
        subagents,
        mcp,
    )
    if subagents:
        subagents.provider_factory = lambda: build_provider(orchestrator.config.llm)
    mcp.start()
    return orchestrator, SirinaSpeechIO(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ulysses local-first AI voice agent")
    parser.add_argument("--config", default="config/ulysses.yaml")
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args(argv)
    orchestrator, voice = build_agent(args.config)
    create_tui(orchestrator, None if args.text_only else voice).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
