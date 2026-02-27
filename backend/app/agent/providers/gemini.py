from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.providers.base import ProviderClient
from app.agent.types import ProviderStreamResult, ToolCall

logger = logging.getLogger(__name__)


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            maybe = value.model_dump()
            if isinstance(maybe, dict):
                return maybe
        except Exception:
            return None
    return None


def _is_thinking_part(part: dict[str, Any]) -> bool:
    if part.get("thought"):
        return True
    part_type = str(part.get("type") or "").strip().lower()
    return part_type in {"thought", "thinking", "reasoning", "redacted_thinking"}


def _extract_thinking_tokens(delta: Any) -> list[str]:
    tokens: list[str] = []
    if delta is None:
        return tokens

    reasoning_content = getattr(delta, "reasoning_content", None)
    if reasoning_content is None and isinstance(delta, dict):
        reasoning_content = delta.get("reasoning_content")

    if isinstance(reasoning_content, list):
        for part in reasoning_content:
            maybe_text = None
            part_dict = _as_dict(part)
            if part_dict:
                maybe_text = part_dict.get("text") or part_dict.get("content")
            elif hasattr(part, "text"):
                maybe_text = getattr(part, "text", None)
            elif isinstance(part, str):
                maybe_text = part
            if maybe_text:
                tokens.append(_coerce_text(maybe_text))
    elif isinstance(reasoning_content, str) and reasoning_content:
        tokens.append(reasoning_content)

    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    if isinstance(content, list):
        for part in content:
            part_dict = _as_dict(part) if not isinstance(part, dict) else part
            if not isinstance(part_dict, dict):
                continue
            is_thought = _is_thinking_part(part_dict)
            if not is_thought:
                continue
            maybe_text = part_dict.get("text") or part_dict.get("content")
            if maybe_text:
                tokens.append(_coerce_text(maybe_text))

    extra_content = getattr(delta, "extra_content", None)
    if extra_content is None and isinstance(delta, dict):
        extra_content = delta.get("extra_content")
    extra_content_dict = _as_dict(extra_content) if not isinstance(extra_content, dict) else extra_content
    if isinstance(extra_content_dict, dict):
        google_payload = extra_content_dict.get("google")
        if isinstance(google_payload, dict):
            thought_text = google_payload.get("thoughts") or google_payload.get("thought")
            if thought_text:
                tokens.append(_coerce_text(thought_text))

    return tokens


def _extract_text_token(delta: Any) -> str:
    if delta is None:
        return ""

    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")

    if isinstance(content, str):
        return content

    text_parts: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
                continue

            part_dict = _as_dict(part) if not isinstance(part, dict) else part
            if not isinstance(part_dict, dict):
                continue
            if _is_thinking_part(part_dict):
                continue
            if "text" in part_dict:
                text_parts.append(_coerce_text(part_dict.get("text")))
            elif "content" in part_dict:
                text_parts.append(_coerce_text(part_dict.get("content")))

    return "".join(text_parts)


def _read_tool_calls(payload: Any) -> list[Any]:
    if payload is None:
        return []
    tool_calls = getattr(payload, "tool_calls", None)
    if tool_calls is None and isinstance(payload, dict):
        tool_calls = payload.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    return tool_calls


class GeminiProvider(ProviderClient):
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
                user_text = str(msg.get("content", ""))
                break

        thinking = "Running a quick structured analysis before answering."
        for token in thinking.split(" "):
            on_thinking_token(token + " ")

        lowered = user_text.lower()
        tool_calls: list[ToolCall] = []
        if "search" in lowered:
            tool_calls.append(ToolCall(id="mock_gemini_search_1", name="web_search_mock", input={"query": user_text}))
            text = "I will run a quick search first."
        else:
            text = f"Gemini mock response: {user_text or 'Ready.'}"

        for token in text.split(" "):
            on_text_token(token + " ")

        return ProviderStreamResult(
            text=text,
            thinking=thinking,
            tool_calls=tool_calls,
            provider_state={"provider": "gemini", "model": self.model, "mock": True},
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
        if self.mock_mode:
            return self._mock_turn(messages=messages, on_thinking_token=on_thinking_token, on_text_token=on_text_token)
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required when MOCK_LLM=false")

        try:
            import litellm
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"LiteLLM is required for Gemini streaming: {exc}") from exc

        litellm.drop_params = True

        request_messages = list(messages)
        if system_prompt and not any(msg.get("role") == "system" for msg in request_messages):
            request_messages = [{"role": "system", "content": system_prompt}] + request_messages

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": request_messages,
            "stream": True,
            "api_key": self.api_key,
            "extra_body": {
                "extra_body": {
                    "google": {
                        "thinking_config": {
                            "include_thoughts": True,
                        }
                    }
                }
            },
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_acc: dict[str, dict[str, Any]] = {}
        final_chunks: list[Any] = []
        model_used = str(kwargs.get("model") or self.model)

        def _accumulate_tool_calls(tool_calls: list[Any]) -> None:
            for raw_tc in tool_calls:
                tc = _as_dict(raw_tc) if not isinstance(raw_tc, dict) else raw_tc
                if not isinstance(tc, dict):
                    continue

                tc_id = tc.get("id") or tc.get("tool_call_id") or f"gemini_tool_{len(tool_acc) + 1}"
                bucket = tool_acc.setdefault(
                    str(tc_id),
                    {
                        "id": str(tc_id),
                        "name": None,
                        "arguments": "",
                        "provider_specific_fields": None,
                        "extra_content": None,
                    },
                )

                fn_payload_raw = tc.get("function")
                fn_payload = _as_dict(fn_payload_raw) if not isinstance(fn_payload_raw, dict) else fn_payload_raw
                fn_payload = fn_payload or {}
                fn_name = fn_payload.get("name") or tc.get("name")
                if fn_name and not bucket.get("name"):
                    bucket["name"] = fn_name

                args_delta = fn_payload.get("arguments")
                if isinstance(args_delta, str) and args_delta:
                    bucket["arguments"] += args_delta
                elif args_delta is not None and not bucket["arguments"]:
                    bucket["arguments"] = _coerce_text(args_delta)

                provider_specific_fields = tc.get("provider_specific_fields")
                if isinstance(provider_specific_fields, dict) and provider_specific_fields and bucket.get("provider_specific_fields") is None:
                    bucket["provider_specific_fields"] = provider_specific_fields

                extra_content = tc.get("extra_content")
                if isinstance(extra_content, dict) and extra_content and bucket.get("extra_content") is None:
                    bucket["extra_content"] = extra_content

        def _fallback_model_name(raw_model: str) -> str | None:
            candidate = raw_model.strip()
            if "gemini-3.1-" in candidate:
                return candidate.replace("gemini-3.1-", "gemini-3-")
            return None

        def _is_not_found_error(exc: Exception) -> bool:
            lowered = str(exc).lower()
            return "not found" in lowered or "404" in lowered

        def _consume_stream(stream: Any) -> None:
            for chunk in stream:
                final_chunks.append(chunk)
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue

                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is None and isinstance(choice, dict):
                    delta = choice.get("delta")
                if delta is None:
                    continue

                for token in _extract_thinking_tokens(delta):
                    thinking_chunks.append(token)
                    on_thinking_token(token)

                text_token = _extract_text_token(delta)
                if text_token:
                    text_chunks.append(text_token)
                    on_text_token(text_token)

                _accumulate_tool_calls(_read_tool_calls(delta))

                full_message = getattr(choice, "message", None)
                if full_message is None and isinstance(choice, dict):
                    full_message = choice.get("message")
                _accumulate_tool_calls(_read_tool_calls(full_message))

        try:
            stream = litellm.completion(**kwargs)
            _consume_stream(stream)
        except Exception as exc:
            fallback = _fallback_model_name(model_used)
            if fallback and _is_not_found_error(exc):
                retry_kwargs = dict(kwargs)
                retry_kwargs["model"] = fallback
                model_used = fallback
                final_chunks.clear()
                text_chunks.clear()
                thinking_chunks.clear()
                tool_acc.clear()
                logger.warning(
                    "Gemini model '%s' not found; retrying with fallback '%s'.",
                    kwargs.get("model"),
                    fallback,
                )
                try:
                    stream = litellm.completion(**retry_kwargs)
                    _consume_stream(stream)
                except Exception as retry_exc:
                    raise RuntimeError(
                        f"Gemini stream failed: {retry_exc}. "
                        "Set GEMINI_MODEL to a supported model (for example: gemini/gemini-3-pro or gemini/gemini-3-flash)."
                    ) from retry_exc
            else:
                raise RuntimeError(
                    f"Gemini stream failed: {exc}. "
                    "If this is a model-not-found error, set GEMINI_MODEL to a supported Gemini model."
                ) from exc

        if final_chunks:
            try:
                final_response = litellm.stream_chunk_builder(final_chunks, messages=request_messages)
            except Exception:
                final_response = None
        else:
            final_response = None

        if final_response is not None:
            choices = getattr(final_response, "choices", None) or []
            if choices:
                final_choice = choices[0]
                final_message = getattr(final_choice, "message", None)
                if final_message is None and isinstance(final_choice, dict):
                    final_message = final_choice.get("message")

                if final_message is not None:
                    _accumulate_tool_calls(_read_tool_calls(final_message))

                    if not text_chunks:
                        content = getattr(final_message, "content", None)
                        if content is None and isinstance(final_message, dict):
                            content = final_message.get("content")
                        if isinstance(content, str) and content:
                            text_chunks.append(content)
                        elif isinstance(content, list):
                            for part in content:
                                part_dict = _as_dict(part) if not isinstance(part, dict) else part
                                if isinstance(part_dict, dict):
                                    if _is_thinking_part(part_dict):
                                        continue
                                    maybe_text = part_dict.get("text") or part_dict.get("content")
                                    if maybe_text:
                                        text_chunks.append(_coerce_text(maybe_text))
                                elif isinstance(part, str):
                                    text_chunks.append(part)

                    if not thinking_chunks:
                        reasoning_content = getattr(final_message, "reasoning_content", None)
                        if reasoning_content is None and isinstance(final_message, dict):
                            reasoning_content = final_message.get("reasoning_content")
                        if isinstance(reasoning_content, str) and reasoning_content:
                            thinking_chunks.append(reasoning_content)
                        elif isinstance(reasoning_content, list):
                            for part in reasoning_content:
                                part_dict = _as_dict(part) if not isinstance(part, dict) else part
                                if isinstance(part_dict, dict):
                                    maybe_text = part_dict.get("text") or part_dict.get("content")
                                    if maybe_text:
                                        thinking_chunks.append(_coerce_text(maybe_text))
                                elif isinstance(part, str):
                                    thinking_chunks.append(part)

                        if not thinking_chunks:
                            content = getattr(final_message, "content", None)
                            if content is None and isinstance(final_message, dict):
                                content = final_message.get("content")
                            if isinstance(content, list):
                                for part in content:
                                    part_dict = _as_dict(part) if not isinstance(part, dict) else part
                                    if isinstance(part_dict, dict) and _is_thinking_part(part_dict):
                                        maybe_text = part_dict.get("text") or part_dict.get("content")
                                        if maybe_text:
                                            thinking_chunks.append(_coerce_text(maybe_text))

        parsed_calls: list[ToolCall] = []
        for value in tool_acc.values():
            args_text = value.get("arguments", "")
            parsed_input: dict[str, Any] = {}
            if args_text:
                try:
                    maybe = json.loads(args_text)
                    if isinstance(maybe, dict):
                        parsed_input = maybe
                except Exception:
                    parsed_input = {"raw": args_text}
            parsed_calls.append(
                ToolCall(
                    id=value["id"],
                    name=str(value.get("name") or "unknown_tool"),
                    input=parsed_input,
                    provider_specific_fields=value.get("provider_specific_fields"),
                    extra_content=value.get("extra_content"),
                )
            )

        return ProviderStreamResult(
            text="".join(text_chunks).strip(),
            thinking="".join(thinking_chunks).strip(),
            tool_calls=parsed_calls,
            provider_state={"provider": "gemini", "model": model_used},
        )
