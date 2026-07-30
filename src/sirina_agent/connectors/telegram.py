from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

import httpx

from sirina_agent.config.models import TelegramConnectorConfig

from .base import ConnectorStatus, EventHandler, MessageHandler


class TelegramConnector:
    id = "telegram"
    label = "Telegram"

    def __init__(
        self,
        config: TelegramConnectorConfig,
        message_handler: MessageHandler,
        event_handler: EventHandler | None = None,
        token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.message_handler = message_handler
        self.event_handler = event_handler
        self._token = token if token is not None else os.environ.get(config.token_env, "")
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pairing_code: str | None = None
        self._pairing_expires_at = 0.0
        self._pairing_attempts_remaining = 0
        self._verified_chat_ids = self._load_verified_chat_ids()
        self.connected = False
        self.bot_username = ""

    @property
    def configured(self) -> bool:
        return bool(self.config.enabled and self._token)

    @property
    def verified_count(self) -> int:
        with self._lock:
            return len(self._verified_chat_ids)

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(self.id, self.label, self.configured, self.connected, self.verified_count)

    def validate(self) -> str:
        if not self._token:
            raise RuntimeError(f"Telegram bot token is missing from {self.config.token_env}.")
        result = self._api("getMe")
        username = str(result.get("username") or "").strip()
        if not username:
            raise RuntimeError("Telegram returned a bot without a username.")
        self.bot_username = username
        return username

    def begin_pairing(self) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._pairing_code = code
            self._pairing_expires_at = time.time() + self.config.pairing_code_ttl_seconds
            self._pairing_attempts_remaining = 5
        return code

    def start(self) -> None:
        if not self.configured or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ulysses-telegram", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._owns_client:
            self._client.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self.connected = False

    def process_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = message.get("text")
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or not isinstance(text, str):
            return
        text = text.strip()
        if not text:
            return
        if not self._is_verified(chat_id):
            self._process_verification(chat_id, text)
            return
        if text.lower() == "/start":
            self.send_message(chat_id, "Ulysses is connected. Send a request or command. Use /status to check the connector.")
            return
        if text.lower() == "/status":
            self.send_message(chat_id, "Ulysses Telegram connector is online and this chat is verified.")
            return
        if text.lower() == "/disconnect":
            self._remove_verified_chat(chat_id)
            self.send_message(chat_id, "This Telegram chat has been disconnected from Ulysses.")
            self._emit(f"Telegram chat {chat_id} disconnected.")
            return
        try:
            self._api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except RuntimeError:
            pass
        try:
            response = self.message_handler(self.id, chat_id, text)
        except Exception:
            self.send_message(chat_id, "Ulysses could not complete that request. Review the local console for details.")
            self._emit(f"Telegram request from chat {chat_id} failed.")
            return
        self.send_message(chat_id, response or "Request completed without a text response.")

    def send_message(self, chat_id: int, text: str) -> None:
        chunks = _split_message(text, self.config.max_message_chars)
        for chunk in chunks:
            self._api("sendMessage", {"chat_id": chat_id, "text": chunk})

    def _run(self) -> None:
        offset = 0
        initialized = False
        announced_error = False
        while not self._stop.is_set():
            try:
                if not self.connected:
                    self.validate()
                    if not initialized:
                        offset = self._initial_offset()
                        initialized = True
                    self.connected = True
                    announced_error = False
                    self._emit(f"Telegram connector online as @{self.bot_username}.")
                updates = self._api(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": int(self.config.polling_timeout_seconds),
                        "allowed_updates": ["message"],
                    },
                    timeout=self.config.polling_timeout_seconds + 5,
                )
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    offset = max(offset, update_id + 1)
                    self.process_update(update)
            except Exception:
                self.connected = False
                if not self._stop.is_set() and not announced_error:
                    self._emit("Telegram connector temporarily offline; retrying automatically.")
                    announced_error = True
                self._stop.wait(3)
        self.connected = False

    def _initial_offset(self) -> int:
        updates = self._api("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
        return max((int(update.get("update_id", 0)) + 1 for update in updates), default=0)

    def _process_verification(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        submitted = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "/verify" else ""
        with self._lock:
            expected = self._pairing_code
            valid = bool(
                submitted
                and expected
                and self._pairing_attempts_remaining > 0
                and time.time() <= self._pairing_expires_at
                and hmac.compare_digest(submitted, expected)
            )
            if valid:
                self._pairing_code = None
                self._pairing_expires_at = 0.0
                self._pairing_attempts_remaining = 0
                self._verified_chat_ids.add(chat_id)
            elif submitted and self._pairing_attempts_remaining > 0:
                self._pairing_attempts_remaining -= 1
                if self._pairing_attempts_remaining == 0:
                    self._pairing_code = None
                    self._pairing_expires_at = 0.0
        if valid:
            self._save_verified_chat_ids()
            self.send_message(chat_id, "Verification complete. This chat can now communicate with Ulysses.")
            self._emit(f"Telegram chat {chat_id} verified.")
        else:
            self.send_message(chat_id, "This chat is not verified. Start pairing from the local Ulysses console with /setup connectors.")

    def _is_verified(self, chat_id: int) -> bool:
        with self._lock:
            return chat_id in self._verified_chat_ids

    def _remove_verified_chat(self, chat_id: int) -> None:
        with self._lock:
            self._verified_chat_ids.discard(chat_id)
        self._save_verified_chat_ids()

    def _load_verified_chat_ids(self) -> set[int]:
        path = Path(self.config.state_path).expanduser()
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {int(value) for value in data.get("verified_chat_ids", [])}
        except (OSError, ValueError, TypeError):
            return set()

    def _save_verified_chat_ids(self) -> None:
        path = Path(self.config.state_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {"verified_chat_ids": sorted(self._verified_chat_ids)}
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def _api(self, method: str, payload: dict | None = None, timeout: float = 15.0):
        try:
            response = self._client.post(
                f"https://api.telegram.org/bot{self._token}/{method}",
                json=payload or {},
                timeout=timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Telegram API request failed.") from exc
        if not body.get("ok"):
            raise RuntimeError("Telegram rejected the connector request.")
        return body.get("result") or []

    def _emit(self, message: str) -> None:
        if self.event_handler:
            self.event_handler(message)


def _split_message(text: str, max_chars: int) -> list[str]:
    clean = text.strip()
    if not clean:
        return [""]
    chunks: list[str] = []
    while len(clean) > max_chars:
        boundary = clean.rfind("\n", 0, max_chars + 1)
        if boundary < max_chars // 2:
            boundary = clean.rfind(" ", 0, max_chars + 1)
        if boundary <= 0:
            boundary = max_chars
        chunks.append(clean[:boundary].rstrip())
        clean = clean[boundary:].lstrip()
    if clean:
        chunks.append(clean)
    return chunks
