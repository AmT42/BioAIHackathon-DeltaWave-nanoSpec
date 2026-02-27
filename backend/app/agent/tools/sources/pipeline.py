from __future__ import annotations

from typing import Any

from app.config import Settings
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec
from app.agent.tools.sources.literature import _parse_pubmed_xml_records


_HARD_ENDPOINT_HINTS = {
    "mortality",
    "death",
    "hospitalization",
    "frailty",
    "functional",
    "walk",
    "grip",
    "survival",
}
_SURROGATE_HINTS = {
    "biomarker",
    "clock",
    "epigenetic",
    "crp",
    "lipid",
    "glucose",
    "insulin",
    "homa-ir",
}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _dedupe(items: list[str], *, max_items: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _extract_rxnorm_candidates(http: SimpleHttpClient, term: str) -> tuple[str | None, list[str], list[dict[str, Any]]]:
    data, _ = http.get_json(
        url="https://rxnav.nlm.nih.gov/REST/rxcui.json",
        params={"name": term, "search": "2"},
    )
    rxcui_ids = (((data or {}).get("idGroup") or {}).get("rxnormId") or [])
    if not rxcui_ids:
        return None, [], []

    names: list[str] = []
    candidates: list[dict[str, Any]] = []
    ingredient_rxcui: str | None = None
    for raw_rxcui in rxcui_ids[:10]:
        rxcui = str(raw_rxcui)
        props_data, _ = http.get_json(url=f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json")
        props = (props_data or {}).get("properties") or {}
        name = str(props.get("name") or "").strip()
        tty = str(props.get("tty") or "").strip()
        if name:
            names.append(name)
        candidates.append({"rxcui": rxcui, "name": name or None, "tty": tty or None})
        if tty == "IN" and ingredient_rxcui is None:
            ingredient_rxcui = rxcui

    if ingredient_rxcui is None and candidates:
        ingredient_rxcui = candidates[0]["rxcui"]
    return ingredient_rxcui, names, candidates


def _extract_pubchem_candidates(http: SimpleHttpClient, term: str) -> tuple[dict[str, Any] | None, list[str]]:
    cids_data, _ = http.get_json(
        url=f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{term}/cids/JSON",
    )
    cids = (((cids_data or {}).get("IdentifierList") or {}).get("CID") or [])
    if not cids:
        return None, []

    cid = str(cids[0])
    prop_data, _ = http.get_json(
        url=f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/Title,InChIKey/JSON",
    )
    syn_data, _ = http.get_json(
        url=f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON",
    )

    props = (((prop_data or {}).get("PropertyTable") or {}).get("Properties") or [])
    prop_entry = props[0] if props else {}
    syn_info = (((syn_data or {}).get("InformationList") or {}).get("Information") or [])
    synonyms = list((syn_info[0] if syn_info else {}).get("Synonym") or [])

    return {
        "cid": cid,
        "preferred_name": prop_entry.get("Title"),
        "inchikey": prop_entry.get("InChIKey"),
    }, [str(item) for item in synonyms[:25]]


def _extract_ols_candidate(http: SimpleHttpClient, term: str) -> dict[str, Any] | None:
    data, _ = http.get_json(
        url="https://www.ebi.ac.uk/ols4/api/search",
        params={"q": term, "rows": 5, "ontology": "efo,mondo,hp"},
    )
    docs = (((data or {}).get("response") or {}).get("docs") or [])
    if not docs:
        return None
    doc = docs[0]
    return {
        "obo_id": doc.get("obo_id"),
        "label": doc.get("label"),
        "ontology": doc.get("ontology_name"),
        "synonyms": list(doc.get("synonym") or [])[:15],
    }


def _resolve_concept(http: SimpleHttpClient, intervention: str) -> dict[str, Any]:
    warnings: list[str] = []

    rxnorm_id = None
    rxnorm_names: list[str] = []
    rxnorm_candidates: list[dict[str, Any]] = []
    try:
        rxnorm_id, rxnorm_names, rxnorm_candidates = _extract_rxnorm_candidates(http, intervention)
    except ToolExecutionError:
        warnings.append("RxNorm resolution unavailable.")

    pubchem_compound = None
    pubchem_synonyms: list[str] = []
    try:
        pubchem_compound, pubchem_synonyms = _extract_pubchem_candidates(http, intervention)
    except ToolExecutionError:
        warnings.append("PubChem resolution unavailable.")

    ols_candidate = None
    try:
        ols_candidate = _extract_ols_candidate(http, intervention)
    except ToolExecutionError:
        warnings.append("OLS resolution unavailable.")

    concept_type = "free_text"
    pivot_source = "free_text"
    pivot_id = intervention
    label = intervention

    if rxnorm_id:
        concept_type = "drug"
        pivot_source = "rxnorm"
        pivot_id = rxnorm_id
        if rxnorm_names:
            label = rxnorm_names[0]
    elif pubchem_compound and pubchem_compound.get("inchikey"):
        concept_type = "chemical"
        pivot_source = "pubchem"
        pivot_id = str(pubchem_compound["inchikey"])
        label = str(pubchem_compound.get("preferred_name") or intervention)
    elif ols_candidate and ols_candidate.get("obo_id"):
        ontology = str(ols_candidate.get("ontology") or "").lower()
        if ontology == "efo":
            concept_type = "procedure"
        elif ontology == "mondo":
            concept_type = "disease"
        elif ontology in {"hp", "hpo"}:
            concept_type = "phenotype"
        else:
            concept_type = "ontology_term"
        pivot_source = "ols"
        pivot_id = str(ols_candidate.get("obo_id"))
        label = str(ols_candidate.get("label") or intervention)

    synonyms = _dedupe([label, *rxnorm_names, *pubchem_synonyms, *(ols_candidate or {}).get("synonyms", [])], max_items=20)
    xrefs: list[dict[str, Any]] = []
    if rxnorm_id:
        xrefs.append({"source": "rxnorm", "id": f"RxCUI:{rxnorm_id}"})
    if pubchem_compound and pubchem_compound.get("cid"):
        xrefs.append({"source": "pubchem", "id": f"CID:{pubchem_compound['cid']}"})
    if ols_candidate and ols_candidate.get("obo_id"):
        xrefs.append({"source": "ols", "id": str(ols_candidate["obo_id"])})

    return {
        "label": label,
        "type": concept_type,
        "pivot": {"source": pivot_source, "id": pivot_id},
        "synonyms": [{"text": term, "source": "derived", "weight": 0.5} for term in synonyms],
        "xrefs": xrefs,
        "warnings": warnings,
    }


def _pubmed_esearch(http: SimpleHttpClient, settings: Settings, term: str, retmax: int) -> list[str]:
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": min(max(retmax, 1), 5000),
    }
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key

    data, _ = http.get_json(url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
    return list((((data or {}).get("esearchresult") or {}).get("idlist") or []))


def _pubmed_efetch(http: SimpleHttpClient, settings: Settings, pmids: list[str]) -> list[dict[str, Any]]:
    if not pmids:
        return []
    params: dict[str, Any] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key
    xml_text, _ = http.get_text(url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params=params)
    return _parse_pubmed_xml_records(xml_text)


def _clinicaltrials_search(http: SimpleHttpClient, intervention: str, max_trials: int) -> list[dict[str, Any]]:
    data, _ = http.get_json(
        url="https://clinicaltrials.gov/api/v2/studies",
        params={"query.intr": intervention, "pageSize": min(max(max_trials, 1), 100), "format": "json"},
    )
    studies = list((data or {}).get("studies") or [])
    out: list[dict[str, Any]] = []
    for study in studies:
        protocol = study.get("protocolSection") or {}
        ident = protocol.get("identificationModule") or {}
        status = protocol.get("statusModule") or {}
        out.append(
            {
                "nct_id": ident.get("nctId") or study.get("nctId"),
                "brief_title": ident.get("briefTitle"),
                "overall_status": status.get("overallStatus"),
                "completion_date": (status.get("completionDateStruct") or {}).get("date"),
                "primary_completion_date": (status.get("primaryCompletionDateStruct") or {}).get("date"),
                "has_results": bool(study.get("hasResults")),
            }
        )
    return [trial for trial in out if trial.get("nct_id")]


def _trial_publication_links(http: SimpleHttpClient, settings: Settings, trials: list[dict[str, Any]], evidence_age_days: int) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for trial in trials:
        nct_id = str(trial.get("nct_id") or "").strip().upper()
        if not nct_id:
            continue
        warnings: list[str] = []
        strict_pmids: list[str] = []
        fallback_pmids: list[str] = []

        params: dict[str, Any] = {"db": "pubmed", "retmode": "json", "retmax": 20}
        if settings.pubmed_api_key:
            params["api_key"] = settings.pubmed_api_key

        try:
            strict_data, _ = http.get_json(
                url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={**params, "term": f'"{nct_id}"[si]'},
            )
            strict_pmids = list((((strict_data or {}).get("esearchresult") or {}).get("idlist") or []))
        except ToolExecutionError as exc:
            warnings.append(f"pubmed_strict_failed: {exc.message}")

        if not strict_pmids:
            try:
                fallback_data, _ = http.get_json(
                    url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                    params={**params, "term": f"{nct_id}[All Fields]"},
                )
                fallback_pmids = list((((fallback_data or {}).get("esearchresult") or {}).get("idlist") or []))
            except ToolExecutionError as exc:
                warnings.append(f"pubmed_fallback_failed: {exc.message}")

        pmids = strict_pmids or fallback_pmids

        completion_date = trial.get("completion_date") or trial.get("primary_completion_date")
        status = str(trial.get("overall_status") or "")
        has_results = bool(trial.get("has_results"))

        flag = "no_mismatch_signal"
        if not status:
            flag = "insufficient_trial_context"
        elif status.upper() == "COMPLETED" and not pmids:
            from app.agent.tools.sources.trials import _is_older_than  # local import to avoid cycle during module load

            if completion_date and _is_older_than(completion_date, evidence_age_days):
                flag = "possible_unpublished_completed_trial"
            elif not completion_date:
                flag = "insufficient_trial_context"
        elif has_results and not pmids:
            flag = "registry_results_without_publication"

        links.append(
            {
                "nct_id": nct_id,
                "status": status or None,
                "has_results": has_results,
                "completion_date": completion_date,
                "pmids": pmids,
                "pubmed_match_mode": "strict" if strict_pmids else ("fallback" if fallback_pmids else "none"),
                "openalex_ids": [],
                "counts": {"pmid_count": len(pmids), "openalex_count": 0},
                "flag": flag,
                "warnings": warnings,
            }
        )
    return links


def _compose_pubmed_query(search_terms: list[str], topic_terms: list[str]) -> str:
    concept_clause = " OR ".join(f'"{term}"' for term in search_terms) if search_terms else ""
    topic_clause = " OR ".join(f'"{term}"' for term in topic_terms) if topic_terms else ""
    if concept_clause and topic_clause:
        return f"({concept_clause}) AND ({topic_clause})"
    return concept_clause or topic_clause


def _classify_study(record: dict[str, Any]) -> str:
    pub_types = [str(item).lower() for item in (record.get("publication_types") or [])]
    if any("meta-analysis" in item or "systematic review" in item for item in pub_types):
        return "level_1"
    if any("randomized controlled trial" in item or "clinical trial" in item for item in pub_types):
        return "level_2"
    if record.get("humans"):
        return "level_3"
    if record.get("animals"):
        return "level_4"
    return "level_5_or_6"


def _score_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    studies = list(bundle.get("studies") or [])
    links = list(bundle.get("trial_publication_links") or [])

    level_counts = {"level_1": 0, "level_2": 0, "level_3": 0, "level_4": 0, "level_5_or_6": 0}
    hard_hits = 0
    surrogate_hits = 0
    human_count = 0
    for study in studies:
        level = _classify_study(study)
        level_counts[level] += 1
        if study.get("humans"):
            human_count += 1

        text = f"{study.get('title') or ''} {study.get('abstract') or ''}".lower()
        if any(token in text for token in _HARD_ENDPOINT_HINTS):
            hard_hits += 1
        if any(token in text for token in _SURROGATE_HINTS):
            surrogate_hits += 1

    clinical_evidence = min(45, level_counts["level_1"] * 20 + level_counts["level_2"] * 12 + level_counts["level_3"] * 4 + level_counts["level_4"] * 2)

    quality = 25
    if len(studies) < 5:
        quality -= 8
    if not studies:
        quality = 0

    relevance = 20
    if studies:
        if human_count == 0:
            relevance -= 12
        if hard_hits == 0:
            relevance -= 6
        if surrogate_hits > hard_hits:
            relevance -= 4
    else:
        relevance = 0
    relevance = max(0, relevance)

    mismatch_penalty = 0
    mismatch_counts = {
        "possible_unpublished_completed_trial": 0,
        "registry_results_without_publication": 0,
        "insufficient_trial_context": 0,
    }
    for link in links:
        flag = str(link.get("flag") or "")
        if flag in mismatch_counts:
            mismatch_counts[flag] += 1

    mismatch_penalty += mismatch_counts["possible_unpublished_completed_trial"] * 8
    mismatch_penalty += mismatch_counts["registry_results_without_publication"] * 5
    mismatch_penalty += mismatch_counts["insufficient_trial_context"] * 2

    total = max(0, min(100, clinical_evidence + quality + relevance - mismatch_penalty))

    if total >= 80:
        label = "A"
    elif total >= 65:
        label = "B"
    elif total >= 50:
        label = "C"
    elif total >= 35:
        label = "D"
    else:
        label = "E"

    trace = [
        {
            "component": "clinical_evidence",
            "delta": clinical_evidence,
            "reason": f"L1={level_counts['level_1']}, L2={level_counts['level_2']}, L3={level_counts['level_3']}, L4={level_counts['level_4']}",
        },
        {
            "component": "quality",
            "delta": quality,
            "reason": f"study_count={len(studies)}",
        },
        {
            "component": "relevance",
            "delta": relevance,
            "reason": f"human_count={human_count}, hard_endpoint_hits={hard_hits}, surrogate_hits={surrogate_hits}",
        },
        {
            "component": "mismatch_penalty",
            "delta": -mismatch_penalty,
            "reason": str(mismatch_counts),
        },
    ]

    gaps: list[str] = []
    if level_counts["level_1"] == 0:
        gaps.append("No Level 1 systematic review/meta-analysis evidence found.")
    if level_counts["level_2"] == 0:
        gaps.append("No Level 2 randomized controlled trial evidence found.")
    if human_count == 0:
        gaps.append("No human evidence detected; confidence remains preclinical.")
    if hard_hits == 0:
        gaps.append("No clear hard clinical endpoint signal detected.")

    return {
        "score": total,
        "label": label,
        "breakdown": {
            "clinical_evidence": clinical_evidence,
            "quality": quality,
            "relevance": relevance,
            "mismatch_penalty": mismatch_penalty,
            "total": total,
        },
        "trace": trace,
        "gaps": gaps,
    }


def build_pipeline_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def evidence_retrieve_bundle(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        intervention = str(payload.get("intervention", "")).strip()
        if not intervention:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'intervention' is required")

        topic_terms = payload.get("topic_terms") or ["aging", "lifespan", "frailty", "healthspan"]
        topic_terms = [str(item).strip() for item in topic_terms if str(item).strip()]
        max_pmids = min(max(_safe_int(payload.get("max_pmids", 200), 200), 1), 5000)
        max_trials = min(max(_safe_int(payload.get("max_trials", 50), 50), 1), 100)
        include_safety = bool(payload.get("include_safety", True))
        include_longevity = bool(payload.get("include_longevity", True))
        evidence_age_days = max(_safe_int(payload.get("evidence_age_days", 365), 365), 0)

        warnings: list[str] = []

        concept = _resolve_concept(http, intervention)
        if concept.get("warnings"):
            warnings.extend(str(item) for item in concept.get("warnings") or [])

        concept_terms = _dedupe(
            [concept.get("label") or intervention]
            + [str(item.get("text")) for item in concept.get("synonyms") or [] if isinstance(item, dict)],
            max_items=10,
        )

        query = _compose_pubmed_query(concept_terms, topic_terms)
        pmids = _pubmed_esearch(http, settings, query, max_pmids)
        studies = _pubmed_efetch(http, settings, pmids)

        trials = _clinicaltrials_search(http, intervention, max_trials)
        trial_publication_links = _trial_publication_links(http, settings, trials, evidence_age_days)

        safety: dict[str, Any] | None = None
        if include_safety:
            try:
                dailymed_data, _ = http.get_json(
                    url="https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
                    params={"drug_name": concept_terms[0] if concept_terms else intervention, "pagesize": 5, "page": 1},
                )
                faers_data, _ = http.get_json(
                    url="https://api.fda.gov/drug/event.json",
                    params={
                        "search": f"patient.drug.medicinalproduct:{concept_terms[0] if concept_terms else intervention}",
                        "count": "patient.reaction.reactionmeddrapt.exact",
                        "limit": 5,
                    },
                )
                safety = {
                    "dailymed": dailymed_data,
                    "openfda_faers": faers_data,
                }
            except ToolExecutionError as exc:
                warnings.append(f"Safety retrieval degraded: {exc.message}")

        longevity: dict[str, Any] | None = None
        if include_longevity:
            try:
                url = "https://genomics.senescence.info/longevity/drugs/drugage.csv"
                csv_text, _ = http.get_text(url=url)
                matching_rows = [line for line in csv_text.splitlines() if intervention.lower() in line.lower()][:10]
                longevity = {
                    "drugage_matches": matching_rows,
                    "source": url,
                }
            except ToolExecutionError as exc:
                warnings.append(f"Longevity retrieval degraded: {exc.message}")

        source_counts = {
            "pmid_count": len(pmids),
            "study_count": len(studies),
            "trial_count": len(trials),
            "trial_link_count": len(trial_publication_links),
        }

        bundle = {
            "concept": concept,
            "search_terms": {
                "pubmed": concept_terms,
                "clinicaltrials": concept_terms[:5],
                "safety": concept_terms[:1],
                "topic_terms": topic_terms,
                "query": query,
            },
            "studies": studies,
            "trials": trials,
            "trial_publication_links": trial_publication_links,
            "source_counts": source_counts,
            "warnings": warnings,
        }
        if safety is not None:
            bundle["safety"] = safety
        if longevity is not None:
            bundle["longevity"] = longevity

        return make_tool_output(
            source="pipeline",
            summary=f"Built evidence retrieval bundle for '{intervention}'.",
            data=bundle,
            ids=[str(concept.get("pivot", {}).get("id") or intervention)],
            warnings=warnings,
            citations=[
                {
                    "pmid": study.get("pmid"),
                    "doi": study.get("doi"),
                    "title": study.get("title"),
                    "year": study.get("pub_date"),
                }
                for study in studies
                if study.get("pmid")
            ],
            ctx=ctx,
        )

    def evidence_grade_bundle(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else payload
        if not isinstance(bundle, dict):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Provide 'bundle' object or bundle-like payload")

        score_data = _score_bundle(bundle)

        return make_tool_output(
            source="pipeline",
            summary="Computed deterministic evidence grade.",
            data=score_data,
            ids=[str(score_data.get("score"))],
            ctx=ctx,
        )

    def evidence_generate_report(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else {}
        grade = payload.get("grade") if isinstance(payload.get("grade"), dict) else {}

        if not bundle:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'bundle' is required")
        if not grade:
            grade = _score_bundle(bundle)

        concept = bundle.get("concept") or {}
        source_counts = bundle.get("source_counts") or {}
        trace = grade.get("trace") or []
        gaps = grade.get("gaps") or []

        markdown_lines = [
            f"# Evidence Report: {concept.get('label') or 'Intervention'}",
            "",
            f"- **Type:** {concept.get('type') or 'unknown'}",
            f"- **Pivot:** {concept.get('pivot') or {}}",
            f"- **Score:** {grade.get('score')} ({grade.get('label')})",
            "",
            "## Evidence Counts",
            f"- PMIDs retrieved: {source_counts.get('pmid_count', 0)}",
            f"- Studies parsed: {source_counts.get('study_count', 0)}",
            f"- Trials found: {source_counts.get('trial_count', 0)}",
            f"- Trial-publication links: {source_counts.get('trial_link_count', 0)}",
            "",
            "## Score Trace",
        ]
        for item in trace:
            markdown_lines.append(f"- `{item.get('component')}`: {item.get('delta')} ({item.get('reason')})")

        markdown_lines.append("")
        markdown_lines.append("## Gaps")
        for gap in gaps:
            markdown_lines.append(f"- {gap}")

        report_markdown = "\n".join(markdown_lines)
        report_json = {
            "intervention": concept,
            "evidence_summary": grade,
            "source_counts": source_counts,
            "trial_publication_links": bundle.get("trial_publication_links") or [],
            "warnings": bundle.get("warnings") or [],
        }

        return make_tool_output(
            source="pipeline",
            summary="Generated evidence report artifacts.",
            data={
                "report_markdown": report_markdown,
                "report_json": report_json,
            },
            ids=[str(concept.get("pivot", {}).get("id") or concept.get("label") or "")],
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="evidence_retrieve_bundle",
            description="Retrieve normalized intervention evidence bundle across core sources.",
            input_schema={
                "type": "object",
                "properties": {
                    "intervention": {"type": "string"},
                    "topic_terms": {"type": "array", "items": {"type": "string"}},
                    "max_pmids": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200},
                    "max_trials": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                    "include_safety": {"type": "boolean", "default": True},
                    "include_longevity": {"type": "boolean", "default": True},
                    "evidence_age_days": {"type": "integer", "minimum": 0, "default": 365},
                },
                "required": ["intervention"],
            },
            handler=evidence_retrieve_bundle,
            source="pipeline",
        ),
        ToolSpec(
            name="evidence_grade_bundle",
            description="Deterministically score an evidence bundle and provide a scoring trace.",
            input_schema={
                "type": "object",
                "properties": {
                    "bundle": {"type": "object"},
                },
                "required": ["bundle"],
            },
            handler=evidence_grade_bundle,
            source="pipeline",
        ),
        ToolSpec(
            name="evidence_generate_report",
            description="Generate markdown and JSON evidence reports from bundle and grade outputs.",
            input_schema={
                "type": "object",
                "properties": {
                    "bundle": {"type": "object"},
                    "grade": {"type": "object"},
                },
                "required": ["bundle"],
            },
            handler=evidence_generate_report,
            source="pipeline",
        ),
    ]
