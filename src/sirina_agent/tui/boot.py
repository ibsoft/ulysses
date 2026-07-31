from __future__ import annotations

import os

from sirina_agent.llm.openai_auth import codex_chatgpt_authenticated


def startup_brief(orchestrator, voice_io=None) -> str:
    cfg = orchestrator.config
    llm_ok, llm_status = _llm_status(cfg)
    memory_ok, memory_status = _memory_status(orchestrator)
    skills_ok, skills_status = _skills_status(orchestrator)
    prompt_ok, prompt_status = _prompt_status(orchestrator)
    lines = [
        f"[bold cyan]◆  {cfg.agent_name.upper()} CYBER SENTINEL[/bold cyan]",
        "[dim]VAPT  /  PENTEST  /  VULNERABILITY ASSESSMENT[/dim]",
        "",
    ]
    lines.append(_readiness_line("Brain", llm_ok, llm_status))
    lines.append(_readiness_line("Memory", memory_ok, memory_status))
    lines.append(_readiness_line("Skills", skills_ok, skills_status))
    lines.append(_readiness_line("Prompt", prompt_ok, prompt_status))
    lines.append(_readiness_line("Voice", voice_io is not None, _voice_status(voice_io)))
    lines.append("")
    if llm_ok and memory_ok and skills_ok and prompt_ok:
        lines.append("[bold green]●  OPERATIONAL[/bold green]  All core systems ready.")
    else:
        lines.append("[bold yellow]!  ATTENTION[/bold yellow]  Review setup items above.")
    return "\n".join(lines)


def spoken_startup_brief(orchestrator, voice_io=None) -> str:
    cfg = orchestrator.config
    llm_ok, _ = _llm_status(cfg)
    memory_ok, _ = _memory_status(orchestrator)
    skills_ok, _ = _skills_status(orchestrator)
    prompt_ok, _ = _prompt_status(orchestrator)
    checks = [
        ("Brain", llm_ok),
        ("Memory", memory_ok),
        ("Skills", skills_ok),
        ("Prompt", prompt_ok),
        ("Voice", voice_io is not None),
    ]
    lines = [f"{name} {'up' if ok else 'needs setup'}." for name, ok in checks]
    if all(ok for _, ok in checks[:4]):
        lines.append("All systems ready and operational.")
    else:
        lines.append("Core systems initialized. Review setup.")
    return " ".join(lines)


def _llm_status(cfg) -> tuple[bool, str]:
    llm = cfg.llm
    if llm.provider == "mock":
        return True, f"configured ({llm.provider} / {llm.model})"
    if llm.provider == "ollama":
        return True, f"configured ({llm.provider} / {llm.model})"
    if llm.provider in {"openai", "kimi"}:
        if os.getenv(llm.api_key_env):
            return True, f"configured ({llm.provider} / {llm.model})"
        return False, f"needs setup ({llm.api_key_env} missing)"
    if llm.provider == "openai_chatgpt":
        if not codex_chatgpt_authenticated():
            return False, "needs setup (OpenAI browser login)"
        return True, f"authenticated (openai / {llm.model})"
    return False, "needs provider setup"


def _memory_status(orchestrator) -> tuple[bool, str]:
    try:
        orchestrator.memory.search("__ulysses_boot_check__", top_k=1)
        return True, f"verified ({len(orchestrator.memory.items)} memories indexed)"
    except Exception as exc:
        return False, f"needs attention ({exc})"


def _skills_status(orchestrator) -> tuple[bool, str]:
    try:
        names = [skill.manifest.name for skill in orchestrator.skills.enabled()]
        if not names:
            return False, "needs setup (no enabled skills)"
        preview = ", ".join(names[:3])
        remaining = f", +{len(names) - 3} more" if len(names) > 3 else ""
        return True, f"loaded ({len(names)} active: {preview}{remaining})"
    except Exception as exc:
        return False, f"needs attention ({exc})"


def _prompt_status(orchestrator) -> tuple[bool, str]:
    try:
        prompt = orchestrator._system_prompt()
    except Exception as exc:
        return False, f"needs attention ({exc})"
    profile = "Kali VAPT profile" if "vulnerability assessor" in prompt and "Kali" in prompt else "custom profile"
    return True, f"compiled ({profile}, {len(prompt)} chars)"


def _voice_status(voice_io) -> str:
    if not voice_io:
        return "inactive (text-only)"
    state = voice_io.state
    return f"initialized ({'on' if state.enabled else 'off'}, muted={state.muted}, tts={state.tts})"


def _readiness_line(label: str, ok: bool, status: str) -> str:
    icon = "[green]✓[/green]" if ok else "[yellow]![/yellow]"
    return f"{icon}  {label + ':':<8} {status}"
