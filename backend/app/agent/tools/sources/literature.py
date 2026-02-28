from __future__ import annotations

from typing import Any
from urllib import parse
import xml.etree.ElementTree as ET

from app.config import Settings
from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.descriptions import render_tool_description
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


MODES = {"precision", "balanced", "recall"}


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


def _require_ids(payload: dict[str, Any], *, max_size: int = 200) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in ids:
        value = str(item or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        cleaned.append(value)

    if not cleaned:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid IDs provided in 'ids'")
    if len(cleaned) > max_size:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"Too many IDs. Maximum is {max_size}",
            details={"provided": len(cleaned), "max": max_size},
        )
    return cleaned


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


def _pubmed_api_params(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    params = {k: v for k, v in kwargs.items() if v is not None}
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key
    return params


def _extract_abstracts_from_efetch_xml(xml_text: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return {}

    out: dict[str, str] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid_node = article.find(".//PMID")
        if pmid_node is None or not (pmid_node.text or "").strip():
            continue
        pmid = (pmid_node.text or "").strip()
        abstract_parts: list[str] = []
        for abstract_text in article.findall(".//Abstract/AbstractText"):
            label = abstract_text.attrib.get("Label") if isinstance(abstract_text.attrib, dict) else None
            text = "".join(abstract_text.itertext()).strip()
            if not text:
                continue
            abstract_parts.append(f"{label}: {text}" if label else text)
        if abstract_parts:
            out[pmid] = "\n".join(abstract_parts)
    return out


def build_literature_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def pubmed_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=25, default_balanced=100, default_recall=250, maximum=500)

        page_token = str(payload.get("page_token", "")).strip() or "0"
        try:
            retstart = max(int(page_token), 0)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer offset string") from exc

        sort = str(payload.get("sort", "relevance")).strip().lower() or "relevance"
        if sort not in {"relevance", "pub date"}:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'sort' must be 'relevance' or 'pub date'")

        params = _pubmed_api_params(
            settings,
            db="pubmed",
            term=query,
            retmode="json",
            retmax=limit,
            retstart=retstart,
            usehistory="y",
            sort=sort,
        )
        data, headers = http.get_json(url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"pubmed_search_{retstart}", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        search = (data or {}).get("esearchresult") or {}
        ids = [str(item) for item in (search.get("idlist") or []) if str(item).strip()]
        count = int(search.get("count") or 0)
        next_offset = retstart + len(ids)
        has_more = next_offset < count

        return make_tool_output(
            source="pubmed",
            summary=f"Retrieved {len(ids)} PMID(s) from PubMed search.",
            result_kind="id_list",
            data={
                "query": query,
                "mode": mode,
                "count": count,
                "retstart": retstart,
                "webenv": search.get("webenv"),
                "query_key": search.get("querykey"),
                "query_translation": search.get("querytranslation"),
            },
            ids=ids,
            artifacts=artifact_refs,
            pagination={"next_page_token": str(next_offset) if has_more else None, "has_more": has_more},
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def pubmed_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=200)
        mode = _require_mode(payload)
        include_abstract = bool(payload.get("include_abstract", False))
        fields = payload.get("fields") or []
        if fields is not None and not isinstance(fields, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'fields' must be a list of field names")
        selected_fields = {str(item).strip() for item in fields if str(item).strip()}

        params = _pubmed_api_params(
            settings,
            db="pubmed",
            id=",".join(ids),
            retmode="json",
        )
        summary_data, headers = http.get_json(
            url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params=params,
        )

        abstracts_by_pmid: dict[str, str] = {}
        artifacts: list[dict[str, Any]] = []
        if include_abstract:
            fetch_params = _pubmed_api_params(
                settings,
                db="pubmed",
                id=",".join(ids),
                retmode="xml",
            )
            xml_text, _ = http.get_text(url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params=fetch_params)
            abstracts_by_pmid = _extract_abstracts_from_efetch_xml(xml_text)
            xml_ref = write_raw_json_artifact(ctx, "pubmed_fetch_xml_metadata", {"xml_size": len(xml_text)}) if ctx else None
            if xml_ref:
                artifacts.append(xml_ref)

        result = (summary_data or {}).get("result") or {}
        uids = [str(item) for item in (result.get("uids") or []) if str(item).strip()]

        records: list[dict[str, Any]] = []
        for uid in uids:
            item = result.get(uid) or {}
            pub_types = list(item.get("pubtype") or [])
            article_ids = list(item.get("articleids") or [])
            doi = None
            for aid in article_ids:
                if isinstance(aid, dict) and str(aid.get("idtype") or "").lower() == "doi":
                    doi = aid.get("value")
                    break

            record = {
                "pmid": uid,
                "title": item.get("title"),
                "pubdate": item.get("pubdate"),
                "source": item.get("source"),
                "pub_types": pub_types,
                "article_ids": article_ids,
                "doi": doi,
                "is_meta_or_systematic": any("meta" in str(pt).lower() or "systematic" in str(pt).lower() for pt in pub_types),
                "is_rct_like": any("randomized" in str(pt).lower() or "clinical trial" in str(pt).lower() for pt in pub_types),
            }
            if include_abstract:
                record["abstract"] = abstracts_by_pmid.get(uid)
            records.append(record)

        if selected_fields:
            filtered_records: list[dict[str, Any]] = []
            for record in records:
                filtered = {k: v for k, v in record.items() if k in selected_fields or k == "pmid"}
                filtered_records.append(filtered)
            records = filtered_records

        raw_ref = write_raw_json_artifact(ctx, "pubmed_fetch_esummary", summary_data) if ctx else None
        if raw_ref:
            artifacts.append(raw_ref)

        return make_tool_output(
            source="pubmed",
            summary=f"Fetched {len(records)} PubMed record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[record.get("pmid") for record in records if record.get("pmid")],
            citations=[{"pmid": rec.get("pmid"), "doi": rec.get("doi"), "title": rec.get("title"), "year": rec.get("pubdate")} for rec in records],
            artifacts=artifacts,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def openalex_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=20, default_balanced=50, default_recall=100, maximum=200)

        page_token = str(payload.get("page_token", "")).strip() or "1"
        try:
            page = max(int(page_token), 1)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer page string") from exc

        filter_value = str(payload.get("filter", "")).strip() or None

        params = {
            "search": query,
            "per-page": limit,
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
        has_more = bool(page * limit < count)

        return make_tool_output(
            source="openalex",
            summary=f"Retrieved {len(compact)} OpenAlex work(s) for query '{query}'.",
            result_kind="record_list",
            data={"query": query, "mode": mode, "works": compact, "meta": meta},
            ids=ids,
            citations=[
                {
                    "openalex_id": item.get("id"),
                    "doi": item.get("doi"),
                    "pmid": item.get("pmid"),
                    "title": item.get("display_name"),
                    "year": item.get("publication_year"),
                }
                for item in compact
            ],
            artifacts=artifacts,
            pagination={"next_page_token": str(page + 1) if has_more else None, "has_more": has_more},
            request_id=_request_id(headers),
            auth_required=True,
            auth_configured=bool(settings.openalex_api_key),
            ctx=ctx,
        )

    def openalex_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        ids = _require_ids(payload, max_size=50)
        mode = _require_mode(payload)

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for raw in ids:
            if raw.startswith("https://openalex.org/"):
                work_id = raw.rsplit("/", 1)[-1]
            else:
                work_id = raw
            url = f"https://api.openalex.org/works/{parse.quote(work_id)}"
            try:
                data, headers = http.get_json(url=url, params={"api_key": key})
                records.append(_compact_openalex_record(data))
                raw_ref = write_raw_json_artifact(ctx, f"openalex_fetch_{work_id}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{work_id}: {exc.message}")

        return make_tool_output(
            source="openalex",
            summary=f"Fetched {len(records)} OpenAlex record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
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
            request_id=_request_id(headers) if "headers" in locals() else None,
            auth_required=True,
            auth_configured=bool(settings.openalex_api_key),
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="pubmed_search",
            description=render_tool_description(
                purpose="Search PubMed as the primary biomedical literature source.",
                when=["you need biomedical evidence retrieval", "starting high-evidence-first literature discovery"],
                avoid=["you already have exact PMID list", "you need full paper metadata fetch"],
                critical_args=["query: PubMed query string", "mode: precision/balanced/recall", "limit/page_token: pagination controls"],
                returns="ID-first PMID list with count, history context, and pagination token.",
                fails_if=["query missing", "invalid mode", "limit/page token invalid", "PubMed upstream unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    "page_token": {"type": "string", "description": "Retstart offset as string"},
                    "sort": {"type": "string", "enum": ["relevance", "pub date"], "default": "relevance"},
                },
                "required": ["query"],
            },
            handler=pubmed_search,
            source="pubmed",
        ),
        ToolSpec(
            name="pubmed_fetch",
            description=render_tool_description(
                purpose="Fetch PubMed metadata records by PMID list.",
                when=["you already have PMID IDs", "you need publication type and evidence-level metadata"],
                avoid=["you only have free-text query", "you exceed PMID batch limits"],
                critical_args=["ids: PMID list", "mode: kept for policy consistency", "include_abstract/fields: payload size tuning"],
                returns="Record list keyed by PMID with publication metadata and optional abstracts.",
                fails_if=["ids missing", "ids exceed max batch", "PubMed upstream unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "include_abstract": {"type": "boolean", "default": False},
                    "fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["ids"],
            },
            handler=pubmed_fetch,
            source="pubmed",
        ),
        ToolSpec(
            name="openalex_search",
            description=render_tool_description(
                purpose="Search OpenAlex for citation expansion and cross-indexed literature discovery.",
                when=["you need citation-graph expansion", "you need broader scholarly coverage after core PubMed retrieval"],
                avoid=["OPENALEX_API_KEY is not configured", "using OpenAlex as sole biomedical source"],
                critical_args=["query: OpenAlex search text", "mode: precision/balanced/recall", "limit/page_token/filter: result controls"],
                returns="Record list of compact OpenAlex works plus pagination.",
                fails_if=["query missing", "OpenAlex key missing", "invalid limit/page token"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    "page_token": {"type": "string", "description": "OpenAlex page number as string"},
                    "filter": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=openalex_search,
            source="openalex",
        ),
        ToolSpec(
            name="openalex_fetch",
            description=render_tool_description(
                purpose="Fetch full OpenAlex work records for known work IDs.",
                when=["you already have OpenAlex IDs", "you need stable metadata for selected works"],
                avoid=["OPENALEX_API_KEY is not configured", "you only have free-text query"],
                critical_args=["ids: OpenAlex work IDs", "mode: policy consistency"],
                returns="Record list for requested OpenAlex IDs.",
                fails_if=["ids missing", "OpenAlex key missing", "invalid IDs"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=openalex_fetch,
            source="openalex",
        ),
    ]
