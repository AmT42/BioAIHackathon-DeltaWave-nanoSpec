from __future__ import annotations

from datetime import datetime, timezone
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


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _openalex_auth_params(settings: Settings) -> dict[str, str]:
    params: dict[str, str] = {}
    api_key = str(settings.openalex_api_key or "").strip()
    mailto = str(settings.openalex_mailto or "").strip()
    if api_key:
        params["api_key"] = api_key
    if mailto:
        params["mailto"] = mailto
    return params


def _parse_trial_date(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_older_than(date_value: Any, threshold_days: int) -> bool:
    parsed = _parse_trial_date(date_value)
    if parsed is None:
        return False
    age_days = (datetime.now(timezone.utc) - parsed).days
    return age_days >= max(0, threshold_days)


def _compact_trial(study: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    protocol = study.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    status_module = protocol.get("statusModule") or {}
    design = protocol.get("designModule") or {}
    outcomes = protocol.get("outcomesModule") or {}
    eligibility = protocol.get("eligibilityModule") or {}
    results_section = study.get("resultsSection") or {}

    completion_date = (status_module.get("completionDateStruct") or {}).get("date")
    primary_completion_date = (status_module.get("primaryCompletionDateStruct") or {}).get("date")

    nct_id = ident.get("nctId") or study.get("nctId")

    arm_groups = list((design.get("armsInterventionsModule") or {}).get("armGroups") or [])
    if not arm_groups:
        arm_groups = list((results_section.get("participantFlowModule") or {}).get("groups") or [])

    eligibility_summary = str(eligibility.get("eligibilityCriteria") or "").strip()
    if eligibility_summary:
        eligibility_summary = " ".join(eligibility_summary.split())[:500]

    compact = {
        "nct_id": nct_id,
        "brief_title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "overall_status": status_module.get("overallStatus"),
        "study_type": design.get("studyType"),
        "phases": list(design.get("phases") or []),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "primary_outcomes": list(outcomes.get("primaryOutcomes") or []),
        "primary_completion_date": primary_completion_date,
        "completion_date": completion_date,
        "results_first_posted_date": (status_module.get("resultsFirstPostDateStruct") or {}).get("date"),
        "eligibility_summary": eligibility_summary or None,
        "arms_count": len(arm_groups),
        "has_results": bool(study.get("hasResults")),
    }
    if include_raw:
        compact["raw_study"] = study
    return compact


def _normalize_nct_ids(payload: dict[str, Any], *, max_ids: int = 100) -> list[str]:
    ids = payload.get("ids")
    if ids is None:
        ids = payload.get("nct_ids")
    if not isinstance(ids, list) or not ids:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

    clean: list[str] = []
    seen: set[str] = set()
    for item in ids:
        nct = str(item or "").strip().upper()
        if not nct:
            continue
        if nct in seen:
            continue
        seen.add(nct)
        clean.append(nct)
        if len(clean) >= max_ids:
            break

    if not clean:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid NCT IDs provided")
    return clean


def build_trial_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def clinicaltrials_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip() or None
        intervention = str(payload.get("intervention", "")).strip() or str(payload.get("intr", "")).strip() or None
        condition = str(payload.get("condition", "")).strip() or str(payload.get("cond", "")).strip() or None

        if not query and not intervention and not condition:
            raise ToolExecutionError(
                code="VALIDATION_ERROR",
                message="Provide at least one of 'query', 'intervention', or 'condition'",
            )

        limit = min(max(_safe_int(payload.get("limit", payload.get("page_size", 25)), 25), 1), 100)
        page_token = str(payload.get("page_token", "")).strip() or None

        params: dict[str, Any] = {
            "pageSize": limit,
            "format": "json",
        }
        if query:
            params["query.term"] = query
        if intervention:
            params["query.intr"] = intervention
        if condition:
            params["query.cond"] = condition
        if page_token:
            params["pageToken"] = page_token

        data, headers = http.get_json(url="https://clinicaltrials.gov/api/v2/studies", params=params)
        studies = list((data or {}).get("studies") or [])
        compact = [_compact_trial(study, include_raw=False) for study in studies]
        next_page_token = (data or {}).get("nextPageToken")

        artifacts: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, "clinicaltrials_search", data) if ctx else None
        if raw_ref:
            artifacts.append(raw_ref)

        return make_tool_output(
            source="clinicaltrials",
            summary=f"Retrieved {len(compact)} ClinicalTrials.gov study record(s).",
            data={"query": query, "intervention": intervention, "condition": condition, "studies": compact},
            ids=[item.get("nct_id") for item in compact if item.get("nct_id")],
            artifacts=artifacts,
            pagination={"next_page_token": next_page_token, "has_more": bool(next_page_token)},
            request_id=_request_id(headers),
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def clinicaltrials_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _normalize_nct_ids(payload, max_ids=100)
        include_raw = bool(payload.get("include_raw", False))

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for nct_id in ids:
            url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}"
            try:
                data, _ = http.get_json(url=url, params={"format": "json"})
                compact = _compact_trial(data or {}, include_raw=include_raw)
                records.append(compact)
                raw_ref = write_raw_json_artifact(ctx, f"clinicaltrials_{nct_id}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{nct_id}: {exc.message}")

        return make_tool_output(
            source="clinicaltrials",
            summary=f"Fetched {len(records)} ClinicalTrials.gov study detail record(s).",
            data={"studies": records, "include_raw": include_raw},
            ids=[item.get("nct_id") for item in records if item.get("nct_id")],
            warnings=warnings,
            artifacts=artifacts,
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def clinicaltrials_search_studies(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return clinicaltrials_search(payload, ctx)

    def clinicaltrials_get_studies(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        remapped = dict(payload)
        if "nct_ids" in remapped and "ids" not in remapped:
            remapped["ids"] = remapped.get("nct_ids")
        return clinicaltrials_fetch(remapped, ctx)

    def trial_publication_linker(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _normalize_nct_ids(payload, max_ids=100)
        openalex_per_nct = min(max(_safe_int(payload.get("openalex_per_nct", 10), 10), 1), 50)
        evidence_age_days = max(_safe_int(payload.get("evidence_age_days", 365), 365), 0)
        trials = payload.get("trials") or []
        trial_by_nct: dict[str, dict[str, Any]] = {}
        if isinstance(trials, list):
            for trial in trials:
                if isinstance(trial, dict) and trial.get("nct_id"):
                    trial_by_nct[str(trial.get("nct_id")).upper()] = trial

        links: list[dict[str, Any]] = []
        warnings: list[str] = []

        for nct in ids:
            per_nct_warnings: list[str] = []
            strict_pmids: list[str] = []
            fallback_pmids: list[str] = []

            params: dict[str, Any] = {
                "db": "pubmed",
                "retmode": "json",
                "retmax": 20,
            }
            if settings.pubmed_api_key:
                params["api_key"] = settings.pubmed_api_key

            try:
                strict_data, _ = http.get_json(
                    url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                    params={**params, "term": f'"{nct}"[si]'},
                )
                strict_pmids = [
                    str(item)
                    for item in (((strict_data or {}).get("esearchresult") or {}).get("idlist") or [])
                    if str(item).strip()
                ]
            except ToolExecutionError as exc:
                per_nct_warnings.append(f"pubmed_strict_failed: {exc.message}")

            if not strict_pmids:
                try:
                    fallback_data, _ = http.get_json(
                        url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                        params={**params, "term": f"{nct}[All Fields]"},
                    )
                    fallback_pmids = [
                        str(item)
                        for item in (((fallback_data or {}).get("esearchresult") or {}).get("idlist") or [])
                        if str(item).strip()
                    ]
                except ToolExecutionError as exc:
                    per_nct_warnings.append(f"pubmed_fallback_failed: {exc.message}")

            pmids = strict_pmids or fallback_pmids

            openalex_ids: list[str] = []
            openalex_auth = _openalex_auth_params(settings)
            if openalex_auth:
                try:
                    oa_data, _ = http.get_json(
                        url="https://api.openalex.org/works",
                        params={
                            "search": nct,
                            "per-page": openalex_per_nct,
                            **openalex_auth,
                        },
                    )
                    for work in list((oa_data or {}).get("results") or []):
                        work_id = work.get("id")
                        if work_id:
                            openalex_ids.append(str(work_id))
                except ToolExecutionError as exc:
                    per_nct_warnings.append(f"openalex_failed: {exc.message}")

            trial = trial_by_nct.get(nct)
            status = str((trial or {}).get("overall_status") or "")
            has_results = bool((trial or {}).get("has_results"))
            completion_date = (trial or {}).get("completion_date") or (trial or {}).get("primary_completion_date")

            flag = "no_mismatch_signal"
            if not trial:
                flag = "insufficient_trial_context"
                per_nct_warnings.append("Trial context missing; mismatch classification is conservative.")
            else:
                completed = bool(status and status.upper() == "COMPLETED")
                has_pubmed_publication = bool(pmids)

                if completed and not has_pubmed_publication:
                    if completion_date and _is_older_than(completion_date, evidence_age_days):
                        flag = "completed_but_unpublished_possible"
                    elif not completion_date:
                        flag = "completed_but_unpublished_possible"
                elif has_results and not has_pubmed_publication:
                    flag = "registry_results_without_publication"
                elif has_pubmed_publication and not status:
                    flag = "publication_without_trial_context"

            if per_nct_warnings:
                warnings.extend(f"{nct}: {item}" for item in per_nct_warnings)

            links.append(
                {
                    "nct_id": nct,
                    "status": status or None,
                    "has_results": has_results,
                    "completion_date": completion_date,
                    "pmids": pmids,
                    "pubmed_match_mode": "strict" if strict_pmids else ("fallback" if fallback_pmids else "none"),
                    "openalex_ids": openalex_ids,
                    "counts": {
                        "pmid_count": len(pmids),
                        "openalex_count": len(openalex_ids),
                    },
                    "flag": flag,
                    "warnings": per_nct_warnings,
                }
            )

        return make_tool_output(
            source="trial_publication_linker",
            summary=f"Linked {len(links)} trial(s) to publication evidence.",
            data={"links": links, "evidence_age_days": evidence_age_days},
            ids=[item.get("nct_id") for item in links if item.get("nct_id")],
            warnings=warnings,
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="clinicaltrials_search",
            description=(
                "WHEN: Identify registered human trial evidence for an intervention/condition.\n"
                "AVOID: Using registry search output as final trial metadata without fetch.\n"
                "CRITICAL_ARGS: one of query/intervention/condition.\n"
                "RETURNS: ID-first compact trial list with pagination token.\n"
                "FAILS_IF: all query arguments are missing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "query": {"type": "string"},
                    "intervention": {"type": "string"},
                    "condition": {"type": "string"},
                    "intr": {"type": "string"},
                    "cond": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                    "page_token": {"type": "string"},
                },
            },
            handler=clinicaltrials_search,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="clinicaltrials_fetch",
            description=(
                "WHEN: Fetch trial details for known NCT IDs before classification.\n"
                "AVOID: include_raw=true unless needed for deep debugging.\n"
                "CRITICAL_ARGS: ids, include_raw.\n"
                "RETURNS: compact trial records with completion/results timing and eligibility summary.\n"
                "FAILS_IF: ids is missing or empty."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "include_raw": {"type": "boolean", "default": False},
                },
                "required": ["ids"],
            },
            handler=clinicaltrials_fetch,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="clinicaltrials_search_studies",
            description="Backward-compatible alias for clinicaltrials_search.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "intervention": {"type": "string"},
                    "condition": {"type": "string"},
                    "intr": {"type": "string"},
                    "cond": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                    "page_token": {"type": "string"},
                },
            },
            handler=clinicaltrials_search_studies,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="clinicaltrials_get_studies",
            description="Backward-compatible alias for clinicaltrials_fetch.",
            input_schema={
                "type": "object",
                "properties": {
                    "nct_ids": {"type": "array", "items": {"type": "string"}},
                    "include_raw": {"type": "boolean", "default": False},
                },
                "required": ["nct_ids"],
            },
            handler=clinicaltrials_get_studies,
            source="clinicaltrials",
        ),
        ToolSpec(
            name="trial_publication_linker",
            description="Link trial registry records to publication evidence and flag mismatch patterns.",
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "nct_ids": {"type": "array", "items": {"type": "string"}},
                    "trials": {"type": "array", "items": {"type": "object"}},
                    "openalex_per_nct": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "evidence_age_days": {"type": "integer", "minimum": 0, "default": 365},
                },
                "required": ["ids"],
            },
            handler=trial_publication_linker,
            source="trial_publication_linker",
        ),
    ]
