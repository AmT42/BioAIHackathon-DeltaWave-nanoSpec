from __future__ import annotations

from dataclasses import replace

import pytest

from app.agent.core import AgentCore
from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings
from app.persistence.db import SessionLocal, init_db
from app.persistence.models import MessageRole
from app.persistence.service import ChatStore


@pytest.mark.asyncio
async def test_core_emits_main_agent_events_and_persists_trace() -> None:
    await init_db()
    settings = replace(
        get_settings(),
        mock_llm=True,
        gemini_api_key=None,
        gemini_model="gemini/gemini-3-flash",
        openalex_api_key=None,
        epistemonikos_api_key=None,
    )

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()
        core = AgentCore(settings=settings, store=store, tools=create_science_registry(settings))

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        result = await core.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="search rapamycin evidence",
            emit=emit,
        )

        assert result["thread_id"] == thread.id
        assert events[0]["type"] == "main_agent_start"
        assert events[-1]["type"] == "main_agent_complete"
        assert any(event["type"] == "main_agent_segment_token" for event in events)
        assert any(event["type"] == "main_agent_thinking_token" for event in events)
        assert any(event["type"] == "main_agent_tool_start" for event in events)
        assert any(event["type"] == "main_agent_tool_result" for event in events)

        messages = await store.get_thread_messages(thread.id, skip=0, limit=200)
        assistant_messages = [msg for msg in messages if msg.role == MessageRole.ASSISTANT]
        assert assistant_messages

        last_assistant = assistant_messages[-1]
        trace = (last_assistant.message_metadata or {}).get("trace_v1")
        assert isinstance(trace, dict)
        assert trace.get("stream_mode") == "interleaved"
