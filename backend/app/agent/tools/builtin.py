from __future__ import annotations

import ast
import operator
from typing import Any
import httpx
try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency in constrained envs
    BeautifulSoup = None

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


def tool_web_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise ValueError("'query' is required")

    if BeautifulSoup is None:
        results = [
            {
                "title": "Dependency missing",
                "url": "",
                "snippet": "BeautifulSoup (bs4) is not installed in this runtime.",
            }
        ]
        return make_tool_output(
            source="builtin",
            summary=f"Performed web search for {query}.",
            data={
                "query": query,
                "results": results,
            },
            ids=[],
            citations=[],
            ctx=ctx,
        )
        
    url = "https://lite.duckduckgo.com/lite/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    data = {"q": query}
    try:
        resp = httpx.post(url, headers=headers, data=data, timeout=10.0)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for tr in soup.find_all("tr"):
            td = tr.find("td", class_="result-snippet")
            if td:
                snippet = td.get_text(" ", strip=True)
                prev_tr = tr.find_previous_sibling("tr")
                a_tag = prev_tr.find("a", class_="result-link") if prev_tr else None
                if not a_tag:
                    prev_prev_tr = prev_tr.find_previous_sibling("tr") if prev_tr else None
                    a_tag = prev_prev_tr.find("a", class_="result-link") if prev_prev_tr else None
                if a_tag:
                    results.append({
                        "title": a_tag.get_text(strip=True),
                        "url": a_tag.get("href"),
                        "snippet": snippet
                    })
    except Exception as e:
        results = [{"title": "Error", "url": "", "snippet": str(e)}]

    return make_tool_output(
        source="builtin",
        summary=f"Performed web search for {query}.",
        data={
            "query": query,
            "results": results[:5],
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
            "year": 2023,
            "doi": "10.0000/mock-doi-2",
        },
    ]
    return make_tool_output(
        source="builtin",
        summary="Returned mock paper stubs.",
        data={
            "topic": topic,
            "papers": papers,
        },
        ids=[str(paper.get("doi") or "") for paper in papers if str(paper.get("doi") or "").strip()],
        citations=[],
        ctx=ctx,
    )


def builtin_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="calc",
            description=(
                "WHEN: Evaluate a basic arithmetic expression for deterministic local computation.\n"
                "AVOID: Passing non-arithmetic or unsafe code-like expressions.\n"
                "CRITICAL_ARGS: expression.\n"
                "RETURNS: numeric evaluation result.\n"
                "FAILS_IF: expression is missing or unsupported."
            ),
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
            name="web_search",
            description=(
                "WHEN: Need to search the internet for current or general information.\n"
                "AVOID: Only using for biomedical data if a specialized wrapper is better.\n"
                "CRITICAL_ARGS: query.\n"
                "RETURNS: web search result list.\n"
                "FAILS_IF: query is missing."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=tool_web_search,
            source="builtin",
        ),
        ToolSpec(
            name="fetch_paper_stub",
            description=(
                "WHEN: Provide deterministic placeholder paper metadata for demos/tests.\n"
                "AVOID: Treating output as real publications.\n"
                "CRITICAL_ARGS: topic.\n"
                "RETURNS: mock paper metadata list.\n"
                "FAILS_IF: topic is missing."
            ),
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
