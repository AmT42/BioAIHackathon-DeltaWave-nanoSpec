from __future__ import annotations

from urllib.error import HTTPError

import pytest

from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient


class _FakeResponse:
    def __init__(self, *, status: int = 200, headers: dict[str, str] | None = None, body: bytes = b"{}") -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_http_client_maps_404_to_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_404(req, timeout):
        raise HTTPError(req.full_url, 404, "not found", hdrs=None, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", _raise_404)
    client = SimpleHttpClient(timeout_seconds=1, max_retries=0)

    with pytest.raises(ToolExecutionError) as exc:
        client.get_json(url="https://example.org/missing")

    assert exc.value.code == "NOT_FOUND"


def test_http_client_maps_429_to_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_429(req, timeout):
        raise HTTPError(req.full_url, 429, "rate limit", hdrs={"Retry-After": "3"}, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", _raise_429)
    client = SimpleHttpClient(timeout_seconds=1, max_retries=0)

    with pytest.raises(ToolExecutionError) as exc:
        client.get_json(url="https://example.org/ratelimit")

    assert exc.value.code == "RATE_LIMIT"
    assert exc.value.retryable is True
    assert exc.value.details["retry"]["retry_after_seconds"] == 3.0


def test_http_client_retries_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def _flaky(req, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError(req.full_url, 503, "unavailable", hdrs=None, fp=None)
        return _FakeResponse(status=200, body=b'{"ok":true}')

    monkeypatch.setattr("urllib.request.urlopen", _flaky)
    client = SimpleHttpClient(timeout_seconds=1, max_retries=1)

    payload, _ = client.get_json(url="https://example.org/flaky")
    assert payload["ok"] is True
    assert calls["count"] == 2


def test_http_client_respects_retry_after_when_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def _flaky(req, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError(req.full_url, 503, "unavailable", hdrs={"Retry-After": "1"}, fp=None)
        return _FakeResponse(status=200, body=b'{"ok":true}')

    monkeypatch.setattr("urllib.request.urlopen", _flaky)
    monkeypatch.setattr("time.sleep", lambda value: sleeps.append(float(value)))
    client = SimpleHttpClient(timeout_seconds=1, max_retries=1)

    payload, _ = client.get_json(url="https://example.org/retry-after")
    assert payload["ok"] is True
    assert calls["count"] == 2
    assert sleeps and sleeps[0] >= 1.0


def test_http_client_get_json_non_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def _non_json(req, timeout):
        return _FakeResponse(status=200, body=b"not-json")

    monkeypatch.setattr("urllib.request.urlopen", _non_json)
    client = SimpleHttpClient(timeout_seconds=1, max_retries=0)

    with pytest.raises(ToolExecutionError) as exc:
        client.get_json(url="https://example.org/nonjson")

    assert exc.value.code == "UPSTREAM_ERROR"
