# Ulysses

**By [CyberPhylax](https://www.cyberphylax.com)**

**Copyleft 2026 - Ioannis A. Bouhras <ioannis.bouhras@gmail.com>**

Ulysses is a local-first Kali/Linux security assistant built on Sirina. It is designed for authorized vulnerability assessment, penetration testing, security-tool operation, evidence collection, remediation guidance, and defensive monitoring of the local host. It combines a terminal UI, OpenAI-compatible LLM providers, local speech-to-text and text-to-speech, persistent sessions, semantic memory, skill execution, and command safety controls into one assistant runtime.

The project also contains Sirina, the reusable local speech toolkit that powers Ulysses voice input and output.

## What It Does

- Runs as a text or voice AI agent from the `ulysses` command.
- Uses Sirina for microphone capture, VAD, STT, and TTS.
- Stores conversations in SQLite and long-term memory in FAISS plus JSONL metadata.
- Supports automatic context consolidation for long-running sessions.
- Provides a Textual/Rich terminal UI with sessions, memory, voice toggles, themes, and slash commands.
- Keeps an in-session composer history: Up recalls older submitted entries, Down moves forward, and returning past the
  newest entry restores the unfinished draft.
- Loads built-in and local skills through a registry.
- Runs single or batched internet searches with ranked, deduplicated, source-linked results and bounded automatic
  correction when a model emits malformed tool arguments.
- Creates persistent specialist sub-agents with isolated prompts, workspaces, files, and task histories; delegates jobs in
  the background and incorporates completed reports into later answers while the main chat remains available.
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

## Internet Search

The built-in `internet_search` skill performs source-linked web research. It supports one query or a batch of up to six
independent queries, ranks and deduplicates results, rejects unusable result URLs, and groups batched output by query.
When a model emits malformed tool-call JSON, Ulysses sends an internal correction result back to the provider and retries
within a fixed bound instead of exposing a parser exception or stopping immediately.

Ask naturally for one search:

```text
Search for the current official OWASP guidance for IDOR and summarize the primary source.
```

For related searches, ask Ulysses to batch them:

```text
Search for public subdomain and IP-address evidence for egt.gr and bizcore.gr. Group and summarize results by domain.
```

The tool schema uses `query` for one search and `queries` for up to six searches. Domain-discovery wording adds targeted
site and certificate-transparency search variants. Search results are passive public-source evidence and may be incomplete;
they do not prove that every subdomain or address has been identified. Authorized assessments should correlate search
results with DNS resolution, certificate-transparency data, and approved discovery tools.

Restart Ulysses after upgrading, press `F6`, and verify that `internet_search` is enabled. Then submit the batched example
above. The status view should briefly show `Using: internet_search`, and the final response should contain grouped source
links without raw JSON-parser or backend diagnostics.

## Persistent Sub-agents

Ulysses can create a persistent specialist when a user request or complex task benefits from independent work. Creation,
capability updates, delegation, inspection, and deletion are exposed only as Ulysses tools; there is no direct user slash
command that bypasses the supervisor. Ask naturally, for example:
`Create a persistent TLS specialist and have it review this evidence.`

Delegation is asynchronous. Ulysses returns after assigning the job, the composer remains available, and the sub-agent
runs against the currently configured provider. The TUI polls for completion or failure reports and posts a concise
supervisor update automatically; a report racing with a user message is instead injected into that answer. The sidebar and `F5`
status show counts plus a `Delegated jobs` list with each responsible agent, shortened task, and current state. Active jobs
are shown before recent completed or failed work. Granted skills and the skill currently executing appear with the job.
`F6` marks every registered skill as `Ulysses only` or `Ulysses + sub-agents` and lists five supervisor tools:

- `subagent_create`: create a named persistent agent with a purpose, prompt, and minimum skill allowlist.
- `subagent_update`: replace an existing agent's purpose, prompt, or skill allowlist.
- `subagent_delegate`: assign a bounded background job, optional context, and a subset of the agent's allowed skills.
- `subagent_jobs`: list agents and job states.
- `subagent_delete`: permanently remove an idle agent after typed confirmation.

Persistent state is stored under `var/ulysses/subagents/<agent>/` with `agent.json`, `prompt.md`, `workspace/`, `files/`,
and per-job request, metadata, response, and delegated-skill audit files under `tasks/`. Installer upgrades preserve this
runtime directory. Sub-agents report only to Ulysses. Workspace tools are always confined. Additional skills must be
allowed globally, persisted on the agent, and granted to the individual job. Existing agents migrate as workspace-only.
Sub-agents cannot create peers, run shell commands, approve confirmations, handle sudo or secrets, bypass command policy,
or answer the user directly.
Before creating or delegating, the default prompt requires Ulysses to call `subagent_jobs`, reuse a suitable persistent
agent, and avoid duplicate active assignments. This lookup occurs only for sub-agent workflows; the complete agent catalog
is not added to unrelated conversation turns.

### Sub-agent Smoke Test

Restart Ulysses after an upgrade, press `F6`, and confirm that `subagent_create`, `subagent_update`,
`subagent_delegate`, `subagent_jobs`, and `subagent_delete` are listed. Then send this request in normal chat:

```text
Create a persistent sub-agent named test_researcher. Its purpose is to summarize bounded technical notes. Give it a
concise specialist prompt, then assign it a background job to write workspace file check.txt containing
"sub-agent test successful" and report completion.
```

Expected behavior:

1. Ulysses creates the agent and returns control to the composer after delegation; no slash command is required.
2. `F5` shows one agent and a queued/running or completed job. You can continue chatting while the job runs.
3. Ulysses posts a concise completion update automatically. Ask `Show the jobs for test_researcher` to inspect history.
4. Restart Ulysses and ask `List my persistent sub-agents`; `test_researcher` and its prior job should still exist.
5. Ask `Delete test_researcher`. Enter `/confirm <token>` using the displayed token. Deletion is refused while a job is active.

To test delegated search, say:

```text
Update test_researcher so its allowed skills contain only internet_search. Then delegate a job that uses internet_search
to find the current official MCP Python SDK documentation and report the source URL. Grant only internet_search to the job.
```

`F5` should show `Skills: internet_search` and briefly `Using: internet_search`. `F6` should label `internet_search` as
`Ulysses + sub-agents`; supervisor and command skills remain `Ulysses only`.

For a development checkout, run the focused automated tests with:

```bash
.venv/bin/python -m pytest -q tests/agent/test_subagents.py
```

See [the extended sub-agent test guide](docs/ULYSSES.md#testing-sub-agents) for filesystem and isolation checks.

## Installation

These instructions install Ulysses for the current Linux user from
[github.com/ibsoft/ulysses](https://github.com/ibsoft/ulysses). The installer creates an isolated Python environment under
`~/.ulysses`, installs the application, downloads required voice models, and creates the `~/.local/bin/ulysses` launcher.
It does not require running Ulysses itself as root.

### 1. Install System Prerequisites

Ulysses requires Linux and Python 3.11 or newer. On Kali Linux, Ubuntu, Debian, or a derivative, run:

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv python3-dev build-essential \
  portaudio19-dev libsndfile1 ripgrep xclip
```

Use `wl-clipboard` instead of `xclip` for a Wayland-only desktop:

```bash
sudo apt install -y wl-clipboard
```

Confirm the Python version before continuing:

```bash
python3 --version
```

### 2. Clone The Repository

```bash
git clone https://github.com/ibsoft/ulysses.git
cd ulysses
```

### 3. Run The Installer

```bash
./scripts/install-ulysses-linux
```

The first installation can take several minutes because Python packages and local Sirina speech models are prepared.
When it completes, the installer prints the application, configuration, model, environment, and launcher paths.

The current-user installation contains:

```text
~/.ulysses/app/                         Installed application source
~/.ulysses/app/models/                  Local speech models
~/.ulysses/venv/                        Isolated Python environment
~/.config/ulysses/ulysses.yaml          Active configuration
~/.config/ulysses/env                   Provider secrets; mode 0600
~/.local/bin/ulysses                    Command launcher
```

### 4. Download And Verify All Sirina Models

The installer automatically downloads the complete Sirina `all` model group. This includes the ASR/STT assets used for
speech recognition and the TTS assets used for Kokoro voices. To explicitly download all models again, or resume after an
interrupted model download, run this single command:

```bash
~/.ulysses/venv/bin/sirina --model-dir "$HOME/.ulysses/app/models" download --group all
```

Verify every downloaded model and checksum:

```bash
~/.ulysses/venv/bin/sirina --model-dir "$HOME/.ulysses/app/models" check-models --group all
```

Both commands are safe to run again. Existing valid model files are reused, while missing or invalid files are downloaded
or reported. Use `--group asr` or `--group tts` only when you intentionally want one model family instead of the complete
installation.

### 5. Add The Launcher To PATH

Most Linux desktops already include `~/.local/bin`. If `command -v ulysses` returns nothing, run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add the same line to `~/.bashrc`, `~/.zshrc`, or the appropriate shell profile to make it permanent, then open a new
terminal or reload that profile.

### 6. Start Ulysses

```bash
ulysses
```

The installed launcher automatically loads `~/.config/ulysses/env`, selects the active configuration at
`~/.config/ulysses/ulysses.yaml`, and uses the installed model directory. Do not run the TUI with `sudo`.

If audio hardware is unavailable or not configured yet, start in text-only mode:

```bash
ulysses --text-only
```

### 7. Complete Initial Provider Setup

From the Ulysses command box, open provider setup:

```text
/setup providers
```

You can also press `F7`. Select one provider:

- **OpenAI API key:** enter the API key in the masked field. Ulysses stores it only in
  `~/.config/ulysses/env` and activates the provider immediately.
- **OpenAI browser:** requires the Codex CLI. Ulysses displays the real login URL; open it manually, sign in, and paste
  the complete localhost return URL into the masked callback field.
- **Kimi:** enter the Moonshot API key in the masked field.
- **Ollama:** use a running local Ollama server; no real API key is required.

Do not paste API keys, OAuth return URLs, sudo passwords, or other secrets into normal chat.

### 8. Verify The Installation

Inside Ulysses:

1. Press `F5` or enter `/status` and confirm the provider, memory, skills, and voice states.
2. Press `F6` or enter `/skills` and confirm the built-in skills are registered.
3. Send `Reply with: Ulysses is ready.` to verify the configured model.
4. Press `Ctrl+V` in the composer to verify clipboard paste. Use `Ctrl+Shift+V` as the terminal-native fallback.
5. Press `F4` to test push-to-talk after microphone access and speech models are available.

### Upgrade An Existing Installation

From the cloned repository:

```bash
git pull --ff-only
./scripts/install-ulysses-linux --preserve-config
```

The upgrade preserves runtime projects, reports, sessions, memory, generated skills, connector state, logs, and downloaded
models. `--preserve-config` retains the active configuration. Without it, the installer refreshes the active configuration
from the repository and keeps the previous file as a timestamped backup.

Developers can publish source changes into an existing installation without rebuilding its environment or models:

```bash
./scripts/install-ulysses-linux --sync-only --preserve-config
```

### Check For Updates From GitHub Main

Each installation records the source branch and Git commit from the repository's `main` branch. On startup, Ulysses
performs a bounded background `git ls-remote` check against the configured repository. It resolves the highest
version-named branch and shows it as the latest release branch in the TUI. Update availability is determined only by
comparing the installed `main` commit with remote `main`. If `main` has advanced, the sidebar and `/status` show the latest
release branch and that an update is available, and the transcript displays one concise notification.

The sidebar displays the version once, centered directly below the Ulysses logo. The remaining sidebar status shows only
the update state so the version is not duplicated.

Check manually from the command box:

```text
/update
```

Install the latest merged `main` branch:

```text
/update install
```

The updater first clones `main` into `~/.ulysses/update-stage` without changing the running application. Exit Ulysses and
run `ulysses` again; the launcher applies the staged version with `--preserve-config` before the new process opens SQLite,
memory, projects, or logs. It does not merge into the installed application or modify the active
`~/.config/ulysses/ulysses.yaml`. Assessment projects, reports, sessions, memory, generated skills, connector state, logs,
secrets, and downloaded models are preserved. `/update install` refuses to stage anything when remote `main` is current.

Update behavior is configurable:

```yaml
updates:
  enabled: true
  check_on_startup: true
  repository_url: https://github.com/ibsoft/ulysses.git
  branch: main
  metadata_path: .ulysses-build.json
  updater_path: scripts/update-ulysses-linux
  timeout_seconds: 10
  install_timeout_seconds: 1800
```

#### Test The Update Workflow

1. Restart Ulysses:

   ```bash
   ulysses
   ```

2. Confirm that `Ulysses <version-branch>` appears once, centered directly below the logo. The same version must not be
   repeated in the persistent sidebar status.
3. Enter `/update`. An up-to-date installation reports the latest version branch and abbreviated `main` commit. A newer
   remote `main` reports that an update is available and instructs you to run `/update install`.
4. Enter `/status` and confirm that the detailed output contains `Latest branch` and `Update` fields.
5. Before testing the first available update, record the active configuration checksum in another terminal:

   ```bash
   sha256sum "$HOME/.config/ulysses/ulysses.yaml"
   ```

6. Enter `/update install` only when an update is available. Wait until Ulysses reports that the update is staged, exit,
   and run `ulysses` again. The launcher applies the staged update before opening runtime databases. Run the checksum
   command again; it must be unchanged because the launcher always uses `--preserve-config`.
7. Confirm that existing projects, reports, sessions, generated skills, connector verification, and downloaded models are
   still present after restart.

Developers can run the focused update and sidebar tests from the repository checkout:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/agent/test_updates.py tests/agent/test_tui_push_to_talk.py
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

## MCP Servers

Ulysses can expose tools from Model Context Protocol servers through an optional, policy-controlled adapter. MCP is
disabled by default and does not change built-in skills, command policy, connectors, or sub-agent isolation. Configure a
server from the local console:

```text
/setup mcp
```

The setup dialog supports local `stdio` servers and remote Streamable HTTP servers. It validates connectivity before
saving, then discovers only the tools named in the server's explicit allowlist. Discovered tools use collision-resistant
names such as `mcp__asset_inventory__search`. `F6` lists them with normal skills; the sidebar, `F5`, and `/mcp servers`
show connection state and tool counts.

Useful commands:

```text
/mcp servers
/mcp tools
/mcp reconnect <server_id>
```

For `stdio`, the executable basename must also appear in `mcp.allowed_stdio_commands`. For Streamable HTTP, Ulysses
requires HTTPS except for loopback development endpoints. Bearer tokens are entered in a masked field and stored only in
`~/.config/ulysses/env`; YAML stores the environment-variable name. Tool descriptions and results are treated as
untrusted external data. Per-server confirmation policy, risk level, timeouts, catalog limits, output caps, and artifacts
remain enforced. One unavailable server is isolated and does not stop Ulysses or other MCP servers.

Sub-agents do not inherit MCP tools. Explicit MCP delegation additionally requires `subagents.allow_mcp: true`, an exact
global, agent, and job grant, an allowed risk level, and a tool call that does not request confirmation. It remains off by
default. Ulysses remains the supervisor and may use MCP output when composing the final answer.
See [the full MCP configuration and test guide](docs/ULYSSES.md#mcp-servers).

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

### Available Kokoro TTS Voices

Sirina uses Kokoro for these TTS voices. Set the Kokoro voice ID through `sirina.tts_voice` in `config/ulysses.yaml` or the
installed `~/.config/ulysses/ulysses.yaml`, then restart Ulysses:

```yaml
sirina:
  tts_voice: am_michael
```

| Κατηγορία | Voice IDs |
| --- | --- |
| Γυναικείες US | `af_alloy`, `af_aoede`, `af_bella`, `af_jessica`, `af_kore`, `af_nicole`, `af_nova`, `af_river`, `af_sarah`, `af_sky` |
| Γυναικείες UK | `bf_alice`, `bf_emma`, `bf_isabella`, `bf_lily` |
| Ανδρικές US | `am_adam`, `am_echo`, `am_eric`, `am_fenrir`, `am_liam`, `am_michael`, `am_onyx`, `am_puck` |
| Ανδρικές UK | `bm_daniel`, `bm_fable`, `bm_george`, `bm_lewis` |

The corresponding Kokoro/Sirina voice assets must be installed. Use `sirina download --group all` to download the
supported model set and `sirina check-models --group all` to verify local files.

Model lookup order:

1. `SIRINA_MODEL_DIR`
2. `models/` in this checkout
3. bundled metadata under `sirina.assets`
4. `../models` for nested checkouts
5. `~/.sirina/models`

Large `.onnx` and `.bin` files are intentionally not tracked by git.

## Run

Run the installed application:

```bash
ulysses
```

Run without microphone, STT, or TTS:

```bash
ulysses --text-only
```

For development, run text-only with the mock provider and checkout configuration:

```bash
ULYSSES__LLM__PROVIDER=mock ulysses --config config/ulysses.yaml --text-only
```

Provider setup is available inside the TUI with `F7` or `/setup providers`. It can save and activate:

- OpenAI API key: `https://api.openai.com/v1`, key env `OPENAI_API_KEY`
- OpenAI browser: Codex-managed ChatGPT login; model is discovered from authenticated `model/list`
- Kimi / Moonshot: `https://api.moonshot.ai/v1`, key env `KIMI_API_KEY`
- Local Ollama: `http://localhost:11434/v1`, no real API key required

In the composer, `Ctrl+V` reads the system clipboard through `xclip`/`xsel` on X11 or `wl-clipboard` on Wayland.
`Ctrl+Shift+V` remains the terminal emulator's native paste fallback. Multiline and large clipboard content is preserved
as a text attachment instead of being truncated into a single-line input.

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
- `mcp`: optional external tool servers, transport policy, allowlists, limits, and artifact storage.
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
/update
/update install
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
/setup mcp
/mcp servers
/mcp tools
/mcp reconnect <server_id>
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
- MCP is disabled by default; each server and tool must be enabled explicitly, and remote HTTP endpoints require TLS except on loopback.
- MCP metadata and results are untrusted input. Server tool catalogs and returned output are capped before entering model context.

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
