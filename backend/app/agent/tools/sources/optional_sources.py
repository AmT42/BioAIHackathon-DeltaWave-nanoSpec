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


def build_optional_source_tools(_settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
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
    ]
