from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.providers.base import ProviderClient
from app.agent.types import ProviderStreamResult, ToolCall

logger = logging.getLogger(__name__)


class ClaudeProvider(ProviderClient):
    def __init__(self, *, api_key: str | None, model: str, mock_mode: bool = False) -> None:
        self.api_key = api_key
        self.model = model
        self.mock_mode = mock_mode

    def _mock_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        on_thinking_token,
        on_text_token,
    ) -> ProviderStreamResult:
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list) and content and isinstance(content[0], dict):
                    user_text = str(content[0].get("text", ""))
                elif isinstance(content, str):
                    user_text = content
                break

        thinking = "Planning the next best step with available tools."
        for token in thinking.split(" "):
            on_thinking_token(token + " ")

        lowered = user_text.lower()
        tool_calls: list[ToolCall] = []
        if "calc" in lowered or any(ch in lowered for ch in ["+", "-", "*", "/"]):
            tool_calls.append(ToolCall(id="mock_claude_calc_1", name="calc", input={"expression": user_text}))
            text = "I will use calc to evaluate this expression."
        elif "paper" in lowered or "literature" in lowered:
            tool_calls.append(ToolCall(id="mock_claude_paper_1", name="fetch_paper_stub", input={"topic": user_text}))
            text = "I will fetch paper metadata for this topic."
        else:
            text = f"Claude mock response: {user_text or 'Ready.'}"

        for token in text.split(" "):
            on_text_token(token + " ")

        return ProviderStreamResult(
            text=text,
            thinking=thinking,
            tool_calls=tool_calls,
            provider_state={"provider": "claude", "model": self.model, "mock": True},
        )

    def stream_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        on_thinking_token,
        on_text_token,
    ) -> ProviderStreamResult:
        if self.mock_mode or not self.api_key:
            return self._mock_turn(messages=messages, on_thinking_token=on_thinking_token, on_text_token=on_text_token)

        try:
            from anthropic import Anthropic
        except Exception as exc:  # pragma: no cover
            logger.warning("Anthropic SDK unavailable (%s), using mock mode", exc)
            return self._mock_turn(messages=messages, on_thinking_token=on_thinking_token, on_text_token=on_text_token)

        client = Anthropic(api_key=self.api_key)
        request_payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": messages,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        }
        if system_prompt:
            request_payload["system"] = system_prompt
        if tools:
            request_payload["tools"] = tools

        thinking_chunks: list[str] = []
        text_chunks: list[str] = []
        tool_states: dict[int, dict[str, Any]] = {}
        tool_calls: list[ToolCall] = []

        with client.beta.messages.stream(**request_payload) as stream:
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    index = getattr(event, "index", None)
                    if block is None or index is None:
                        continue
                    block_type = getattr(block, "type", None)
                    if block_type in {"tool_use", "server_tool_use", "mcp_tool_use"}:
                        tool_states[index] = {
                            "id": getattr(block, "id", None),
                            "name": getattr(block, "name", None),
                            "arguments_buffer": "",
                        }
                elif event_type == "content_block_delta":
                    index = getattr(event, "index", None)
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "thinking_delta":
                        token = getattr(delta, "thinking", "")
                        if token:
                            thinking_chunks.append(token)
                            on_thinking_token(token)
                    elif delta_type == "text_delta":
                        token = getattr(delta, "text", "")
                        if token:
                            text_chunks.append(token)
                            on_text_token(token)
                    elif delta_type == "input_json_delta" and index in tool_states:
                        part = getattr(delta, "partial_json", "")
                        if part:
                            tool_states[index]["arguments_buffer"] += part
                elif event_type == "content_block_stop":
                    index = getattr(event, "index", None)
                    if index is None or index not in tool_states:
                        continue
                    state = tool_states[index]
                    parsed_input: dict[str, Any] = {}
                    raw_args = state.get("arguments_buffer", "")
                    if raw_args:
                        try:
                            maybe = json.loads(raw_args)
                            if isinstance(maybe, dict):
                                parsed_input = maybe
                        except Exception:
                            parsed_input = {"raw": raw_args}
                    tool_calls.append(
                        ToolCall(
                            id=str(state.get("id") or state.get("name") or f"claude_tool_{index}"),
                            name=str(state.get("name") or "unknown_tool"),
                            input=parsed_input,
                        )
                    )

            final_message = stream.get_final_message()
            final_dump = final_message.model_dump()

        return ProviderStreamResult(
            text="".join(text_chunks).strip(),
            thinking="".join(thinking_chunks).strip(),
            tool_calls=tool_calls,
            provider_state={
                "provider": "claude",
                "model": self.model,
                "stop_reason": final_dump.get("stop_reason"),
                "message_id": final_dump.get("id"),
            },
        )
