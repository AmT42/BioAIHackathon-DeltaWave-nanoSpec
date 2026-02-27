from __future__ import annotations

from typing import Any
from urllib import parse

from app.config import Settings
from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid")


def _ensure_openalex_key(settings: Settings) -> str:
    if not settings.openalex_api_key:
        raise ToolExecutionError(
            code="UNCONFIGURED",
            message="OPENALEX_API_KEY is required for OpenAlex tools",
            details={"env": "OPENALEX_API_KEY"},
        )
    return settings.openalex_api_key


def _compact_openalex_record(work: dict[str, Any]) -> dict[str, Any]:
    ids = work.get("ids") or {}
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return {
        "id": work.get("id"),
        "display_name": work.get("display_name"),
        "publication_year": work.get("publication_year"),
        "type": work.get("type"),
        "doi": (ids.get("doi") or "").replace("https://doi.org/", "") if ids.get("doi") else None,
        "pmid": (ids.get("pmid") or "").replace("https://pubmed.ncbi.nlm.nih.gov/", "").strip("/") if ids.get("pmid") else None,
        "openalex": ids.get("openalex"),
        "cited_by_count": work.get("cited_by_count"),
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
        "journal": source.get("display_name"),
    }


def build_literature_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def openalex_search_works(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")

        per_page = min(max(int(payload.get("per_page", 25)), 1), 200)
        page = max(int(payload.get("page", 1)), 1)
        filter_value = str(payload.get("filter", "")).strip() or None

        params = {
            "search": query,
            "per-page": per_page,
            "page": page,
            "api_key": key,
        }
        if filter_value:
            params["filter"] = filter_value

        data, headers = http.get_json(url="https://api.openalex.org/works", params=params)
        artifacts: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"openalex_search_page_{page}", data) if ctx else None
        if raw_ref:
            artifacts.append(raw_ref)

        works = list((data or {}).get("results") or [])
        compact = [_compact_openalex_record(item) for item in works]
        ids = [item.get("id") for item in compact if item.get("id")]

        meta = (data or {}).get("meta") or {}
        count = int(meta.get("count") or 0)
        has_more = bool(page * per_page < count)

        citations = [
            {
                "openalex_id": item.get("id"),
                "doi": item.get("doi"),
                "pmid": item.get("pmid"),
                "title": item.get("display_name"),
                "year": item.get("publication_year"),
            }
            for item in compact
        ]

        return make_tool_output(
            source="openalex",
            summary=f"Retrieved {len(compact)} OpenAlex work(s) for query '{query}'.",
            data={"query": query, "works": compact, "meta": meta},
            ids=ids,
            citations=citations,
            artifacts=artifacts,
            pagination={
                "next_page_token": str(page + 1) if has_more else None,
                "has_more": has_more,
            },
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def openalex_get_works(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for raw_id in ids:
            raw = str(raw_id).strip()
            if not raw:
                continue
            if raw.startswith("https://openalex.org/"):
                work_id = raw.rsplit("/", 1)[-1]
            elif raw.startswith("W"):
                work_id = raw
            else:
                work_id = raw
            url = f"https://api.openalex.org/works/{parse.quote(work_id)}"
            try:
                data, headers = http.get_json(url=url, params={"api_key": key})
                compact = _compact_openalex_record(data)
                records.append(compact)
                raw_ref = write_raw_json_artifact(ctx, f"openalex_work_{work_id}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{work_id}: {exc.message}")

        return make_tool_output(
            source="openalex",
            summary=f"Fetched {len(records)} OpenAlex work record(s).",
            data={"records": records},
            ids=[item.get("id") for item in records if item.get("id")],
            citations=[
                {
                    "openalex_id": item.get("id"),
                    "doi": item.get("doi"),
                    "pmid": item.get("pmid"),
                    "title": item.get("display_name"),
                    "year": item.get("publication_year"),
                }
                for item in records
            ],
            warnings=warnings,
            artifacts=artifacts,
            request_id=_request_id(headers) if records else None,
            ctx=ctx,
        )

    def pubmed_enrich_pmids(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        pmids = payload.get("pmids") or []
        if not isinstance(pmids, list) or not pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'pmids' must be a non-empty list")

        clean_pmids = [str(item).strip() for item in pmids if str(item).strip()]
        if not clean_pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid PMID values provided")

        params: dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(clean_pmids),
            "retmode": "json",
        }
        if settings.pubmed_api_key:
            params["api_key"] = settings.pubmed_api_key

        data, headers = http.get_json(
            url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params=params,
        )

        result = (data or {}).get("result") or {}
        uids = list(result.get("uids") or [])
        records: list[dict[str, Any]] = []
        for uid in uids:
            item = result.get(uid) or {}
            pub_types = list(item.get("pubtype") or [])
            article_ids = list(item.get("articleids") or [])
            records.append(
                {
                    "pmid": uid,
                    "title": item.get("title"),
                    "pubdate": item.get("pubdate"),
                    "source": item.get("source"),
                    "pub_types": pub_types,
                    "article_ids": article_ids,
                    "is_meta_or_systematic": any(
                        "meta" in str(pt).lower() or "systematic" in str(pt).lower() for pt in pub_types
                    ),
                    "is_rct_like": any("randomized" in str(pt).lower() or "clinical trial" in str(pt).lower() for pt in pub_types),
                }
            )

        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, "pubmed_esummary", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        return make_tool_output(
            source="pubmed",
            summary=f"Enriched {len(records)} PMID record(s) from PubMed metadata.",
            data={"records": records},
            ids=[record["pmid"] for record in records],
            citations=[{"pmid": rec["pmid"], "title": rec.get("title"), "year": rec.get("pubdate")} for rec in records],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="openalex_search_works",
            description="Search OpenAlex works and return IDs + compact metadata.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "per_page": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "filter": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=openalex_search_works,
            source="openalex",
        ),
        ToolSpec(
            name="openalex_get_works",
            description="Fetch specific OpenAlex works by IDs.",
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["ids"],
            },
            handler=openalex_get_works,
            source="openalex",
        ),
        ToolSpec(
            name="pubmed_enrich_pmids",
            description="Enrich PMIDs using PubMed ESummary metadata for evidence classification.",
            input_schema={
                "type": "object",
                "properties": {
                    "pmids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["pmids"],
            },
            handler=pubmed_enrich_pmids,
            source="pubmed",
        ),
    ]
