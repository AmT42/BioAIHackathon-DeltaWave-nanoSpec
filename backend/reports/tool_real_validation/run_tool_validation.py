from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.tools.context import ToolContext
from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings


SKIPPED_TOOLS = {
    "openalex_search_works",
    "openalex_get_works",
}
OPTIONAL_TOOL_PREFIXES = ("openalex_",)
OPTIONAL_TOOL_NAMES = {"chembl_search", "chembl_fetch", "chebi_search", "chebi_fetch"}

TOOL_ENDPOINT_HINTS = {
    "openalex_search_works": "https://api.openalex.org/works",
    "openalex_get_works": "https://api.openalex.org/works/{id}",
    "pubmed_enrich_pmids": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
    "clinicaltrials_search_studies": "https://clinicaltrials.gov/api/v2/studies",
    "clinicaltrials_get_studies": "https://clinicaltrials.gov/api/v2/studies/{nct_id}",
    "trial_publication_linker": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    "rxnorm_resolve": "https://rxnav.nlm.nih.gov/REST/rxcui.json",
    "rxnorm_get_related_terms": "https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/allrelated.json",
    "pubchem_resolve": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON",
    "pubchem_get_compound": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/JSON",
    "ols_search_terms": "https://www.ebi.ac.uk/ols4/api/search",
    "ols_get_term": "https://www.ebi.ac.uk/ols4/api/terms?iri={iri}",
    "normalize_mesh_expand": "https://id.nlm.nih.gov/mesh/lookup/descriptor",
    "normalize_expand_terms_llm": "internal",
    "pubmed_search": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    "pubmed_fetch": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
    "europmc_search": "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
    "europmc_fetch": "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
    "clinicaltrials_search": "https://clinicaltrials.gov/api/v2/studies",
    "clinicaltrials_fetch": "https://clinicaltrials.gov/api/v2/studies/{nct_id}",
    "evidence_classify_pubmed_records": "internal",
    "evidence_classify_trial_records": "internal",
    "evidence_build_ledger": "internal",
    "evidence_grade": "internal",
    "evidence_gap_map": "internal",
    "evidence_render_report": "internal",
    "dailymed_search_labels": "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
    "dailymed_get_label_sections": "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.json",
    "openfda_faers_aggregate": "https://api.fda.gov/drug/event.json",
    "hagr_drugage_refresh": "https://genomics.senescence.info/drugs/dataset.zip",
    "hagr_drugage_query": "local_cache:hagr_drugage",
    "itp_fetch_survival_summary": "https://phenome.jax.org/itp/surv/MetRapa/C2011",
    "chembl_search_molecules": "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json",
    "chembl_get_molecule": "https://www.ebi.ac.uk/chembl/api/data/molecule/{chembl_id}.json",
    "chebi_search_entities": "https://www.ebi.ac.uk/chebi/backend/api/public/es_search/",
    "chebi_get_entity": "https://www.ebi.ac.uk/chebi/backend/api/public/compound/{chebi_id}/",
}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_get(result: dict[str, Any], *keys: str, default: Any = None) -> Any:
    node: Any = result
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _is_optional_tool(name: str) -> bool:
    if name.startswith(OPTIONAL_TOOL_PREFIXES):
        return True
    return name in OPTIONAL_TOOL_NAMES


def summarize_strict_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    checked_rows = [
        row
        for row in results
        if bool(row.get("enabled", True))
        if row.get("tool") not in SKIPPED_TOOLS
    ]
    core_rows = [row for row in checked_rows if not _is_optional_tool(str(row.get("tool") or ""))]
    optional_rows = [row for row in checked_rows if _is_optional_tool(str(row.get("tool") or ""))]

    core_failures = [row for row in core_rows if safe_get(row, "result", "status", default="unknown") != "success"]
    optional_failures = [row for row in optional_rows if safe_get(row, "result", "status", default="unknown") != "success"]

    optional_failure_details = []
    for row in optional_failures:
        optional_failure_details.append(
            {
                "tool": row.get("tool"),
                "status": safe_get(row, "result", "status", default="unknown"),
                "error_code": safe_get(row, "result", "error", "code", default=None),
                "error_message": safe_get(row, "result", "error", "message", default=None),
            }
        )

    return {
        "checked_rows": checked_rows,
        "core_rows": core_rows,
        "optional_rows": optional_rows,
        "core_failures": core_failures,
        "optional_failures": optional_failures,
        "optional_failure_details": optional_failure_details,
        "core_passed": not core_failures,
        "strict_passed": not core_failures,
        "strict_failure_tools": [str(row.get("tool")) for row in core_failures],
    }


def main() -> None:
    strict_mode = os.getenv("TOOL_VALIDATION_STRICT", "1").strip().lower() not in {"0", "false", "no", "off"}
    settings = get_settings()
    registry = create_science_registry(settings)
    available_tools = {schema["function"]["name"] for schema in registry.openai_schemas()}

    stamp = now_stamp()
    out_dir = BACKEND_ROOT / "reports" / "tool_real_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"tool_validation_{stamp}.json"
    md_path = out_dir / f"tool_validation_{stamp}.md"

    ctx_base = {
        "thread_id": f"tool-validate-thread-{stamp}",
        "run_id": f"tool-validate-run-{stamp}",
        "request_index": 1,
        "user_msg_index": 1,
    }

    results: list[dict[str, Any]] = []
    call_counter = 0

    def run_tool(name: str, payload: dict[str, Any], *, note: str | None = None) -> dict[str, Any]:
        nonlocal call_counter
        call_counter += 1
        if name not in available_tools:
            rec = {
                "tool": name,
                "payload": payload,
                "result": {"status": "skipped", "reason": "Tool disabled/not registered"},
                "note": note,
                "latency_ms": 0,
                "endpoint": TOOL_ENDPOINT_HINTS.get(name),
                "enabled": False,
            }
            results.append(rec)
            return rec["result"]
        ctx = ToolContext(
            thread_id=ctx_base["thread_id"],
            run_id=ctx_base["run_id"],
            request_index=ctx_base["request_index"],
            user_msg_index=ctx_base["user_msg_index"],
            tool_use_id=f"manual-{call_counter:03d}",
            tool_name=name,
        )
        started = time.perf_counter()
        out = registry.execute(name, payload, ctx=ctx)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
        rec = {
            "tool": name,
            "payload": payload,
            "result": out,
            "note": note,
            "latency_ms": latency_ms,
            "endpoint": TOOL_ENDPOINT_HINTS.get(name),
            "enabled": True,
        }
        results.append(rec)
        return out

    # Core normalization chain
    rx = run_tool("rxnorm_resolve", {"term": "rapamycin", "max_candidates": 5})
    rxcui = safe_get(rx, "output", "data", "ingredient_rxcui")
    if rxcui:
        run_tool("rxnorm_get_related_terms", {"rxcui": str(rxcui)})
    else:
        run_tool("rxnorm_get_related_terms", {"rxcui": "153165"}, note="fallback RxCUI")

    pubchem = run_tool("pubchem_resolve", {"name": "nicotinamide mononucleotide"})
    cid = safe_get(pubchem, "output", "data", "cid")
    inchikey = safe_get(pubchem, "output", "data", "inchikey")
    if cid:
        run_tool("pubchem_get_compound", {"cid": str(cid)})
    elif inchikey:
        run_tool("pubchem_get_compound", {"inchikey": str(inchikey)})
    else:
        run_tool("pubchem_get_compound", {"cid": "14180"}, note="fallback CID")

    ols = run_tool("ols_search_terms", {"q": "hyperbaric oxygen therapy", "ontologies": ["efo", "mondo", "hp"], "rows": 5})
    best_iri = safe_get(ols, "output", "data", "best", "iri")
    best_onto = safe_get(ols, "output", "data", "best", "ontology")
    best_obo = safe_get(ols, "output", "data", "best", "obo_id")
    if best_iri and best_onto:
        run_tool("ols_get_term", {"iri": str(best_iri), "ontology": str(best_onto)})
    elif best_obo:
        run_tool("ols_get_term", {"obo_id": str(best_obo)})
    else:
        run_tool("ols_get_term", {"obo_id": "EFO:0000721"}, note="fallback OBO")

    concept = run_tool(
        "concept_merge_candidates",
        {
            "user_text": "rapamycin",
            "rxnorm": safe_get(rx, "output", default={}),
            "pubchem": safe_get(pubchem, "output", default={}),
            "ols": safe_get(ols, "output", default={}),
        },
    )
    run_tool("build_search_terms", {"concept": safe_get(concept, "output", default={}), "max_synonyms": 10})
    run_tool("normalize_mesh_expand", {"query": "rapamycin", "limit": 5})
    run_tool(
        "normalize_expand_terms_llm",
        {
            "concept": safe_get(concept, "output", "data", "concept", default={}),
            "mode": "balanced",
            "max_exact_synonyms": 8,
            "max_related_terms": 5,
            "llm_suggestions": {
                "exact_synonyms": ["sirolimus", "rapa"],
                "related_terms": ["mTOR inhibitor", "mTOR pathway"],
                "disambiguators": ["mTOR", "aging"],
                "excluded_terms": ["drug", "therapy"],
                "reasoning_notes": "Seeded deterministic expansion for validation run.",
            },
        },
    )

    # Literature enrichment-only (OpenAlex skipped intentionally)
    pub_search = run_tool("pubmed_search", {"query": "(rapamycin OR sirolimus) AND aging", "limit": 10})
    pub_ids = safe_get(pub_search, "output", "ids", default=[]) or []
    if pub_ids:
        run_tool("pubmed_fetch", {"ids": pub_ids[:5], "include_classification_fields": True})
    run_tool("pubmed_enrich_pmids", {"pmids": ["31452104", "31919194"]}, note="PMID enrichment only")
    run_tool("europmc_search", {"query": "rapamycin aging", "page_size": 5})

    # Trials chain
    ct_search = run_tool(
        "clinicaltrials_search_studies",
        {"intr": "rapamycin", "cond": "aging", "page_size": 5},
    )
    nct_ids = safe_get(ct_search, "output", "ids", default=[]) or []
    if nct_ids:
        ct_get = run_tool("clinicaltrials_get_studies", {"nct_ids": nct_ids[:3]})
        run_tool("clinicaltrials_fetch", {"ids": nct_ids[:3], "include_raw": False})
        trials = safe_get(ct_get, "output", "data", "studies", default=[]) or []
        run_tool("trial_publication_linker", {"nct_ids": nct_ids[:3], "trials": trials})
    else:
        run_tool("clinicaltrials_get_studies", {"nct_ids": ["NCT02432287"]}, note="fallback NCT")
        run_tool("clinicaltrials_fetch", {"ids": ["NCT02432287"], "include_raw": False}, note="fallback NCT")
        run_tool("trial_publication_linker", {"nct_ids": ["NCT02432287"], "trials": []}, note="fallback NCT")

    # Deterministic evidence pipeline
    pubmed_for_evidence = run_tool(
        "pubmed_fetch",
        {"ids": ["34567890", "31234567"], "include_classification_fields": True},
        note="best-effort classification sample",
    )
    trials_for_evidence = run_tool(
        "clinicaltrials_get_studies",
        {"nct_ids": nct_ids[:2] if nct_ids else ["NCT02432287"]},
        note="classification sample",
    )
    classified_pub = run_tool(
        "evidence_classify_pubmed_records",
        {"records": safe_get(pubmed_for_evidence, "output", "data", "records", default=[])},
    )
    classified_trials = run_tool(
        "evidence_classify_trial_records",
        {"records": safe_get(trials_for_evidence, "output", "data", "studies", default=[])},
    )
    mismatch_for_ledger = run_tool(
        "trial_publication_linker",
        {
            "nct_ids": nct_ids[:2] if nct_ids else ["NCT02432287"],
            "trials": safe_get(trials_for_evidence, "output", "data", "studies", default=[]),
        },
    )
    ledger = run_tool(
        "evidence_build_ledger",
        {
            "pubmed_records": safe_get(classified_pub, "output", "data", "records", default=[]),
            "trial_records": safe_get(classified_trials, "output", "data", "records", default=[]),
            "optional_source_status": safe_get(mismatch_for_ledger, "output", "data", "links", default=[]),
        },
    )
    grade = run_tool("evidence_grade", {"ledger": safe_get(ledger, "output", "data", default={})})
    gaps = run_tool(
        "evidence_gap_map",
        {"ledger": safe_get(ledger, "output", "data", default={}), "grade": safe_get(grade, "output", "data", default={})},
    )
    run_tool(
        "evidence_render_report",
        {
            "intervention": safe_get(concept, "output", "data", "concept", default={"label": "rapamycin"}),
            "ledger": safe_get(ledger, "output", "data", default={}),
            "grade": safe_get(grade, "output", "data", default={}),
            "gap_map": safe_get(gaps, "output", "data", default={}),
        },
    )

    # Safety
    dm = run_tool("dailymed_search_labels", {"drug_name": "sirolimus", "page": 1, "page_size": 10})
    setids = safe_get(dm, "output", "ids", default=[]) or []
    if setids:
        run_tool("dailymed_get_label_sections", {"setid": str(setids[0])})
    else:
        run_tool("dailymed_get_label_sections", {"setid": "2e9f8f43-a999-489f-a420-f5d0f170f71c"}, note="fallback setid")

    run_tool(
        "openfda_faers_aggregate",
        {
            "search": "patient.drug.medicinalproduct:SIROLIMUS",
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": 5,
        },
    )

    # Longevity
    run_tool("hagr_drugage_refresh", {"dataset": "drugage"})
    run_tool("hagr_drugage_query", {"compound": "rapamycin", "limit": 10, "auto_refresh": True})
    run_tool("itp_fetch_survival_summary", {"url": "https://phenome.jax.org/itp/surv/MetRapa/C2011"})

    # Optional sources
    chembl = run_tool("chembl_search_molecules", {"query": "sirolimus", "limit": 5})
    chembl_ids = safe_get(chembl, "output", "ids", default=[]) or []
    if chembl_ids:
        run_tool("chembl_get_molecule", {"chembl_id": str(chembl_ids[0])})
    else:
        run_tool("chembl_get_molecule", {"chembl_id": "CHEMBL413"}, note="fallback ChEMBL")

    chebi = run_tool("chebi_search_entities", {"query": "nicotinamide mononucleotide", "limit": 5})
    chebi_ids = safe_get(chebi, "output", "ids", default=[]) or []
    if chebi_ids:
        run_tool("chebi_get_entity", {"chebi_id": str(chebi_ids[0])})
    else:
        run_tool("chebi_get_entity", {"chebi_id": "CHEBI:16708"}, note="fallback ChEBI")

    # OpenAlex explicitly skipped per user request
    results.append(
        {
            "tool": "openalex_search_works",
            "payload": {"query": "rapamycin aging", "per_page": 5},
            "result": {"status": "skipped", "reason": "Skipped by user request"},
            "note": "user requested skip",
            "latency_ms": 0,
            "endpoint": TOOL_ENDPOINT_HINTS.get("openalex_search_works"),
            "enabled": "openalex_search_works" in available_tools,
        }
    )
    results.append(
        {
            "tool": "openalex_get_works",
            "payload": {"ids": ["https://openalex.org/W2741809807"]},
            "result": {"status": "skipped", "reason": "Skipped by user request"},
            "note": "user requested skip",
            "latency_ms": 0,
            "endpoint": TOOL_ENDPOINT_HINTS.get("openalex_get_works"),
            "enabled": "openalex_get_works" in available_tools,
        }
    )

    statuses = {}
    for row in results:
        status = safe_get(row, "result", "status", default="unknown")
        statuses[status] = statuses.get(status, 0) + 1

    strict_summary = summarize_strict_results(results)
    checked_rows = strict_summary["checked_rows"]
    core_rows = strict_summary["core_rows"]
    optional_rows = strict_summary["optional_rows"]
    core_failures = strict_summary["core_failures"]
    optional_failures = strict_summary["optional_failures"]
    optional_failure_details = strict_summary["optional_failure_details"]
    core_passed = bool(strict_summary["core_passed"])
    strict_passed = bool(strict_summary["strict_passed"])
    strict_failure_tools = list(strict_summary["strict_failure_tools"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "environment": {
            "openalex_api_key_set": bool(settings.openalex_api_key),
            "pubmed_api_key_set": bool(settings.pubmed_api_key),
            "artifacts_root": str(settings.artifacts_root),
            "source_cache_root": str(settings.source_cache_root),
            "tool_http_timeout_seconds": settings.tool_http_timeout_seconds,
            "tool_http_max_retries": settings.tool_http_max_retries,
        },
        "lineage": ctx_base,
        "summary": {
            "tool_calls": len(results),
            "status_counts": statuses,
            "strict_mode": strict_mode,
            "strict_science_tools_checked": len(checked_rows),
            "strict_science_tools_failed": len(core_failures),
            "core_checked": len(core_rows),
            "core_failed": len(core_failures),
            "core_passed": core_passed,
            "optional_checked": len(optional_rows),
            "optional_failures": optional_failure_details,
            "strict_passed": strict_passed if strict_mode else None,
            "strict_failure_tools": strict_failure_tools,
        },
        "results": results,
    }

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Real Tool Validation Report",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- JSON: `{json_path}`",
        "",
        "## Status Summary",
        "",
    ]
    for k, v in sorted(statuses.items()):
        lines.append(f"- `{k}`: {v}")
    lines.append(f"- `strict_mode`: {strict_mode}")
    lines.append(f"- `strict_science_tools_checked`: {len(checked_rows)}")
    lines.append(f"- `strict_science_tools_failed`: {len(core_failures)}")
    lines.append(f"- `core_checked`: {len(core_rows)}")
    lines.append(f"- `core_failed`: {len(core_failures)}")
    lines.append(f"- `core_passed`: {core_passed}")
    lines.append(f"- `optional_checked`: {len(optional_rows)}")
    lines.append(f"- `optional_failed`: {len(optional_failures)}")
    lines.append(f"- `strict_passed`: {strict_passed if strict_mode else 'n/a'}")

    if optional_failure_details:
        lines.extend(["", "## Optional Failures"])
        for row in optional_failure_details:
            tool = row.get("tool")
            status = row.get("status")
            error_code = row.get("error_code")
            if error_code in {"RATE_LIMIT", "UNCONFIGURED"}:
                lines.append(f"- `{tool}`: `{status}` ({error_code}) - warning only.")
            else:
                lines.append(f"- `{tool}`: `{status}` ({error_code}) - optional failure retained in report.")

    lines.extend([
        "",
        "## Tool Results",
        "",
        "| Tool | Status | Latency (ms) | Endpoint | Notes |",
        "|---|---|---:|---|---|",
    ])

    for row in results:
        tool = row["tool"]
        status = safe_get(row, "result", "status", default="unknown")
        note = row.get("note") or ""
        latency_ms = row.get("latency_ms", "")
        endpoint = row.get("endpoint") or ""
        if status == "error":
            code = safe_get(row, "result", "error", "code", default="")
            msg = safe_get(row, "result", "error", "message", default="")
            note = (note + f" | {code}: {msg}").strip(" |")
        lines.append(f"| `{tool}` | `{status}` | `{latency_ms}` | `{endpoint}` | {note} |")

    if strict_mode and not strict_passed:
        lines.extend(
            [
                "",
                "## Strict Mode Failure",
                "",
                f"Strict validation failed for: {', '.join(f'`{name}`' for name in strict_failure_tools)}",
            ]
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(str(json_path.resolve()))
    print(str(md_path.resolve()))

    if strict_mode and not strict_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
