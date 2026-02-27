from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.agent.providers.gemini import GeminiProvider


def _install_fake_google_genai(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chunks: list[dict[str, Any]],
    captured_kwargs: dict[str, Any],
    per_model_exception: dict[str, Exception] | None = None,
) -> None:
    per_model_exception = per_model_exception or {}

    class _Models:
        def generate_content_stream(self, **kwargs: Any):
            captured_kwargs.update(kwargs)
            model = str(kwargs.get("model") or "")
            if model in per_model_exception:
                raise per_model_exception[model]
            return list(chunks)

    class _Client:
        def __init__(self, *, api_key: str) -> None:
            captured_kwargs["api_key"] = api_key
            self.models = _Models()

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = _Client
    fake_genai.types = types.SimpleNamespace()

    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


def test_gemini_provider_requires_api_key_in_real_mode() -> None:
    provider = GeminiProvider(api_key=None, model="gemini/gemini-3.1-flash", mock_mode=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        provider.stream_turn(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            system_prompt="",
            on_thinking_token=lambda _token: None,
            on_text_token=lambda _token: None,
        )


def test_gemini_provider_stream_extracts_thinking_text_and_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "think-a", "thought": True},
                            {"text": "Hello "},
                            {
                                "function_call": {
                                    "id": "call_1",
                                    "name": "calc",
                                    "args": {"expression": "2+2"},
                                    "thought_signature": "sig-1",
                                }
                            },
                        ]
                    }
                }
            ]
        },
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "world"},
                        ]
                    }
                }
            ]
        },
    ]
    _install_fake_google_genai(monkeypatch, chunks=chunks, captured_kwargs=captured_kwargs)

    thinking_tokens: list[str] = []
    text_tokens: list[str] = []
    provider = GeminiProvider(
        api_key="test-key",
        model="gemini/gemini-3.1-flash",
        include_thoughts=True,
        reasoning_effort="high",
        mock_mode=False,
    )
    result = provider.stream_turn(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system_prompt="sys",
        on_thinking_token=lambda token: thinking_tokens.append(token),
        on_text_token=lambda token: text_tokens.append(token),
    )

    assert captured_kwargs["api_key"] == "test-key"
    assert captured_kwargs["model"] == "gemini/gemini-3.1-flash"
    assert captured_kwargs["config"]["system_instruction"] == "sys"
    assert captured_kwargs["config"]["thinking_config"]["include_thoughts"] is True

    assert result.text == "Hello world"
    assert result.thinking == "think-a"
    assert result.provider_state["provider"] == "gemini"
    assert result.provider_state["thinking_token_count"] > 0

    assert len(result.tool_calls) == 1
    tool_call = result.tool_calls[0]
    assert tool_call.id == "call_1"
    assert tool_call.name == "calc"
    assert tool_call.input == {"expression": "2+2"}
    assert tool_call.provider_specific_fields == {"thought_signature": "sig-1"}

    assert "".join(text_tokens) == "Hello world"
    assert "".join(thinking_tokens) == "think-a"


def test_gemini_provider_replays_signed_history_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "done"}]}}]},
    ]
    _install_fake_google_genai(monkeypatch, chunks=chunks, captured_kwargs=captured_kwargs)

    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-flash", mock_mode=False)
    provider.stream_turn(
        messages=[
            {"role": "user", "content": "compute"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "calc", "arguments": "{\"expression\":\"2+2\"}"},
                        "provider_specific_fields": {"thought_signature": "sig-real"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "calc",
                "content": "{\"value\":4}",
            },
        ],
        tools=[],
        system_prompt="sys",
        on_thinking_token=lambda _token: None,
        on_text_token=lambda _token: None,
    )

    contents = captured_kwargs["contents"]
    assert isinstance(contents, list)
    model_messages = [entry for entry in contents if entry.get("role") == "model"]
    assert model_messages

    function_parts = [
        part
        for message in model_messages
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("function_call"), dict)
    ]
    assert len(function_parts) == 1
    assert function_parts[0]["function_call"]["thought_signature"] == "sig-real"


def test_gemini_provider_downgrades_unsigned_history_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "done"}]}}]},
    ]
    _install_fake_google_genai(monkeypatch, chunks=chunks, captured_kwargs=captured_kwargs)

    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-flash", mock_mode=False)
    result = provider.stream_turn(
        messages=[
            {"role": "user", "content": "compute"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "calc", "arguments": "{\"expression\":\"2+2\"}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "calc",
                "content": "{\"value\":4}",
            },
        ],
        tools=[],
        system_prompt="",
        on_thinking_token=lambda _token: None,
        on_text_token=lambda _token: None,
    )

    contents = captured_kwargs["contents"]
    assert isinstance(contents, list)

    function_parts = [
        part
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("function_call"), dict)
    ]
    assert function_parts == []

    fallback_text_parts = [
        part.get("text", "")
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    assert any("[tool_call_without_thought_signature]" in value for value in fallback_text_parts)
    assert result.provider_state["unsigned_history_tool_call_count"] == 1


def test_gemini_provider_parses_first_json_object_from_concatenated_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "function_call": {
                                    "id": "call_1",
                                    "name": "calc",
                                    "args": "{\"expression\":\"2+2\"}{\"expression\":\"2+2\"}",
                                    "thought_signature": "sig-raw",
                                }
                            }
                        ]
                    }
                }
            ]
        }
    ]
    _install_fake_google_genai(monkeypatch, chunks=chunks, captured_kwargs=captured_kwargs)

    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-flash", mock_mode=False)
    result = provider.stream_turn(
        messages=[{"role": "user", "content": "compute"}],
        tools=[],
        system_prompt="",
        on_thinking_token=lambda _token: None,
        on_text_token=lambda _token: None,
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].input == {"expression": "2+2"}


def test_gemini_provider_falls_back_from_3_1_model_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
    ]
    _install_fake_google_genai(
        monkeypatch,
        chunks=chunks,
        captured_kwargs=captured_kwargs,
        per_model_exception={"gemini/gemini-3.1-pro": RuntimeError("404 model not found")},
    )

    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-pro", mock_mode=False)
    result = provider.stream_turn(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system_prompt="",
        on_thinking_token=lambda _token: None,
        on_text_token=lambda _token: None,
    )

    assert result.text == "ok"
    assert result.provider_state["model"] == "gemini/gemini-3-pro"
