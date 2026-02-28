from __future__ import annotations

import base64
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
    types_module: Any | None = None,
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
    fake_genai.types = types_module if types_module is not None else types.SimpleNamespace()

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
    assert function_parts[0]["thought_signature"] == "sig-real"


def test_gemini_provider_replay_preserves_signature_even_with_typed_sdk_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "done"}]}}]},
    ]

    class _Part:
        @staticmethod
        def from_text(*, text: str) -> dict[str, Any]:
            return {"text": text}

        @staticmethod
        def from_function_call(*, name: str, args: dict[str, Any], id: str | None = None) -> dict[str, Any]:
            payload: dict[str, Any] = {"name": name, "args": args}
            if id:
                payload["id"] = id
            return {"function_call": payload}

    class _Content:
        def __init__(self, *, role: str, parts: list[Any]) -> None:
            self.role = role
            self.parts = parts

    class _GenerateContentConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_types = types.SimpleNamespace(
        Part=_Part,
        Content=_Content,
        GenerateContentConfig=_GenerateContentConfig,
    )

    _install_fake_google_genai(
        monkeypatch,
        chunks=chunks,
        captured_kwargs=captured_kwargs,
        types_module=fake_types,
    )

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
    assert len(function_parts) == 1
    assert function_parts[0]["thought_signature"] == "sig-real"


def test_gemini_provider_placeholder_mode_injects_leading_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "done"}]}}]},
    ]
    _install_fake_google_genai(monkeypatch, chunks=chunks, captured_kwargs=captured_kwargs)

    provider = GeminiProvider(
        api_key="test-key",
        model="gemini/gemini-3.1-flash",
        replay_signature_mode="placeholder",
        mock_mode=False,
    )
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
    function_calls = [
        part
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("function_call"), dict)
    ]
    assert len(function_calls) == 1
    function_call_part = function_calls[0]
    assert function_call_part["thought_signature"] == "skip_thought_signature_validator"
    assert result.provider_state["replay_calls_injected_placeholder_signature"] == 1
    assert result.provider_state["unsigned_history_tool_call_count"] == 0


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
    assert any("Historical tool call (calc)" in value for value in fallback_text_parts)
    assert any("Historical tool output (calc)" in value for value in fallback_text_parts)
    assert result.provider_state["unsigned_history_tool_call_count"] == 1
    assert result.provider_state["replay_steps_downgraded_missing_leading_signature"] == 1
    assert result.provider_state["replay_calls_dropped_unrecoverable_signature"] == 1


def test_gemini_provider_downgrades_parallel_step_when_leading_signature_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                        "id": "call_unsigned",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{\"q\":\"A\"}"},
                    },
                    {
                        "id": "call_signed",
                        "type": "function",
                        "function": {"name": "calc", "arguments": "{\"expression\":\"2+2\"}"},
                        "provider_specific_fields": {"thought_signature": "sig-real"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_unsigned",
                "name": "lookup",
                "content": "{\"hits\":1}",
            },
            {
                "role": "tool",
                "tool_call_id": "call_signed",
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
    model_messages = [entry for entry in contents if entry.get("role") == "model"]
    assert model_messages

    function_parts = [
        part
        for message in model_messages
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("function_call"), dict)
    ]
    assert function_parts == []

    function_response_parts = [
        part
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("function_response"), dict)
    ]
    assert function_response_parts == []

    fallback_text_parts = [
        part.get("text", "")
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    assert any("Historical tool output (lookup)" in value for value in fallback_text_parts)
    assert any("Historical tool output (calc)" in value for value in fallback_text_parts)
    assert result.provider_state["unsigned_history_tool_call_count"] == 2
    assert result.provider_state["replay_steps_downgraded_missing_leading_signature"] == 1
    assert result.provider_state["replay_calls_dropped_unrecoverable_signature"] == 2


def test_gemini_provider_keeps_unsigned_nonleading_calls_when_leading_is_signed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                        "id": "call_signed",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{\"q\":\"A\"}"},
                        "provider_specific_fields": {"thought_signature": "sig-real"},
                    },
                    {
                        "id": "call_unsigned",
                        "type": "function",
                        "function": {"name": "calc", "arguments": "{\"expression\":\"2+2\"}"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_signed",
                "name": "lookup",
                "content": "{\"hits\":1}",
            },
            {
                "role": "tool",
                "tool_call_id": "call_unsigned",
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
    assert len(function_parts) == 2
    assert function_parts[0]["function_call"]["name"] == "lookup"
    assert function_parts[0]["thought_signature"] == "sig-real"
    assert function_parts[1]["function_call"]["name"] == "calc"
    assert "thought_signature" not in function_parts[1]

    function_response_parts = [
        part["function_response"]
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("function_response"), dict)
    ]
    assert len(function_response_parts) == 2
    assert {item["name"] for item in function_response_parts} == {"lookup", "calc"}

    fallback_text_parts = [
        part.get("text", "")
        for message in contents
        for part in message.get("parts", [])
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    assert not any("Historical tool output (" in value for value in fallback_text_parts)
    assert result.provider_state["unsigned_history_tool_call_count"] == 0
    assert result.provider_state["replay_calls_kept_unsigned_nonleading"] == 1


def test_gemini_provider_extracts_bytes_thought_signature_from_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    raw_signature = b"\x01\x02signature"
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
                                    "args": {"expression": "2+2"},
                                    "thought_signature": raw_signature,
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
    expected_signature = base64.b64encode(raw_signature).decode("ascii")
    assert result.tool_calls[0].provider_specific_fields == {"thought_signature": expected_signature}


def test_gemini_provider_falls_back_to_dict_when_sdk_part_drops_signature() -> None:
    class _Part:
        @classmethod
        def from_function_call(cls, *, name: str, args: dict[str, Any], id: str | None = None) -> dict[str, Any]:
            payload: dict[str, Any] = {"name": name, "args": args}
            if id is not None:
                payload["id"] = id
            return {"function_call": payload}

    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-flash", mock_mode=False)
    part = provider._build_function_call_part(
        tool_name="calc",
        tool_input={"expression": "2+2"},
        tool_call_id="call_1",
        thought_signature="sig-raw",
        genai_types=types.SimpleNamespace(Part=_Part),
    )

    assert isinstance(part, dict)
    assert part["function_call"]["name"] == "calc"
    assert part["function_call"]["args"] == {"expression": "2+2"}
    assert part["function_call"]["id"] == "call_1"
    assert part["thought_signature"] == "sig-raw"


def test_gemini_provider_sanitizes_union_type_lists_in_tool_parameters() -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-flash", mock_mode=False)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "evidence_render_report",
                "description": "render report",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intervention": {"type": ["object", "string"]},
                    },
                },
            },
        }
    ]

    cfg = provider._build_tool_config(tools=tools)
    params = cfg[0]["function_declarations"][0]["parameters"]
    assert params["properties"]["intervention"]["type"] == "object"


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


def test_gemini_provider_generates_unique_fallback_tool_call_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}
    chunks = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "function_call": {
                                    "name": "calc",
                                    "args": {"expression": "2+2"},
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
    assert result.tool_calls[0].id.startswith("gemini_tool_")
    assert result.tool_calls[0].id != "gemini_tool_1"


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
