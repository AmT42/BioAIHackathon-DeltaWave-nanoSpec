from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from app.agent.core import AgentCore
from app.agent.tools.builtin import create_builtin_registry
from app.agent.types import ProviderStreamResult, ToolCall
from app.config import get_settings
from app.persistence.db import SessionLocal, init_db
from app.persistence.models import MessageRole
from app.persistence.service import ChatStore


@pytest.mark.asyncio
async def test_core_emits_main_agent_events_and_persists_trace() -> None:
    await init_db()
    settings = replace(get_settings(), mock_llm=True, gemini_api_key=None, gemini_model="gemini/gemini-3-flash")

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()
        core = AgentCore(settings=settings, store=store, tools=create_builtin_registry())

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        result = await core.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="hello",
            emit=emit,
        )

        assert result["thread_id"] == thread.id
        assert events[0]["type"] == "main_agent_start"
        assert events[-1]["type"] == "main_agent_complete"
        assert any(event["type"] == "main_agent_segment_token" for event in events)
        assert any(event["type"] == "main_agent_thinking_token" for event in events)

        messages = await store.get_thread_messages(thread.id, skip=0, limit=200)
        assistant_messages = [msg for msg in messages if msg.role == MessageRole.ASSISTANT]
        assert assistant_messages

        last_assistant = assistant_messages[-1]
        trace = (last_assistant.message_metadata or {}).get("trace_v1")
        assert isinstance(trace, dict)
        assert trace.get("stream_mode") == "interleaved"


@pytest.mark.asyncio
async def test_core_writes_limit_message_when_iterations_exhausted_without_text() -> None:
    await init_db()
    settings = replace(get_settings(), mock_llm=True, gemini_api_key=None, gemini_model="gemini/gemini-3-flash")

    class _ToolOnlyProvider:
        def __init__(self) -> None:
            self.calls = 0
            self._id_prefix = uuid.uuid4().hex[:8]

        def stream_turn(self, **_kwargs: object) -> ProviderStreamResult:
            self.calls += 1
            return ProviderStreamResult(
                text="",
                thinking="planning",
                tool_calls=[
                    ToolCall(
                        id=f"tool_loop_{self._id_prefix}_{self.calls}",
                        name="calc",
                        input={"expression": "2+2"},
                    )
                ],
                provider_state={"provider": "gemini", "mock": True},
            )

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()
        core = AgentCore(settings=settings, store=store, tools=create_builtin_registry())
        core._providers["gemini"] = _ToolOnlyProvider()  # type: ignore[assignment]

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        result = await core.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="loop tools",
            emit=emit,
            max_iterations=2,
        )

        assert "tool-iteration limit (2)" in result["content"]
        assert events[-1]["type"] == "main_agent_complete"
        assert "tool-iteration limit (2)" in str((events[-1].get("message") or {}).get("content", ""))

        messages = await store.get_thread_messages(thread.id, skip=0, limit=200)
        assistant_messages = [msg for msg in messages if msg.role == MessageRole.ASSISTANT]
        assert assistant_messages
        assert "tool-iteration limit (2)" in (assistant_messages[-1].content or "")
