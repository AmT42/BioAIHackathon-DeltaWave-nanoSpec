from datetime import datetime

from app.agent.adapters import build_claude_messages, build_gemini_messages
from app.persistence.models import (
    ConversationEventKind,
    ConversationEventRole,
    MessageProviderFormat,
)
from app.persistence.service import CanonicalEventView


def _event(
    *,
    role,
    kind,
    content,
    tool_call_id=None,
    visible_to_model=True,
    message_provider_format=None,
    message_content_blocks=None,
):
    return CanonicalEventView(
        event_id="evt-test",
        thread_id="thread-test",
        role=role,
        kind=kind,
        position=1,
        created_at=datetime.utcnow(),
        content=content,
        tool_call_id=tool_call_id,
        visible_to_model=visible_to_model,
        message_id=None,
        message_provider_format=message_provider_format,
        message_content_blocks=message_content_blocks,
    )


def test_build_gemini_messages_orphan_tool_result_falls_back_to_assistant_text() -> None:
    events = [
        _event(
            role=ConversationEventRole.USER,
            kind=ConversationEventKind.TEXT,
            content={"type": "text", "text": "hello"},
        ),
        _event(
            role=ConversationEventRole.TOOL,
            kind=ConversationEventKind.TOOL_RESULT,
            content={
                "type": "tool_result",
                "tool_call_id": "missing",
                "status": "error",
                "error": {"message": "boom"},
            },
            tool_call_id="missing",
        ),
    ]

    messages = build_gemini_messages(events)
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1]["role"] == "assistant"
    assert "Historical tool output:" in messages[1]["content"]


def test_build_claude_messages_drops_orphan_tool_use_without_result() -> None:
    events = [
        _event(
            role=ConversationEventRole.ASSISTANT,
            kind=ConversationEventKind.TEXT,
            content={"type": "text", "text": ""},
            message_provider_format=MessageProviderFormat.CLAUDE_INTERLEAVED,
            message_content_blocks=[
                {"type": "thinking", "thinking": "plan"},
                {"type": "tool_use", "id": "call_1", "name": "calc", "input": {"expression": "2+2"}},
            ],
        ),
    ]

    messages = build_claude_messages(events)
    tool_use_blocks = [
        block
        for message in messages
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    assert tool_use_blocks == []


def test_build_gemini_messages_embedded_tool_use_dedupes_canonical_tool_call() -> None:
    events = [
        _event(
            role=ConversationEventRole.ASSISTANT,
            kind=ConversationEventKind.TEXT,
            content={"type": "text", "text": ""},
            message_provider_format=MessageProviderFormat.GEMINI_INTERLEAVED,
            message_content_blocks=[
                {"type": "thinking", "thinking": "plan"},
                {"type": "tool_use", "id": "embedded_1", "name": "calc", "input": {"expression": "2+2"}},
            ],
        ),
        _event(
            role=ConversationEventRole.ASSISTANT,
            kind=ConversationEventKind.TOOL_CALL,
            content={
                "type": "tool_call",
                "tool_call_id": "embedded_1",
                "tool_name": "calc",
                "input": {"expression": "2+2"},
            },
            tool_call_id="embedded_1",
        ),
    ]

    messages = build_gemini_messages(events)
    assistant_with_calls = [
        message
        for message in messages
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list)
    ]
    assert len(assistant_with_calls) == 1
    assert assistant_with_calls[0]["tool_calls"][0]["id"] == "embedded_1"


def test_build_gemini_messages_tool_call_keeps_provider_specific_metadata() -> None:
    events = [
        _event(
            role=ConversationEventRole.ASSISTANT,
            kind=ConversationEventKind.TOOL_CALL,
            content={
                "type": "tool_call",
                "tool_call_id": "call_1",
                "tool_name": "calc",
                "input": {"expression": "2+2"},
                "provider_specific_fields": {"thought_signature": "sig"},
                "extra_content": {"source": "gemini"},
            },
            tool_call_id="call_1",
        ),
        _event(
            role=ConversationEventRole.TOOL,
            kind=ConversationEventKind.TOOL_RESULT,
            content={
                "type": "tool_result",
                "tool_call_id": "call_1",
                "status": "success",
                "output": {"value": 4},
            },
            tool_call_id="call_1",
        ),
    ]

    messages = build_gemini_messages(events)
    assistant_call = next(
        message
        for message in messages
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list)
    )
    tool_call = assistant_call["tool_calls"][0]
    assert tool_call["provider_specific_fields"] == {"thought_signature": "sig"}
    assert tool_call["extra_content"] == {"source": "gemini"}
