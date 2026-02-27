from __future__ import annotations

from app.agent.tools.policy import (
    build_pubmed_evidence_queries,
    build_source_query_terms,
    recommend_initial_tools_for_query,
)


def test_recommended_initial_tools_for_representative_prompts() -> None:
    assert recommend_initial_tools_for_query("rapamycin") == ["normalize_drug", "pubmed_search", "clinicaltrials_search"]
    assert recommend_initial_tools_for_query("NMN supplement") == ["normalize_compound", "pubmed_search", "clinicaltrials_search"]
    assert recommend_initial_tools_for_query("HBOT for aging") == ["normalize_ontology", "pubmed_search", "clinicaltrials_search"]
    assert recommend_initial_tools_for_query("sauna longevity") == ["normalize_ontology", "pubmed_search", "clinicaltrials_search"]


def test_build_source_query_terms_respects_mode() -> None:
    precision = build_source_query_terms(label="rapamycin", synonyms=["sirolimus", "mTOR inhibitor"], mode="precision")
    balanced = build_source_query_terms(label="rapamycin", synonyms=["sirolimus", "mTOR inhibitor"], mode="balanced")

    assert len(precision["pubmed"]) <= len(balanced["pubmed"])
    assert precision["pubmed"][0].lower() == "rapamycin"


def test_build_pubmed_evidence_queries_has_tiered_templates() -> None:
    queries = build_pubmed_evidence_queries(intervention_terms=["rapamycin", "sirolimus"], outcome_terms=["aging", "frailty"])
    assert "systematic_reviews" in queries
    assert "rcts" in queries
    assert "observational" in queries
    assert "broad" in queries
    assert "meta-analysis[pt]" in queries["systematic_reviews"]
    assert "randomized controlled trial[pt]" in queries["rcts"]
