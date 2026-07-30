# Ulysses

Ulysses is a local-first Kali/Linux security assistant built on Sirina. It is designed for authorized vulnerability assessment, penetration testing, security-tool operation, evidence collection, remediation guidance, and defensive monitoring of the local host. It combines a terminal UI, OpenAI-compatible LLM providers, local speech-to-text and text-to-speech, persistent sessions, semantic memory, skill execution, and command safety controls into one assistant runtime.

The project also contains Sirina, the reusable local speech toolkit that powers Ulysses voice input and output.

## What It Does

- Runs as a text or voice AI agent from the `ulysses` command.
- Uses Sirina for microphone capture, VAD, STT, and TTS.
- Stores conversations in SQLite and long-term memory in FAISS plus JSONL metadata.
- Supports automatic context consolidation for long-running sessions.
- Provides a Textual/Rich terminal UI with sessions, memory, voice toggles, themes, and slash commands.
- Loads built-in and local skills through a registry.
- Executes local commands only through a policy-controlled runner with confirmations, denylists, timeouts, output caps, environment filtering, and audit logs.
- Plans multi-step system inspection requests as separate tool operations, stores each output in the session, then summarizes the combined results.
- Runs a deterministic external assessment baseline covering asset resolution, HTTP security controls, exposed services, web technologies, TLS configuration, web-server configuration, and vulnerability signatures.
- Continues after non-fatal failures by using available alternatives, generating focused Python helpers, or requesting secure installation of required tooling when policy permits.
- Creates a dedicated project for every assessment with separate `scripts/`, `artifacts/`, `results/`, and `reports/` directories.
- Produces confidential, customer-delivery Markdown reports with Executive, Management, and Technical summaries, severity-ranked findings, evidence, impact, remediation priorities, and objective retest criteria.
- Keeps operational diagnostics in internal evidence instead of exposing allowlist decisions, installation output, command failures, confirmation prompts, or internal paths in customer reports.
- Keeps secrets in environment variables or keyring-backed provider configuration rather than YAML.

## Repository Layout

```text
src/sirina_agent/        Ulysses agent runtime
src/sirina/              Sirina speech toolkit
config/ulysses.yaml      Default Ulysses configuration
prompts/                 System prompt files
scripts/                 Linux install and healthcheck scripts
systemd/                 Example systemd unit
docs/                    Extended Ulysses documentation
tests/                   Agent, API, speech, memory, and config tests
var/ulysses/             Local runtime data used by the default config
```

## Requirements

- Linux
- Python 3.11 or newer
- PortAudio and libsndfile for local audio
- An OpenAI API key or another configured OpenAI-compatible provider
- Optional: CUDA ONNX Runtime for accelerated local speech inference

On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv portaudio19-dev libsndfile1 ripgrep
```

## Install

For a current-user install:

```bash
scripts/install-ulysses-linux
```

Running the installer again upgrades the installed application while preserving runtime projects, reports, sessions,
memory, logs, and downloaded models. The active configuration is refreshed from `config/ulysses.yaml`; the previous
file is retained as a timestamped backup. Use `scripts/install-ulysses-linux --preserve-config` to keep the active
configuration unchanged during an upgrade.

For a fast development deployment that only synchronizes source and configuration into the existing installation:

```bash
scripts/install-ulysses-linux --sync-only
```

The installer creates:

```text
~/.ulysses/app
~/.ulysses/app/models
~/.ulysses/venv
~/.config/ulysses/ulysses.yaml
~/.config/ulysses/env
~/.local/bin/ulysses
```

Sirina model files are downloaded during install when they are missing or invalid. Re-running the installer preserves runtime state under `~/.ulysses/app/var/ulysses`, including assessment projects, reports, sessions, FAISS memory, metadata, and logs.

Set your API key in `~/.config/ulysses/env`:

```bash
OPENAI_API_KEY=your_api_key_here
```

## Provider Setup

Open provider configuration with `F7` or:

```text
/setup providers
```

Select OpenAI API key, OpenAI browser, Kimi, or Ollama. Secret fields are masked and write-only. Submitted API keys are
saved to `~/.config/ulysses/env` with mode `0600`; YAML stores only the environment-variable name. Leaving a secret blank
keeps its current value. The selected provider is rebuilt and activated without restarting Ulysses.

### OpenAI Browser Login

Browser login is supported for OpenAI only and requires the Codex CLI. Select **OpenAI browser**, then save:

1. Ulysses starts the Codex app-server login protocol and displays the authorization link.
2. Copy the link, open it in your browser, and sign in to OpenAI.
3. Copy the complete `http://localhost:.../auth/callback?...` return URL from the browser and paste it into the masked
   Ulysses callback field.
4. Ulysses validates the exact loopback host, port, and callback path, forwards the URL only to the local Codex listener,
   and activates the OpenAI provider after Codex confirms success.

The return URL contains a short-lived authorization code. Ulysses never prints, logs, or saves it. Codex manages OAuth
tokens and refresh. After successful login, Ulysses queries authenticated `model/list`, stores the provider's current
default visible model ID, and sends inference requests through ephemeral, read-only Codex CLI sessions using that exact
model. Service routing remains owned by Codex because the authentication and model-catalog protocol does not return a
user-configurable base URL; Ulysses stores no guessed or synthetic URL. Generic OAuth providers and pasted bearer tokens
are not supported for this login mode. See OpenAI's
[Codex sign-in documentation](https://help.openai.com/en/articles/11381614-api-codex-cli-and-sign-in-with-chatgpt).

The installer resolves Codex with `command -v` and records the discovered executable in the protected Ulysses environment
file. At runtime Ulysses uses that discovered value or resolves `codex` from the current `PATH`; no installation path is
embedded in the application.

## Connectors

Open connector configuration with:

```text
/setup connectors
```

The connector selector is backed by a generic registry and lifecycle manager so multiple connector adapters can operate
concurrently. Telegram is the first available connector.

### Telegram

Create a bot with Telegram BotFather, select Telegram from `/setup connectors`, and enter the token in the masked setup
field. Ulysses validates the token and displays a temporary pairing command such
as `/verify 123456`. Send that command directly to the bot from the Telegram account you want to authorize. Unverified
chats cannot invoke the agent. Pairing codes are single-use, expire after ten minutes, and are invalidated after five
failed attempts.

The bot token is stored in `~/.config/ulysses/env` with mode `0600`; verified Telegram chat IDs are stored separately in
`var/ulysses/connectors/telegram.json` with mode `0600`. Remote sudo passwords are never accepted. Commands that require
sudo authentication must be completed in the local Ulysses console. Send `/disconnect` to revoke the current chat.

### Adding Connectors

Connectors implement the shared `Connector` protocol in `sirina_agent.connectors.base` and are registered with a
`ConnectorDefinition` and factory through `register_connector`. The `ConnectorManager` owns concurrent connector
lifecycle, replacement, shutdown, and combined status reporting. Incoming handlers receive `connector_id`, remote user
ID, and message text, so additional adapters do not need Telegram-specific TUI changes. Add each connector's credentials,
verification flow, and setup form inside its own adapter and setup module.

Then run:

```bash
ulysses
```

If `~/.local/bin` is not on your PATH, add it in your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Development Setup

From this checkout:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[agent,dev]"
cp .env.example .env
```

Install CUDA support if needed:

```bash
python -m pip install -e ".[cuda]"
```

Install wake-word support on compatible Python/Linux environments:

```bash
python -m pip install -e ".[wakeword]"
```

`openwakeword` depends on Linux `tflite-runtime` wheels that are not available for every Python version. Ulysses can still run in text-only mode or with Sirina VAD workflows without the wake-word extra.

## Speech Models

Sirina needs local model files for real STT/TTS:

```bash
sirina download --group all
sirina check-models --group all
```

Model lookup order:

1. `SIRINA_MODEL_DIR`
2. `models/` in this checkout
3. bundled metadata under `sirina.assets`
4. `../models` for nested checkouts
5. `~/.sirina/models`

Large `.onnx` and `.bin` files are intentionally not tracked by git.

## Run

Run Ulysses with the default local config:

```bash
ulysses --config config/ulysses.yaml
```

Run text-only with the mock provider:

```bash
ULYSSES__LLM__PROVIDER=mock ulysses --config config/ulysses.yaml --text-only
```

Run with OpenAI:

```bash
export OPENAI_API_KEY=your_api_key_here
ulysses --config config/ulysses.yaml
```

Provider setup is available inside the TUI with `F7` or `/setup providers`. It can save and activate:

- OpenAI API key: `https://api.openai.com/v1`, key env `OPENAI_API_KEY`
- OpenAI browser: Codex-managed ChatGPT login; model is discovered from authenticated `model/list`
- Kimi / Moonshot: `https://api.moonshot.ai/v1`, key env `KIMI_API_KEY`
- Local Ollama: `http://localhost:11434/v1`, no real API key required

Use an API-key provider by setting `llm.provider`, `llm.base_url`, and key settings in `config/ulysses.yaml` or with
`ULYSSES__...` environment overrides. Interactive browser login must be started from `/setup providers`.

## Configuration

The default config lives at `config/ulysses.yaml`.

Important sections:

- `llm`: provider, model, base URL, and API key environment variable.
- `audio`: microphone/speaker device selection and VAD timing.
- `wake_word`: wake-word behavior and thresholds.
- `sirina`: STT engine and TTS voice.
- `memory`: SQLite, FAISS, metadata paths, and retrieval settings.
- `context`: automatic session consolidation.
- `skills`: internet search and local command policy.
- `logging`: structured runtime and security audit logs.
- `prompt`: agent personality, inline instructions, and system prompt path.
- `privacy`: log redaction and memory retrieval controls.

The default prompt configures Ulysses as a concise Kali/Linux vulnerability assessor and penetration-testing assistant for authorized systems. It treats the machine running Ulysses as a protected host and emphasizes evidence-based findings, remediation guidance, and specialist coverage for XSS, IDOR/BOLA, authentication and authorization flaws, certificate/TLS issues, cloud security, and local Linux security.

When a concrete target is supplied, Ulysses proceeds with a safe, main-domain baseline instead of repeatedly asking broad planning questions. It asks only when authorization, credentials, intrusive or destructive activity, or target identity requires a decision. Multi-step requests are executed as a short sequence of tool operations and summarized after the relevant evidence has been collected.

Environment overrides use the `ULYSSES__` prefix. Example:

```bash
ULYSSES__LLM__PROVIDER=mock
ULYSSES__AUDIO__ENABLED=false
```

## Authorized Assessment Workflow

Ulysses is intended only for systems where the operator has explicit authorization to test. Providing a concrete target starts an external, unauthenticated, non-destructive baseline against the named host. Deeper authenticated, intrusive, exploit-verification, credential, or persistence-related testing requires explicit scope and authorization.

An assessment follows this lifecycle:

1. Create a timestamped project under `var/ulysses/projects/`.
2. Record the original request and project metadata under `artifacts/`.
3. Execute independent discovery, network, HTTP, TLS, fingerprinting, configuration, and vulnerability checks.
4. Store raw command output and intermediate evidence under `results/`.
5. Continue after non-fatal errors and attempt an available equivalent where practical.
6. Generate a focused helper under `scripts/` when a small local Python implementation can recover coverage.
7. When configured, propose one secure, resumable installation cycle for required local tools and continue incomplete checks afterward.
8. Correlate completed technical evidence, deduplicate observations, assign severity, and write the final customer report under `reports/`.

Operational failures remain available to the operator in internal project evidence. They are not represented as successful checks or customer findings.

### Assessment Project Layout

```text
var/ulysses/projects/<session>_<timestamp>_<target>/
|-- README.md
|-- scripts/       Generated helpers and recovery utilities
|-- artifacts/     Request, scope, metadata, and supporting inputs
|-- results/       Raw output and internal operational evidence
`-- reports/       Final customer-delivery Markdown reports
```

## Customer-Delivery Reports

Final assessment reports are classified **Confidential - Customer Delivery** and use stable finding identifiers. Reports include:

- Document control, report reference, target, assessment profile, issue date, status, and distribution classification.
- Executive Summary for decision makers.
- Management Summary with overall risk and governance priorities.
- Technical Summary and severity-count risk profile.
- Scope, engagement boundaries, methodology, and severity definitions.
- Findings register ordered Critical, High, Medium, Low, and Informational.
- Detailed findings with affected asset, evidence confidence, description, technical evidence or proof of concept, business impact, technical impact, actionable remediation, and objective retest criteria.
- Prioritized remediation roadmap, retest and closure requirements, technical evidence appendix, assumptions and limitations, and confidentiality notice.

Customer reports intentionally exclude missing-tool messages, allowlist denials, installation output, command failures, confirmation prompts, internal filesystem paths, and other operator diagnostics. Those records remain in the project's internal `results/` directory for traceability.

Report filenames contain `customer-vulnerability-assessment-report.md` and are available through `/downloads`.

## Privileged Commands And Credentials

Ulysses never asks for a sudo password in ordinary chat. When an approved operation requires privilege:

1. The agent submits the exact `sudo ...` command through `system_command`.
2. The TUI disables the normal composer and opens a dedicated masked password dialog.
3. The password is supplied to `sudo` over standard input and is not included in the transcript, model context, tool schema, session metadata, project evidence, or audit command arguments.
4. Cancelling the dialog cancels the pending privileged operation.

API keys and provider tokens belong in `~/.config/ulysses/env` or provider authentication storage. Do not place credentials in assessment prompts, YAML files, project artifacts, or normal chat messages.

## Slash Commands

Common commands inside the TUI:

```text
/new
/sessions
/switch <id>
/memory
/context
/forget <id>
/forget all
/skills
/config
/status
/reload
/voice on
/voice off
/mute
/theme
/theme list
/run <command>
/create-skill <name> <request>
/confirm [token]
/downloads
/copy selected
/copy all
/select on
/select off
/setup providers
/setup connectors
/autonomous on
/autonomous off
/export
/quit
```

### Complete Skill Creation

`/create-skill <name> <request>` starts the complete skill builder. Ulysses searches the internet for implementation and
security guidance, combines those results with the request and the configured model's knowledge, generates and validates
the Python implementation, and presents typed confirmation before installing executable code. After confirmation, the
manifest is enabled and the skill is loaded into the live registry without restarting Ulysses.

Use `/skills` to verify registration. Enabled skills are automatically included in the model's tool definitions, so they
can be selected from natural-language requests. `/reload` also reloads external skills from `skills.skills_dir`. The TUI
sidebar shows the active skill while research, generation, or execution is in progress.

Autonomous mode is explicit opt-in. When enabled, Ulysses runs a defensive host-monitoring cycle for the local system it is installed on. It logs every check output, detects suspicious evidence such as brute-force attempts and port-scan patterns, adapts check frequency when risk rises, writes a defensive report, and speaks that report when voice is enabled.

If configured to block attackers or install missing security apps, Ulysses plans those system-changing actions. It executes them autonomously only when `skills.command.godmode: true`; otherwise they are logged as planned-only actions.

## Security Model

Ulysses treats local command execution as a privileged capability.

- Commands are parsed and executed with `shell=False`.
- Allowed and denied commands are configured in `skills.command`.
- Risky commands require confirmation.
- High-risk commands can require typed confirmation.
- `bypass_confirmation_for_allowed_commands: true` is the default and skips prompts for allowlisted non-high-risk commands.
- Execution uses a configured working directory, timeout, environment allowlist, and output cap.
- Audit events are written under `var/ulysses/logs` by default.
- Sudo passwords are accepted only through a masked TUI dialog and are excluded from normal chat, model-visible tool schemas, session metadata, and audit arguments.
- Secret-bearing metadata keys are recursively redacted before persistence.
- API keys and provider tokens are expected to live in environment variables or provider authentication storage, not in YAML.

`skills.command.godmode: true` gives full local command access: it bypasses the allowlist, denylist, normal confirmation, high-risk typed confirmation, and permits shell control operators through `bash -lc`. It still uses the configured working directory, environment filtering, timeouts, output caps, and audit logging. Do not enable god mode unless you accept uncontrolled system access, including during autonomous operation.

## Privacy And Data

By default, Ulysses stores:

- Conversation sessions in SQLite.
- Semantic memory text and metadata in JSONL.
- Semantic vectors in FAISS.
- Runtime and security logs under `var/ulysses/logs`.

Use `/forget <memory_id>` to remove one memory item and `/forget all` to erase sessions and memory.

## Push To Talk

Press `F4` in the Textual TUI, speak, and pause when finished. Ulysses transcribes the utterance and submits it through
the same command and assessment path used by typed input. Press `F4` again or `Escape` to cancel an active recording.
Voice input remains available when spoken responses are disabled with `/voice off` or muted with `/mute`.

The Rich fallback supports the same one-shot flow through `/talk`. The Textual key can be supplemented with another
binding by setting `audio.push_to_talk_key` in `config/ulysses.yaml`; `F4` remains the standard binding.

## Audio Troubleshooting

List audio devices:

```bash
sirina audio-devices
```

Set device names or indexes in `config/ulysses.yaml`:

```yaml
audio:
  input_device: auto
  output_device: auto
```

For PipeWire:

```bash
systemctl --user status pipewire pipewire-pulse wireplumber
```

For PulseAudio:

```bash
pactl list short sources
pactl list short sinks
```

## Tests

Run the test suite:

```bash
pytest
```

Run Ruff:

```bash
ruff check .
```

## More Documentation

See `docs/ULYSSES.md` for deeper architecture notes, prompt behavior, context consolidation, voice flow, terminal shortcuts, and the threat model.
