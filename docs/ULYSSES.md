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
  connectors/              remote connector protocol, registry, manager and adapters
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

The default combined prompt frames Ulysses as a Kali/Linux vulnerability assessor and penetration-testing assistant for authorized systems. It also gives Ulysses a standing defensive mission: treat the local system it runs on as a protected host and prioritize defending it from compromise, malware, credential exposure, persistence, misconfiguration, data loss, and cyber attacks.

Security specialties called out in the default prompt include:

- XSS.
- IDOR and BOLA.
- Authentication and authorization flaws.
- Certificate and TLS vulnerabilities.
- Cloud security.
- Local Linux security.

For these findings, Ulysses is instructed to explain exploitability, prerequisites, affected trust boundaries, concrete evidence, proof of concept, business impact, and precise remediation.

For assessed systems, Ulysses prefers Markdown reports. Assessment reports should include scope, executive summary, methodology, severity-ranked findings, detailed findings, technical proof of concept, evidence, impact, detailed remediation, verification steps, and assumptions or limitations. Findings are ranked Critical, High, Medium, Low, and Informational, and Ulysses should not invent vulnerabilities that are not supported by tool output or user-provided evidence.

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
- `skills.command.bypass_confirmation_for_allowed_commands`: defaults to `true` and skips prompts for allowlisted non-high-risk commands.
- `skills.command.godmode`: when set to `true`, gives full local command access. It bypasses the command allowlist, denylist, normal confirmation, high-risk typed confirmation, and permits shell control operators through `bash -lc`. It still uses the configured working directory, filtered environment, timeouts, output caps, and audit logging.
- For multi-step system inspection requests, Ulysses plans separate commands, stores every output as tool history, and then produces one combined summary from the results.
- `create_skill`: researches and generates complete local skills under `skills.skills_dir`. It requires typed confirmation before writing executable code, then enables and registers the skill live.

Sudo behavior:

- In normal mode, commands beginning with `sudo` are allowed only after typed confirmation.
- The Textual TUI opens a sudo password dialog at execution time.
- The Rich fallback prompts for the sudo password in the terminal.
- The password is passed directly to `sudo -S` and is not stored in config, logs, SQLite, FAISS, or skill metadata.
- In godmode, Ulysses does not open the sudo password dialog or ask for typed high-risk confirmation; sudo behaves like any other unrestricted command and the system sudo flow decides what happens.

## Install on Ubuntu/Debian

Current-user home install:

```bash
cd /mnt/data/dev/sirina
scripts/install-ulysses-linux
```

Re-running the installer upgrades the application without deleting runtime projects, reports, sessions, memory, logs,
or downloaded models. It refreshes the active configuration from the source tree and creates a timestamped backup of
the previous configuration. Pass `--preserve-config` when an upgrade must retain the active configuration unchanged.
Use `scripts/install-ulysses-linux --sync-only` to publish development source and configuration without rebuilding the
virtual environment or checking/downloading models.

This creates:

```text
~/.ulysses/app
~/.ulysses/app/models
~/.ulysses/venv
~/.config/ulysses/ulysses.yaml
~/.config/ulysses/env
~/.local/bin/ulysses
```

Sirina model files are downloaded during install when they are missing or invalid. Upgrades preserve runtime projects,
reports, sessions, FAISS memory, metadata, logs, downloaded models, generated skills, and connector verification state.
Interactive terminals show an animated numbered phase indicator for every installer action. Noninteractive environments
receive stable `[ok]` or `[failed]` lines, and captured command output is shown when a phase fails.

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

Set `OPENAI_API_KEY` in your shell or `.env` loader, or use `F7` / `/setup providers` inside the TUI. Provider setup supports OpenAI, Kimi / Moonshot, local Ollama, and OAuth-compatible OpenAI-style providers. Kimi defaults to `https://api.moonshot.ai/v1` with `KIMI_API_KEY`; Ollama defaults to `http://localhost:11434/v1` and does not require a real API key. Ulysses does not scrape browser tokens or bypass authentication controls.

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

In the Textual TUI, press `F4`, speak, then pause to transcribe and submit the utterance. Press `F4` again or `Escape`
to cancel recording. `/talk` provides the same one-shot microphone flow in the Rich fallback. Push-to-talk input is
independent of `/voice off` and `/mute`, which control spoken responses.

## Slash Commands

`/new`, `/sessions`, `/switch <id>`, `/memory`, `/context`, `/forget <id>`, `/forget all`, `/skills`, `/config`, `/talk`, `/voice on`, `/voice off`, `/mute`, `/theme`, `/theme list`, `/setup providers`, `/setup connectors`, `/create-skill <name> <request>`, `/autonomous on`, `/autonomous off`, `/***autonomous on`, `/status`, `/export`, `/quit`.

Autonomous mode is explicit opt-in. In the Textual TUI, Ulysses runs a defensive host-monitoring cycle for the local system it is installed on. Each cycle performs read-only evidence collection, stores every command output as tool history, scores suspicious evidence, adapts the next check interval when risk rises, and writes a defensive report to SQLite and FAISS memory. If voice is enabled and unmuted, the autonomous report is spoken.

The defense cycle checks kernel/platform data, uptime/load, disk pressure, active sessions, recent logins, listening services, running processes, local interfaces, recent journal warnings, and availability of `ufw`, `fail2ban`, and `auditd`. It detects brute-force evidence from authentication failures and port-scan evidence from firewall/kernel log patterns such as repeated `SRC=` and `DPT=` entries.

Blocking and package installation are system-changing actions. Ulysses plans them when configured, but executes them autonomously only when `skills.command.godmode: true` allows unrestricted command execution. With godmode off, the actions are logged as planned-only.

```yaml
autonomous:
  check_interval_seconds: 90
  report_probability: 0.35
  min_seconds_between_reports: 180
  max_recent_messages: 8
  defense_checks_enabled: true
  defense_elevated_interval_seconds: 45
  defense_critical_interval_seconds: 15
  defense_report_min_score: 0
  auto_block_attackers: true
  install_missing_security_apps: true
```

Commands:

```text
/autonomous on
/autonomous off
/autonomous now
/***autonomous on
```

Create a complete skill:

```text
/create-skill weather_lookup lookup weather for a city
/confirm <token>
```

Ulysses researches the request through `internet_search`, combines the sources with the configured model's knowledge,
generates and statically validates `skill.py`, and requests typed confirmation before installing executable code. It writes
`manifest.yaml`, `skill.py`, and `README.md` under `skills.skills_dir`, enables the manifest, and loads the skill immediately.
The active skill appears in the Textual sidebar. `/skills` lists registration state and `/reload` reloads external skills.

## Remote Connectors

Remote messaging is managed through a connector protocol rather than directly by the TUI. `ConnectorManager` starts,
stops, replaces, and reports status for all configured connectors. Each adapter registers a `ConnectorDefinition` and a
factory with `register_connector`. Incoming requests include the connector ID, remote user ID, and message text, allowing
multiple connector types to operate concurrently without connector-specific orchestration code.

Current connector:

- `telegram`: verified direct messages through a Telegram bot using long polling and automatic reconnection.

Configure connectors from the local console:

```text
/setup connectors
```

Select Telegram, enter the BotFather token in the masked field, and wait for token validation. Ulysses displays a
single-use command such as `/verify 123456`. Send that command directly to the bot from the Telegram account to authorize.
The code expires after ten minutes and is invalidated after five failed attempts. Additional accounts require a new local
pairing flow.

Verified Telegram commands:

- Send normal text to invoke Ulysses.
- `/status` checks connector and verification state.
- `/confirm <token>` confirms a pending non-sudo operation.
- `/cancel` cancels a pending operation.
- `/disconnect` revokes the current Telegram chat.

Remote connectors never accept sudo passwords. Any operation requiring sudo authentication must be completed through the
local protected password dialog. Local and remote orchestrator interactions are serialized to protect session and pending
command state.

```yaml
connectors:
  telegram:
    enabled: false
    token_env: TELEGRAM_BOT_TOKEN
    state_path: var/ulysses/connectors/telegram.json
    polling_timeout_seconds: 20
    pairing_code_ttl_seconds: 600
    max_message_chars: 3500
```

Secrets and state:

- Bot tokens are stored in `~/.config/ulysses/env` with mode `0600`, never in YAML or transcripts.
- Verified chat IDs are stored in `var/ulysses/connectors/telegram.json` with mode `0600`.
- Long responses are split at readable boundaries before Telegram delivery.
- Unverified chats cannot invoke the orchestrator or its skills.

To add another connector, implement `Connector` from `sirina_agent.connectors.base`, define its credentials and
verification flow, register its definition and factory, and add its setup form. Shared lifecycle, combined sidebar status,
and source-aware message routing are provided by `ConnectorManager`.

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
- `F7`: provider setup
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

Primary risks are credential leakage, unsafe tool execution, unintended microphone capture, prompt injection through retrieved web content, excessive memory retention, and out-of-scope security testing. Ulysses mitigates these with secret redaction, environment filtering, command allowlists and denylists, confirmation prompts, typed confirmation for high-risk commands, audit logs, source-tagged memory retrieval, explicit deletion commands, and provider authentication through official configuration only.

The default prompt treats the local host as a protected system. When compromise is suspected, Ulysses should prioritize containment, evidence preservation, impact assessment, recovery, and hardening. For intrusive testing against other systems, scope and authorization should be explicit before proceeding.
