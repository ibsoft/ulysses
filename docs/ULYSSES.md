# Ulysses

Ulysses is a modular local-first Linux AI voice agent built on Sirina. The package is split into replaceable adapters for audio, wake-word detection, Sirina STT/TTS, sessions, FAISS memory, LLM providers, skills, security policy, and the terminal UI.

## Architecture

```text
src/sirina_agent/
  main.py                  CLI entry point
  config/                  YAML plus ULYSSES__... environment overrides
  core/                    orchestration and memory injection
  audio/                   Sirina STT/TTS and wake-word adapters
  llm/                     OpenAI-compatible and OAuth-compatible providers
  memory/                  FAISS-backed semantic memory with metadata
  sessions/                SQLite conversation persistence
  security/                command policy, confirmation and audit execution
  skills/                  skill manifests, registry, built-ins
  tui/                     Rich terminal interface and slash commands
```

## Prompt And Personality

Edit `prompt` in `config/ulysses.yaml` for short personality and behavior changes:

```yaml
prompt:
  personality: Pragmatic, calm, technically rigorous, concise, and security-aware.
  instructions: Ask for confirmation before risky skills. Respect privacy.
  system_prompt_path: prompts/ulysses_system.md
```

Use `prompts/ulysses_system.md` for longer system instructions. Ulysses combines the agent name/version, personality, inline instructions, and the prompt file before each LLM call.

## Context Consolidation

Ulysses automatically consolidates long sessions so the active context stays within a practical size. Older messages are summarized into session metadata, the recent tail is kept verbatim, and the summary is injected into future LLM calls.

```yaml
context:
  auto_consolidate: true
  context_window_tokens: 128000
  max_messages: 40
  max_chars: 24000
  keep_last_messages: 12
  summary_target_chars: 3000
```

The TUI shows an estimated context gauge. At 100%, Ulysses summarizes older messages and compacts the session automatically. If summarization fails because the provider is unavailable, Ulysses keeps the existing messages and continues without deleting history.

Default skills:

- `internet_search`: DuckDuckGo search with title, URL, snippet and timestamp fields when available.
- `system_command`: allowlisted local command execution with confirmation, typed confirmation for high-risk commands, timeouts, output caps, environment filtering and audit logs.
- `skills.command.godmode`: when set to `true`, bypasses the command allowlist and denylist. It still uses parsed argv execution with `shell=False`, configured working directory, filtered environment, timeouts, output caps, audit logging, and high-risk confirmation.
- `create_skill`: scaffolds new local skills from user requests into `skills.skills_dir`. It requires typed confirmation and creates disabled reviewable skills by default.

Sudo behavior:

- In normal mode, commands beginning with `sudo` are allowed only after typed confirmation.
- The Textual TUI opens a sudo password dialog at execution time.
- The Rich fallback prompts for the sudo password in the terminal.
- The password is passed directly to `sudo -S` and is not stored in config, logs, SQLite, FAISS, or skill metadata.
- In godmode, Ulysses does not open the sudo password dialog; sudo behaves like any other high-risk command and the system sudo flow decides what happens.

## Install on Ubuntu/Debian

Current-user home install:

```bash
cd /mnt/data/dev/sirina
scripts/install-ulysses-linux
```

This creates:

```text
~/.ulysses/venv
~/.config/ulysses/ulysses.yaml
~/.config/ulysses/env
~/.local/bin/ulysses
```

Set `OPENAI_API_KEY` in `~/.config/ulysses/env`, then run:

```bash
ulysses
```

Manual install:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv portaudio19-dev libsndfile1 ripgrep
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[agent,dev]"
sirina download --group all
cp .env.example .env
```

`openwakeword` currently depends on Linux `tflite-runtime` wheels that are not available for every Python version. Install the base agent first, then add wake-word support only on a compatible Python, usually Python 3.11:

```bash
python -m pip install -e ".[wakeword]"
```

Without that extra, Ulysses still runs text-only and Sirina VAD/push-to-talk style voice flows.

Set `OPENAI_API_KEY` in your shell or `.env` loader. To use an officially supported OpenAI-compatible OAuth provider, set `llm.provider: oauth_compatible`, `llm.base_url`, and either an OAuth token environment variable or OS keyring service details. Ulysses does not scrape browser tokens or bypass authentication controls.

Run text-only:

```bash
ULYSSES__LLM__PROVIDER=mock ulysses --config config/ulysses.yaml --text-only
```

Run with the configured provider:

```bash
export OPENAI_API_KEY=...
ulysses --config config/ulysses.yaml
```

## Voice Flow

The intended runtime flow is:

```text
microphone -> openWakeWord -> Sirina VAD recording -> Sirina STT -> LLM/skills -> Sirina TTS -> wake listening
```

The current wake-word adapter is replaceable and intentionally small; if `openwakeword` is unavailable it falls back to push-to-talk/text operation rather than failing the agent.

## Slash Commands

`/new`, `/sessions`, `/switch <id>`, `/memory`, `/context`, `/forget <id>`, `/forget all`, `/skills`, `/config`, `/voice on`, `/voice off`, `/mute`, `/theme`, `/theme list`, `/create-skill <name> <request>`, `/autonomous on`, `/autonomous off`, `/***autonomous on`, `/status`, `/export`, `/quit`.

Autonomous mode is explicit opt-in. In the Textual TUI, Ulysses periodically checks the current mission/session and may write a short, humane report when it has a useful observation or next step. Each autonomous report is saved to SQLite and FAISS memory for future recovery.

```yaml
autonomous:
  check_interval_seconds: 90
  report_probability: 0.35
  min_seconds_between_reports: 180
  max_recent_messages: 8
```

Commands:

```text
/autonomous on
/autonomous off
/autonomous now
/***autonomous on
```

Create a new skill scaffold:

```text
/create-skill weather_lookup lookup weather for a city
/confirm <token>
```

Ulysses writes `manifest.yaml`, `skill.py`, and `README.md` under the configured `skills.skills_dir`. Review the generated code before enabling it.

When `textual` is installed, Ulysses starts a full-screen TUI with transcript, sidebar status, themes, paste-friendly input, clipboard copy for the last assistant response, and shortcuts:

- `Ctrl+U`: voice responses on/off
- `Ctrl+M`: mute
- `Ctrl+Y`: copy last assistant response
- `Ctrl+Shift+Y`: copy the full transcript
- `Ctrl+S`: toggle terminal selection mode
- `Ctrl+N`: new session
- `Ctrl+L`: clear transcript
- `F2`: cycle theme
- `F5`: status
- `F6`: skills
- `Ctrl+Q`: quit

Terminal drag-selection is owned by your terminal emulator, not Textual. Use `/select on` or `Ctrl+S` to blur the input and make native terminal selection easier; terminals that support copy-on-select will then copy selected text automatically. Ulysses also provides `/copy` for the last answer and `/copy all` for the transcript.

## Audio Troubleshooting

List devices:

```bash
sirina audio-devices
```

For PipeWire, confirm the compatibility layer is running:

```bash
systemctl --user status pipewire pipewire-pulse wireplumber
```

For PulseAudio:

```bash
pactl list short sources
pactl list short sinks
```

Set `audio.input_device` and `audio.output_device` in `config/ulysses.yaml` to an index or stable device name from `sirina audio-devices`.

## Privacy and Retention

Conversation messages are stored in SQLite. Semantic memory text and metadata are stored next to the FAISS index. Use `/forget <memory_id>` for one item and `/forget all` to erase sessions and memory. Secrets must stay in environment variables or keyring and are redacted from structured logs where practical.

## Threat Model

Primary risks are credential leakage, unsafe tool execution, unintended microphone capture, prompt injection through retrieved web content, and excessive memory retention. Ulysses mitigates these with secret redaction, environment filtering, command allowlists and denylists, confirmation prompts, typed confirmation for high-risk commands, audit logs, source-tagged memory retrieval, explicit deletion commands, and provider authentication through official configuration only.
