from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


EventType = Literal[
    "main_agent_start",
    "main_agent_segment_start",
    "main_agent_segment_token",
    "main_agent_segment_end",
    "main_agent_thinking_start",
    "main_agent_thinking_token",
    "main_agent_thinking_end",
    "main_agent_thinking_title",
    "main_agent_tool_start",
    "main_agent_tool_result",
    "main_agent_complete",
    "main_agent_error",
]


class WsEvent(BaseModel):
    type: EventType
    thread_id: str
    run_id: str | None = None
    segment_index: int | None = None
    role: str | None = None
    token: str | None = None
    content: str | None = None
    summary: str | None = None
    message: dict[str, Any] | None = None
    message_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
