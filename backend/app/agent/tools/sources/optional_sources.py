from __future__ import annotations

from typing import Any
from urllib import parse

from app.config import Settings
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.descriptions import render_tool_description
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


MODES = {"precision", "balanced", "recall"}


def _require_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode", "balanced")).strip().lower()
    if mode not in MODES:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message="'mode' must be one of: precision, balanced, recall",
            details={"allowed": sorted(MODES)},
        )
    return mode


def _require_query(payload: dict[str, Any], key: str = "query") -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ToolExecutionError(code="VALIDATION_ERROR", message=f"'{key}' is required")
    return value


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


def _require_ids(payload: dict[str, Any], *, max_size: int = 50) -> list[str]:
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


def _require_epistemonikos_key(settings: Settings) -> str:
    if not settings.epistemonikos_api_key:
        raise ToolExecutionError(
            code="UNCONFIGURED",
            message="EPISTEMONIKOS_API_KEY is required for Epistemonikos tools",
            details={"env": "EPISTEMONIKOS_API_KEY"},
        )
    return settings.epistemonikos_api_key


def _epistemonikos_auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f'Token token="{api_key}"'}


def build_optional_source_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def chembl_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=20, default_recall=50, maximum=100)

        data, _ = http.get_json(
            url="https://www.ebi.ac.uk/chembl/api/data/molecule/search.json",
            params={"q": query, "limit": limit},
        )
        molecules = list((data or {}).get("molecules") or [])
        records = [
            {
                "chembl_id": item.get("molecule_chembl_id"),
                "pref_name": item.get("pref_name"),
                "molecule_type": item.get("molecule_type"),
                "max_phase": item.get("max_phase"),
            }
            for item in molecules
        ]
        return make_tool_output(
            source="chembl",
            summary=f"Found {len(records)} ChEMBL molecule candidate(s).",
            result_kind="record_list",
            data={"query": query, "mode": mode, "records": records},
            ids=[record.get("chembl_id") for record in records if record.get("chembl_id")],
            ctx=ctx,
        )

    def chembl_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=30)
        mode = _require_mode(payload)

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for chembl_id in ids:
            try:
                data, _ = http.get_json(url=f"https://www.ebi.ac.uk/chembl/api/data/molecule/{parse.quote(chembl_id)}.json")
                records.append(data)
            except ToolExecutionError as exc:
                warnings.append(f"{chembl_id}: {exc.message}")

        return make_tool_output(
            source="chembl",
            summary=f"Fetched {len(records)} ChEMBL molecule record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[str((rec or {}).get("molecule_chembl_id")) for rec in records if (rec or {}).get("molecule_chembl_id")],
            warnings=warnings,
            ctx=ctx,
        )

    def chebi_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=20, default_recall=50, maximum=100)
        page_token = str(payload.get("page_token", "")).strip() or "1"
        try:
            page = max(int(page_token), 1)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer page string") from exc

        data, _ = http.get_json(
            url="https://www.ebi.ac.uk/chebi/backend/api/public/es_search/",
            params={"query": query, "size": limit, "page": page},
        )
        items = list((data or {}).get("results") or [])
        records = [
            {
                "chebi_id": ((item.get("_source") or {}).get("chebi_accession") if isinstance(item, dict) else None),
                "name": ((item.get("_source") or {}).get("name") if isinstance(item, dict) else None),
                "stars": ((item.get("_source") or {}).get("stars") if isinstance(item, dict) else None),
                "mass": ((item.get("_source") or {}).get("mass") if isinstance(item, dict) else None),
                "formula": ((item.get("_source") or {}).get("formula") if isinstance(item, dict) else None),
                "inchikey": ((item.get("_source") or {}).get("inchikey") if isinstance(item, dict) else None),
            }
            for item in items
        ]
        has_more = len(records) >= limit
        return make_tool_output(
            source="chebi",
            summary=f"Found {len(records)} ChEBI candidate(s).",
            result_kind="record_list",
            data={"query": query, "mode": mode, "page": page, "records": records},
            ids=[record.get("chebi_id") for record in records if record.get("chebi_id")],
            pagination={"next_page_token": str(page + 1) if has_more else None, "has_more": has_more},
            ctx=ctx,
        )

    def chebi_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=30)
        mode = _require_mode(payload)

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for chebi_id in ids:
            normalized = chebi_id
            if normalized.upper().startswith("CHEBI:"):
                normalized = f"CHEBI:{normalized.split(':', 1)[1]}"
            try:
                data, _ = http.get_json(
                    url=f"https://www.ebi.ac.uk/chebi/backend/api/public/compound/{parse.quote(normalized)}/"
                )
                names = data.get("names") if isinstance(data, dict) else {}
                synonym_nodes = names.get("SYNONYM") if isinstance(names, dict) else []
                synonyms = [str(item.get("name")) for item in (synonym_nodes or []) if isinstance(item, dict) and item.get("name")]
                records.append({"entity": data, "synonyms": synonyms[:50]})
            except ToolExecutionError as exc:
                warnings.append(f"{chebi_id}: {exc.message}")

        return make_tool_output(
            source="chebi",
            summary=f"Fetched {len(records)} ChEBI entity record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[str((item.get("entity") or {}).get("chebi_accession")) for item in records if (item.get("entity") or {}).get("chebi_accession")],
            warnings=warnings,
            ctx=ctx,
        )

    def semanticscholar_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=20, default_recall=50, maximum=100)
        fields = str(payload.get("fields", "title,year,paperId,externalIds,citationCount")).strip()
        headers = {}
        if settings.semanticscholar_api_key:
            headers["x-api-key"] = settings.semanticscholar_api_key

        data, _ = http.get_json(
            url="https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query, "limit": limit, "fields": fields},
            headers=headers,
        )
        papers = list((data or {}).get("data") or [])
        records = [
            {
                "paper_id": item.get("paperId"),
                "title": item.get("title"),
                "year": item.get("year"),
                "citation_count": item.get("citationCount"),
                "external_ids": item.get("externalIds"),
            }
            for item in papers
        ]

        return make_tool_output(
            source="semanticscholar",
            summary=f"Found {len(records)} Semantic Scholar paper(s).",
            result_kind="record_list",
            data={"query": query, "mode": mode, "records": records},
            ids=[record.get("paper_id") for record in records if record.get("paper_id")],
            citations=[
                {
                    "paper_id": record.get("paper_id"),
                    "title": record.get("title"),
                    "year": record.get("year"),
                    "external_ids": record.get("external_ids"),
                }
                for record in records
            ],
            ctx=ctx,
        )

    def semanticscholar_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=50)
        mode = _require_mode(payload)
        fields = str(payload.get("fields", "title,year,externalIds,citationCount,abstract")).strip()
        headers = {}
        if settings.semanticscholar_api_key:
            headers["x-api-key"] = settings.semanticscholar_api_key

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for paper_id in ids:
            try:
                data, _ = http.get_json(
                    url=f"https://api.semanticscholar.org/graph/v1/paper/{parse.quote(paper_id, safe='')}",
                    params={"fields": fields},
                    headers=headers,
                )
                records.append(data)
            except ToolExecutionError as exc:
                warnings.append(f"{paper_id}: {exc.message}")

        return make_tool_output(
            source="semanticscholar",
            summary=f"Fetched {len(records)} Semantic Scholar paper record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[record.get("paperId") for record in records if isinstance(record, dict)],
            warnings=warnings,
            ctx=ctx,
        )

    def epistemonikos_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _require_epistemonikos_key(settings)
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=20, default_recall=50, maximum=100)
        page_token = str(payload.get("page_token", "")).strip() or "1"
        try:
            page = max(int(page_token), 1)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer page string") from exc

        data, _ = http.get_json(
            url="https://api.epistemonikos.org/v1/documents/search",
            params={
                "q": query,
                "p": page,
                "classification": "systematic-review",
            },
            headers=_epistemonikos_auth_headers(key),
        )
        records = list((data or {}).get("results") or [])
        compact = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "year": item.get("year"),
                "classification": item.get("classification"),
                "url": item.get("url"),
            }
            for item in records[:limit]
            if isinstance(item, dict)
        ]

        has_more = len(compact) >= limit
        return make_tool_output(
            source="epistemonikos",
            summary=f"Found {len(compact)} Epistemonikos review candidate(s).",
            result_kind="record_list",
            data={
                "query": query,
                "mode": mode,
                "api_version": "v1",
                "records": compact,
                "search_info": (data or {}).get("search_info") or {},
            },
            ids=[str(item.get("id")) for item in compact if item.get("id")],
            auth_required=True,
            auth_configured=bool(settings.epistemonikos_api_key),
            pagination={"next_page_token": str(page + 1) if has_more else None, "has_more": has_more},
            ctx=ctx,
        )

    def epistemonikos_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _require_epistemonikos_key(settings)
        ids = _require_ids(payload, max_size=30)
        mode = _require_mode(payload)

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for review_id in ids:
            try:
                data, _ = http.get_json(
                    url=f"https://api.epistemonikos.org/v1/documents/{parse.quote(review_id)}",
                    headers=_epistemonikos_auth_headers(key),
                )
                records.append(data)
            except ToolExecutionError as exc:
                warnings.append(f"{review_id}: {exc.message}")

        return make_tool_output(
            source="epistemonikos",
            summary=f"Fetched {len(records)} Epistemonikos review record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[str((record or {}).get("id")) for record in records if (record or {}).get("id")],
            warnings=warnings,
            auth_required=True,
            auth_configured=bool(settings.epistemonikos_api_key),
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="chembl_search",
            description=render_tool_description(
                purpose="Search ChEMBL molecules for mechanism and target-oriented enrichment.",
                when=["you need optional mechanistic context", "compound requires target-level follow-up"],
                avoid=["core evidence retrieval phase", "query unrelated to molecules"],
                critical_args=["query: molecule text", "mode/limit: breadth controls"],
                returns="Record list of candidate ChEMBL molecules.",
                fails_if=["query missing", "invalid mode", "invalid limit"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "required": ["query"],
            },
            handler=chembl_search,
            source="chembl",
        ),
        ToolSpec(
            name="chembl_fetch",
            description=render_tool_description(
                purpose="Fetch ChEMBL molecule records by known ChEMBL IDs.",
                when=["you already have ChEMBL IDs", "you need full molecule metadata"],
                avoid=["no IDs available", "batch too large"],
                critical_args=["ids: ChEMBL IDs", "mode: policy consistency"],
                returns="Record list of ChEMBL molecule payloads.",
                fails_if=["ids missing", "too many IDs"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 30},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=chembl_fetch,
            source="chembl",
        ),
        ToolSpec(
            name="chebi_search",
            description=render_tool_description(
                purpose="Search ChEBI entities for optional ontology-level chemical enrichment.",
                when=["you need ChEBI ontology mapping", "compound ontology disambiguation"],
                avoid=["core retrieval phase", "non-chemical queries"],
                critical_args=["query: entity text", "mode/limit/page_token: search depth controls"],
                returns="Record list of ChEBI candidates with accession IDs.",
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
            handler=chebi_search,
            source="chebi",
        ),
        ToolSpec(
            name="chebi_fetch",
            description=render_tool_description(
                purpose="Fetch ChEBI entity records by accession IDs.",
                when=["you already have ChEBI IDs", "you need synonyms and metadata"],
                avoid=["no IDs available", "using this for core evidence retrieval"],
                critical_args=["ids: ChEBI accession IDs", "mode: policy consistency"],
                returns="Record list with ChEBI entity payload and synonyms.",
                fails_if=["ids missing", "too many IDs"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 30},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=chebi_fetch,
            source="chebi",
        ),
        ToolSpec(
            name="semanticscholar_search",
            description=render_tool_description(
                purpose="Search Semantic Scholar for optional citation expansion.",
                when=["you need citation-driven expansion", "identifying influential related papers"],
                avoid=["primary biomedical typing phase", "querying without clear concept terms"],
                critical_args=["query: paper search text", "mode/limit: breadth", "fields: response fields"],
                returns="Record list of paper candidates with external IDs.",
                fails_if=["query missing", "invalid mode", "invalid limit"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "fields": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=semanticscholar_search,
            source="semanticscholar",
        ),
        ToolSpec(
            name="semanticscholar_fetch",
            description=render_tool_description(
                purpose="Fetch Semantic Scholar paper records by paper IDs.",
                when=["you already have Semantic Scholar IDs", "you need full abstract/citation payload"],
                avoid=["no IDs available", "batch exceeds limits"],
                critical_args=["ids: paper IDs", "mode: policy consistency", "fields: optional output tuning"],
                returns="Record list of Semantic Scholar paper payloads.",
                fails_if=["ids missing", "too many IDs"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "fields": {"type": "string"},
                },
                "required": ["ids"],
            },
            handler=semanticscholar_fetch,
            source="semanticscholar",
        ),
        ToolSpec(
            name="epistemonikos_search",
            description=render_tool_description(
                purpose="Search Epistemonikos systematic reviews (key-gated optional source).",
                when=["API key is configured", "you need review-layer enrichment"],
                avoid=["EPISTEMONIKOS_API_KEY missing", "using this as mandatory core path"],
                critical_args=["query: review search text", "mode/limit/page_token: retrieval controls"],
                returns="Record list of review candidates with Epistemonikos IDs.",
                fails_if=["query missing", "API key missing", "invalid mode or pagination"],
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
            handler=epistemonikos_search,
            source="epistemonikos",
        ),
        ToolSpec(
            name="epistemonikos_fetch",
            description=render_tool_description(
                purpose="Fetch Epistemonikos review records by review IDs (key-gated).",
                when=["API key is configured", "you already have review IDs"],
                avoid=["EPISTEMONIKOS_API_KEY missing", "no IDs provided"],
                critical_args=["ids: review IDs", "mode: policy consistency"],
                returns="Record list of Epistemonikos review payloads.",
                fails_if=["ids missing", "API key missing", "too many IDs"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 30},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=epistemonikos_fetch,
            source="epistemonikos",
        ),
    ]
