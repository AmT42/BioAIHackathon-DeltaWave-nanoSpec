from __future__ import annotations

from typing import Any
from urllib import parse

from app.config import Settings
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


def _require_epistemonikos_key(settings: Settings) -> str:
    if not settings.epistemonikos_api_key:
        raise ToolExecutionError(
            code="UNCONFIGURED",
            message="EPISTEMONIKOS_API_KEY is required for Epistemonikos tools",
            details={"env": "EPISTEMONIKOS_API_KEY"},
        )
    return settings.epistemonikos_api_key


def build_optional_source_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def chembl_search_molecules(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")
        limit = min(max(int(payload.get("limit", 20)), 1), 100)

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
            data={"records": records},
            ids=[record.get("chembl_id") for record in records if record.get("chembl_id")],
            ctx=ctx,
        )

    def chembl_get_molecule(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        chembl_id = str(payload.get("chembl_id", "")).strip()
        if not chembl_id:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'chembl_id' is required")
        data, _ = http.get_json(url=f"https://www.ebi.ac.uk/chembl/api/data/molecule/{parse.quote(chembl_id)}.json")
        return make_tool_output(
            source="chembl",
            summary=f"Fetched ChEMBL molecule {chembl_id}.",
            data={"molecule": data},
            ids=[chembl_id],
            ctx=ctx,
        )

    def chebi_search_entities(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")
        limit = min(max(int(payload.get("limit", 20)), 1), 100)

        data, _ = http.get_json(
            url="https://www.ebi.ac.uk/chebi/backend/api/search",
            params={"q": query, "limit": limit},
        )
        items = list((data or {}).get("items") or (data or {}).get("results") or [])
        records = [
            {
                "chebi_id": item.get("chebiId") or item.get("id"),
                "name": item.get("chebiAsciiName") or item.get("name"),
                "definition": item.get("definition"),
            }
            for item in items
        ]
        return make_tool_output(
            source="chebi",
            summary=f"Found {len(records)} ChEBI candidate(s).",
            data={"records": records},
            ids=[record.get("chebi_id") for record in records if record.get("chebi_id")],
            ctx=ctx,
        )

    def chebi_get_entity(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        chebi_id = str(payload.get("chebi_id", "")).strip()
        if not chebi_id:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'chebi_id' is required")
        data, _ = http.get_json(url=f"https://www.ebi.ac.uk/chebi/backend/api/entities/{parse.quote(chebi_id)}")
        return make_tool_output(
            source="chebi",
            summary=f"Fetched ChEBI entity {chebi_id}.",
            data={"entity": data},
            ids=[chebi_id],
            ctx=ctx,
        )

    def semanticscholar_search_papers(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")

        limit = min(max(int(payload.get("limit", 20)), 1), 100)
        fields = str(
            payload.get(
                "fields",
                "title,year,paperId,externalIds,citationCount",
            )
        ).strip()
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
            data={"records": records},
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

    def semanticscholar_get_papers(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        paper_ids = payload.get("paper_ids") or []
        if not isinstance(paper_ids, list) or not paper_ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'paper_ids' must be a non-empty list")

        fields = str(payload.get("fields", "title,year,externalIds,citationCount,abstract")).strip()
        headers = {}
        if settings.semanticscholar_api_key:
            headers["x-api-key"] = settings.semanticscholar_api_key

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for item in paper_ids:
            paper_id = str(item).strip()
            if not paper_id:
                continue
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
            data={"records": records},
            ids=[record.get("paperId") for record in records if isinstance(record, dict)],
            warnings=warnings,
            ctx=ctx,
        )

    def epistemonikos_search_reviews(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _require_epistemonikos_key(settings)
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")

        limit = min(max(int(payload.get("limit", 20)), 1), 100)
        data, _ = http.get_json(
            url="https://api.epistemonikos.org/v1/reviews/search",
            params={"q": query, "limit": limit},
            headers={"Authorization": f"Bearer {key}"},
        )
        records = list((data or {}).get("results") or (data or {}).get("data") or [])
        return make_tool_output(
            source="epistemonikos",
            summary=f"Found {len(records)} Epistemonikos review candidate(s).",
            data={"records": records},
            ids=[str(item.get("id")) for item in records if isinstance(item, dict) and item.get("id")],
            ctx=ctx,
        )

    def epistemonikos_get_review(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _require_epistemonikos_key(settings)
        review_id = str(payload.get("review_id", "")).strip()
        if not review_id:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'review_id' is required")

        data, _ = http.get_json(
            url=f"https://api.epistemonikos.org/v1/reviews/{parse.quote(review_id)}",
            headers={"Authorization": f"Bearer {key}"},
        )
        return make_tool_output(
            source="epistemonikos",
            summary=f"Fetched Epistemonikos review {review_id}.",
            data={"review": data},
            ids=[review_id],
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="chembl_search_molecules",
            description="Search ChEMBL molecules by free-text query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "required": ["query"],
            },
            handler=chembl_search_molecules,
            source="chembl",
        ),
        ToolSpec(
            name="chembl_get_molecule",
            description="Fetch one ChEMBL molecule by ChEMBL ID.",
            input_schema={
                "type": "object",
                "properties": {"chembl_id": {"type": "string"}},
                "required": ["chembl_id"],
            },
            handler=chembl_get_molecule,
            source="chembl",
        ),
        ToolSpec(
            name="chebi_search_entities",
            description="Search ChEBI entities by free-text query.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "required": ["query"],
            },
            handler=chebi_search_entities,
            source="chebi",
        ),
        ToolSpec(
            name="chebi_get_entity",
            description="Fetch one ChEBI entity by ChEBI ID.",
            input_schema={
                "type": "object",
                "properties": {"chebi_id": {"type": "string"}},
                "required": ["chebi_id"],
            },
            handler=chebi_get_entity,
            source="chebi",
        ),
        ToolSpec(
            name="semanticscholar_search_papers",
            description="Search Semantic Scholar papers.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "fields": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=semanticscholar_search_papers,
            source="semanticscholar",
        ),
        ToolSpec(
            name="semanticscholar_get_papers",
            description="Fetch Semantic Scholar paper details by paper IDs.",
            input_schema={
                "type": "object",
                "properties": {
                    "paper_ids": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "string"},
                },
                "required": ["paper_ids"],
            },
            handler=semanticscholar_get_papers,
            source="semanticscholar",
        ),
        ToolSpec(
            name="epistemonikos_search_reviews",
            description="Search Epistemonikos systematic reviews (requires API key).",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "required": ["query"],
            },
            handler=epistemonikos_search_reviews,
            source="epistemonikos",
        ),
        ToolSpec(
            name="epistemonikos_get_review",
            description="Fetch one Epistemonikos review by review ID (requires API key).",
            input_schema={
                "type": "object",
                "properties": {"review_id": {"type": "string"}},
                "required": ["review_id"],
            },
            handler=epistemonikos_get_review,
            source="epistemonikos",
        ),
    ]
