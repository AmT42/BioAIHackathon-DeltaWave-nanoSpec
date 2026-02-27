from __future__ import annotations

import sys
import types

import pytest

from app.agent.providers.gemini import GeminiProvider


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
    captured_kwargs: dict[str, object] = {}

    chunk_1 = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                delta={
                    "reasoning_content": [{"text": "think-a"}],
                    "content": [{"type": "thought", "text": "think-b"}, {"text": "Hello "}],
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "calc", "arguments": "{\"expression\":\"2"},
                            "provider_specific_fields": {"thought_signature": "sig"},
                            "extra_content": {"provider_note": "x"},
                        }
                    ],
                }
            )
        ]
    )
    chunk_2 = types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                delta={
                    "reasoning_content": "think-c",
                    "content": "world",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"arguments": "+2\"}"},
                        }
                    ],
                }
            )
        ]
    )

    fake_litellm = types.SimpleNamespace(drop_params=False)

    def _completion(**kwargs: object) -> list[object]:
        captured_kwargs.update(kwargs)
        return [chunk_1, chunk_2]

    fake_litellm.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    thinking_tokens: list[str] = []
    text_tokens: list[str] = []
    provider = GeminiProvider(api_key="test-key", model="gemini/gemini-3.1-flash", mock_mode=False)
    result = provider.stream_turn(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system_prompt="sys",
        on_thinking_token=lambda token: thinking_tokens.append(token),
        on_text_token=lambda token: text_tokens.append(token),
    )

    assert fake_litellm.drop_params is True
    assert captured_kwargs["model"] == "gemini/gemini-3.1-flash"
    assert captured_kwargs["extra_body"]["extra_body"]["google"]["thinking_config"]["include_thoughts"] is True

    assert result.text == "Hello world"
    assert "think-a" in result.thinking
    assert "think-b" in result.thinking
    assert "think-c" in result.thinking

    assert len(result.tool_calls) == 1
    tool_call = result.tool_calls[0]
    assert tool_call.id == "call_1"
    assert tool_call.name == "calc"
    assert tool_call.input == {"expression": "2+2"}
    assert tool_call.provider_specific_fields == {"thought_signature": "sig"}
    assert tool_call.extra_content == {"provider_note": "x"}

    assert "".join(text_tokens) == "Hello world"
    assert result.provider_state["provider"] == "gemini"
