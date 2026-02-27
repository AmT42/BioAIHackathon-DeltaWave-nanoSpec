from __future__ import annotations

import copy
import json
import logging
from typing import Any, Callable

from app.agent.providers.base import ProviderClient
from app.agent.types import ProviderStreamResult, ToolCall

logger = logging.getLogger(__name__)

_EFFORT_TO_BUDGET: dict[str, int] = {
    "disable": 0,
    "none": 0,
    "minimal": 256,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}


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
    if hasattr(value, "to_dict"):
        try:
            maybe = value.to_dict()
            if isinstance(maybe, dict):
                return maybe
        except Exception:
            return None
    return None


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return value


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, str):
        text = raw_arguments.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            decoder = json.JSONDecoder()
            parsed, _ = decoder.raw_decode(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"raw": raw_arguments}
    return {"raw": _coerce_text(raw_arguments)}


def _extract_tool_signature(tool_call: dict[str, Any]) -> str | None:
    provider_specific = tool_call.get("provider_specific_fields")
    if isinstance(provider_specific, dict):
        sig = provider_specific.get("thought_signature")
        if isinstance(sig, str) and sig.strip():
            return sig.strip()

    extra_content = tool_call.get("extra_content")
    if isinstance(extra_content, dict):
        google_payload = extra_content.get("google")
        if isinstance(google_payload, dict):
            sig = google_payload.get("thought_signature")
            if isinstance(sig, str) and sig.strip():
                return sig.strip()
    return None


def _extract_parts_from_chunk(chunk: Any) -> list[Any]:
    chunk_dict = _as_dict(chunk) if not isinstance(chunk, dict) else chunk
    candidates = None
    if chunk_dict is not None:
        candidates = chunk_dict.get("candidates")
    if candidates is None:
        candidates = getattr(chunk, "candidates", None)
    if not isinstance(candidates, list) or not candidates:
        return []

    first = candidates[0]
    first_dict = _as_dict(first) if not isinstance(first, dict) else first
    content = None
    if first_dict is not None:
        content = first_dict.get("content")
    if content is None:
        content = getattr(first, "content", None)

    content_dict = _as_dict(content) if not isinstance(content, dict) else content
    if content_dict is not None:
        parts = content_dict.get("parts")
        if isinstance(parts, list):
            return parts
    if hasattr(content, "parts"):
        parts = getattr(content, "parts", None)
        if isinstance(parts, list):
            return parts
    return []


def _extract_part_text_and_thought(part: Any) -> tuple[str, bool]:
    if isinstance(part, str):
        return part, False
    part_dict = _as_dict(part) if not isinstance(part, dict) else part
    if not isinstance(part_dict, dict):
        return "", False
    text = part_dict.get("text")
    if text is None and hasattr(part, "text"):
        text = getattr(part, "text", None)
    if text is None:
        text = part_dict.get("content")

    thought = part_dict.get("thought")
    if thought is None and hasattr(part, "thought"):
        thought = getattr(part, "thought", None)
    part_type = str(part_dict.get("type") or "").strip().lower()
    is_thought = bool(thought) or part_type in {"thought", "thinking", "reasoning", "redacted_thinking"}
    if text is None:
        return "", is_thought
    return _coerce_text(text), is_thought


def _extract_part_function_call(part: Any) -> dict[str, Any] | None:
    part_dict = _as_dict(part) if not isinstance(part, dict) else part
    if not isinstance(part_dict, dict):
        return None

    fn_raw = part_dict.get("function_call") or part_dict.get("functionCall")
    if fn_raw is None:
        fn_raw = getattr(part, "function_call", None) or getattr(part, "functionCall", None)
    fn_dict = _as_dict(fn_raw) if not isinstance(fn_raw, dict) else fn_raw
    if not isinstance(fn_dict, dict):
        return None

    payload: dict[str, Any] = {
        "id": fn_dict.get("id"),
        "name": fn_dict.get("name"),
        "args": fn_dict.get("args") if "args" in fn_dict else fn_dict.get("arguments"),
    }
    thought_signature = fn_dict.get("thought_signature")
    if thought_signature is None:
        thought_signature = fn_dict.get("thoughtSignature")
    if thought_signature is None:
        thought_signature = part_dict.get("thought_signature")
    if thought_signature is None:
        thought_signature = part_dict.get("thoughtSignature")
    if isinstance(thought_signature, str) and thought_signature.strip():
        payload["thought_signature"] = thought_signature.strip()
    return payload


def _emit_incremental(
    *,
    snapshot: str,
    consumed: str,
    emit: Callable[[str], None],
    sink: list[str],
) -> str:
    if not snapshot:
        return consumed
    if snapshot.startswith(consumed):
        delta = snapshot[len(consumed):]
        if delta:
            sink.append(delta)
            emit(delta)
        return snapshot

    sink.append(snapshot)
    emit(snapshot)
    return consumed + snapshot


class GeminiProvider(ProviderClient):
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        reasoning_effort: str = "low",
        include_thoughts: bool = True,
        thinking_budget: int | None = None,
        mock_mode: bool = False,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.include_thoughts = include_thoughts
        self.thinking_budget = thinking_budget
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

    def _resolve_types_module(self) -> Any | None:
        try:
            from google.genai import types as genai_types
        except Exception:
            return None
        return genai_types

    def _build_text_part(self, text: str, *, genai_types: Any | None) -> Any:
        if genai_types is not None:
            part_cls = getattr(genai_types, "Part", None)
            if part_cls is not None and hasattr(part_cls, "from_text"):
                try:
                    return part_cls.from_text(text=text)
                except Exception:
                    pass
        return {"text": text}

    def _build_function_call_part(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_call_id: str | None,
        thought_signature: str | None,
        genai_types: Any | None,
    ) -> Any:
        if genai_types is not None:
            part_cls = getattr(genai_types, "Part", None)
            if part_cls is not None and hasattr(part_cls, "from_function_call"):
                kwargs: dict[str, Any] = {"name": tool_name, "args": tool_input}
                if tool_call_id:
                    kwargs["id"] = tool_call_id
                if thought_signature:
                    kwargs["thought_signature"] = thought_signature
                try:
                    return part_cls.from_function_call(**kwargs)
                except Exception:
                    # Fallback: older SDKs might not accept thought_signature.
                    kwargs.pop("thought_signature", None)
                    try:
                        return part_cls.from_function_call(**kwargs)
                    except Exception:
                        pass

        payload: dict[str, Any] = {"name": tool_name, "args": tool_input}
        if tool_call_id:
            payload["id"] = tool_call_id
        if thought_signature:
            payload["thought_signature"] = thought_signature
        return {"function_call": payload}

    def _build_function_response_part(
        self,
        *,
        tool_name: str,
        response: dict[str, Any],
        tool_call_id: str | None,
        genai_types: Any | None,
    ) -> Any:
        if genai_types is not None:
            part_cls = getattr(genai_types, "Part", None)
            if part_cls is not None and hasattr(part_cls, "from_function_response"):
                kwargs: dict[str, Any] = {"name": tool_name, "response": response}
                if tool_call_id:
                    kwargs["id"] = tool_call_id
                try:
                    return part_cls.from_function_response(**kwargs)
                except Exception:
                    pass

        payload: dict[str, Any] = {"name": tool_name, "response": response}
        if tool_call_id:
            payload["id"] = tool_call_id
        return {"function_response": payload}

    def _build_content(self, *, role: str, parts: list[Any], genai_types: Any | None) -> Any:
        if genai_types is not None:
            content_cls = getattr(genai_types, "Content", None)
            if content_cls is not None:
                try:
                    return content_cls(role=role, parts=parts)
                except Exception:
                    pass
        return {"role": role, "parts": parts}

    def _build_tool_config(self, *, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        declarations: list[dict[str, Any]] = []
        for schema in tools:
            if not isinstance(schema, dict):
                continue
            fn = schema.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            declaration: dict[str, Any] = {
                "name": name.strip(),
                "description": str(fn.get("description") or "").strip(),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            declarations.append(declaration)
        if not declarations:
            return []
        return [{"function_declarations": declarations}]

    def _resolve_thinking_budget(self) -> int | None:
        if self.thinking_budget is not None:
            return max(0, self.thinking_budget)
        return _EFFORT_TO_BUDGET.get(self.reasoning_effort, _EFFORT_TO_BUDGET["medium"])

    def _build_generation_config(
        self,
        *,
        system_instruction: str | None,
        tools: list[dict[str, Any]],
        genai_types: Any | None,
    ) -> Any:
        tool_config = self._build_tool_config(tools=tools)
        thinking_cfg: dict[str, Any] = {"include_thoughts": bool(self.include_thoughts)}
        budget = self._resolve_thinking_budget()
        if budget is not None:
            thinking_cfg["thinking_budget"] = budget

        config_kwargs: dict[str, Any] = {
            "thinking_config": thinking_cfg,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if tool_config:
            config_kwargs["tools"] = tool_config

        if genai_types is None:
            return config_kwargs

        thinking_obj: Any = thinking_cfg
        thinking_cls = getattr(genai_types, "ThinkingConfig", None)
        if thinking_cls is not None:
            try:
                thinking_obj = thinking_cls(**thinking_cfg)
            except Exception:
                thinking_obj = thinking_cfg
        config_kwargs["thinking_config"] = thinking_obj

        config_cls = getattr(genai_types, "GenerateContentConfig", None)
        if config_cls is not None:
            try:
                return config_cls(**config_kwargs)
            except Exception:
                return config_kwargs
        return config_kwargs

    def _build_contents(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str,
        genai_types: Any | None,
    ) -> tuple[list[Any], str | None, int]:
        contents: list[Any] = []
        system_instruction = system_prompt.strip() if system_prompt and system_prompt.strip() else None

        require_signature = "gemini-3" in self.model.lower()
        unsigned_history_tool_calls = 0
        emitted_function_call_ids: set[str] = set()
        tool_name_by_id: dict[str, str] = {}

        for raw_msg in messages:
            if not isinstance(raw_msg, dict):
                continue

            role = str(raw_msg.get("role") or "").strip().lower()
            if role == "system":
                if not system_instruction:
                    maybe = raw_msg.get("content")
                    if isinstance(maybe, str) and maybe.strip():
                        system_instruction = maybe.strip()
                continue

            if role in {"assistant", "model"}:
                parts: list[Any] = []
                content = raw_msg.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(self._build_text_part(content, genai_types=genai_types))
                elif content not in (None, ""):
                    parts.append(self._build_text_part(_coerce_text(content), genai_types=genai_types))

                tool_calls = raw_msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        tc = _as_dict(tool_call) if not isinstance(tool_call, dict) else tool_call
                        if not isinstance(tc, dict):
                            continue
                        tc_id = tc.get("id") or tc.get("tool_call_id")
                        if tc_id is not None:
                            tc_id = str(tc_id)

                        fn = tc.get("function")
                        fn_dict = _as_dict(fn) if not isinstance(fn, dict) else fn
                        fn_dict = fn_dict or {}

                        tool_name = fn_dict.get("name") or tc.get("name")
                        if not isinstance(tool_name, str) or not tool_name.strip():
                            tool_name = "unknown_tool"
                        tool_name = tool_name.strip()

                        raw_args = fn_dict.get("arguments")
                        if raw_args is None:
                            raw_args = tc.get("input")
                        parsed_input = _parse_tool_arguments(raw_args)
                        thought_signature = _extract_tool_signature(tc)

                        if require_signature and not thought_signature:
                            unsigned_history_tool_calls += 1
                            fallback = (
                                "[tool_call_without_thought_signature] "
                                f"{tool_name}({_coerce_text(parsed_input)})"
                            )
                            parts.append(self._build_text_part(fallback, genai_types=genai_types))
                            continue

                        parts.append(
                            self._build_function_call_part(
                                tool_name=tool_name,
                                tool_input=parsed_input,
                                tool_call_id=tc_id,
                                thought_signature=thought_signature,
                                genai_types=genai_types,
                            )
                        )
                        if tc_id:
                            emitted_function_call_ids.add(tc_id)
                            tool_name_by_id[tc_id] = tool_name

                if parts:
                    contents.append(self._build_content(role="model", parts=parts, genai_types=genai_types))
                continue

            if role == "tool":
                tool_call_id = raw_msg.get("tool_call_id")
                tool_call_id_str = str(tool_call_id) if tool_call_id is not None else None
                tool_name = raw_msg.get("name")
                if not isinstance(tool_name, str) or not tool_name.strip():
                    tool_name = tool_name_by_id.get(tool_call_id_str or "", "tool")
                tool_name = tool_name.strip() or "tool"

                raw_content = raw_msg.get("content")
                parsed = _parse_jsonish(raw_content)
                if isinstance(parsed, dict):
                    response_payload = parsed
                elif parsed in ("", None):
                    response_payload = {"status": "empty"}
                else:
                    response_payload = {"value": parsed}

                if tool_call_id_str and tool_call_id_str in emitted_function_call_ids:
                    part = self._build_function_response_part(
                        tool_name=tool_name,
                        response=response_payload,
                        tool_call_id=tool_call_id_str,
                        genai_types=genai_types,
                    )
                    contents.append(self._build_content(role="user", parts=[part], genai_types=genai_types))
                else:
                    fallback_text = f"[tool_output]\n{_coerce_text(response_payload)}"
                    text_part = self._build_text_part(fallback_text, genai_types=genai_types)
                    contents.append(self._build_content(role="user", parts=[text_part], genai_types=genai_types))
                continue

            text = raw_msg.get("content")
            if isinstance(text, str) and text.strip():
                text_part = self._build_text_part(text, genai_types=genai_types)
                contents.append(self._build_content(role="user", parts=[text_part], genai_types=genai_types))
            elif text not in (None, ""):
                text_part = self._build_text_part(_coerce_text(text), genai_types=genai_types)
                contents.append(self._build_content(role="user", parts=[text_part], genai_types=genai_types))

        return contents, system_instruction, unsigned_history_tool_calls

    def _consume_stream(
        self,
        *,
        stream: Any,
        on_thinking_token: Callable[[str], None],
        on_text_token: Callable[[str], None],
    ) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_acc: dict[str, dict[str, Any]] = {}

        consumed_text = ""
        consumed_thinking = ""
        fallback_tool_idx = 0

        def merge_tool_call(call: dict[str, Any]) -> None:
            nonlocal fallback_tool_idx
            call_id = call.get("id")
            tool_name = call.get("name")
            args = call.get("args")
            thought_signature = call.get("thought_signature")

            if call_id is None:
                fallback_tool_idx += 1
                key = f"gemini_tool_{fallback_tool_idx}"
            else:
                key = str(call_id)

            bucket = tool_acc.setdefault(
                key,
                {
                    "id": str(call_id) if call_id is not None else key,
                    "name": "unknown_tool",
                    "arguments_buffer": "",
                    "arguments_dict": {},
                    "provider_specific_fields": {},
                },
            )
            if isinstance(tool_name, str) and tool_name.strip():
                bucket["name"] = tool_name.strip()
            if isinstance(args, dict):
                bucket["arguments_dict"] = dict(args)
            elif isinstance(args, str) and args:
                bucket["arguments_buffer"] = f"{bucket['arguments_buffer']}{args}"
            elif args is not None and not bucket["arguments_buffer"]:
                bucket["arguments_buffer"] = _coerce_text(args)

            if isinstance(thought_signature, str) and thought_signature.strip():
                provider_specific = bucket.setdefault("provider_specific_fields", {})
                provider_specific["thought_signature"] = thought_signature.strip()

        for chunk in stream:
            chunk_text_snapshot = ""
            chunk_thinking_snapshot = ""
            parts = _extract_parts_from_chunk(chunk)

            if parts:
                for part in parts:
                    tool_call = _extract_part_function_call(part)
                    if tool_call is not None:
                        merge_tool_call(tool_call)

                    text, is_thought = _extract_part_text_and_thought(part)
                    if not text:
                        continue
                    if is_thought:
                        chunk_thinking_snapshot += text
                    else:
                        chunk_text_snapshot += text
            else:
                fallback_text = None
                if isinstance(chunk, dict):
                    fallback_text = chunk.get("text")
                if fallback_text is None and hasattr(chunk, "text"):
                    fallback_text = getattr(chunk, "text", None)
                if isinstance(fallback_text, str) and fallback_text:
                    chunk_text_snapshot = fallback_text

            consumed_thinking = _emit_incremental(
                snapshot=chunk_thinking_snapshot,
                consumed=consumed_thinking,
                emit=on_thinking_token,
                sink=thinking_chunks,
            )
            consumed_text = _emit_incremental(
                snapshot=chunk_text_snapshot,
                consumed=consumed_text,
                emit=on_text_token,
                sink=text_chunks,
            )

        return text_chunks, thinking_chunks, tool_acc

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
            from google import genai
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"google-genai is required for Gemini streaming: {exc}") from exc

        genai_types = self._resolve_types_module()
        request_messages = copy.deepcopy(messages)
        if system_prompt and not any(str(msg.get("role") or "").strip().lower() == "system" for msg in request_messages):
            request_messages = [{"role": "system", "content": system_prompt}] + request_messages

        contents, system_instruction, unsigned_history_tool_calls = self._build_contents(
            messages=request_messages,
            system_prompt=system_prompt,
            genai_types=genai_types,
        )
        if not contents:
            user_part = self._build_text_part("", genai_types=genai_types)
            contents = [self._build_content(role="user", parts=[user_part], genai_types=genai_types)]

        if unsigned_history_tool_calls:
            logger.warning(
                "Skipped %d unsigned Gemini historical function call(s) and downgraded them to text fallback.",
                unsigned_history_tool_calls,
            )

        generation_config = self._build_generation_config(
            system_instruction=system_instruction,
            tools=tools,
            genai_types=genai_types,
        )

        client = genai.Client(api_key=self.api_key)
        model_used = self.model

        def _fallback_model_name(raw_model: str) -> str | None:
            candidate = raw_model.strip()
            if "gemini-3.1-" in candidate:
                return candidate.replace("gemini-3.1-", "gemini-3-")
            return None

        def _is_not_found_error(exc: Exception) -> bool:
            lowered = str(exc).lower()
            return "not found" in lowered or "404" in lowered

        try:
            stream = client.models.generate_content_stream(
                model=model_used,
                contents=contents,
                config=generation_config,
            )
            text_chunks, thinking_chunks, tool_acc = self._consume_stream(
                stream=stream,
                on_thinking_token=on_thinking_token,
                on_text_token=on_text_token,
            )
        except Exception as exc:
            fallback = _fallback_model_name(model_used)
            if fallback and _is_not_found_error(exc):
                model_used = fallback
                try:
                    stream = client.models.generate_content_stream(
                        model=model_used,
                        contents=contents,
                        config=generation_config,
                    )
                    text_chunks, thinking_chunks, tool_acc = self._consume_stream(
                        stream=stream,
                        on_thinking_token=on_thinking_token,
                        on_text_token=on_text_token,
                    )
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

        parsed_calls: list[ToolCall] = []
        for value in tool_acc.values():
            parsed_input: dict[str, Any]
            if isinstance(value.get("arguments_dict"), dict) and value["arguments_dict"]:
                parsed_input = dict(value["arguments_dict"])
            else:
                parsed_input = _parse_tool_arguments(value.get("arguments_buffer"))

            provider_specific = value.get("provider_specific_fields")
            parsed_calls.append(
                ToolCall(
                    id=str(value.get("id") or ""),
                    name=str(value.get("name") or "unknown_tool"),
                    input=parsed_input,
                    provider_specific_fields=provider_specific if isinstance(provider_specific, dict) else None,
                )
            )

        return ProviderStreamResult(
            text="".join(text_chunks).strip(),
            thinking="".join(thinking_chunks).strip(),
            tool_calls=parsed_calls,
            provider_state={
                "provider": "gemini",
                "model": model_used,
                "reasoning_effort": self.reasoning_effort,
                "include_thoughts": self.include_thoughts,
                "thinking_budget": self._resolve_thinking_budget(),
                "thinking_token_count": len(thinking_chunks),
                "unsigned_history_tool_call_count": unsigned_history_tool_calls,
            },
        )
