from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from app.agent.core import AgentCore
from app.agent.tools.builtin import create_builtin_registry
from app.agent.types import ProviderStreamResult, ToolCall
from app.config import get_settings
from app.persistence.db import SessionLocal, init_db
from app.persistence.service import ChatStore


class _ReplProviderSequence:
    def __init__(self, codes: list[str]) -> None:
        self._codes = list(codes)
        self._idx = 0

    def stream_turn(self, **_kwargs: object) -> ProviderStreamResult:
        code = self._codes[self._idx] if self._idx < len(self._codes) else "print('done')"
        self._idx += 1
        return ProviderStreamResult(
            text="",
            thinking="planning",
            tool_calls=[
                ToolCall(
                    id=f"repl_call_{self._idx}_{uuid.uuid4().hex[:8]}",
                    name="repl_exec",
                    input={"code": code},
                )
            ],
            provider_state={"provider": "gemini", "mock": True},
        )


class _BashProviderSingle:
    def stream_turn(self, **_kwargs: object) -> ProviderStreamResult:
        return ProviderStreamResult(
            text="",
            thinking="planning",
            tool_calls=[
                ToolCall(
                    id=f"bash_call_{uuid.uuid4().hex[:8]}",
                    name="bash_exec",
                    input={"command": "pwd"},
                )
            ],
            provider_state={"provider": "gemini", "mock": True},
        )


@pytest.mark.asyncio
async def test_repl_state_persists_across_turns() -> None:
    await init_db()
    settings = replace(get_settings(), mock_llm=True, gemini_api_key=None, gemini_model="gemini/gemini-3-flash")

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()

    provider = _ReplProviderSequence(["x = 41", "print(x + 1)"])

    async with SessionLocal() as run_session_1:
        store_1 = ChatStore(run_session_1)
        core_1 = AgentCore(settings=settings, store=store_1, tools=create_builtin_registry())
        core_1._providers["gemini"] = provider  # type: ignore[assignment]

        events_1: list[dict] = []

        async def emit_1(event: dict) -> None:
            events_1.append(event)

        await core_1.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="first turn",
            emit=emit_1,
            max_iterations=1,
        )

    async with SessionLocal() as run_session_2:
        store_2 = ChatStore(run_session_2)
        core_2 = AgentCore(settings=settings, store=store_2, tools=create_builtin_registry())
        core_2._providers["gemini"] = provider  # type: ignore[assignment]

        events_2: list[dict] = []

        async def emit_2(event: dict) -> None:
            events_2.append(event)

        await core_2.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="second turn",
            emit=emit_2,
            max_iterations=1,
        )

    repl_stdout_events = [evt for evt in events_2 if evt.get("type") == "main_agent_repl_stdout"]
    assert repl_stdout_events
    assert "42" in str(repl_stdout_events[-1].get("content") or "")


@pytest.mark.asyncio
async def test_bash_exec_runs_as_provider_tool() -> None:
    await init_db()
    settings = replace(get_settings(), mock_llm=True, gemini_api_key=None, gemini_model="gemini/gemini-3-flash")

    async with SessionLocal() as session:
        store = ChatStore(session)
        thread = await store.create_thread()

    provider = _BashProviderSingle()

    async with SessionLocal() as run_session:
        store = ChatStore(run_session)
        core = AgentCore(settings=settings, store=store, tools=create_builtin_registry())
        core._providers["gemini"] = provider  # type: ignore[assignment]

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        await core.run_turn_stream(
            thread_id=thread.id,
            provider="gemini",
            user_message="run pwd",
            emit=emit,
            max_iterations=1,
        )

    tool_results = [evt for evt in events if evt.get("type") == "main_agent_tool_result"]
    assert tool_results
    last_result = tool_results[-1]
    assert last_result.get("tool_name") == "bash_exec"
    result_payload = last_result.get("result") if isinstance(last_result.get("result"), dict) else {}
    assert str(result_payload.get("status")) == "success"
    output = result_payload.get("output") if isinstance(result_payload.get("output"), dict) else {}
    assert output.get("command") == "pwd"
    assert output.get("returncode") == 0
