from __future__ import annotations

import os


def startup_brief(orchestrator, voice_io=None) -> str:
    cfg = orchestrator.config
    llm_ok, llm_status = _llm_status(cfg)
    memory_ok, memory_status = _memory_status(orchestrator)
    skills_ok, skills_status = _skills_status(orchestrator)
    prompt_ok, prompt_status = _prompt_status(orchestrator)
    lines = [
        f"{cfg.agent_name} Cyber Sentinel initializing",
        f"{cfg.agent_name} Cyber Sentinel online.",
        "VAPT / PenTest / Vulnerability Assessment console initialized.",
        "",
    ]
    lines.append(f"Brain: {llm_status}")
    lines.append(f"Memory: {memory_status}")
    lines.append(f"Skills: {skills_status}")
    lines.append(f"Prompt: {prompt_status}")
    lines.append(f"Voice: {_voice_status(voice_io)}")
    lines.append("")
    if llm_ok and memory_ok and skills_ok and prompt_ok:
        lines.append("All systems ready and operational.")
    else:
        lines.append("Core systems initialized. Review setup items above.")
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
        return True, f"up ({llm.provider} / {llm.model})"
    if llm.provider == "ollama":
        return True, f"up ({llm.provider} / {llm.model} at {llm.base_url})"
    if llm.provider in {"openai", "kimi"}:
        if os.getenv(llm.api_key_env):
            return True, f"up ({llm.provider} / {llm.model})"
        return False, f"needs setup ({llm.api_key_env} missing)"
    token_env = llm.oauth_token_env or ""
    if token_env and os.getenv(token_env):
        return True, f"up ({llm.provider} / {llm.model})"
    return False, "needs setup (OAuth token missing)"


def _memory_status(orchestrator) -> tuple[bool, str]:
    try:
        orchestrator.memory.search("__ulysses_boot_check__", top_k=1)
        return True, f"up ({len(orchestrator.memory.items)} memories indexed)"
    except Exception as exc:
        return False, f"needs attention ({exc})"


def _skills_status(orchestrator) -> tuple[bool, str]:
    try:
        names = [skill.manifest.name for skill in orchestrator.skills.enabled()]
        return (True, "up (" + ", ".join(names) + ")") if names else (False, "needs setup (no enabled skills)")
    except Exception as exc:
        return False, f"needs attention ({exc})"


def _prompt_status(orchestrator) -> tuple[bool, str]:
    try:
        prompt = orchestrator._system_prompt()
    except Exception as exc:
        return False, f"needs attention ({exc})"
    profile = "Kali VAPT profile" if "vulnerability assessor" in prompt and "Kali" in prompt else "custom profile"
    return True, f"up ({profile}, {len(prompt)} chars)"


def _voice_status(voice_io) -> str:
    if not voice_io:
        return "inactive (text-only)"
    state = voice_io.state
    return f"up ({'on' if state.enabled else 'off'}, muted={state.muted}, tts={state.tts})"
