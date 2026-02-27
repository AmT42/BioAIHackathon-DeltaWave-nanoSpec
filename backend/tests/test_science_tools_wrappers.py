from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import get_settings
from app.agent.tools.context import ToolContext
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.sources.literature import build_literature_tools
from app.agent.tools.sources.longevity import build_longevity_tools
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


def test_ols_get_term_uses_query_endpoint_and_embedded_terms() -> None:
    class OlsHttp:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []

        def get_json(self, *, url, params=None, headers=None):
            self.calls.append((url, params))
            if "ols4/api/ontologies/efo/terms" in url:
                return (
                    {
                        "_embedded": {
                            "terms": [
                                {
                                    "obo_id": "EFO:0000721",
                                    "label": "time",
                                    "iri": "http://www.ebi.ac.uk/efo/EFO_0000721",
                                    "ontology_name": "efo",
                                    "synonyms": ["duration"],
                                    "annotation": {"database_cross_reference": ["MESH:D013995"]},
                                }
                            ]
                        }
                    },
                    {},
                )
            raise AssertionError(f"Unhandled url: {url}")

    http = OlsHttp()
    tools = build_normalization_tools(http)
    out = _tool(tools, "ols_get_term").handler(
        {"iri": "http://www.ebi.ac.uk/efo/EFO_0000721", "ontology": "efo"},
        None,
    )

    assert http.calls
    assert http.calls[0][0].endswith("/ols4/api/ontologies/efo/terms")
    assert out["data"]["term"]["obo_id"] == "EFO:0000721"


def test_hagr_refresh_falls_back_to_stale_cache(tmp_path: Path) -> None:
    class FailingHttp:
        def get_bytes(self, *, url, params=None, headers=None):
            raise ToolExecutionError(code="UPSTREAM_ERROR", message="boom")

    cache_root = tmp_path / "cache" / "sources"
    stale_dir = cache_root / "hagr_drugage"
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_csv = stale_dir / "drugage_20260101T000000Z.csv"
    stale_csv.write_text(
        "compound_name,species,avg_lifespan_change_percent,pubmed_id\nrapamycin,Mus musculus,10.0,12345\n",
        encoding="utf-8",
    )

    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u", source_cache_root=cache_root)
    tools = build_longevity_tools(FailingHttp())
    out = _tool(tools, "hagr_drugage_refresh").handler({"dataset": "drugage"}, ctx)

    assert out["data"]["stale_cache"] is True
    assert "stale" in out["summary"].lower()
    assert out["warnings"]


def test_itp_summary_uses_jax_fallback_when_nia_is_blocked() -> None:
    class ITPHttp:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_text(self, *, url, params=None, headers=None):
            self.calls.append(url)
            if "nia.nih.gov" in url:
                raise ToolExecutionError(code="UPSTREAM_ERROR", message="HTTP 405 from upstream source")
            if "phenome.jax.org" in url:
                return ("<html><body>Median lifespan improved, p = 0.03</body></html>", {})
            raise AssertionError(f"Unhandled url: {url}")

    tools = build_longevity_tools(ITPHttp())
    out = _tool(tools, "itp_fetch_survival_summary").handler({"url": "https://www.nia.nih.gov/itp/example"}, None)

    assert out["data"]["fallback_used"] is True
    assert out["data"]["blocked_by_waf"] is True
    assert out["data"]["source_host"] == "phenome.jax.org"
    assert out["data"]["url"] == "https://phenome.jax.org/itp/surv/MetRapa/C2011"
    assert out["warnings"]


def test_itp_summary_uses_requested_url_when_not_blocked() -> None:
    class ITPHttp:
        def get_text(self, *, url, params=None, headers=None):
            assert url == "https://phenome.jax.org/itp/surv/MetRapa/C2011"
            return ("<html><body>p < 0.05</body></html>", {})

    tools = build_longevity_tools(ITPHttp())
    out = _tool(tools, "itp_fetch_survival_summary").handler(
        {"url": "https://phenome.jax.org/itp/surv/MetRapa/C2011"},
        None,
    )

    assert out["data"]["fallback_used"] is False
    assert out["data"]["blocked_by_waf"] is False
    assert out["data"]["source_host"] == "phenome.jax.org"
    assert out["data"]["requested_url"] == "https://phenome.jax.org/itp/surv/MetRapa/C2011"
    assert out["warnings"] == []


def test_chebi_v2_mapping_and_epistemonikos_documents_endpoints() -> None:
    class OptionalHttp:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None, dict | None]] = []

        def get_json(self, *, url, params=None, headers=None):
            self.calls.append((url, params, headers))
            if "/chebi/backend/api/public/es_search/" in url:
                return (
                    {
                        "results": [
                            {
                                "_source": {
                                    "chebi_accession": "CHEBI:123",
                                    "name": "Test Molecule",
                                    "stars": 3,
                                    "mass": 123.4,
                                    "formula": "C4H4",
                                    "inchikey": "ABC",
                                }
                            }
                        ]
                    },
                    {},
                )
            if "/chebi/backend/api/public/compound/" in url:
                return (
                    {
                        "chebi_accession": "CHEBI:16708",
                        "name": "adenine",
                        "names": {"SYNONYM": [{"name": "Ade"}]},
                    },
                    {},
                )
            if "/v1/documents/search" in url:
                assert headers and headers["Authorization"] == 'Token token="epi-key"'
                return (
                    {
                        "search_info": {"total_results": 1},
                        "results": [{"id": "doc1", "title": "Review", "year": 2024, "classification": "systematic-review"}],
                    },
                    {},
                )
            if "/v1/documents/doc1" in url:
                assert headers and headers["Authorization"] == 'Token token="epi-key"'
                return ({"id": "doc1", "title": "Review"}, {})
            raise AssertionError(f"Unhandled url: {url}")

    settings = replace(get_settings(), epistemonikos_api_key="epi-key")
    tools = build_optional_source_tools(settings, OptionalHttp())

    chebi_search = _tool(tools, "chebi_search_entities").handler({"query": "nmn", "limit": 5}, None)
    assert chebi_search["data"]["records"][0]["chebi_id"] == "CHEBI:123"

    chebi_get = _tool(tools, "chebi_get_entity").handler({"chebi_id": "CHEBI:16708"}, None)
    assert chebi_get["data"]["entity"]["chebi_accession"] == "CHEBI:16708"
    assert chebi_get["data"]["synonyms"][0] == "Ade"

    epi_search = _tool(tools, "epistemonikos_search_reviews").handler({"query": "rapamycin"}, None)
    assert epi_search["data"]["api_version"] == "v1"
    assert epi_search["ids"] == ["doc1"]

    epi_get = _tool(tools, "epistemonikos_get_review").handler({"review_id": "doc1"}, None)
    assert epi_get["data"]["review"]["id"] == "doc1"
