from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sirina_agent.config.models import UlyssesConfig

from .base import Connector, ConnectorStatus, EventHandler, MessageHandler
from .telegram import TelegramConnector

ConnectorFactory = Callable[[UlyssesConfig, MessageHandler, EventHandler | None], Connector | None]


@dataclass(frozen=True)
class ConnectorDefinition:
    id: str
    label: str
    description: str


_DEFINITIONS: dict[str, ConnectorDefinition] = {}
_FACTORIES: dict[str, ConnectorFactory] = {}


def register_connector(definition: ConnectorDefinition, factory: ConnectorFactory) -> None:
    _DEFINITIONS[definition.id] = definition
    _FACTORIES[definition.id] = factory


def connector_definitions() -> list[ConnectorDefinition]:
    return list(_DEFINITIONS.values())


class ConnectorManager:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    @classmethod
    def from_config(
        cls,
        config: UlyssesConfig,
        message_handler: MessageHandler,
        event_handler: EventHandler | None = None,
    ) -> ConnectorManager:
        manager = cls()
        for connector_id, factory in _FACTORIES.items():
            connector = factory(config, message_handler, event_handler)
            if connector is not None:
                manager._connectors[connector_id] = connector
        return manager

    def get(self, connector_id: str) -> Connector | None:
        return self._connectors.get(connector_id)

    def replace(self, connector: Connector) -> None:
        previous = self._connectors.get(connector.id)
        if previous is not None and previous is not connector:
            previous.stop()
        self._connectors[connector.id] = connector

    def remove(self, connector_id: str) -> None:
        connector = self._connectors.pop(connector_id, None)
        if connector is not None:
            connector.stop()

    def start_all(self) -> None:
        for connector in self._connectors.values():
            connector.start()

    def stop_all(self) -> None:
        for connector in list(self._connectors.values()):
            connector.stop()

    def statuses(self) -> list[ConnectorStatus]:
        return [connector.status() for connector in self._connectors.values()]

    def summary(self) -> str:
        statuses = self.statuses()
        if not statuses:
            return "disabled"
        return ", ".join(_format_status(status) for status in statuses)


def _format_status(status: ConnectorStatus) -> str:
    if not status.configured:
        state = "token missing"
    else:
        state = "online" if status.connected else "connecting"
    return f"{status.label}: {state} / verified={status.authorized_count}"


def _telegram_factory(
    config: UlyssesConfig,
    message_handler: MessageHandler,
    event_handler: EventHandler | None,
) -> Connector | None:
    telegram = config.connectors.telegram
    if not telegram.enabled:
        return None
    return TelegramConnector(telegram, message_handler, event_handler)


register_connector(
    ConnectorDefinition("telegram", "Telegram", "Verified direct messages through a Telegram bot."),
    _telegram_factory,
)
