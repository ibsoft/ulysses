import json

import yaml

from sirina_agent.config.models import TelegramConnectorConfig, UlyssesConfig
from sirina_agent.connectors.base import ConnectorStatus
from sirina_agent.connectors.registry import ConnectorManager
from sirina_agent.connectors.setup import TelegramSetup, apply_telegram_setup
from sirina_agent.connectors.telegram import TelegramConnector, _split_message


class FakeResponse:
    def __init__(self, result):
        self.result = result

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": self.result}


class FakeTelegramClient:
    def __init__(self):
        self.requests = []

    def post(self, url, json, timeout):
        method = url.rsplit("/", 1)[-1]
        self.requests.append((method, json, timeout))
        if method == "getMe":
            return FakeResponse({"username": "ulysses_test_bot"})
        return FakeResponse([] if method == "getUpdates" else {"message_id": 1})


def test_telegram_setup_keeps_token_out_of_yaml(tmp_path):
    config_path = tmp_path / "config" / "ulysses.yaml"

    apply_telegram_setup(UlyssesConfig(), config_path, TelegramSetup(True, "123:secret-token"))

    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    env_path = config_path.parent / "env"
    assert config_data["connectors"]["telegram"]["enabled"] is True
    assert "secret-token" not in config_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=123:secret-token" in env_path.read_text(encoding="utf-8")
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_unverified_chat_cannot_call_agent_and_pairing_is_persisted(tmp_path, monkeypatch):
    calls = []
    client = FakeTelegramClient()
    config = TelegramConnectorConfig(enabled=True, state_path=tmp_path / "telegram.json")
    connector = TelegramConnector(
        config,
        lambda connector_id, chat_id, text: calls.append((connector_id, chat_id, text)) or "done",
        token="token",
        client=client,
    )
    monkeypatch.setattr("sirina_agent.connectors.telegram.secrets.randbelow", lambda limit: 123456)

    connector.process_update({"message": {"chat": {"id": 42}, "text": "run uptime"}})
    assert calls == []

    assert connector.begin_pairing() == "123456"
    connector.process_update({"message": {"chat": {"id": 42}, "text": "/verify 123456"}})
    connector.process_update({"message": {"chat": {"id": 42}, "text": "run uptime"}})

    assert calls == [("telegram", 42, "run uptime")]
    assert connector.verified_count == 1
    assert json.loads(config.state_path.read_text(encoding="utf-8")) == {"verified_chat_ids": [42]}
    assert config.state_path.stat().st_mode & 0o777 == 0o600
    sent = [payload["text"] for method, payload, _ in client.requests if method == "sendMessage"]
    assert any("not verified" in text for text in sent)
    assert any("Verification complete" in text for text in sent)
    assert "done" in sent


def test_pairing_code_can_only_verify_one_chat(tmp_path, monkeypatch):
    client = FakeTelegramClient()
    config = TelegramConnectorConfig(enabled=True, state_path=tmp_path / "telegram.json")
    connector = TelegramConnector(config, lambda connector_id, chat_id, text: "done", token="token", client=client)
    monkeypatch.setattr("sirina_agent.connectors.telegram.secrets.randbelow", lambda limit: 7)
    connector.begin_pairing()

    connector.process_update({"message": {"chat": {"id": 1}, "text": "/verify 000007"}})
    connector.process_update({"message": {"chat": {"id": 2}, "text": "/verify 000007"}})

    assert connector.verified_count == 1
    assert json.loads(config.state_path.read_text(encoding="utf-8"))["verified_chat_ids"] == [1]


def test_pairing_code_is_invalidated_after_five_failed_attempts(tmp_path, monkeypatch):
    client = FakeTelegramClient()
    config = TelegramConnectorConfig(enabled=True, state_path=tmp_path / "telegram.json")
    connector = TelegramConnector(config, lambda connector_id, chat_id, text: "done", token="token", client=client)
    monkeypatch.setattr("sirina_agent.connectors.telegram.secrets.randbelow", lambda limit: 123456)
    connector.begin_pairing()

    for chat_id in range(1, 6):
        connector.process_update({"message": {"chat": {"id": chat_id}, "text": "/verify 000000"}})
    connector.process_update({"message": {"chat": {"id": 99}, "text": "/verify 123456"}})

    assert connector.verified_count == 0


def test_connector_validates_bot_without_exposing_token():
    client = FakeTelegramClient()
    connector = TelegramConnector(
        TelegramConnectorConfig(enabled=True),
        lambda connector_id, chat_id, text: "done",
        token="123:secret-token",
        client=client,
    )

    assert connector.validate() == "ulysses_test_bot"
    assert connector.bot_username == "ulysses_test_bot"


def test_telegram_messages_split_at_readable_boundaries():
    chunks = _split_message("first line\n" + "x" * 20, 12)

    assert chunks == ["first line", "xxxxxxxxxxxx", "xxxxxxxx"]
    assert all(len(chunk) <= 12 for chunk in chunks)


def test_connector_manager_supports_multiple_connector_lifecycles():
    events = []

    class FakeConnector:
        def __init__(self, connector_id, label):
            self.id = connector_id
            self.label = label
            self.connected = False

        def start(self):
            self.connected = True
            events.append((self.id, "start"))

        def stop(self):
            self.connected = False
            events.append((self.id, "stop"))

        def status(self):
            return ConnectorStatus(self.id, self.label, True, self.connected, 1)

    manager = ConnectorManager()
    manager.replace(FakeConnector("telegram", "Telegram"))
    manager.replace(FakeConnector("future_chat", "Future Chat"))

    manager.start_all()

    assert len(manager.statuses()) == 2
    assert "Telegram: online" in manager.summary()
    assert "Future Chat: online" in manager.summary()

    manager.stop_all()
    assert events == [
        ("telegram", "start"),
        ("future_chat", "start"),
        ("telegram", "stop"),
        ("future_chat", "stop"),
    ]
