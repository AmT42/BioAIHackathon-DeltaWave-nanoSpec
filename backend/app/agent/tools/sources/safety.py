from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from app.agent.tools.artifacts import write_raw_json_artifact, write_text_file_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.descriptions import render_tool_description
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


MODES = {"precision", "balanced", "recall"}


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid")


def _require_query(payload: dict[str, Any], key: str = "query") -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ToolExecutionError(code="VALIDATION_ERROR", message=f"'{key}' is required")
    return value


def _require_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode", "balanced")).strip().lower()
    if mode not in MODES:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message="'mode' must be one of: precision, balanced, recall",
            details={"allowed": sorted(MODES)},
        )
    return mode


def _limit_for_mode(payload: dict[str, Any], *, default_precision: int, default_balanced: int, default_recall: int, maximum: int) -> int:
    mode = _require_mode(payload)
    default = {
        "precision": default_precision,
        "balanced": default_balanced,
        "recall": default_recall,
    }[mode]
    raw = payload.get("limit", default)
    try:
        value = int(raw)
    except Exception as exc:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'limit' must be an integer") from exc
    if value < 1 or value > maximum:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"'limit' must be between 1 and {maximum}",
            details={"limit": value, "max": maximum},
        )
    return value


def _require_ids(payload: dict[str, Any], *, max_size: int = 20) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

    out: list[str] = []
    seen: set[str] = set()
    for item in ids:
        value = str(item or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)

    if not out:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid IDs provided in 'ids'")
    if len(out) > max_size:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"Too many IDs. Maximum is {max_size}",
            details={"provided": len(out), "max": max_size},
        )
    return out


def build_safety_tools(http: SimpleHttpClient) -> list[ToolSpec]:
    def dailymed_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=20, default_recall=50, maximum=100)
        page_token = str(payload.get("page_token", "")).strip() or "1"
        try:
            page = max(int(page_token), 1)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer page string") from exc

        data, headers = http.get_json(
            url="https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
            params={
                "drug_name": query,
                "page": page,
                "pagesize": limit,
            },
        )

        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"dailymed_search_{query}_{page}", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        spls = list((data or {}).get("data") or [])
        records = [
            {
                "setid": item.get("setid") or item.get("setId"),
                "title": item.get("title"),
                "published_date": item.get("published_date") or item.get("publishedDate"),
            }
            for item in spls
        ]
        ids = [record.get("setid") for record in records if record.get("setid")]

        has_more = len(records) >= limit

        return make_tool_output(
            source="dailymed",
            summary=f"Found {len(records)} DailyMed SPL label(s) for '{query}'.",
            result_kind="record_list",
            data={"query": query, "mode": mode, "records": records},
            ids=ids,
            artifacts=artifact_refs,
            pagination={"next_page_token": str(page + 1) if has_more else None, "has_more": has_more},
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def dailymed_fetch_sections(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=20)
        mode = _require_mode(payload)

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for setid in ids:
            try:
                xml_text, headers = http.get_text(
                    url=f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml",
                )

                raw_file = write_text_file_artifact(ctx, f"dailymed_{setid}.xml", xml_text, subdir="raw") if ctx else None
                if raw_file:
                    artifacts.append(raw_file)

                sections: list[str] = []
                try:
                    root = ET.fromstring(xml_text)
                    for element in root.iter():
                        tag = element.tag.lower()
                        if tag.endswith("title"):
                            text = (element.text or "").strip()
                            if text:
                                sections.append(text)
                except Exception:
                    sections = []

                if not sections:
                    sections = re.findall(r"<title[^>]*>(.*?)</title>", xml_text, flags=re.IGNORECASE | re.DOTALL)
                    sections = [re.sub(r"\s+", " ", item).strip() for item in sections if item.strip()]

                unique_sections: list[str] = []
                seen = set()
                for sec in sections:
                    key = sec.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    unique_sections.append(sec)

                records.append(
                    {
                        "setid": setid,
                        "sections": unique_sections[:80],
                    }
                )
            except ToolExecutionError as exc:
                warnings.append(f"{setid}: {exc.message}")

        return make_tool_output(
            source="dailymed",
            summary=f"Fetched DailyMed sections for {len(records)} SPL label(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[record.get("setid") for record in records if record.get("setid")],
            warnings=warnings,
            artifacts=artifacts,
            request_id=_request_id(headers) if "headers" in locals() else None,
            ctx=ctx,
        )

    def openfda_faers_aggregate(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=25, default_recall=50, maximum=100)
        count_field = str(payload.get("count_field", "patient.reaction.reactionmeddrapt.exact")).strip()

        data, headers = http.get_json(
            url="https://api.fda.gov/drug/event.json",
            params={
                "search": query,
                "count": count_field,
                "limit": limit,
            },
        )

        results = list((data or {}).get("results") or [])
        rows = [{"term": item.get("term"), "count": item.get("count")} for item in results]

        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, "openfda_faers_aggregate", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        return make_tool_output(
            source="openfda",
            summary=f"Aggregated {len(rows)} FAERS bucket(s) from openFDA.",
            result_kind="aggregate",
            data={
                "query": query,
                "mode": mode,
                "count_field": count_field,
                "rows": rows,
                "note": "Spontaneous reports are signal-only and do not establish incidence or causality.",
            },
            ids=[row.get("term") for row in rows if row.get("term")],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="dailymed_search",
            description=render_tool_description(
                purpose="Search DailyMed SPL labels for drug safety labeling context.",
                when=["you need official label anchors", "you have a normalized drug label query"],
                avoid=["you have exact setid and only need sections", "query is not drug-like"],
                critical_args=["query: drug name", "mode: precision/balanced/recall", "limit/page_token: paging controls"],
                returns="Record list of SPL label candidates with setid IDs.",
                fails_if=["query missing", "invalid mode", "invalid limit/page token"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "page_token": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=dailymed_search,
            source="dailymed",
        ),
        ToolSpec(
            name="dailymed_fetch_sections",
            description=render_tool_description(
                purpose="Fetch section titles from DailyMed SPL XML documents by setid.",
                when=["you already have DailyMed setid IDs", "you need warnings/contraindication section anchors"],
                avoid=["you only have free-text query", "you exceed fetch batch limits"],
                critical_args=["ids: DailyMed setid list", "mode: policy consistency"],
                returns="Record list with setid and extracted section titles.",
                fails_if=["ids missing", "too many IDs", "upstream XML retrieval failure"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=dailymed_fetch_sections,
            source="dailymed",
        ),
        ToolSpec(
            name="openfda_faers_aggregate",
            description=render_tool_description(
                purpose="Aggregate openFDA FAERS events for post-marketing safety signal awareness.",
                when=["you need safety signal context", "you want reaction frequency buckets"],
                avoid=["treating FAERS as causal incidence", "querying without normalized drug terms"],
                critical_args=["query: openFDA search expression", "count_field: aggregation target", "mode/limit: bucket depth"],
                returns="Aggregate rows (term/count) plus interpretation note.",
                fails_if=["query missing", "invalid mode", "invalid limit", "openFDA unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count_field": {"type": "string", "default": "patient.reaction.reactionmeddrapt.exact"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                },
                "required": ["query"],
            },
            handler=openfda_faers_aggregate,
            source="openfda",
        ),
    ]
