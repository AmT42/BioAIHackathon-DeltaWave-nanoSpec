from __future__ import annotations

from typing import Any


def dedupe_terms(terms: list[str], *, min_length: int = 3, max_terms: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = str(raw or "").strip()
        if len(term) < min_length:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= max_terms:
            break
    return out


def build_source_query_terms(
    *,
    label: str,
    synonyms: list[str],
    mode: str = "balanced",
) -> dict[str, list[str]]:
    canonical = str(label or "").strip()
    candidates = [canonical] + list(synonyms or [])
    max_terms = 6 if mode == "precision" else (10 if mode == "balanced" else 16)
    base_terms = dedupe_terms(candidates, max_terms=max_terms)
    return {
        "pubmed": base_terms[: max(3, min(len(base_terms), 10))],
        "clinicaltrials": base_terms[: max(2, min(len(base_terms), 6))],
        "safety": base_terms[:1],
        "expansion": base_terms[1:],
    }


def build_pubmed_evidence_queries(
    *,
    intervention_terms: list[str],
    outcome_terms: list[str] | None = None,
) -> dict[str, str]:
    terms = dedupe_terms(intervention_terms, max_terms=10)
    outcomes = dedupe_terms(outcome_terms or ["aging", "healthspan", "lifespan", "frailty"], max_terms=6)
    interventions = " OR ".join(f'"{term}"[Title/Abstract]' for term in terms) or '""[Title/Abstract]'
    outcomes_clause = " OR ".join(f'"{term}"[Title/Abstract]' for term in outcomes)
    base = f"({interventions}) AND ({outcomes_clause})"
    return {
        "systematic_reviews": f"{base} AND (meta-analysis[pt] OR systematic review[pt])",
        "rcts": f"{base} AND (randomized controlled trial[pt] OR clinical trial[pt])",
        "observational": f"{base} AND (cohort[Title/Abstract] OR observational[Title/Abstract])",
        "broad": base,
    }


def should_run_trial_publication_audit(trials: list[dict[str, Any]]) -> bool:
    for trial in trials:
        status = str((trial or {}).get("overall_status") or "").upper()
        has_results = bool((trial or {}).get("has_results"))
        if status == "COMPLETED" or has_results:
            return True
    return False


def classify_intervention_hint(query: str) -> str:
    text = str(query or "").lower()
    procedure_markers = ["hbot", "hyperbaric", "sauna", "exercise", "fasting", "cold plunge", "caloric restriction"]
    supplement_markers = ["nmn", "nicotinamide", "resveratrol", "quercetin", "nad", "nr "]
    disease_markers = ["disease", "syndrome", "alzheimer", "diabetes", "parkinson", "cancer", "frailty"]
    drug_markers = ["rapamycin", "metformin", "dasatinib", "sirolimus", "inhibitor", "statin", "mab", "inib"]

    if any(marker in text for marker in procedure_markers):
        return "procedure_or_lifestyle"
    if any(marker in text for marker in supplement_markers):
        return "supplement_or_chemical"
    if any(marker in text for marker in disease_markers):
        return "disease_or_phenotype"
    if any(marker in text for marker in drug_markers):
        return "drug"
    return "unknown"


def recommend_initial_tools_for_query(query: str) -> list[str]:
    category = classify_intervention_hint(query)
    if category == "drug":
        return ["normalize_drug", "pubmed_search", "clinicaltrials_search"]
    if category == "supplement_or_chemical":
        return ["normalize_compound", "pubmed_search", "clinicaltrials_search"]
    if category == "procedure_or_lifestyle":
        return ["normalize_ontology", "pubmed_search", "clinicaltrials_search"]
    if category == "disease_or_phenotype":
        return ["normalize_ontology", "pubmed_search", "clinicaltrials_search"]
    return ["normalize_ontology", "pubmed_search", "clinicaltrials_search"]
