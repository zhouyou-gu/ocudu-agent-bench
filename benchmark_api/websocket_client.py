"""Minimal WebSocket request container for OCUDU command infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class WebSocketProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class WebSocketFrame:
    payload: dict[str, Any]


class WebSocketClient:
    """Small injectable client abstraction.

    The design-owned action module builds requests; this client only transports
    an already formed payload when a concrete runtime adapter is installed.
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def request(self, payload: dict[str, Any]) -> WebSocketFrame:
        return WebSocketFrame({"endpoint": self.endpoint, "request": payload, "status": "not_connected"})
