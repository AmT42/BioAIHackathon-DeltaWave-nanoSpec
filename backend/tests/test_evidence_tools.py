from __future__ import annotations

from dataclasses import replace

from app.agent.evidence.classify import classify_endpoint_class
from app.config import get_settings
from app.agent.tools.context import ToolContext
from app.agent.tools.science_registry import create_science_registry
from app.agent.tools.sources.evidence_tools import build_evidence_tools
from app.agent.tools.sources.literature import build_literature_tools
from app.agent.tools.sources.normalization import build_normalization_tools
from app.agent.tools.sources.trials import build_trial_tools


class TrialsHttp:
    def get_json(self, *, url, params=None, headers=None):
        if "clinicaltrials.gov/api/v2/studies/" in url:
            nct_id = url.rsplit("/", 1)[-1]
            return (
                {
                    "protocolSection": {
                        "identificationModule": {
                            "nctId": nct_id,
                            "briefTitle": "Rapamycin and aging",
                            "officialTitle": "Rapamycin trial",
                        },
                        "statusModule": {
                            "overallStatus": "COMPLETED",
                            "primaryCompletionDateStruct": {"date": "2024-01-01"},
                            "completionDateStruct": {"date": "2024-06-01"},
                            "resultsFirstPostDateStruct": {"date": "2025-01-15"},
                        },
                        "designModule": {
                            "studyType": "INTERVENTIONAL",
                            "phases": ["PHASE2"],
                            "enrollmentInfo": {"count": 88},
                            "armsInterventionsModule": {"armGroups": [{"label": "A"}, {"label": "B"}]},
                        },
                        "outcomesModule": {"primaryOutcomes": [{"measure": "Frailty index"}]},
                        "eligibilityModule": {"eligibilityCriteria": "Adults age >= 65 years."},
                    },
                    "hasResults": True,
                },
                {},
            )
        if "clinicaltrials.gov/api/v2/studies" in url:
            return ({"studies": []}, {})
        if "esearch.fcgi" in url:
            return ({"esearchresult": {"idlist": []}}, {})
        raise AssertionError(f"Unhandled URL {url}")


class LiteratureAndMeshHttp:
    def get_json(self, *, url, params=None, headers=None):
        if "europepmc/webservices/rest/search" in url:
            return (
                {
                    "hitCount": 1,
                    "resultList": {
                        "result": [
                            {
                                "source": "MED",
                                "pmid": "12345",
                                "doi": "10.1000/test",
                                "title": "Rapamycin in aging",
                                "journalTitle": "Aging Journal",
                                "authorString": "Doe et al",
                                "pubYear": "2024",
                                "isOpenAccess": "Y",
                                "abstractText": "Randomized trial in older adults",
                            }
                        ]
                    },
                },
                {},
            )
        if "id.nlm.nih.gov/mesh/lookup/descriptor" in url:
            return (
                [
                    {
                        "resource": "http://id.nlm.nih.gov/mesh/D000001",
                        "label": "Calcimycin",
                    }
                ],
                {},
            )
        if "id.nlm.nih.gov/mesh/lookup/details" in url:
            return (
                {
                    "terms": [{"label": "A-23187"}, {"label": "A23187"}],
                    "scopeNote": "Ionophore antibiotic used in experimental systems.",
                },
                {},
            )
        raise AssertionError(f"Unhandled URL {url}")


class NoopHttp:
    def get_json(self, *, url, params=None, headers=None):
        raise AssertionError(f"Unexpected HTTP call {url}")


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_pubmed_rct_fallback_classifies_level2_with_population_warning() -> None:
    tools = build_evidence_tools()
    out = _tool(tools, "evidence_classify_pubmed_records").handler(
        {
            "records": [
                {
                    "pmid": "123",
                    "title": "Trial in older adults",
                    "abstract": "Randomized study measured frailty.",
                    "publication_types": ["Randomized Controlled Trial"],
                    "mesh_terms": [],
                    "humans": False,
                    "animals": False,
                }
            ]
        },
        None,
    )

    row = out["data"]["records"][0]
    assert row["evidence_level"] == 2
    assert "population_unspecified" in row["quality_flags"]


def test_endpoint_class_does_not_false_positive_canada_as_nad() -> None:
    assert classify_endpoint_class("canada cohort study") != "surrogate_biomarker"


def test_clinicaltrials_fetch_compact_fields_without_raw_by_default() -> None:
    tools = build_trial_tools(get_settings(), TrialsHttp())
    out = _tool(tools, "clinicaltrials_fetch").handler({"ids": ["NCT00000001"]}, None)

    record = out["data"]["studies"][0]
    assert record["primary_completion_date"] == "2024-01-01"
    assert record["completion_date"] == "2024-06-01"
    assert record["results_first_posted_date"] == "2025-01-15"
    assert record["eligibility_summary"]
    assert record["arms_count"] == 2
    assert "raw_study" not in record


def test_clinicaltrials_fetch_includes_raw_when_requested() -> None:
    tools = build_trial_tools(get_settings(), TrialsHttp())
    out = _tool(tools, "clinicaltrials_fetch").handler({"ids": ["NCT00000001"], "include_raw": True}, None)

    record = out["data"]["studies"][0]
    assert "raw_study" in record


def test_normalize_expand_terms_llm_applies_caps_and_short_acronym_filter() -> None:
    tools = build_normalization_tools(NoopHttp(), get_settings())
    out = _tool(tools, "normalize_expand_terms_llm").handler(
        {
            "concept": {
                "label": "rapamycin",
                "synonyms": [{"text": "sirolimus"}],
            },
            "max_exact_synonyms": 2,
            "max_related_terms": 1,
            "llm_suggestions": {
                "exact_synonyms": ["NR", "sirolimus", "rapamycin"],
                "related_terms": ["mTOR inhibitor", "nutrient sensing"],
            },
        },
        None,
    )

    exact = out["data"]["exact_synonyms"]
    related = out["data"]["related_terms"]
    assert len(exact) == 2
    assert all(item["term"].lower() != "nr" for item in exact)
    assert len(related) == 1


def test_europmc_and_mesh_tools_return_contract_shape() -> None:
    settings = replace(get_settings(), openalex_api_key=None, enable_openalex_tools=False, enable_pubmed_tools=True)
    lit_tools = build_literature_tools(settings, LiteratureAndMeshHttp())
    norm_tools = build_normalization_tools(LiteratureAndMeshHttp(), settings)

    europe = _tool(lit_tools, "europmc_search").handler({"query": "rapamycin aging", "page_size": 5}, None)
    assert "summary" in europe
    assert "data" in europe
    assert "ids" in europe
    assert europe["ids"]
    assert europe["source_meta"]["data_schema_version"] == "v2.1"

    mesh = _tool(norm_tools, "normalize_mesh_expand").handler({"query": "calcimycin", "limit": 5}, None)
    assert mesh["data"]["records"][0]["mesh_id"] == "D000001"
    assert mesh["source_meta"]["data_schema_version"] == "v2.1"


def test_evidence_tool_chain_is_deterministic() -> None:
    tools = build_evidence_tools()
    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u")

    classified_pub = _tool(tools, "evidence_classify_pubmed_records").handler(
        {
            "records": [
                {
                    "pmid": "111",
                    "title": "Randomized trial in older adults",
                    "abstract": "Randomized controlled trial measured frailty and hospitalization.",
                    "publication_types": ["Randomized Controlled Trial"],
                    "mesh_terms": ["Humans"],
                    "humans": True,
                    "animals": False,
                    "publication_year": 2024,
                }
            ]
        },
        ctx,
    )
    classified_trials = _tool(tools, "evidence_classify_trial_records").handler(
        {
            "records": [
                {
                    "nct_id": "NCT00000001",
                    "brief_title": "Rapamycin aging study",
                    "study_type": "INTERVENTIONAL",
                    "overall_status": "COMPLETED",
                    "enrollment": 120,
                    "has_results": True,
                    "primary_outcomes": [{"measure": "Frailty"}],
                    "completion_date": "2025-01-01",
                }
            ]
        },
        ctx,
    )

    ledger = _tool(tools, "evidence_build_ledger").handler(
        {
            "pubmed_records": classified_pub["data"]["records"],
            "trial_records": classified_trials["data"]["records"],
        },
        ctx,
    )

    grade_1 = _tool(tools, "evidence_grade").handler({"ledger": ledger["data"]}, ctx)
    grade_2 = _tool(tools, "evidence_grade").handler({"ledger": ledger["data"]}, ctx)
    assert grade_1["data"]["score"] == grade_2["data"]["score"]

    gap = _tool(tools, "evidence_gap_map").handler({"ledger": ledger["data"], "grade": grade_1["data"]}, ctx)
    report = _tool(tools, "evidence_render_report").handler(
        {
            "intervention": {"label": "rapamycin", "type": "drug", "pivot": {"source": "rxnorm", "id": "111"}},
            "ledger": ledger["data"],
            "grade": grade_1["data"],
            "gap_map": gap["data"],
        },
        ctx,
    )

    assert report["data"]["report_json"]["evidence_summary"]["score"] == grade_1["data"]["score"]
    assert report["source_meta"]["data_schema_version"] == "v2.1"
