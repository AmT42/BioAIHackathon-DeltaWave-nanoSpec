from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec]) -> None:
        self._by_name = {tool.name: tool for tool in tools}

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self._by_name.values()]

    def anthropic_schemas(self) -> list[dict[str, Any]]:
        return [tool.anthropic_schema() for tool in self._by_name.values()]

    def execute(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        tool = self._by_name.get(tool_name)
        if not tool:
            return {
                "status": "error",
                "error": {"message": f"Unknown tool '{tool_name}'"},
            }
        try:
            result = tool.handler(payload)
            return {
                "status": "success",
                "output": result,
            }
        except Exception as exc:  # pragma: no cover
            return {
                "status": "error",
                "error": {"message": str(exc)},
            }
