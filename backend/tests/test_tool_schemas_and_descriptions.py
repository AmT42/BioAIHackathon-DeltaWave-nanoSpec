from __future__ import annotations

from dataclasses import replace

from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings


def _registry():
    settings = replace(
        get_settings(),
        mock_llm=True,
        openalex_api_key="oa-key",
        enable_builtin_demo_tools=False,
    )
    return create_science_registry(settings)


def test_all_tool_descriptions_include_policy_sections() -> None:
    schemas = _registry().openai_schemas()
    for schema in schemas:
        desc = schema["function"]["description"]
        assert "WHEN:" in desc
        assert "AVOID:" in desc
        assert "CRITICAL_ARGS:" in desc
        assert "RETURNS:" in desc
        assert "FAILS_IF:" in desc


def test_search_tools_expose_query_mode_and_limit_bounds() -> None:
    search_tool_names = {
        "normalize_drug",
        "normalize_compound",
        "normalize_ontology",
        "pubmed_search",
        "openalex_search",
        "clinicaltrials_search",
        "dailymed_search",
        "openfda_faers_aggregate",
        "longevity_drugage_query",
        "chembl_search",
        "chebi_search",
    }

    by_name = {schema["function"]["name"]: schema["function"]["parameters"] for schema in _registry().openai_schemas()}
    for name in search_tool_names:
        params = by_name[name]
        props = params["properties"]
        assert "mode" in props
        assert props["mode"].get("enum") == ["precision", "balanced", "recall"]
        assert props["mode"].get("default") == "balanced"
        assert "limit" in props
        assert "minimum" in props["limit"]
        assert "maximum" in props["limit"]


def test_fetch_tools_require_ids() -> None:
    fetch_tool_names = {
        "normalize_drug_related",
        "normalize_compound_fetch",
        "normalize_ontology_fetch",
        "pubmed_fetch",
        "openalex_fetch",
        "clinicaltrials_fetch",
        "trial_publication_linker",
        "dailymed_fetch_sections",
        "longevity_itp_fetch_summary",
        "chembl_fetch",
        "chebi_fetch",
    }
    by_name = {schema["function"]["name"]: schema["function"]["parameters"] for schema in _registry().openai_schemas()}
    for name in fetch_tool_names:
        params = by_name[name]
        required = set(params.get("required") or [])
        assert "ids" in required
        assert params["properties"]["ids"]["type"] == "array"
