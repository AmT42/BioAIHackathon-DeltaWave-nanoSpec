from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agent.tools.context import ToolContext

CONTRACT_VERSION = "2.0"
RESULT_KINDS = {"id_list", "record_list", "document", "aggregate", "status"}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _lineage_from_ctx(ctx: ToolContext | None) -> dict[str, Any]:
    if ctx is None:
        return {
            "thread_id": None,
            "run_id": None,
            "tool_use_id": None,
        }
    return {
        "thread_id": ctx.thread_id,
        "run_id": ctx.run_id,
        "tool_use_id": ctx.tool_use_id,
    }


def _coerce_result_kind(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in RESULT_KINDS:
        return candidate
    return "record_list"


def _coerce_tool_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        tool = str(item or "").strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        out.append(tool)
    return out


def make_tool_output(
    *,
    source: str,
    summary: str,
    result_kind: str = "record_list",
    data: Any | None = None,
    ids: list[Any] | None = None,
    citations: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    pagination: dict[str, Any] | None = None,
    auth_required: bool = False,
    auth_configured: bool = True,
    next_recommended_tools: list[str] | None = None,
    request_id: str | None = None,
    ctx: ToolContext | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "result_kind": _coerce_result_kind(result_kind),
        "summary": summary,
        "data": data if data is not None else {},
        "ids": ids or [],
        "citations": citations or [],
        "warnings": warnings or [],
        "artifacts": artifacts or [],
        "pagination": pagination
        or {
            "next_page_token": None,
            "has_more": False,
        },
        "source_meta": {
            "source": source,
            "request_id": request_id,
            "retrieved_at": utc_iso(),
            "auth": {
                "required": bool(auth_required),
                "configured": bool(auth_configured),
            },
            "lineage": _lineage_from_ctx(ctx),
        },
        "guidance": {
            "next_recommended_tools": _coerce_tool_list(next_recommended_tools or []),
        },
    }


def normalize_tool_output(
    output: Any,
    *,
    source: str,
    ctx: ToolContext | None,
) -> dict[str, Any]:
    if not isinstance(output, dict):
        return make_tool_output(
            source=source,
            summary="Tool completed.",
            result_kind="status",
            data={"value": output},
            ctx=ctx,
        )

    has_contract = all(
        key in output
        for key in [
            "summary",
            "data",
            "ids",
            "citations",
            "warnings",
            "artifacts",
            "pagination",
            "source_meta",
        ]
    )
    if has_contract:
        normalized = dict(output)
        normalized["contract_version"] = CONTRACT_VERSION
        normalized["result_kind"] = _coerce_result_kind(normalized.get("result_kind"))
        source_meta = normalized.get("source_meta")
        if not isinstance(source_meta, dict):
            source_meta = {}
        source_meta.setdefault("source", source)
        source_meta.setdefault("request_id", None)
        source_meta.setdefault("retrieved_at", utc_iso())
        auth = source_meta.get("auth")
        if not isinstance(auth, dict):
            auth = {}
        auth.setdefault("required", False)
        auth.setdefault("configured", True)
        auth["required"] = bool(auth.get("required"))
        auth["configured"] = bool(auth.get("configured"))
        source_meta["auth"] = auth
        source_meta.setdefault("lineage", _lineage_from_ctx(ctx))
        normalized["source_meta"] = source_meta
        guidance = normalized.get("guidance")
        if not isinstance(guidance, dict):
            guidance = {}
        guidance["next_recommended_tools"] = _coerce_tool_list(guidance.get("next_recommended_tools"))
        normalized["guidance"] = guidance
        return normalized

    summary = str(output.get("summary") or "Tool completed.")
    data = output.get("data") if "data" in output else output
    return make_tool_output(
        source=source,
        summary=summary,
        result_kind=str(output.get("result_kind") or "record_list"),
        data=data,
        ids=list(output.get("ids") or []),
        citations=list(output.get("citations") or []),
        warnings=list(output.get("warnings") or []),
        artifacts=list(output.get("artifacts") or []),
        pagination=output.get("pagination"),
        auth_required=bool(((output.get("source_meta") or {}).get("auth") or {}).get("required"))
        if isinstance(output.get("source_meta"), dict)
        else False,
        auth_configured=bool(((output.get("source_meta") or {}).get("auth") or {}).get("configured"))
        if isinstance(output.get("source_meta"), dict)
        else True,
        next_recommended_tools=_coerce_tool_list((output.get("guidance") or {}).get("next_recommended_tools"))
        if isinstance(output.get("guidance"), dict)
        else [],
        request_id=(output.get("source_meta") or {}).get("request_id") if isinstance(output.get("source_meta"), dict) else None,
        ctx=ctx,
    )
