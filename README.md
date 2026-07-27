# Ulysses

Ulysses is a local-first Linux AI voice agent built on Sirina. It combines a terminal UI, OpenAI-compatible LLM providers, local speech-to-text and text-to-speech, persistent sessions, semantic memory, skill execution, and command safety controls into one assistant runtime.

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

The installer creates:

```text
~/.ulysses/app
~/.ulysses/app/models
~/.ulysses/venv
~/.config/ulysses/ulysses.yaml
~/.config/ulysses/env
~/.local/bin/ulysses
```

Sirina model files are downloaded during install when they are missing or invalid.
Runtime state under `~/.ulysses/app/var/ulysses` starts empty for each install, including sessions, FAISS memory, metadata, and logs.

Set your API key in `~/.config/ulysses/env`:

```bash
OPENAI_API_KEY=your_api_key_here
```

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

Provider setup is available inside the TUI with `F7` or `/setup`. It can save and activate:

- OpenAI: `https://api.openai.com/v1`, key env `OPENAI_API_KEY`
- Kimi / Moonshot: `https://api.moonshot.ai/v1`, key env `KIMI_API_KEY`
- Local Ollama: `http://localhost:11434/v1`, no real API key required
- OAuth-compatible OpenAI-style providers: custom base URL and token env

Use an OpenAI-compatible provider by setting `llm.provider`, `llm.base_url`, and token/key settings in `config/ulysses.yaml` or with `ULYSSES__...` environment overrides.

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

Environment overrides use the `ULYSSES__` prefix. Example:

```bash
ULYSSES__LLM__PROVIDER=mock
ULYSSES__AUDIO__ENABLED=false
```

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
/voice on
/voice off
/mute
/theme
/theme list
/create-skill <name> <request>
/autonomous on
/autonomous off
/status
/export
/quit
```

Autonomous mode is explicit opt-in. When enabled, Ulysses periodically checks the current mission/session and may write a short report when it has a useful observation or next step.

## Security Model

Ulysses treats local command execution as a privileged capability.

- Commands are parsed and executed with `shell=False`.
- Allowed and denied commands are configured in `skills.command`.
- Risky commands require confirmation.
- High-risk commands can require typed confirmation.
- `bypass_confirmation_for_allowed_commands: true` is the default and skips prompts for allowlisted non-high-risk commands.
- Execution uses a configured working directory, timeout, environment allowlist, and output cap.
- Audit events are written under `var/ulysses/logs` by default.
- Secrets are expected to live in environment variables or provider authentication storage, not in YAML.

`skills.command.godmode: true` gives full local command access: it bypasses the allowlist, denylist, normal confirmation, high-risk typed confirmation, and permits shell control operators through `bash -lc`. It still uses the configured working directory, environment filtering, timeouts, output caps, and audit logging. Do not enable god mode unless you accept uncontrolled system access, including during autonomous operation.

## Privacy And Data

By default, Ulysses stores:

- Conversation sessions in SQLite.
- Semantic memory text and metadata in JSONL.
- Semantic vectors in FAISS.
- Runtime and security logs under `var/ulysses/logs`.

Use `/forget <memory_id>` to remove one memory item and `/forget all` to erase sessions and memory.

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
