from __future__ import annotations

from typing import Any


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value if value is not None else "")


def build_trace_v1(
    *,
    provider: str,
    content_blocks: list[dict[str, Any]] | None,
    assistant_message_id: str | None = None,
    include_thinking: bool = True,
) -> dict[str, Any] | None:
    if not content_blocks:
        return None

    normalized: list[dict[str, Any]] = []
    tool_use_index: dict[str, int] = {}
    assistant_segments: list[dict[str, Any]] = []

    for block in content_blocks:
        block_type = block.get("type")
        segment_index = block.get("segment_index")

        if block_type in {"thinking", "redacted_thinking"}:
            if not include_thinking:
                continue
            thinking_text = _coerce_text(block.get("thinking") or block.get("text") or block.get("content"))
            if not thinking_text.strip():
                continue
            out = {"type": "thinking", "thinking": thinking_text}
            if isinstance(segment_index, int):
                out["segment_index"] = segment_index
            normalized.append(out)
            continue

        if block_type == "text":
            text = _coerce_text(block.get("text"))
            if not text.strip():
                continue
            out = {"type": "text", "text": text}
            if isinstance(segment_index, int):
                out["segment_index"] = segment_index
                if assistant_message_id:
                    assistant_segments.append(
                        {"message_id": assistant_message_id, "segment_index": segment_index}
                    )
            normalized.append(out)
            continue

        if block_type == "tool_use":
            tool_use_id = block.get("id") or block.get("tool_use_id") or block.get("tool_call_id")
            out = {
                "type": "tool_use",
                "id": tool_use_id,
                "name": block.get("name"),
                "input": block.get("input"),
            }
            if isinstance(block.get("ui_visible"), bool):
                out["ui_visible"] = block.get("ui_visible")
            if block.get("parent_tool_use_id"):
                out["parent_tool_use_id"] = block.get("parent_tool_use_id")
            if isinstance(segment_index, int):
                out["segment_index"] = segment_index
                if tool_use_id:
                    tool_use_index[str(tool_use_id)] = segment_index
            if block.get("provider_specific_fields"):
                out["provider_specific_fields"] = block.get("provider_specific_fields")
            if block.get("extra_content"):
                out["extra_content"] = block.get("extra_content")
            normalized.append(out)
            continue

        if block_type == "tool_result":
            tool_use_id = block.get("tool_use_id") or block.get("tool_call_id")
            out = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "name": block.get("name"),
                "content": block.get("content"),
            }
            if isinstance(block.get("ui_visible"), bool):
                out["ui_visible"] = block.get("ui_visible")
            if block.get("parent_tool_use_id"):
                out["parent_tool_use_id"] = block.get("parent_tool_use_id")
            if isinstance(segment_index, int):
                out["segment_index"] = segment_index
                if tool_use_id:
                    tool_use_index.setdefault(str(tool_use_id), segment_index)
            normalized.append(out)

    if not normalized:
        return None

    trace: dict[str, Any] = {
        "provider": provider,
        "stream_mode": "interleaved",
        "content_blocks_normalized": normalized,
        "segments": {"count": len(normalized)},
        "flags": {"ui_hide_work": False},
    }

    index_map: dict[str, Any] = {}
    if tool_use_index:
        index_map["tool_use"] = tool_use_index
    if assistant_segments:
        index_map["assistant_segments"] = assistant_segments
    if assistant_message_id:
        index_map["anchor_message_id"] = assistant_message_id
    if index_map:
        trace["index_map"] = index_map
    return trace
