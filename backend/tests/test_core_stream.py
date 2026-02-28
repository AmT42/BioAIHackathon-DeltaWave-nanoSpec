from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from app.agent import core as core_module
from app.agent.core import AgentCore
from app.agent.tools.builtin import create_builtin_registry
from app.agent.types import ProviderStreamResult, ToolCall
from app.config import get_settings
from app.persistence.db import SessionLocal, init_db
from app.persistence.models import MessageRole
from app.persistence.service import ChatStore


@pytest.mark.asyncio
async def test_core_uses_runtime_system_prompt_for_messages_and_provider_call() -> None:
    await init_db()
    settings = replace(get_settings(), mock_llm=True, gemini_api_key=None, gemini_model="gemini/gemini-3-flash")

    class _CaptureProvider:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream_turn(self, **kwargs: object) -> ProviderStreamResult:
            self.calls.append(kwargs)
            return ProviderStreamResult(
                text="ok",
                thinking="planning",
                tool_calls=[],
                provider_state={"provider": "gemini", "mock": True},
            )

    provider = _CaptureProvider()

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()
        core = AgentCore(settings=settings, store=store, tools=create_builtin_registry())
        core._providers["gemini"] = provider  # type: ignore[assignment]

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        await core.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="hello",
            emit=emit,
            max_iterations=1,
        )

    assert provider.calls
    first_call = provider.calls[0]
    system_prompt = str(first_call.get("system_prompt") or "")
    assert "Runtime Environment Brief" in system_prompt
    messages = first_call.get("messages")
    assert isinstance(messages, list) and messages
    first_message = messages[0] if isinstance(messages[0], dict) else {}
    assert first_message.get("role") == "system"
    assert "Runtime Environment Brief" in str(first_message.get("content") or "")


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
        assert not any(evt.get("type") == "main_agent_tool_start" for evt in events)
        assert not any(evt.get("type") == "main_agent_tool_result" for evt in events)

        messages = await store.get_thread_messages(thread.id, skip=0, limit=200)
        assistant_messages = [msg for msg in messages if msg.role == MessageRole.ASSISTANT]
        assert assistant_messages
        assert "tool-iteration limit (2)" in (assistant_messages[-1].content or "")
        trace = (assistant_messages[-1].message_metadata or {}).get("trace_v1")
        assert isinstance(trace, dict)
        blocks = trace.get("content_blocks_normalized")
        assert isinstance(blocks, list)
        calc_blocks = [block for block in blocks if str(block.get("name") or "") == "calc"]
        assert calc_blocks
        assert all(block.get("ui_visible") is False for block in calc_blocks)


@pytest.mark.asyncio
async def test_core_emits_reprompt_required_when_runtime_code_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    await init_db()
    settings = replace(
        get_settings(),
        mock_llm=True,
        gemini_api_key=None,
        gemini_model="gemini/gemini-3-flash",
        repl_controlled_reload_enabled=False,
    )

    class _SequenceProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream_turn(self, **_kwargs: object) -> ProviderStreamResult:
            self.calls += 1
            if self.calls == 1:
                return ProviderStreamResult(
                    text="Applying runtime patch.",
                    thinking="planning",
                    tool_calls=[
                        ToolCall(
                            id=f"repl_call_{uuid.uuid4().hex[:8]}",
                            name="repl_exec",
                            input={"code": "print('patched')"},
                        )
                    ],
                    provider_state={"provider": "gemini", "mock": True},
                )
            return ProviderStreamResult(
                text="Patch complete.",
                thinking="done",
                tool_calls=[],
                provider_state={"provider": "gemini", "mock": True},
            )

    status_calls = {"count": 0}

    def _fake_git_status(_repo_root) -> set[str]:
        status_calls["count"] += 1
        if status_calls["count"] <= 1:
            return set()
        return {"backend/app/agent/prompt.py"}

    monkeypatch.setattr(core_module, "_git_status_files", _fake_git_status)

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()
        core = AgentCore(settings=settings, store=store, tools=create_builtin_registry())
        core._providers["gemini"] = _SequenceProvider()  # type: ignore[assignment]

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        result = await core.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="apply patch",
            emit=emit,
            max_iterations=3,
        )

        reprompt_events = [event for event in events if event.get("type") == "main_agent_reprompt_required"]
        assert reprompt_events
        assert "runtime code was updated" in str(reprompt_events[-1].get("content") or "").lower()
        assert "Please send another prompt" in result["content"]

        messages = await store.get_thread_messages(thread.id, skip=0, limit=200)
        assistant_messages = [msg for msg in messages if msg.role == MessageRole.ASSISTANT]
        assert assistant_messages
        metadata = assistant_messages[-1].message_metadata or {}
        assert metadata.get("reprompt_required") is True
        assert metadata.get("runtime_code_updated") is True
