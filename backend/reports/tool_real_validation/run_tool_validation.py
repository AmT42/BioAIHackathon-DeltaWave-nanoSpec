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

TOOL_ENDPOINT_HINTS = {
    "normalize_drug": "https://rxnav.nlm.nih.gov/REST/rxcui.json",
    "normalize_drug_related": "https://rxnav.nlm.nih.gov/REST/rxcui/{id}/allrelated.json",
    "normalize_compound": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/cids/JSON",
    "normalize_compound_fetch": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/JSON",
    "normalize_ontology": "https://www.ebi.ac.uk/ols4/api/search",
    "normalize_ontology_fetch": "https://www.ebi.ac.uk/ols4/api/terms",
    "normalize_merge_candidates": "internal",
    "retrieval_build_query_terms": "internal",
    "retrieval_build_pubmed_templates": "internal",
    "retrieval_should_run_trial_audit": "internal",
    "pubmed_search": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    "pubmed_fetch": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
    "clinicaltrials_search": "https://clinicaltrials.gov/api/v2/studies",
    "clinicaltrials_fetch": "https://clinicaltrials.gov/api/v2/studies/{nct}",
    "trial_publication_linker": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    "dailymed_search": "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
    "dailymed_fetch_sections": "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml",
    "openfda_faers_aggregate": "https://api.fda.gov/drug/event.json",
    "longevity_drugage_refresh": "https://genomics.senescence.info/drugs/dataset.zip",
    "longevity_drugage_query": "local_cache:hagr_drugage",
    "longevity_itp_fetch_summary": "https://phenome.jax.org/itp/surv/MetRapa/C2011",
    "chembl_search": "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json",
    "chembl_fetch": "https://www.ebi.ac.uk/chembl/api/data/molecule/{id}.json",
    "chebi_search": "https://www.ebi.ac.uk/chebi/backend/api/public/es_search/",
    "chebi_fetch": "https://www.ebi.ac.uk/chebi/backend/api/public/compound/{id}/",
    "semanticscholar_search": "https://api.semanticscholar.org/graph/v1/paper/search",
    "semanticscholar_fetch": "https://api.semanticscholar.org/graph/v1/paper/{id}",
    "openalex_search": "https://api.openalex.org/works",
    "openalex_fetch": "https://api.openalex.org/works/{id}",
    "epistemonikos_search": "https://api.epistemonikos.org/v1/documents/search",
    "epistemonikos_fetch": "https://api.epistemonikos.org/v1/documents/{id}",
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

    drug = run_tool("normalize_drug", {"query": "rapamycin", "mode": "precision"})
    rxcui = safe_get(drug, "output", "data", "ingredient_rxcui")
    if rxcui:
        run_tool("normalize_drug_related", {"ids": [str(rxcui)], "mode": "precision"})

    compound = run_tool("normalize_compound", {"query": "nicotinamide mononucleotide", "mode": "balanced"})
    compound_records = safe_get(compound, "output", "data", "records", default=[]) or []
    compound_ids = []
    if compound_records:
        first = compound_records[0]
        if first.get("cid"):
            compound_ids.append(str(first["cid"]))
    if compound_ids:
        run_tool("normalize_compound_fetch", {"ids": compound_ids, "mode": "balanced"})

    ontology = run_tool("normalize_ontology", {"query": "hyperbaric oxygen therapy", "mode": "precision", "ontologies": ["efo", "mondo", "hp"]})
    obo_id = safe_get(ontology, "output", "data", "best", "obo_id")
    if obo_id:
        run_tool("normalize_ontology_fetch", {"ids": [str(obo_id)], "mode": "precision"})

    merged = run_tool(
        "normalize_merge_candidates",
        {
            "user_text": "rapamycin",
            "drug_candidates": safe_get(drug, "output", default={}),
            "compound_candidates": safe_get(compound, "output", default={}),
            "ontology_candidates": safe_get(ontology, "output", default={}),
        },
    )

    terms = run_tool("retrieval_build_query_terms", {"concept": safe_get(merged, "output", default={}), "mode": "precision"})
    run_tool("retrieval_build_pubmed_templates", {"intervention_terms": safe_get(terms, "output", "data", "terms", "pubmed", default=[]), "outcome_terms": ["aging", "healthspan"]})

    pm = run_tool("pubmed_search", {"query": '"rapamycin"[Title/Abstract] AND aging[Title/Abstract]', "mode": "precision", "limit": 10})
    pmids = safe_get(pm, "output", "ids", default=[]) or []
    if pmids:
        run_tool("pubmed_fetch", {"ids": pmids[:5], "mode": "balanced"})

    ct = run_tool("clinicaltrials_search", {"query": "rapamycin aging", "mode": "precision", "limit": 10})
    ncts = safe_get(ct, "output", "ids", default=[]) or []
    if ncts:
        ctd = run_tool("clinicaltrials_fetch", {"ids": ncts[:3], "mode": "balanced"})
        trials = safe_get(ctd, "output", "data", "studies", default=[]) or []
        run_tool("retrieval_should_run_trial_audit", {"trials": trials})
        run_tool("trial_publication_linker", {"ids": ncts[:3], "trials": trials, "mode": "balanced"})

    dm = run_tool("dailymed_search", {"query": "sirolimus", "mode": "precision", "limit": 5})
    setids = safe_get(dm, "output", "ids", default=[]) or []
    if setids:
        run_tool("dailymed_fetch_sections", {"ids": [setids[0]], "mode": "balanced"})

    run_tool("openfda_faers_aggregate", {"query": "patient.drug.medicinalproduct:SIROLIMUS", "mode": "precision", "limit": 5})

    run_tool("longevity_drugage_refresh", {"mode": "balanced"})
    run_tool("longevity_drugage_query", {"query": "rapamycin", "mode": "balanced", "limit": 10})
    run_tool("longevity_itp_fetch_summary", {"ids": ["https://phenome.jax.org/itp/surv/MetRapa/C2011"], "mode": "precision"})

    run_tool("chembl_search", {"query": "sirolimus", "mode": "balanced", "limit": 5})
    run_tool("chebi_search", {"query": "nicotinamide mononucleotide", "mode": "balanced", "limit": 5})
    run_tool("semanticscholar_search", {"query": "rapamycin aging trial", "mode": "balanced", "limit": 5})

    statuses = {}
    for row in results:
        status = safe_get(row, "result", "status", default="unknown")
        statuses[status] = statuses.get(status, 0) + 1

    strict_rows = [row for row in results if bool(row.get("enabled", True))]
    strict_failures = [row for row in strict_rows if safe_get(row, "result", "status", default="unknown") != "success"]
    strict_passed = not strict_failures

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": {
            "tool_calls": len(results),
            "status_counts": statuses,
            "strict_mode": strict_mode,
            "strict_science_tools_checked": len(strict_rows),
            "strict_science_tools_failed": len(strict_failures),
            "strict_passed": strict_passed if strict_mode else None,
            "strict_failure_tools": [str(row.get("tool")) for row in strict_failures],
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
    lines.append(f"- `strict_science_tools_checked`: {len(strict_rows)}")
    lines.append(f"- `strict_science_tools_failed`: {len(strict_failures)}")

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

    md_path.write_text("\n".join(lines), encoding="utf-8")

    if strict_mode and not strict_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
