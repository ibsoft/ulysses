import json

from sirina_agent.llm.providers import CodexProvider


def test_codex_provider_returns_ulysses_tool_call(tmp_path, monkeypatch):
    monkeypatch.setattr("sirina_agent.llm.providers.find_codex_cli", lambda: "/usr/bin/codex")

    def run(command, **kwargs):
        output_index = command.index("--output-last-message") + 1
        payload = {
            "content": None,
            "tool_calls": [{"name": "system_command", "arguments": '{"command":"whoami"}'}],
        }
        with open(command[output_index], "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("sirina_agent.llm.providers.subprocess.run", run)

    response = CodexProvider("gpt-5.3-codex").complete(
        [{"role": "user", "content": "who am I"}],
        tools=[{"type": "function", "function": {"name": "system_command", "parameters": {}}}],
    )

    call = response["choices"][0]["message"]["tool_calls"][0]
    assert call["function"] == {"name": "system_command", "arguments": '{"command":"whoami"}'}
