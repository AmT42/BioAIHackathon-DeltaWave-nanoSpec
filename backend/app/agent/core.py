from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from app.agent.adapters import build_gemini_openai_messages
from app.agent.providers import GeminiProvider
from app.agent.prompt import DEFAULT_SYSTEM_PROMPT
from app.agent.tools.context import ToolContext
from app.agent.tools.registry import ToolRegistry
from app.agent.types import ToolCall
from app.config import Settings
from app.persistence.models import (
    ConversationEventKind,
    ConversationEventRole,
    Message,
    MessageProviderFormat,
    MessageRole,
)
from app.persistence.service import ChatStore
from app.run_logging import (
    build_llm_request_record,
    write_llm_io_files,
    write_tool_io_file,
)
from app.trace_normalizer import build_trace_v1

ProviderName = Literal["gemini"]
Emitter = Callable[[dict[str, Any]], Awaitable[None]]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _thinking_title(text: str, max_words: int = 10) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    words = stripped.replace("\n", " ").split()
    if not words:
        return None
    title = " ".join(words[:max_words]).strip()
    if len(words) > max_words:
        title += "..."
    return title


class AgentCore:
    def __init__(self, *, settings: Settings, store: ChatStore, tools: ToolRegistry) -> None:
        self.settings = settings
        self.store = store
        self.tools = tools
        self._providers = {
            "gemini": GeminiProvider(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                reasoning_effort=settings.gemini_reasoning_effort,
                include_thoughts=settings.gemini_include_thoughts,
                thinking_budget=settings.gemini_thinking_budget,
                mock_mode=settings.mock_llm,
            ),
        }

    async def run_turn_stream(
        self,
        *,
        thread_id: str,
        provider: ProviderName,
        user_message: str,
        emit: Emitter,
        run_id: str | None = None,
        max_iterations: int = 50,
    ) -> dict[str, Any]:
        if provider not in self._providers:
            raise ValueError(f"Unsupported provider '{provider}'")

        run_id = run_id or uuid.uuid4().hex

        await emit(
            {
                "type": "main_agent_start",
                "thread_id": thread_id,
                "run_id": run_id,
                "message": "Processing your message...",
            }
        )

        await self.store.create_message(
            thread_id=thread_id,
            role=MessageRole.USER,
            content=user_message,
            record_text_event=True,
        )

        user_msg_index = await self.store.count_user_messages(thread_id)

        provider_client = self._providers[provider]
        provider_format = MessageProviderFormat.GEMINI_INTERLEAVED

        segment_counter = 0
        request_index = 0
        tool_call_index = 0
        final_text = ""

        collected_blocks: list[dict[str, Any]] = []
        last_assistant_message_id: str | None = None
        iteration_limit_exhausted = True

        for _ in range(max_iterations):
            request_index += 1

            canonical_events = await self.store.get_canonical_events(thread_id)
            provider_messages = build_gemini_openai_messages(canonical_events, system_prompt=DEFAULT_SYSTEM_PROMPT)
            tool_schemas = self.tools.openai_schemas()

            request_payload: dict[str, Any] = {
                "model": self.settings.gemini_model,
                "messages": provider_messages,
                "stream": True,
                "reasoning_effort": self.settings.gemini_reasoning_effort,
                "thinking_config": {
                    "include_thoughts": self.settings.gemini_include_thoughts,
                    "thinking_budget": self.settings.gemini_thinking_budget,
                },
                "tools": tool_schemas,
                "tool_choice": "auto" if tool_schemas else None,
            }
            if request_payload.get("tool_choice") is None:
                request_payload.pop("tool_choice", None)

            request_record = build_llm_request_record(
                function_name="GeminiProvider.stream_turn",
                provider="gemini",
                model=self.settings.gemini_model,
                raw_payload=request_payload,
            )

            thinking_tokens: list[str] = []
            assistant_tokens: list[str] = []
            thinking_segment: int | None = None
            assistant_segment: int | None = None

            streaming_assistant = await self.store.create_message(
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                content=None,
                provider_format=provider_format,
                content_blocks=None,
                message_metadata={
                    "provider": provider,
                    "model": self.settings.gemini_model,
                    "run_id": run_id,
                    "request_index": request_index,
                    "stream_mode": "interleaved",
                },
                record_text_event=False,
            )
            last_assistant_message_id = streaming_assistant.id

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            sentinel = {"type": "__done__"}

            async def _pump() -> None:
                while True:
                    item = await queue.get()
                    if item.get("type") == "__done__":
                        break
                    await emit(item)

            pump_task = asyncio.create_task(_pump())

            def _next_segment() -> int:
                nonlocal segment_counter
                idx = segment_counter
                segment_counter += 1
                return idx

            def on_thinking_token(token: str) -> None:
                nonlocal thinking_segment
                if not token:
                    return
                if thinking_segment is None:
                    thinking_segment = _next_segment()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "main_agent_thinking_start",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": thinking_segment,
                        },
                    )
                thinking_tokens.append(token)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "main_agent_thinking_token",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": thinking_segment,
                        "token": token,
                    },
                )

            def on_text_token(token: str) -> None:
                nonlocal assistant_segment
                if not token:
                    return
                if assistant_segment is None:
                    assistant_segment = _next_segment()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "main_agent_segment_start",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": assistant_segment,
                            "role": "assistant",
                            "message_id": streaming_assistant.id,
                        },
                    )
                assistant_tokens.append(token)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "main_agent_segment_token",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": assistant_segment,
                        "token": token,
                    },
                )

            stream_error: Exception | None = None
            stream_result = None
            try:
                stream_result = await asyncio.to_thread(
                    provider_client.stream_turn,
                    messages=provider_messages,
                    tools=tool_schemas,
                    system_prompt=DEFAULT_SYSTEM_PROMPT,
                    on_thinking_token=on_thinking_token,
                    on_text_token=on_text_token,
                )
            except Exception as exc:
                stream_error = exc
            finally:
                queue.put_nowait(sentinel)
                await pump_task

            if stream_error is not None:
                write_llm_io_files(
                    thread_id=thread_id,
                    user_index=user_msg_index,
                    request_index=request_index,
                    request_record=request_record,
                    answer_json={
                        "error": str(stream_error),
                        "error_type": type(stream_error).__name__,
                    },
                    answer_text=None,
                )
                raise stream_error

            assert stream_result is not None

            normalized_tool_calls: list[ToolCall] = []
            for tc in stream_result.tool_calls:
                tool_use_id = tc.id or f"tool_{uuid.uuid4().hex[:10]}"
                normalized_tool_calls.append(
                    ToolCall(
                        id=str(tool_use_id),
                        name=tc.name,
                        input=tc.input,
                        provider_specific_fields=tc.provider_specific_fields,
                        extra_content=tc.extra_content,
                    )
                )

            thinking_text = stream_result.thinking.strip() or "".join(thinking_tokens).strip()
            assistant_text = stream_result.text.strip() or "".join(assistant_tokens).strip()
            if assistant_text:
                final_text = assistant_text

            if thinking_text and thinking_segment is None:
                thinking_segment = _next_segment()
                await emit(
                    {
                        "type": "main_agent_thinking_start",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": thinking_segment,
                    }
                )
                if not thinking_tokens:
                    await emit(
                        {
                            "type": "main_agent_thinking_token",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": thinking_segment,
                            "token": thinking_text,
                        }
                    )

            if thinking_segment is not None:
                await emit(
                    {
                        "type": "main_agent_thinking_end",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": thinking_segment,
                        "summary": thinking_text,
                    }
                )
                title = _thinking_title(thinking_text)
                if title:
                    await emit(
                        {
                            "type": "main_agent_thinking_title",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": thinking_segment,
                            "summary": title,
                        }
                    )

            if assistant_text and assistant_segment is None:
                assistant_segment = _next_segment()
                await emit(
                    {
                        "type": "main_agent_segment_start",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": assistant_segment,
                        "role": "assistant",
                        "message_id": streaming_assistant.id,
                    }
                )
                if not assistant_tokens:
                    await emit(
                        {
                            "type": "main_agent_segment_token",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": assistant_segment,
                            "token": assistant_text,
                        }
                    )

            if assistant_segment is not None:
                await emit(
                    {
                        "type": "main_agent_segment_end",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": assistant_segment,
                        "message_id": streaming_assistant.id,
                        "content": assistant_text,
                        "tool_calls": [
                            {
                                "id": tool.id,
                                "type": "function",
                                "function": {
                                    "name": tool.name,
                                    "arguments": json.dumps(tool.input),
                                },
                            }
                            for tool in normalized_tool_calls
                        ]
                        or None,
                    }
                )

            tool_segment_by_call_id: dict[str, int] = {}
            for tc in normalized_tool_calls:
                tool_segment_by_call_id[tc.id] = _next_segment()

            iteration_blocks: list[dict[str, Any]] = []
            if thinking_text:
                thinking_block: dict[str, Any] = {"type": "thinking", "thinking": thinking_text}
                if thinking_segment is not None:
                    thinking_block["segment_index"] = thinking_segment
                iteration_blocks.append(thinking_block)

            if assistant_text:
                text_block: dict[str, Any] = {"type": "text", "text": assistant_text}
                if assistant_segment is not None:
                    text_block["segment_index"] = assistant_segment
                iteration_blocks.append(text_block)

            for tc in normalized_tool_calls:
                block: dict[str, Any] = {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                    "segment_index": tool_segment_by_call_id[tc.id],
                }
                if tc.provider_specific_fields:
                    block["provider_specific_fields"] = tc.provider_specific_fields
                if tc.extra_content:
                    block["extra_content"] = tc.extra_content
                iteration_blocks.append(block)

            collected_blocks.extend(iteration_blocks)

            updated_message = await self.store.update_message(
                message_id=streaming_assistant.id,
                content=assistant_text if assistant_text else None,
                content_blocks=iteration_blocks or None,
                provider_format=provider_format,
                message_metadata={
                    "provider": provider,
                    "model": self.settings.gemini_model,
                    "provider_state": stream_result.provider_state,
                    "run_id": run_id,
                    "request_index": request_index,
                    "stream_mode": "interleaved",
                },
            )
            if updated_message is not None:
                last_assistant_message_id = updated_message.id

            if not assistant_text and iteration_blocks:
                await self.store.append_event(
                    thread_id=thread_id,
                    role=ConversationEventRole.ASSISTANT,
                    kind=ConversationEventKind.CONTROL,
                    content={
                        "type": "assistant_interleaved_blocks",
                        "provider_format": provider_format.value,
                        "content_blocks": iteration_blocks,
                    },
                    message_id=streaming_assistant.id,
                    visible_to_model=True,
                )
                await self.store.session.commit()
                await self.store._log_thread_snapshot(thread_id)

            answer_json = {
                "thinking": thinking_text,
                "text": assistant_text,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                        "provider_specific_fields": tc.provider_specific_fields,
                        "extra_content": tc.extra_content,
                    }
                    for tc in normalized_tool_calls
                ],
                "provider_state": stream_result.provider_state,
            }
            write_llm_io_files(
                thread_id=thread_id,
                user_index=user_msg_index,
                request_index=request_index,
                request_record=request_record,
                answer_json=answer_json,
                answer_text=assistant_text,
            )

            if not normalized_tool_calls:
                iteration_limit_exhausted = False
                break

            for tc in normalized_tool_calls:
                tool_segment = tool_segment_by_call_id[tc.id]
                await emit(
                    {
                        "type": "main_agent_tool_start",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": tool_segment,
                        "tool_use_id": tc.id,
                        "tool_name": tc.name,
                        "arguments": tc.input,
                    }
                )

                await self.store.record_tool_call(
                    thread_id=thread_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    input_payload=tc.input,
                    provider_specific_fields=tc.provider_specific_fields,
                    extra_content=tc.extra_content,
                    visible_to_model=True,
                )

                started_at = _utc_iso()
                tool_result = self.tools.execute(
                    tc.name,
                    tc.input,
                    ctx=ToolContext(
                        thread_id=thread_id,
                        run_id=run_id,
                        request_index=request_index,
                        user_msg_index=user_msg_index,
                        tool_use_id=tc.id,
                        tool_name=tc.name,
                    ),
                )
                finished_at = _utc_iso()

                await emit(
                    {
                        "type": "main_agent_tool_result",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": tool_segment,
                        "tool_use_id": tc.id,
                        "result": tool_result,
                    }
                )

                await self.store.record_tool_result(
                    thread_id=thread_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    status=tool_result["status"],
                    output=tool_result.get("output"),
                    error=tool_result.get("error"),
                    visible_to_model=True,
                )

                tool_call_index += 1
                write_tool_io_file(
                    thread_id=thread_id,
                    tool_name=tc.name,
                    tool_call_index=tool_call_index,
                    tool_use_id=tc.id,
                    user_index=user_msg_index,
                    request_index=request_index,
                    run_id=run_id,
                    arguments=tc.input,
                    result=tool_result,
                    status=tool_result.get("status", "unknown"),
                    error=(tool_result.get("error") or {}).get("message")
                    if isinstance(tool_result.get("error"), dict)
                    else None,
                    started_at=started_at,
                    finished_at=finished_at,
                )

                collected_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": tool_result,
                        "segment_index": tool_segment,
                    }
                )

        limit_completion_text = ""
        if iteration_limit_exhausted and not final_text.strip():
            limit_completion_text = (
                f"I stopped after reaching the tool-iteration limit ({max_iterations}) "
                "before producing a final narrative answer. "
                "Please continue with a narrower scope or a higher iteration budget."
            )

        completion_message: dict[str, Any] | None = None
        if last_assistant_message_id:
            last_message = await self.store.session.get(Message, last_assistant_message_id)
            if last_message:
                metadata = dict(last_message.message_metadata or {})
                trace_v1 = build_trace_v1(
                    provider=provider,
                    content_blocks=collected_blocks,
                    assistant_message_id=last_message.id,
                    include_thinking=True,
                )
                if trace_v1:
                    metadata["trace_v1"] = trace_v1
                metadata["run_id"] = run_id
                metadata["stream_mode"] = "interleaved"
                metadata["max_iterations"] = max_iterations
                metadata["iteration_limit_exhausted"] = iteration_limit_exhausted

                updated_last = await self.store.update_message(
                    message_id=last_message.id,
                    content=limit_completion_text or None,
                    message_metadata=metadata,
                )
                final_message = updated_last or last_message
                completion_message = {
                    "id": final_message.id,
                    "thread_id": final_message.thread_id,
                    "role": final_message.role.value if hasattr(final_message.role, "value") else str(final_message.role),
                    "content": final_message.content or "",
                    "created_at": final_message.created_at.isoformat() if final_message.created_at else None,
                    "metadata": final_message.message_metadata or {},
                }
                if final_message.content:
                    final_text = final_message.content

        await emit(
            {
                "type": "main_agent_complete",
                "thread_id": thread_id,
                "run_id": run_id,
                "message": completion_message,
            }
        )

        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "content": final_text,
            "provider": provider,
            "message": completion_message,
        }


def normalize_provider(value: str | None) -> ProviderName:
    lowered = (value or "gemini").strip().lower()
    if lowered != "gemini":
        raise ValueError("provider must be 'gemini'")
    return lowered  # type: ignore[return-value]


def parse_tool_result_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"value": payload}
