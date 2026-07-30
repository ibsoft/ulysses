from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

MessageHandler = Callable[[str, int, str], str]
EventHandler = Callable[[str], None]


@dataclass(frozen=True)
class ConnectorStatus:
    id: str
    label: str
    configured: bool
    connected: bool
    authorized_count: int = 0


class Connector(Protocol):
    id: str
    label: str

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> ConnectorStatus: ...
