from __future__ import annotations

import ast
import operator
from typing import Any

from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.registry import ToolRegistry, ToolSpec


_ALLOWED_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


def _safe_eval(expr: str) -> float:
    node = ast.parse(expr, mode="eval")

    def _eval(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp):
            op_type = type(n.op)
            if op_type not in _ALLOWED_OPS:
                raise ValueError("unsupported operator")
            return float(_ALLOWED_OPS[op_type](_eval(n.left), _eval(n.right)))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -_eval(n.operand)
        raise ValueError("unsupported expression")

    return _eval(node)


def tool_calc(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
    expression = str(payload.get("expression", "")).strip()
    if not expression:
        raise ValueError("'expression' is required")
    value = _safe_eval(expression)
    return make_tool_output(
        source="builtin",
        summary="Calculated arithmetic expression.",
        data={
            "expression": expression,
            "value": value,
        },
        ids=[],
        ctx=ctx,
    )


def tool_web_search_mock(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise ValueError("'query' is required")
    return make_tool_output(
        source="builtin",
        summary="Returned deterministic mock web search result.",
        data={
            "query": query,
            "results": [
                {
                    "title": f"Mock result for {query}",
                    "url": "https://example.org/mock-result",
                    "snippet": "This is a deterministic mock web result for demo purposes.",
                }
            ],
        },
        ids=[],
        citations=[],
        ctx=ctx,
    )


def tool_fetch_paper_stub(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
    topic = str(payload.get("topic", "")).strip()
    if not topic:
        raise ValueError("'topic' is required")
    papers = [
        {
            "title": f"{topic}: methods overview",
            "authors": ["A. Researcher", "B. Scientist"],
            "year": 2024,
            "doi": "10.0000/mock-doi-1",
        },
        {
            "title": f"{topic}: practical benchmark",
            "authors": ["C. Analyst"],
            "year": 2025,
            "doi": "10.0000/mock-doi-2",
        },
    ]
    return make_tool_output(
        source="builtin",
        summary="Returned deterministic mock papers.",
        data={"topic": topic, "papers": papers},
        ids=[paper["doi"] for paper in papers],
        citations=[{"doi": paper["doi"], "title": paper["title"], "year": paper["year"]} for paper in papers],
        ctx=ctx,
    )


def builtin_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="calc",
            description="Evaluate a basic arithmetic expression.",
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Arithmetic expression, e.g. (2+3)*4"}
                },
                "required": ["expression"],
            },
            handler=tool_calc,
            source="builtin",
        ),
        ToolSpec(
            name="web_search_mock",
            description="Return deterministic mock web search results for demos.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=tool_web_search_mock,
            source="builtin",
        ),
        ToolSpec(
            name="fetch_paper_stub",
            description="Return mock life-science paper metadata for a topic.",
            input_schema={
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
            handler=tool_fetch_paper_stub,
            source="builtin",
        ),
    ]


def create_builtin_registry() -> ToolRegistry:
    tools = builtin_tool_specs()
    return ToolRegistry(tools)
