# Ulysses

**By [CyberPhylax](https://www.cyberphylax.com)**

**Copyleft 2026 - Ioannis A. Bouhras <ioannis.bouhras@gmail.com>**

Ulysses is a modular local-first Linux AI voice agent built on Sirina. The package is split into replaceable adapters for audio, wake-word detection, Sirina STT/TTS, sessions, FAISS memory, LLM providers, skills, security policy, and the terminal UI.

## Architecture

```text
src/sirina_agent/
  main.py                  CLI entry point
  config/                  YAML plus ULYSSES__... environment overrides
  core/                    orchestration and memory injection
  audio/                   Sirina STT/TTS and wake-word adapters
  llm/                     OpenAI-compatible providers and OpenAI-Codex authentication
  memory/                  FAISS-backed semantic memory with metadata
  sessions/                SQLite conversation persistence
  security/                command policy, confirmation and audit execution
  skills/                  skill manifests, registry, built-ins
  subagents/               persistent subordinate-agent manager and isolated workspaces
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

## Persistent Sub-agents

Ulysses is the sole supervisor for subordinate agents. It may create one because the user asks for a persistent specialist,
or because a complex request benefits from independent bounded work. Users request the outcome in normal language; only
the parent model can invoke `subagent_create`, `subagent_update`, `subagent_delegate`, `subagent_jobs`, and
`subagent_delete`.
Before creation or delegation, the default system prompt requires a `subagent_jobs` lookup. Ulysses checks persistent
agents and active assignments, reuses a suitable specialist when one exists, and avoids duplicate active work. This adds
a tool call only to sub-agent workflows rather than spending context tokens on a full catalog during every normal turn.

Each agent persists under the configured `subagents.root_dir` with its own identity metadata, system prompt, isolated
workspace, general files directory, and task history. Every job records its request and state. Successful jobs also save a
Markdown completion report. Jobs interrupted by a restart are marked failed explicitly so Ulysses can decide whether to
delegate them again rather than silently losing work.

Delegation uses a bounded thread pool and returns a job ID immediately. The TUI therefore accepts further conversation
while jobs run. It collects newly completed and failed reports and posts a supervisor update automatically. If a user
turn is already in progress, the reports are injected into that parent response instead. Ulysses incorporates relevant
results, handles uncertainty, and marks reports delivered only after a successful parent response. The sidebar and `F5`
status include a `Delegated jobs` section showing which agent owns each recent task and whether it is queued, running,
completed, or failed. It also shows each job's granted skills and the skill currently executing. Active work is listed
first; complete history remains available through `subagent_jobs`.

```yaml
subagents:
  enabled: true
  root_dir: var/ulysses/subagents
  max_agents: 16
  max_concurrent_jobs: 4
  max_tool_rounds: 6
  max_file_chars: 200000
  delegable_skills: [internet_search]
  denied_skills: [system_command, create_skill, subagent_create, subagent_update, subagent_delegate, subagent_jobs, subagent_delete]
  allow_mcp: false
  allowed_risk_levels: [low, medium]
  max_skill_calls_per_job: 10
  max_skill_output_chars: 20000
```

Sub-agents use the provider and model active when their job starts. Every agent receives confined `workspace_list`,
`workspace_read`, and `workspace_write` tools. Additional capabilities pass through `SubagentSkillBroker` and require
three independent approvals: the skill must be globally delegable, persisted in the agent's allowlist, and granted to
that job. A job may narrow but never expand its agent policy. Existing agent records without `allowed_skills` remain
workspace-only after upgrade.

The broker resolves tools from Ulysses' live registry, enforces enabled state and risk policy, caps calls and output, and
passes only approved schemas into the sub-agent context. It records `skill-calls.jsonl` using skill names and result
metadata without argument values. Skills requesting confirmation are refused without exposing confirmation tokens; the
sub-agent reports the blocked requirement to Ulysses. Nested agent operations, command execution, sudo, credentials,
policy changes, and direct user replies remain unavailable. MCP delegation is disabled by default. Ulysses retains final
responsibility for authorization and the answer.

`subagent_update` safely changes future grants on an existing agent. Running jobs retain their immutable job-level grant
snapshot. `/reload` applies changes to the global delegation policy; thread-pool size changes take effect after restart.
Deletion is refused while jobs are active and otherwise requires typed confirmation.

### Testing Sub-agents

Use a disposable name such as `test_researcher`. These steps test registration, asynchronous delegation, automatic report
collection, workspace writes, persistence, isolation, and deletion.

#### 1. Verify Registration

Restart Ulysses, press `F6`, and verify these enabled skills are present:

```text
subagent_create
subagent_update
subagent_delegate
subagent_jobs
subagent_delete
```

Press `F5`. The status output should include `Sub-agents`, even when the count is zero. There is intentionally no direct
`/create-subagent` command: creation and delegation go through the supervising Ulysses model.

#### 2. Create and Delegate

Send this as one normal chat message:

```text
Create a persistent sub-agent named test_researcher. Its purpose is to summarize bounded technical notes. Give it a
concise specialist prompt, then assign it a background job to write workspace file check.txt containing
"sub-agent test successful" and report completion.
```

Ulysses should invoke `subagent_create`, then `subagent_delegate`. Delegation should return a `job_...` identifier quickly.
The composer should become available while the job is queued or running. Send an unrelated question immediately to verify
that the main conversation remains usable; the sub-agent uses a separate provider instance.

#### 3. Observe Completion

The sidebar and `F5` show agent, active-job, and completed-job counts followed by `Delegated jobs`. While work runs, verify
that it includes `[running] test_researcher` and the shortened assignment. When the job finishes, its state changes to
`[completed]` and Ulysses posts one concise supervisor update automatically. If a user response is already being composed,
the report is incorporated into that answer instead of producing a duplicate update.

Ask:

```text
Show all jobs and their status for test_researcher.
```

Ulysses should use `subagent_jobs` and show the job as `completed` or clearly report a persisted failure that can be retried.

#### 4. Verify Files and Persistence

For the default current-user installation, inspect the persistent files from another terminal:

```bash
find ~/.ulysses/app/var/ulysses/subagents/test_researcher -maxdepth 4 -type f -print
cat ~/.ulysses/app/var/ulysses/subagents/test_researcher/workspace/check.txt
```

Expected files include `agent.json`, `prompt.md`, task `job.json`, `request.md`, `response.md`, and `workspace/check.txt`.
The final command should print `sub-agent test successful` if the model followed the workspace-writing assignment.

Quit and restart Ulysses, then ask:

```text
List my persistent sub-agents and the latest job for test_researcher.
```

The same agent and task history should remain. An interrupted queued or running job is deliberately persisted as `failed`
on restart so Ulysses can report and retry it rather than silently losing it.

#### 5. Verify Workspace Isolation

Assign this bounded negative test:

```text
Ask test_researcher to attempt to write ../../ulysses-subagent-escape.txt using its workspace tool and report the result.
```

The write should be rejected as escaping the configured workspace. Confirm that no file was created:

```bash
test ! -e ~/.ulysses/app/var/ulysses/subagents/ulysses-subagent-escape.txt && echo "isolation passed"
```

#### 6. Verify Delegated Skill Policy

Ask:

```text
Update test_researcher so its allowed skills contain only internet_search. Then delegate a job that uses internet_search
to find the current official MCP Python SDK documentation and report the source URL. Grant only internet_search to the job.
```

Expected behavior:

1. `subagent_update` persists `allowed_skills: ["internet_search"]` in `agent.json`.
2. The new job persists `granted_skills: ["internet_search"]`; previous jobs remain unchanged.
3. `F5` and the sidebar show `Skills: internet_search` and, during the call, `Using: internet_search`.
4. `F6` labels `internet_search` as `Ulysses + sub-agents` and supervisor tools as `Ulysses only`.
5. The job directory contains `skill-calls.jsonl` without the search query value.

Negative test:

```text
Ask test_researcher to use system_command.
```

The tool is absent from the job schema and must not execute. Adding `system_command` to an agent or job grant is rejected
even if it is mistakenly added to `delegable_skills`, because supervisor-only denial is enforced separately.

#### 7. Delete the Test Agent

After all jobs finish, ask:

```text
Delete the persistent sub-agent test_researcher.
```

Ulysses displays a typed confirmation token. Submit `/confirm <token>` with that exact token. The agent directory should be
removed. Deletion while a job is queued or running must be refused.

#### 8. Run Automated Tests

From the development checkout:

```bash
.venv/bin/python -m pytest -q tests/agent/test_subagents.py
.venv/bin/python -m pytest -q
```

The focused suite verifies background completion, report handoff, persistence, path traversal rejection, workspace file
writes, and confirmed deletion. The full suite checks that the feature does not regress the rest of Ulysses.

Default skills:

- `internet_search`: ranked and deduplicated internet search with title, URL, snippet, and timestamp fields when
  available. Pass `query` for one search or `queries` for up to six independent searches in one call; results are grouped
  by query. Domain-discovery requests add targeted site and certificate-transparency search variants. Malformed model
  arguments trigger bounded internal correction attempts instead of exposing parser diagnostics to the operator.
- `system_command`: allowlisted local command execution with confirmation, typed confirmation for high-risk commands, timeouts, output caps, environment filtering and audit logs.
- `skills.command.bypass_confirmation_for_allowed_commands`: defaults to `true` and skips prompts for allowlisted non-high-risk commands.
- `skills.command.godmode`: off by default. When set to `true`, it gives full local command access. It bypasses the command allowlist, denylist, normal confirmation, high-risk typed confirmation, and permits shell control operators through `bash -lc`. It still uses the configured working directory, filtered environment, timeouts, output caps, and audit logging.
- For multi-step system inspection requests, Ulysses plans separate commands, stores every output as tool history, and then produces one combined summary from the results.
- `create_skill`: researches and generates complete local skills under `skills.skills_dir`. It requires typed confirmation before writing executable code, then enables and registers the skill live.

Sudo behavior:

- In normal mode, commands beginning with `sudo` are allowed only after typed confirmation.
- The Textual TUI opens a sudo password dialog at execution time.
- The Rich fallback prompts for the sudo password in the terminal.
- The password is passed directly to `sudo -S` and is not stored in config, logs, SQLite, FAISS, or skill metadata.
- Enabling Godmode through `/godmode on I ACCEPT UNRESTRICTED COMMAND EXECUTION` checks for an encrypted OS credential
  vault before changing state. Missing GNOME Keyring or SecretStorage prerequisites are reported with proposed install
  commands. After preflight, Ulysses asks once and stores the password in the encrypted OS vault for trusted sudo
  execution only.
- `/godmode off` deletes the cached sudo credential. Normal mode resumes masked sudo prompts for each requested command.

## Internet Search

`internet_search` is a network-enabled, read-only research skill. It queries bounded public search backends and returns
normalized records containing a title, HTTP(S) source URL, snippet, and timestamp when the backend provides one. Results
are ranked against the original request, deduplicated by URL, and grouped when several queries are submitted together.
Invalid relative or non-web result URLs are discarded.

### Arguments

The published tool schema accepts:

| Field | Type | Required | Behavior |
| --- | --- | --- | --- |
| `query` | string | One of `query` or `queries` | Executes one search. |
| `queries` | array of strings | One of `query` or `queries` | Executes up to six independent searches and groups the results. |
| `limit` | integer | No | Returns 1-10 results per query; the default is 5. |

Ulysses is prompted to use `queries` rather than inventing fields such as `query2`. If a provider still emits malformed
JSON, the orchestrator records a tool correction message internally and asks the provider to issue a valid replacement
call. Correction attempts are bounded. The original parser exception is not shown in normal chat, and ambiguous arguments
are never executed.

Example single-query request:

```text
Find the current official OWASP guidance for BOLA and summarize it with source links.
```

Example batched request:

```text
Find public subdomain and IP-address evidence for example.com and example.org using internet_search.
Group the sources and summarize the observations by domain.
```

For domain-discovery wording, the skill derives bounded site-search, DNS, and certificate-transparency query variants.
This is passive public-source research, not an exhaustive asset-discovery guarantee. A professional authorized assessment
should validate candidates using DNS resolution, certificate-transparency records, and approved enumeration tools before
treating them as confirmed assets.

### Search Failure Handling

Search providers are attempted through bounded fallbacks. Ulysses stops collecting once enough unique results are
available for a query. Backend exceptions and package warnings remain operator diagnostics and are not included in the
normal answer. A query with no usable sources returns a concise no-results outcome so the agent can continue with another
approved evidence source.

### Search Smoke Test

After installation or upgrade:

1. Restart Ulysses and press `F6`.
2. Confirm that `internet_search` is enabled.
3. Submit the batched domain example above.
4. Open `F5` while it runs and confirm `Using: internet_search`.
5. Confirm that the answer is grouped by domain and contains valid source links.
6. Confirm that no raw JSON parser exception, Python warning, or backend traceback appears in chat.

Developers can run the focused regression tests with:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/agent/test_skills.py tests/agent/test_integration.py
```

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

### Updates From GitHub Main

The installer writes `.ulysses-build.json` in the installed application with the source branch, source commit, tracked
`main` commit, repository URL, and installation timestamp. When enabled, startup checks compare the source commit actually
installed with `refs/heads/main` and compare the installed source branch with the highest remote version branch. This
prevents stale remote-tracking metadata from marking an older local release current. The check is bounded, read-only, and
does not modify the checkout.

The top header and the label directly below the sidebar logo always use the locally installed `source_branch` from build
metadata. They do not change when a remote check discovers a newer version. The remote latest branch is reported only in
update status, `/update`, `/status`, and update notifications. When an update is available or staged, the two local labels
append `(update)` without replacing the installed version number.

Use `/update` for a manual check. `/update install` refuses to proceed when remote `main` is current. When an update is
available, it clones the configured branch into `~/.ulysses/update-stage` without modifying the running application.
Exit and launch `ulysses` again; the launcher applies the staged installer with `--preserve-config` before opening SQLite
or other runtime files. This avoids live-file replacement and preserves the active config, runtime projects, reports,
sessions, memory, generated skills, connectors, logs, secrets, and models. A failed apply retains both the existing
installation and staged checkout for diagnosis or retry.

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

Set `OPENAI_API_KEY` in `~/.config/ulysses/env`, then run:

```bash
ulysses
```

Manual install:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv portaudio19-dev libsndfile1 ripgrep xclip
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[agent,dev]"
sirina download --group all
cp .env.example .env
```

The Linux installer preserves an existing `~/.config/ulysses/ulysses.yaml` by default, including the selected provider.
Pass `--replace-config` only to intentionally back up that file and restore repository defaults. `--preserve-config`
remains accepted for compatibility but is now the default behavior.

The Linux installer selects CUDA automatically when `nvidia-smi` reports a usable NVIDIA GPU, verifies that ONNX
Runtime exposes `CUDAExecutionProvider`, and falls back to CPU inference if verification fails. Set
`sirina.onnx_device` to `auto`, `cpu`, or `cuda` in the YAML config. The environment variables
`ULYSSES_ONNX_DEVICE=cpu` and `ULYSSES_ONNX_DEVICE=cuda` provide temporary installer/runtime overrides.

`openwakeword` currently depends on Linux `tflite-runtime` wheels that are not available for every Python version. Install the base agent first, then add wake-word support only on a compatible Python, usually Python 3.11:

```bash
python -m pip install -e ".[wakeword]"
```

Without that extra, Ulysses still runs text-only and Sirina VAD/push-to-talk style voice flows.

Set `OPENAI_API_KEY` in your shell or `.env` loader, or use `F7` / `/setup providers` inside the TUI. Provider setup supports OpenAI API keys, OpenAI-Codex login, Kimi / Moonshot, and local Ollama. Kimi defaults to model `kimi-k2.7-code` at `https://api.moonshot.ai/v1` with `KIMI_API_KEY`; Ollama defaults to `http://localhost:11434/v1` and does not require a real API key.
The preferred-name question appears only after the first successful provider setup on a new installation. Completion is
persisted under `tui.name_prompt_completed`, preventing later provider changes from displaying or speaking it again.
Startup greetings use a separate bounded request: at most 64 output tokens and the configurable
`llm.startup_greeting_timeout_seconds` timeout (10 seconds by default). If the provider is slower or unavailable, Ulysses
then selects a local fallback greeting; normal user requests retain the general `llm.timeout_seconds` value.
Operational external-network requests use a separate `llm.network_planning_timeout_seconds` value (15 seconds by
default). They request `system_command` on the first LLM call, execute only the first returned command, and return its
output without another LLM/tool round.
During execution, the TUI shows the complete command, an animated activity indicator, and live elapsed seconds. Ulysses
does not substitute scan flags or rewrite a network command after the LLM selects it.

Saved-report navigation is local and provider-independent. `show reports` lists reports newest first, `show report 2`
selects by list number, `show the latest report` selects the newest, and `show report for <target>` selects the newest
matching assessment. Within an active assessment, `show me the report` prefers that project's newest report; otherwise an
ambiguous request displays the list instead of guessing.

## Providers

Run `/setup providers` or press `F7` to open provider setup. The Textual dialog masks secret fields; the Rich fallback uses
password-style terminal prompts. Setup writes non-secret provider settings to YAML, writes submitted secrets to the adjacent
`env` file with mode `0600`, reloads that environment file, rebuilds the provider, and activates it immediately. Leaving a
secret field blank preserves its existing value.

Provider modes:

- `openai`: OpenAI-compatible HTTPS endpoint using the environment variable named by `api_key_env`.
- `openai_chatgpt`: OpenAI-only browser login managed by the Codex CLI.
- `kimi`: Moonshot's OpenAI-compatible endpoint, defaulting to `KIMI_API_KEY`.
- `ollama`: local OpenAI-compatible endpoint; defaults to the placeholder key `ollama` when no key is configured.
- `mock`: local development response provider, configured through YAML or environment override rather than the setup dialog.

### OpenAI-Codex Mode

OpenAI-Codex uses the Codex app-server's managed ChatGPT browser-authentication protocol. The Codex CLI must be installed
and available on `PATH`. Select **OpenAI-Codex** from `/setup providers`; Ulysses displays the authorization URL in a
selectable field with a **Copy login link** button. Open that link manually, sign in, then paste only the complete localhost
return URL into the masked callback field.

Ulysses accepts only the expected `http://localhost:<port>/auth/callback` origin returned for that login and requires both
the OAuth `code` and `state`. It sends the URL to the local callback listener without following redirects. The URL is never
added to chat, logs, YAML, or the Ulysses env file. Codex stores and refreshes its own OAuth credentials. Ulysses does not
extract an API key or send these credentials to the Chat Completions API.

After authentication, provider setup queries authenticated `model/list`, selects the current default visible model, and
stores its exact provider-returned `model` value. Codex service routing remains internal because its protocol does not
advertise a configurable base URL. The browser-provider form therefore hides model, URL, and API-key fields before login;
it saves the discovered model and leaves `base_url` empty rather than inventing a value. Completions run through ephemeral
Codex CLI sessions in an isolated temporary directory with a read-only sandbox and explicitly pass the discovered model ID.
Codex returns either response text or structured Ulysses tool requests; Ulysses remains responsible for executing those
tools under its command policy.

The resulting non-secret YAML configuration is:

```yaml
llm:
  provider: openai_chatgpt
  model: <value returned by model/list>
  base_url: ""
  timeout_seconds: 60
```

The installer discovers the Codex executable with `command -v` and records that result as `ULYSSES_CODEX_BIN` in the
protected environment file. Runtime resolution uses that value or the current `PATH`; Ulysses contains no editor- or
platform-specific Codex installation paths.

This flow is intentionally OpenAI-only. Ulysses does not accept pasted OAuth bearer tokens or implement generic provider
OAuth. OpenAI documents Sign in with ChatGPT as a Codex-managed login that links the ChatGPT identity to an API account and
creates an API key automatically. See [Codex CLI and Sign in with ChatGPT](https://help.openai.com/en/articles/11381614-api-codex-cli-and-sign-in-with-chatgpt)
and the [Codex app-server authentication protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md).

## MCP Servers

MCP support is an optional client adapter for external tools. It uses the stable 1.x official Python SDK and supports the
standard `stdio` and Streamable HTTP transports. It is disabled by default, and enabling it does not bypass the existing
skill registry, confirmation flow, command policy, audit logging, output limits, or TUI lifecycle.

### Configure A Server

Run `/setup mcp`. Select an existing server to edit or choose **Add server**. The setup form accepts:

- A stable server ID used in exported tool names.
- `stdio` command plus a JSON argument array, or a Streamable HTTP URL.
- Environment-variable names that may be passed to a local process.
- An optional bearer-token environment-variable name and masked token value for HTTP.
- An exact comma-separated tool allowlist; `*` explicitly permits the bounded discovered catalog.
- Risk level, timeout, enabled state, and whether every invocation requires confirmation.

Setup performs a live initialization and tool-discovery check before writing configuration. Tokens are written to the
adjacent protected `env` file with mode `0600`; they are never written to YAML. Leaving the token field blank preserves an
existing value. Configuration reload replaces prior dynamic registrations cleanly.

Example non-secret configuration:

```yaml
mcp:
  enabled: true
  allowed_stdio_commands: [python, python3, uv, uvx, node, npx, docker]
  artifacts_dir: var/ulysses/mcp/artifacts
  max_output_chars: 50000
  max_tools_per_server: 50
  max_description_chars: 500
  servers:
    - id: local_inventory
      enabled: true
      transport: stdio
      command: python3
      args: [/opt/company-mcp/server.py]
      environment_variables: []
      tool_allowlist: [search_assets, inspect_asset]
      risk_level: medium
      require_confirmation: true
      timeout_seconds: 60
    - id: ticketing
      enabled: true
      transport: streamable_http
      url: https://mcp.example.com/mcp
      bearer_token_env: TICKETING_MCP_TOKEN
      tool_allowlist: [search_tickets]
      risk_level: high
      require_confirmation: true
      timeout_seconds: 60
```

Discovered names are normalized and namespaced as `mcp__<server_id>__<tool_name>`, preventing collisions with built-in,
external, and other servers' tools. Use `F6` or `/mcp tools` to inspect registered names. Use `/mcp servers`, `F5`, or the
sidebar for state and counts; `/mcp reconnect <server_id>` repeats discovery after a server change.

### MCP Security Boundary

- `stdio` executables require a second allowlist in `mcp.allowed_stdio_commands`; arguments are arrays and never pass through a shell.
- Only selected environment variables are forwarded. Ulysses adds a minimal runtime environment required to launch the process.
- Streamable HTTP requires HTTPS, except for `localhost`, `127.0.0.1`, and `::1`; URL credentials and fragments are rejected.
- Remote tool descriptions, schemas, text, structured content, and resources are untrusted input, not agent instructions.
- Tool catalogs and descriptions are bounded, returned text is capped, and binary content is saved under the MCP artifact directory.
- Confirmation and typed high-risk confirmation are applied by Ulysses regardless of server-provided annotations.
- A failed server becomes offline or degraded without preventing other servers and built-in skills from operating.
- Sub-agents do not inherit MCP capabilities. Explicit delegation requires `subagents.allow_mcp`, exact policy grants,
  an allowed risk level, and a call that does not require confirmation; it is disabled by default.

MCP currently exposes server tools. Server resources, prompts, roots, sampling requests, and OAuth discovery are not
enabled by this adapter. For authenticated HTTP servers, provision a bearer token through the protected environment file.

### MCP Smoke Test

1. Start a trusted local MCP server whose launcher is listed in `mcp.allowed_stdio_commands`.
2. Run `/setup mcp`, enter an exact tool allowlist, and save after validation succeeds.
3. Run `/mcp servers` and confirm the server is `online`; run `/mcp tools` and verify namespaced tools only.
4. Ask Ulysses to perform a harmless operation exposed by that server and complete the confirmation prompt.
5. Stop the server, run `/mcp reconnect <server_id>`, and verify Ulysses remains usable while that server reports offline.

Development regression test:

```bash
.venv/bin/python -m pytest -q tests/agent/test_mcp.py
```

Protocol and transport background is available in the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and
[MCP security guidance](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices).

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

### Kokoro TTS Voice Selection

Sirina's TTS backend uses Kokoro voices. Select a Kokoro voice ID through the `sirina.tts_voice` configuration key:

```yaml
sirina:
  stt_engine: tdt
  tts_voice: am_michael
  onnx_device: auto
```

For the current-user installation, edit `~/.config/ulysses/ulysses.yaml` and restart Ulysses. Available voice IDs are:

| Κατηγορία | Voice IDs |
| --- | --- |
| Γυναικείες US | `af_alloy`, `af_aoede`, `af_bella`, `af_jessica`, `af_kore`, `af_nicole`, `af_nova`, `af_river`, `af_sarah`, `af_sky` |
| Γυναικείες UK | `bf_alice`, `bf_emma`, `bf_isabella`, `bf_lily` |
| Ανδρικές US | `am_adam`, `am_echo`, `am_eric`, `am_fenrir`, `am_liam`, `am_michael`, `am_onyx`, `am_puck` |
| Ανδρικές UK | `bm_daniel`, `bm_fable`, `bm_george`, `bm_lewis` |

Voice selection does not download missing models automatically at runtime. Install and validate the Kokoro/Sirina model
set first:

```bash
sirina download --group all
sirina check-models --group all
```

### ONNX CPU/CUDA Device Selection

`sirina.onnx_device` accepts `auto`, `cpu`, or `cuda`:

- `auto` detects a usable NVIDIA GPU through `nvidia-smi`, installs the CUDA runtime when available, and otherwise uses
  CPU inference.
- `cpu` forces `CPUExecutionProvider`.
- `cuda` requests `CUDAExecutionProvider` with CPU fallback.

During installation, CUDA selection is verified through ONNX Runtime. A failed verification removes the GPU package and
restores the CPU package. NVIDIA CUDA is currently supported; AMD/ROCm is not automatically configured. Override YAML
for one invocation with `ULYSSES_ONNX_DEVICE=auto|cpu|cuda`. The environment value takes precedence.

Check the installed runtime with:

```bash
~/.ulysses/venv/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

Local Sirina speech models use CUDA when `CUDAExecutionProvider` is present. Hosted LLM providers remain remote.

In the Textual TUI, press `F4`, speak, then pause to transcribe and submit the utterance. Press `F4` again or `Escape`
to cancel recording. `/talk` provides the same one-shot microphone flow in the Rich fallback. Push-to-talk input is
independent of `/voice off` and `/mute`, which control spoken responses.

## Slash Commands

`/new`, `/sessions`, `/switch <id>`, `/memory`, `/context`, `/forget <id>`, `/forget all`, `/skills`, `/config`, `/talk`, `/voice on`, `/voice off`, `/mute`, `/theme`, `/theme list`, `/setup providers`, `/setup connectors`, `/setup mcp`, `/mcp servers`, `/mcp tools`, `/mcp reconnect <server_id>`, `/create-skill <name> <request>`, `/autonomous on`, `/autonomous off`, `/***autonomous on`, `/update`, `/update install`, `/status`, `/export`, `/quit`.

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

`/setup connectors` opens the registered connector selector. Each connector owns its credentials, verification process,
transport, and authorization state, while `ConnectorManager` provides shared startup, replacement, shutdown, automatic
status aggregation, and source-aware routing. Connector status appears in the Textual sidebar and `/status` output.

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

The command composer keeps the latest 200 non-empty submissions for the current Ulysses process. Press Up to recall older
entries and Down to move toward newer entries. If text was present before history navigation, moving Down past the newest
entry restores that unfinished draft. Consecutive duplicate submissions are stored once; history is not written to a
separate plaintext history file.

- `Ctrl+U`: voice responses on/off
- `Ctrl+M`: mute
- `Ctrl+V`: paste from the system clipboard into the composer
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

`Ctrl+V` requires `xclip` or `xsel` on X11, `wl-clipboard` on Wayland, or PowerShell clipboard access under WSL.
`Ctrl+Shift+V` is handled by the terminal emulator and remains the fallback when no native clipboard reader is installed.
Multiline clipboard data is intercepted before the single-line composer can truncate it and is saved as a text attachment.

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
