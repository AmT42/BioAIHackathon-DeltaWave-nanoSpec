from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.agent.tools.artifacts import finalize_manifest, write_request_artifact, write_response_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import normalize_tool_output
from app.agent.tools.errors import ToolExecutionError, unknown_error_payload


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    source: str = "internal"

    def _description_with_policy(self) -> str:
        description = str(self.description or "").strip()
        required_tokens = ("WHEN:", "AVOID:", "CRITICAL_ARGS:", "RETURNS:", "FAILS_IF:")
        if all(token in description for token in required_tokens):
            return description
        base = description or "Tool execution helper."
        return (
            f"WHEN: {base}\n"
            "AVOID: Misusing the tool outside its declared schema and intent.\n"
            "CRITICAL_ARGS: Refer to parameters schema for required fields.\n"
            "RETURNS: Structured tool output contract with data and metadata.\n"
            "FAILS_IF: Required args are missing or upstream/tool validation fails."
        )

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._description_with_policy(),
                "parameters": self.input_schema,
            },
        }

    def anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._description_with_policy(),
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(
        self,
        tools: list[ToolSpec],
        *,
        artifact_root: Path | None = None,
        source_cache_root: Path | None = None,
    ) -> None:
        self._by_name = {tool.name: tool for tool in tools}
        self._artifact_root = artifact_root
        self._source_cache_root = source_cache_root

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self._by_name.values()]

    def anthropic_schemas(self) -> list[dict[str, Any]]:
        return [tool.anthropic_schema() for tool in self._by_name.values()]

    def names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def get_spec(self, tool_name: str) -> ToolSpec | None:
        return self._by_name.get(tool_name)

    def _call_handler(self, handler: Callable[..., dict[str, Any]], payload: dict[str, Any], ctx: ToolContext | None) -> dict[str, Any]:
        params = list(inspect.signature(handler).parameters.values())
        if len(params) >= 2:
            return handler(payload, ctx)
        return handler(payload)

    def execute(self, tool_name: str, payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        tool = self._by_name.get(tool_name)
        if not tool:
            return {
                "status": "error",
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Unknown tool '{tool_name}'",
                    "retryable": False,
                    "details": {},
                },
            }

        effective_ctx = (
            ctx.with_tool(tool_name=tool_name, artifact_root=self._artifact_root, source_cache_root=self._source_cache_root)
            if ctx is not None
            else None
        )

        if effective_ctx is not None:
            try:
                write_request_artifact(effective_ctx, payload)
            except Exception:
                pass

        try:
            raw_result = self._call_handler(tool.handler, payload, effective_ctx)
            normalized = normalize_tool_output(raw_result, source=tool.source, ctx=effective_ctx)
            out = {
                "status": "success",
                "output": normalized,
            }
            if effective_ctx is not None:
                try:
                    write_response_artifact(effective_ctx, out)
                    finalize_manifest(effective_ctx, extra_artifacts=list(normalized.get("artifacts") or []))
                except Exception:
                    pass
            return out
        except ToolExecutionError as exc:
            error_payload = exc.to_error_payload()
            out = {
                "status": "error",
                "error": error_payload,
            }
            if effective_ctx is not None:
                try:
                    write_response_artifact(effective_ctx, out)
                    finalize_manifest(effective_ctx)
                except Exception:
                    pass
            return out
        except Exception as exc:  # pragma: no cover
            error_payload = unknown_error_payload(exc)
            out = {
                "status": "error",
                "error": error_payload,
            }
            if effective_ctx is not None:
                try:
                    write_response_artifact(effective_ctx, out)
                    finalize_manifest(effective_ctx)
                except Exception:
                    pass
            return out
