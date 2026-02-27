from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import get_settings
from app.agent.tools.context import ToolContext
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.sources.literature import build_literature_tools
from app.agent.tools.sources.normalization import build_normalization_tools
from app.agent.tools.sources.optional_sources import build_optional_source_tools


class FakeHttp:
    def get_json(self, *, url, params=None, headers=None):
        if "api.openalex.org/works" in url and "/works/" not in url:
            return (
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "display_name": "Study A",
                            "publication_year": 2024,
                            "type": "journal-article",
                            "cited_by_count": 12,
                            "open_access": {"is_oa": True},
                            "ids": {
                                "doi": "https://doi.org/10.1/a",
                                "pmid": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                                "openalex": "https://openalex.org/W1",
                            },
                            "primary_location": {"source": {"display_name": "Nature"}},
                        }
                    ],
                    "meta": {"count": 1, "page": 1, "per_page": 25},
                },
                {"x-request-id": "oa-1"},
            )
        if "esummary.fcgi" in url:
            return (
                {
                    "result": {
                        "uids": ["12345"],
                        "12345": {
                            "title": "Trial paper",
                            "pubdate": "2022",
                            "source": "JAMA",
                            "pubtype": ["Randomized Controlled Trial"],
                            "articleids": [{"idtype": "doi", "value": "10.1/x"}],
                        },
                    }
                },
                {},
            )
        if "rxcui.json" in url:
            return ({"idGroup": {"rxnormId": ["111"]}}, {})
        if "properties.json" in url:
            return ({"properties": {"name": "Sirolimus", "tty": "IN"}}, {})
        raise AssertionError(f"Unhandled url: {url}")


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_literature_wrappers_openalex_and_pubmed() -> None:
    settings = replace(get_settings(), openalex_api_key="test-key")
    tools = build_literature_tools(settings, FakeHttp())
    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u")

    out = _tool(tools, "openalex_search_works").handler({"query": "rapamycin"}, ctx)
    assert out["ids"] == ["https://openalex.org/W1"]
    assert out["data"]["works"][0]["pmid"] == "12345"

    pub = _tool(tools, "pubmed_enrich_pmids").handler({"pmids": ["12345"]}, ctx)
    assert pub["data"]["records"][0]["is_rct_like"] is True


def test_concept_merge_prefers_rxnorm_ingredient() -> None:
    tools = build_normalization_tools(FakeHttp())
    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u")

    merged = _tool(tools, "concept_merge_candidates").handler(
        {
            "user_text": "rapamycin",
            "rxnorm": {
                "data": {
                    "ingredient_rxcui": "111",
                    "candidates": [{"rxcui": "111", "name": "Sirolimus", "tty": "IN"}],
                }
            },
            "pubchem": {
                "data": {
                    "cid": "5284616",
                    "inchikey": "AAA",
                    "preferred_name": "Rapamycin",
                }
            },
        },
        ctx,
    )

    concept = merged["data"]["concept"]
    assert concept["type"] == "drug"
    assert concept["pivot"] == {"source": "rxnorm", "id": "111"}


def test_optional_epistemonikos_requires_key() -> None:
    settings = replace(get_settings(), epistemonikos_api_key=None)
    tools = build_optional_source_tools(settings, FakeHttp())
    with pytest.raises(ToolExecutionError) as exc:
        _tool(tools, "epistemonikos_search_reviews").handler({"query": "aging"}, None)
    assert exc.value.code == "UNCONFIGURED"
