from __future__ import annotations

from typing import Any

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
    return headers.get("x-request-id") or headers.get("nctid")


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


def _require_ids(payload: dict[str, Any], *, max_size: int = 50) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

    out: list[str] = []
    seen: set[str] = set()
    for item in ids:
        nct = str(item or "").strip().upper()
        if not nct:
            continue
        if nct in seen:
            continue
        seen.add(nct)
        out.append(nct)

    if not out:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid IDs provided in 'ids'")
    if len(out) > max_size:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"Too many IDs. Maximum is {max_size}",
            details={"provided": len(out), "max": max_size},
        )
    return out


def _compact_trial(study: dict[str, Any]) -> dict[str, Any]:
    protocol = study.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    status_module = protocol.get("statusModule") or {}
    design = protocol.get("designModule") or {}
    outcomes = protocol.get("outcomesModule") or {}

    nct_id = ident.get("nctId") or study.get("nctId")
    return {
        "nct_id": nct_id,
        "brief_title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "overall_status": status_module.get("overallStatus"),
        "study_type": design.get("studyType"),
        "phases": list(design.get("phases") or []),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "primary_outcomes": list(outcomes.get("primaryOutcomes") or []),
        "has_results": bool(study.get("hasResults")),
    }


def build_trial_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def clinicaltrials_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=25, default_recall=50, maximum=100)
        page_token = str(payload.get("page_token", "")).strip() or None
        condition = str(payload.get("condition", "")).strip() or None
        intervention = str(payload.get("intervention", "")).strip() or None

        params: dict[str, Any] = {
            "pageSize": limit,
            "format": "json",
            "query.term": query,
        }
        if intervention:
            params["query.intr"] = intervention
        if condition:
            params["query.cond"] = condition
        if page_token:
            params["pageToken"] = page_token

        data, headers = http.get_json(url="https://clinicaltrials.gov/api/v2/studies", params=params)
        studies = list((data or {}).get("studies") or [])
        compact = [_compact_trial(study) for study in studies]
        next_page_token = (data or {}).get("nextPageToken")

        artifacts: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, "clinicaltrials_search", data) if ctx else None
        if raw_ref:
            artifacts.append(raw_ref)

        return make_tool_output(
            source="clinicaltrials",
            summary=f"Retrieved {len(compact)} ClinicalTrials.gov study record(s).",
            result_kind="record_list",
            data={"query": query, "mode": mode, "studies": compact},
            ids=[item.get("nct_id") for item in compact if item.get("nct_id")],
            artifacts=artifacts,
            pagination={"next_page_token": next_page_token, "has_more": bool(next_page_token)},
            request_id=_request_id(headers),
            next_recommended_tools=["clinicaltrials_fetch", "retrieval_should_run_trial_audit"],
            ctx=ctx,
        )

    def clinicaltrials_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=50)
        mode = _require_mode(payload)

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for nct_id in ids:
            url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
            try:
                data, _ = http.get_json(url=url, params={"format": "json"})
                compact = _compact_trial(data or {})
                compact["raw_study"] = data
                records.append(compact)
                raw_ref = write_raw_json_artifact(ctx, f"clinicaltrials_{nct_id}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{nct_id}: {exc.message}")

        return make_tool_output(
            source="clinicaltrials",
            summary=f"Fetched {len(records)} ClinicalTrials.gov study detail record(s).",
            result_kind="record_list",
            data={"mode": mode, "studies": records},
            ids=[item.get("nct_id") for item in records if item.get("nct_id")],
            warnings=warnings,
            artifacts=artifacts,
            next_recommended_tools=["trial_publication_linker"],
            ctx=ctx,
        )

    def trial_publication_linker(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=100)
        mode = _require_mode(payload)
        openalex_limit = _limit_for_mode(
            {"mode": mode, "limit": payload.get("openalex_limit", 10)},
            default_precision=5,
            default_balanced=10,
            default_recall=20,
            maximum=50,
        )

        trials = payload.get("trials") or []
        trial_by_nct: dict[str, dict[str, Any]] = {}
        if isinstance(trials, list):
            for trial in trials:
                if isinstance(trial, dict) and trial.get("nct_id"):
                    trial_by_nct[str(trial.get("nct_id")).upper()] = trial

        links: list[dict[str, Any]] = []
        warnings: list[str] = []

        for nct in ids:
            pubmed_params: dict[str, Any] = {
                "db": "pubmed",
                "term": f'"{nct}"[si] OR {nct}[All Fields]',
                "retmode": "json",
                "retmax": 20,
            }
            if settings.pubmed_api_key:
                pubmed_params["api_key"] = settings.pubmed_api_key

            pubmed_data, _ = http.get_json(
                url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=pubmed_params,
            )
            pmids = [
                str(item)
                for item in (((pubmed_data or {}).get("esearchresult") or {}).get("idlist") or [])
                if str(item).strip()
            ]

            openalex_ids: list[str] = []
            if settings.openalex_api_key:
                oa_data, _ = http.get_json(
                    url="https://api.openalex.org/works",
                    params={
                        "search": nct,
                        "per-page": openalex_limit,
                        "api_key": settings.openalex_api_key,
                    },
                )
                for work in list((oa_data or {}).get("results") or []):
                    work_id = work.get("id")
                    if work_id:
                        openalex_ids.append(str(work_id))
            else:
                warnings.append("OpenAlex key missing; linker used PubMed only.")

            trial = trial_by_nct.get(nct, {})
            status = str(trial.get("overall_status") or "")
            has_results = bool(trial.get("has_results"))

            flag: str | None = None
            if status.upper() == "COMPLETED" and not pmids and not openalex_ids:
                flag = "completed_but_unpublished_possible"
            elif has_results and not pmids and not openalex_ids:
                flag = "registry_results_without_publication"
            elif (pmids or openalex_ids) and not status:
                flag = "publication_without_trial_context"

            links.append(
                {
                    "nct_id": nct,
                    "status": status or None,
                    "has_results": has_results,
                    "pmids": pmids,
                    "openalex_ids": openalex_ids,
                    "counts": {
                        "pmid_count": len(pmids),
                        "openalex_count": len(openalex_ids),
                    },
                    "flag": flag,
                }
            )

        return make_tool_output(
            source="trial_publication_linker",
            summary=f"Linked {len(links)} trial(s) to publication evidence.",
            result_kind="record_list",
            data={"mode": mode, "links": links},
            ids=[item.get("nct_id") for item in links if item.get("nct_id")],
            warnings=warnings,
            auth_required=False,
            auth_configured=True,
            next_recommended_tools=["pubmed_fetch"],
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="clinicaltrials_search",
            description=render_tool_description(
                purpose="Search ClinicalTrials.gov to establish registered human trial evidence.",
                when=["you need trial registry truth", "cross-checking if human intervention studies exist"],
                avoid=["you already have exact NCT IDs", "you need full trial detail fetch"],
                critical_args=["query: primary trial search text", "mode: precision/balanced/recall", "limit/page_token: paging controls"],
                returns="Record list of compact trial summaries with NCT IDs and status.",
                fails_if=["query missing", "invalid mode", "limit out of range", "ClinicalTrials upstream unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "intervention": {"type": "string"},
                    "condition": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                    "page_token": {"type": "string"},
                },
                "required": ["query"],
            },
            handler=clinicaltrials_search,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="clinicaltrials_fetch",
            description=render_tool_description(
                purpose="Fetch full trial detail records from ClinicalTrials.gov by NCT ID.",
                when=["you already have NCT IDs", "you need endpoints/status/enrollment detail"],
                avoid=["you only have free-text query", "batch size exceeds supported limits"],
                critical_args=["ids: NCT ID list", "mode: policy consistency"],
                returns="Record list with detailed trial metadata and raw study payload.",
                fails_if=["ids missing", "too many IDs", "upstream trial fetch failures"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=clinicaltrials_fetch,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="trial_publication_linker",
            description=render_tool_description(
                purpose="Link trial registry records to publication evidence and flag mismatch patterns.",
                when=["you have NCT IDs", "you need registered-vs-published mismatch audit"],
                avoid=["you have no trial IDs", "you expect full-text outcome extraction"],
                critical_args=["ids: NCT IDs", "trials: optional trial metadata for stronger flags", "openalex_limit: optional expansion depth"],
                returns="Record list per NCT with linked PMIDs/OpenAlex IDs and mismatch flags.",
                fails_if=["ids missing", "invalid mode", "upstream PubMed unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                    "trials": {"type": "array", "items": {"type": "object"}},
                    "openalex_limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=trial_publication_linker,
            source="trial_publication_linker",
        ),
    ]
