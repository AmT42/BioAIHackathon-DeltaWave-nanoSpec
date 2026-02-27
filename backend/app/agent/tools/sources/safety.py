from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from app.agent.tools.artifacts import write_raw_json_artifact, write_text_file_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid")


def build_safety_tools(http: SimpleHttpClient) -> list[ToolSpec]:
    def dailymed_search_labels(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        drug_name = str(payload.get("drug_name", "")).strip()
        if not drug_name:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'drug_name' is required")

        page = max(int(payload.get("page", 1)), 1)
        page_size = min(max(int(payload.get("page_size", 20)), 1), 100)

        data, headers = http.get_json(
            url="https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
            params={
                "drug_name": drug_name,
                "page": page,
                "pagesize": page_size,
            },
        )
        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"dailymed_search_{drug_name}", data) if ctx else None
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

        return make_tool_output(
            source="dailymed",
            summary=f"Found {len(records)} DailyMed SPL label(s) for '{drug_name}'.",
            data={"records": records},
            ids=ids,
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def dailymed_get_label_sections(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        setid = str(payload.get("setid", "")).strip()
        if not setid:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'setid' is required")

        xml_text, headers = http.get_text(
            url=f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml",
        )

        artifacts: list[dict[str, Any]] = []
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
            # fallback lightweight extraction
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

        return make_tool_output(
            source="dailymed",
            summary=f"Fetched DailyMed SPL XML for {setid} and extracted {len(unique_sections)} section title(s).",
            data={
                "setid": setid,
                "sections": unique_sections[:50],
            },
            ids=[setid],
            artifacts=artifacts,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def openfda_faers_aggregate(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        search = str(payload.get("search", "")).strip()
        count = str(payload.get("count", "patient.reaction.reactionmeddrapt.exact")).strip()
        limit = min(max(int(payload.get("limit", 10)), 1), 100)

        if not search:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'search' is required")

        data, headers = http.get_json(
            url="https://api.fda.gov/drug/event.json",
            params={
                "search": search,
                "count": count,
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
            data={
                "search": search,
                "count_field": count,
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
            name="dailymed_search_labels",
            description="Search DailyMed SPL labels by drug name.",
            input_schema={
                "type": "object",
                "properties": {
                    "drug_name": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "required": ["drug_name"],
            },
            handler=dailymed_search_labels,
            source="dailymed",
        ),
        ToolSpec(
            name="dailymed_get_label_sections",
            description="Fetch SPL XML by DailyMed setid and extract section titles.",
            input_schema={
                "type": "object",
                "properties": {
                    "setid": {"type": "string"},
                },
                "required": ["setid"],
            },
            handler=dailymed_get_label_sections,
            source="dailymed",
        ),
        ToolSpec(
            name="openfda_faers_aggregate",
            description="Run openFDA FAERS aggregate query with search/count parameters.",
            input_schema={
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "count": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
                "required": ["search"],
            },
            handler=openfda_faers_aggregate,
            source="openfda",
        ),
    ]
