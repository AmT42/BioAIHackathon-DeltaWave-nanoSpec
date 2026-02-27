from __future__ import annotations

import copy
import json
from typing import Any

from app.persistence.models import (
    ConversationEventKind,
    ConversationEventRole,
    MessageProviderFormat,
)
from app.persistence.service import CanonicalEventView


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def build_claude_messages(events: list[CanonicalEventView]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    tool_result_by_id: dict[str, list[dict[str, Any]]] = {}

    for event in events:
        if not event.visible_to_model:
            continue

        if event.kind == ConversationEventKind.CONTROL and isinstance(event.content, dict):
            if event.content.get("type") != "assistant_interleaved_blocks":
                continue
            provider_tag = event.content.get("provider_format")
            if provider_tag and provider_tag != MessageProviderFormat.CLAUDE_INTERLEAVED.value:
                continue
            raw_blocks = event.content.get("content_blocks")
            if isinstance(raw_blocks, list) and raw_blocks:
                messages.append({"role": "assistant", "content": copy.deepcopy(raw_blocks)})
            continue

        if event.kind == ConversationEventKind.TEXT:
            if (
                event.message_provider_format == MessageProviderFormat.CLAUDE_INTERLEAVED
                and event.message_content_blocks
            ):
                messages.append(
                    {
                        "role": event.role.value,
                        "content": copy.deepcopy(event.message_content_blocks),
                    }
                )
            else:
                messages.append(
                    {
                        "role": event.role.value,
                        "content": [{"type": "text", "text": event.content.get("text", "")}],
                    }
                )
            continue

        if event.kind == ConversationEventKind.TOOL_CALL:
            tool_call_id = event.content.get("tool_call_id") or event.tool_call_id
            messages.append(
                {
                    "role": ConversationEventRole.ASSISTANT.value,
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_call_id,
                            "name": event.content.get("tool_name"),
                            "input": event.content.get("input", {}),
                        }
                    ],
                }
            )
            continue

        if event.kind == ConversationEventKind.TOOL_RESULT:
            envelope_payload = {
                "status": event.content.get("status"),
                "output": event.content.get("output"),
                "error": event.content.get("error"),
                "tool_name": event.content.get("tool_name"),
            }
            payload_text = _coerce_text(envelope_payload)
            tool_use_id = event.content.get("tool_call_id") or event.tool_call_id
            block = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": payload_text,
            }
            tool_result_by_id.setdefault(str(tool_use_id), []).append(block)
            messages.append(
                {
                    "role": ConversationEventRole.USER.value,
                    "content": [block],
                }
            )

    return _normalize_claude_adjacency(messages, tool_result_by_id)


def _normalize_claude_adjacency(
    messages: list[dict[str, Any]],
    tool_result_queues: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") != "assistant":
            # Skip tool_result messages; they will be inserted adjacent to tool_use.
            content = msg.get("content")
            if isinstance(content, list) and len(content) == 1:
                first = content[0]
                if isinstance(first, dict) and first.get("type") == "tool_result":
                    continue
            normalized.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            normalized.append(msg)
            continue

        assistant_blocks: list[dict[str, Any]] = []
        immediate_results: list[dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                assistant_blocks.append(block)
                continue
            tool_use_id = str(block.get("id"))
            queue = tool_result_queues.get(tool_use_id, [])
            if not queue:
                # Drop orphan tool_use because Anthropic requires adjacency.
                continue
            assistant_blocks.append(block)
            immediate_results.append(queue.pop(0))

        if assistant_blocks:
            normalized.append({"role": "assistant", "content": assistant_blocks})
        if immediate_results:
            normalized.append({"role": "user", "content": immediate_results})

    for queue in tool_result_queues.values():
        for remaining in queue:
            payload = _coerce_text(remaining.get("content"))
            normalized.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": f"[tool_output]\\n{payload}"}],
                }
            )

    return normalized


def build_gemini_openai_messages(
    events: list[CanonicalEventView],
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    def _extract_tool_call_id(raw_id: Any) -> str | None:
        if raw_id is None:
            return None
        return str(raw_id)

    def _build_tool_call_from_block(block: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        tool_id = (
            block.get("id")
            or block.get("tool_call_id")
            or block.get("tool_use_id")
            or block.get("name")
        )
        tool_name = block.get("name")
        tool_input = block.get("input", {})
        if isinstance(tool_input, str):
            arguments = tool_input
        else:
            try:
                arguments = json.dumps(tool_input)
            except Exception:
                arguments = "{}" if tool_input is None else str(tool_input)

        if not tool_id and not tool_name:
            return None, None

        tool_call: dict[str, Any] = {
            "id": tool_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": arguments or "{}",
            },
        }
        provider_specific_fields = block.get("provider_specific_fields")
        if isinstance(provider_specific_fields, dict) and provider_specific_fields:
            tool_call["provider_specific_fields"] = copy.deepcopy(provider_specific_fields)
        extra_content = block.get("extra_content")
        if isinstance(extra_content, dict) and extra_content:
            tool_call["extra_content"] = copy.deepcopy(extra_content)
        return tool_call, _extract_tool_call_id(tool_id)

    def _extract_openai_parts_from_blocks(
        blocks: list[dict[str, Any]],
        *,
        include_tools: bool,
    ) -> tuple[str, str, list[dict[str, Any]], list[str]]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_ids: list[str] = []

        for block in blocks:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type in {"thinking", "redacted_thinking"}:
                    thinking_value = block.get("thinking")
                    if thinking_value is None and "text" in block:
                        thinking_value = block.get("text")
                    if thinking_value is None and "content" in block:
                        thinking_value = block.get("content")
                    maybe_thinking = _coerce_text(thinking_value).strip()
                    if maybe_thinking:
                        reasoning_parts.append(maybe_thinking)
                elif block_type == "text":
                    text_parts.append(_coerce_text(block.get("text")))
                elif block_type == "tool_use":
                    if not include_tools:
                        continue
                    tool_call, tool_id = _build_tool_call_from_block(block)
                    if tool_call:
                        tool_calls.append(tool_call)
                    if tool_id:
                        tool_ids.append(tool_id)
                elif block_type == "tool_result":
                    continue
                elif "text" in block:
                    text_parts.append(_coerce_text(block.get("text")))
            elif isinstance(block, str):
                text_parts.append(block)
            else:
                text_parts.append(_coerce_text(block))

        text_value = " ".join(part for part in text_parts if part).strip()
        reasoning_value = "\n\n".join(part for part in reasoning_parts if part).strip()
        return text_value, reasoning_value, tool_calls, tool_ids

    events_list = list(events)
    tool_ids_embedded: set[str] = set()
    for event in events_list:
        if not event.visible_to_model:
            continue
        if event.kind == ConversationEventKind.CONTROL and isinstance(event.content, dict):
            if event.content.get("type") != "assistant_interleaved_blocks":
                continue
            if event.content.get("provider_format") != MessageProviderFormat.GEMINI_INTERLEAVED.value:
                continue
            raw_blocks = event.content.get("content_blocks")
            if isinstance(raw_blocks, list):
                _, _, _, tool_ids = _extract_openai_parts_from_blocks(raw_blocks, include_tools=True)
                tool_ids_embedded.update(tool_ids)
            continue
        if (
            event.kind == ConversationEventKind.TEXT
            and event.message_provider_format == MessageProviderFormat.GEMINI_INTERLEAVED
            and event.message_content_blocks
        ):
            _, _, _, tool_ids = _extract_openai_parts_from_blocks(event.message_content_blocks, include_tools=True)
            tool_ids_embedded.update(tool_ids)

    pending_tool_calls: list[dict[str, Any]] = []
    seen_tool_call_ids: set[str] = set()

    def flush_tool_calls() -> None:
        nonlocal pending_tool_calls
        if not pending_tool_calls:
            return
        for tc in pending_tool_calls:
            tcid = tc.get("id")
            if tcid:
                seen_tool_call_ids.add(str(tcid))
        messages.append({"role": "assistant", "content": "", "tool_calls": pending_tool_calls})
        pending_tool_calls = []

    for event in events_list:
        if not event.visible_to_model:
            continue

        if event.kind == ConversationEventKind.CONTROL and isinstance(event.content, dict):
            if event.content.get("type") != "assistant_interleaved_blocks":
                continue
            if event.content.get("provider_format") != MessageProviderFormat.GEMINI_INTERLEAVED.value:
                continue
            raw_blocks = event.content.get("content_blocks")
            if isinstance(raw_blocks, list) and raw_blocks:
                text_value, reasoning_value, tool_calls, _ = _extract_openai_parts_from_blocks(
                    raw_blocks,
                    include_tools=True,
                )
                flush_tool_calls()
                if text_value or tool_calls or reasoning_value:
                    for tool_call in tool_calls:
                        tool_call_id = tool_call.get("id")
                        if tool_call_id is not None:
                            seen_tool_call_ids.add(str(tool_call_id))
                    entry: dict[str, Any] = {"role": event.role.value, "content": text_value}
                    if reasoning_value:
                        entry["reasoning_content"] = reasoning_value
                    if tool_calls:
                        entry["tool_calls"] = tool_calls
                    messages.append(entry)
            continue

        if event.kind == ConversationEventKind.TEXT:
            if (
                event.message_provider_format == MessageProviderFormat.GEMINI_INTERLEAVED
                and event.message_content_blocks
            ):
                flush_tool_calls()
                text_value, reasoning_value, tool_calls, _ = _extract_openai_parts_from_blocks(
                    event.message_content_blocks,
                    include_tools=True,
                )
                if text_value or tool_calls or reasoning_value:
                    for tool_call in tool_calls:
                        tool_call_id = tool_call.get("id")
                        if tool_call_id is not None:
                            seen_tool_call_ids.add(str(tool_call_id))
                    entry: dict[str, Any] = {"role": event.role.value, "content": text_value}
                    if reasoning_value:
                        entry["reasoning_content"] = reasoning_value
                    if tool_calls:
                        entry["tool_calls"] = tool_calls
                    messages.append(entry)
                continue

            flush_tool_calls()
            messages.append({"role": event.role.value, "content": event.content.get("text", "")})
            continue

        if event.kind == ConversationEventKind.TOOL_CALL:
            tool_call_id = event.content.get("tool_call_id") or event.tool_call_id
            if tool_call_id and str(tool_call_id) in tool_ids_embedded:
                continue
            args = event.content.get("input", {})
            if not isinstance(args, str):
                try:
                    args = json.dumps(args)
                except Exception:
                    args = "{}" if args is None else str(args)
            tool_call: dict[str, Any] = {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": event.content.get("tool_name"),
                    "arguments": args or "{}",
                },
            }
            provider_specific_fields = event.content.get("provider_specific_fields")
            if isinstance(provider_specific_fields, dict) and provider_specific_fields:
                tool_call["provider_specific_fields"] = copy.deepcopy(provider_specific_fields)
            extra_content = event.content.get("extra_content")
            if isinstance(extra_content, dict) and extra_content:
                tool_call["extra_content"] = copy.deepcopy(extra_content)
            pending_tool_calls.append(tool_call)
            continue

        if event.kind == ConversationEventKind.TOOL_RESULT:
            flush_tool_calls()
            tool_call_id = event.content.get("tool_call_id") or event.tool_call_id
            envelope_payload = {
                "status": event.content.get("status"),
                "output": event.content.get("output"),
                "error": event.content.get("error"),
                "tool_name": event.content.get("tool_name"),
            }
            payload_text = _coerce_text(envelope_payload)
            if tool_call_id and str(tool_call_id) in seen_tool_call_ids:
                tool_msg: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": str(tool_call_id),
                    "content": payload_text,
                }
                if event.content.get("tool_name"):
                    tool_msg["name"] = event.content.get("tool_name")
                messages.append(tool_msg)
            else:
                messages.append({"role": "assistant", "content": f"[tool_output]\\n{payload_text}"})

    flush_tool_calls()
    return messages


def build_gemini_messages(events: list[CanonicalEventView], system_prompt: str | None = None) -> list[dict[str, Any]]:
    """Backward-compatible alias."""
    return build_gemini_openai_messages(events, system_prompt=system_prompt)
