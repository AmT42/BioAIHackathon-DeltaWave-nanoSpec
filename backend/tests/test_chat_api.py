from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect

from app.api.chat import _is_disconnect_error, _safe_send_json


class _WebSocketClosed:
    async def send_json(self, _payload: dict) -> None:
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class _WebSocketOk:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _WebSocketBoom:
    async def send_json(self, _payload: dict) -> None:
        raise ValueError("boom")


@pytest.mark.asyncio
async def test_safe_send_json_returns_false_on_closed_socket_runtime_error() -> None:
    websocket = _WebSocketClosed()
    assert await _safe_send_json(websocket, {"type": "main_agent_error"}) is False


@pytest.mark.asyncio
async def test_safe_send_json_sends_payload_when_socket_is_open() -> None:
    websocket = _WebSocketOk()
    sent = await _safe_send_json(websocket, {"type": "pong"})
    assert sent is True
    assert websocket.sent == [{"type": "pong"}]


@pytest.mark.asyncio
async def test_safe_send_json_re_raises_non_disconnect_errors() -> None:
    websocket = _WebSocketBoom()
    with pytest.raises(ValueError, match="boom"):
        await _safe_send_json(websocket, {"type": "main_agent_error"})


def test_is_disconnect_error_detects_websocket_disconnect() -> None:
    assert _is_disconnect_error(WebSocketDisconnect(code=1001))


def test_is_disconnect_error_detects_client_disconnected_by_name() -> None:
    class ClientDisconnected(Exception):
        pass

    assert _is_disconnect_error(ClientDisconnected())

