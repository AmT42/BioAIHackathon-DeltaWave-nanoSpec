from __future__ import annotations

from typing import Any

from app.config import Settings
from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("nctid")


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
    def clinicaltrials_search_studies(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        intr = str(payload.get("intr", "")).strip() or None
        cond = str(payload.get("cond", "")).strip() or None
        query_term = str(payload.get("query_term", "")).strip() or None
        page_size = min(max(int(payload.get("page_size", 20)), 1), 100)
        page_token = str(payload.get("page_token", "")).strip() or None

        if not intr and not cond and not query_term:
            raise ToolExecutionError(
                code="VALIDATION_ERROR",
                message="Provide at least one of 'intr', 'cond', or 'query_term'",
            )

        params: dict[str, Any] = {
            "pageSize": page_size,
            "format": "json",
        }
        if intr:
            params["query.intr"] = intr
        if cond:
            params["query.cond"] = cond
        if query_term:
            params["query.term"] = query_term
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
            data={"studies": compact},
            ids=[item.get("nct_id") for item in compact if item.get("nct_id")],
            warnings=[],
            artifacts=artifacts,
            pagination={
                "next_page_token": next_page_token,
                "has_more": bool(next_page_token),
            },
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def clinicaltrials_get_studies(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        nct_ids = payload.get("nct_ids") or []
        if not isinstance(nct_ids, list) or not nct_ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'nct_ids' must be a non-empty list")

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for nct_id in nct_ids:
            nct = str(nct_id).strip()
            if not nct:
                continue
            url = f"https://clinicaltrials.gov/api/v2/studies/{nct}"
            try:
                data, _ = http.get_json(url=url, params={"format": "json"})
                compact = _compact_trial(data or {})
                compact["raw_study"] = data
                records.append(compact)
                raw_ref = write_raw_json_artifact(ctx, f"clinicaltrials_{nct}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{nct}: {exc.message}")

        return make_tool_output(
            source="clinicaltrials",
            summary=f"Fetched {len(records)} ClinicalTrials.gov study detail record(s).",
            data={"studies": records},
            ids=[item.get("nct_id") for item in records if item.get("nct_id")],
            warnings=warnings,
            artifacts=artifacts,
            ctx=ctx,
        )

    def trial_publication_linker(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        nct_ids = payload.get("nct_ids") or []
        if not isinstance(nct_ids, list) or not nct_ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'nct_ids' must be a non-empty list")

        openalex_per_nct = min(max(int(payload.get("openalex_per_nct", 10)), 1), 50)
        trials = payload.get("trials") or []
        trial_by_nct: dict[str, dict[str, Any]] = {}
        if isinstance(trials, list):
            for trial in trials:
                if isinstance(trial, dict) and trial.get("nct_id"):
                    trial_by_nct[str(trial.get("nct_id"))] = trial

        links: list[dict[str, Any]] = []
        warnings: list[str] = []

        for nct in [str(item).strip() for item in nct_ids if str(item).strip()]:
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
            pmids = (((pubmed_data or {}).get("esearchresult") or {}).get("idlist") or [])

            openalex_ids: list[str] = []
            if settings.openalex_api_key:
                oa_data, _ = http.get_json(
                    url="https://api.openalex.org/works",
                    params={
                        "search": nct,
                        "per-page": openalex_per_nct,
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
            data={"links": links},
            ids=[item.get("nct_id") for item in links if item.get("nct_id")],
            warnings=warnings,
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="clinicaltrials_search_studies",
            description="Search ClinicalTrials.gov studies by intervention/condition/query term.",
            input_schema={
                "type": "object",
                "properties": {
                    "intr": {"type": "string"},
                    "cond": {"type": "string"},
                    "query_term": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "page_token": {"type": "string"},
                },
            },
            handler=clinicaltrials_search_studies,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="clinicaltrials_get_studies",
            description="Fetch ClinicalTrials.gov study details by NCT IDs.",
            input_schema={
                "type": "object",
                "properties": {
                    "nct_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["nct_ids"],
            },
            handler=clinicaltrials_get_studies,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="trial_publication_linker",
            description="Link NCT IDs to PubMed/OpenAlex publications and flag mismatch patterns.",
            input_schema={
                "type": "object",
                "properties": {
                    "nct_ids": {"type": "array", "items": {"type": "string"}},
                    "trials": {"type": "array", "items": {"type": "object"}},
                    "openalex_per_nct": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "required": ["nct_ids"],
            },
            handler=trial_publication_linker,
            source="trial_publication_linker",
        ),
    ]
