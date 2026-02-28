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
        if "esearch.fcgi" in url:
            return (
                {
                    "esearchresult": {
                        "idlist": ["12345", "67890"],
                        "count": "2",
                        "webenv": "WENV",
                        "querykey": "1",
                        "querytranslation": "rapamycin[Title/Abstract]",
                    }
                },
                {"x-request-id": "pm-search"},
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
                            "articleids": [
                                {"idtype": "doi", "value": "10.1/x"},
                                {"idtype": "pmc", "value": "PMC9999999"},
                            ],
                        },
                    }
                },
                {"x-request-id": "pm-fetch"},
            )
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
                {"x-request-id": "oa-search"},
            )
        if "rxcui.json" in url:
            return ({"idGroup": {"rxnormId": ["111"]}}, {})
        if "properties.json" in url:
            return ({"properties": {"name": "Sirolimus", "tty": "IN"}}, {})
        raise AssertionError(f"Unhandled url: {url}")

    def get_text(self, *, url, params=None, headers=None):
        if "pmc/utils/oa/oa.fcgi" in url:
            return (
                """
                <OA>
                  <records>
                    <record id="PMC9999999">
                      <link format="pdf" href="https://example.org/pmc9999999.pdf" />
                    </record>
                  </records>
                </OA>
                """,
                {},
            )
        if "efetch.fcgi" in url:
            return (
                """
                <PubmedArticleSet>
                  <PubmedArticle>
                    <MedlineCitation>
                      <PMID>12345</PMID>
                      <Article><Abstract><AbstractText>Abstract A</AbstractText></Abstract></Article>
                    </MedlineCitation>
                  </PubmedArticle>
                </PubmedArticleSet>
                """,
                {},
            )
        raise AssertionError(f"Unhandled text url: {url}")

    def get_bytes(self, *, url, params=None, headers=None):
        if "pmc9999999.pdf" in url:
            return (b"%PDF-1.4\nfake-pdf", {})
        raise AssertionError(f"Unhandled bytes url: {url}")


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_literature_wrappers_pubmed_and_openalex() -> None:
    settings = replace(get_settings(), openalex_api_key="test-key")
    tools = build_literature_tools(settings, FakeHttp())
    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u")

    out = _tool(tools, "pubmed_search").handler({"query": "rapamycin aging", "mode": "precision", "limit": 5}, ctx)
    assert out["ids"] == ["12345", "67890"]

    fetch = _tool(tools, "pubmed_fetch").handler(
        {"ids": ["12345"], "mode": "balanced", "include_abstract": False, "include_full_text": True},
        ctx,
    )
    assert fetch["data"]["records"][0]["is_rct_like"] is True
    assert fetch["data"]["records"][0]["abstract"] == "Abstract A"
    assert fetch["data"]["records"][0]["pdf_url"] == "https://example.org/pmc9999999.pdf"
    assert fetch["data"]["records"][0]["pdf_downloaded"] is True
    assert fetch["data"]["records"][0]["pdf_artifact_path"] is None
    assert fetch["data"]["include_abstract"] is True
    assert fetch["data"]["download_pdf"] is True
    assert "include_abstract=false ignored" in " ".join(fetch["warnings"])

    oa = _tool(tools, "openalex_search").handler({"query": "rapamycin", "mode": "balanced"}, ctx)
    assert oa["ids"] == ["https://openalex.org/W1"]


def test_openalex_get_works_accepts_pmid_style_ids() -> None:
    settings = replace(get_settings(), openalex_api_key="test-key")
    tools = build_literature_tools(settings, FakeHttp())
    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u")

    out = _tool(tools, "openalex_get_works").handler({"ids": ["12345", "PMID:12345"]}, ctx)

    records = out["data"]["records"]
    assert len(records) == 2
    assert all(record.get("pmid") == "12345" for record in records)


def test_concept_merge_prefers_rxnorm_ingredient() -> None:
    tools = build_normalization_tools(FakeHttp())
    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u")

    merged = _tool(tools, "normalize_merge_candidates").handler(
        {
            "user_text": "rapamycin",
            "drug_candidates": {
                "data": {
                    "ingredient_rxcui": "111",
                    "candidates": [{"rxcui": "111", "name": "Sirolimus", "tty": "IN"}],
                }
            },
            "compound_candidates": {
                "data": {
                    "records": [
                        {
                            "cid": "5284616",
                            "inchikey": "AAA",
                            "preferred_name": "Rapamycin",
                        }
                    ]
                }
            },
        },
        ctx,
    )

    concept = merged["data"]["concept"]
    assert concept["type"] == "drug"
    assert concept["pivot"] == {"source": "rxnorm", "id": "111"}


def test_normalize_ontology_fetch_uses_query_endpoint() -> None:
    class OlsHttp:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []

        def get_json(self, *, url, params=None, headers=None):
            self.calls.append((url, params))
            if "ols4/api/search" in url:
                return (
                    {
                        "response": {
                            "docs": [
                                {
                                    "obo_id": "EFO:0000721",
                                    "label": "time",
                                    "iri": "http://www.ebi.ac.uk/efo/EFO_0000721",
                                    "ontology_name": "efo",
                                    "synonym": ["duration"],
                                    "database_cross_reference": ["MESH:D013995"],
                                }
                            ]
                        }
                    },
                    {},
                )
            raise AssertionError(f"Unhandled url: {url}")

    http = OlsHttp()
    tools = build_normalization_tools(http)
    out = _tool(tools, "normalize_ontology_fetch").handler(
        {"ids": ["EFO:0000721"], "ontology": "efo", "mode": "precision"},
        None,
    )

    assert http.calls
    assert http.calls[0][0].endswith("/ols4/api/search")
    assert out["data"]["records"][0]["obo_id"] == "EFO:0000721"


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
    out = _tool(tools, "longevity_drugage_refresh").handler({"mode": "balanced"}, ctx)

    assert out["data"]["stale_cache"] is True
    assert "stale" in out["summary"].lower()
    assert out["warnings"]


def test_hagr_query_honors_string_false_auto_refresh(tmp_path: Path) -> None:
    class NoRefreshHttp:
        def get_bytes(self, *, url, params=None, headers=None):
            raise AssertionError("refresh must not be called when auto_refresh=false")

    cache_root = tmp_path / "cache" / "sources"
    cache_root.mkdir(parents=True, exist_ok=True)

    ctx = ToolContext(thread_id="t", run_id="r", tool_use_id="u", source_cache_root=cache_root)
    tools = build_longevity_tools(NoRefreshHttp())

    with pytest.raises(ToolExecutionError) as exc:
        _tool(tools, "longevity_drugage_query").handler(
            {"query": "rapamycin", "mode": "balanced", "auto_refresh": "false"},
            ctx,
        )

    assert exc.value.code == "NOT_FOUND"


def test_itp_summary_uses_jax_fallback_when_nia_is_blocked() -> None:
    class ITPHttp:
        def get_text(self, *, url, params=None, headers=None):
            if "nia.nih.gov" in url:
                raise ToolExecutionError(code="UPSTREAM_ERROR", message="HTTP 405 from upstream source")
            if "phenome.jax.org" in url:
                return ("<html><body>Median lifespan improved, p = 0.03</body></html>", {})
            raise AssertionError(f"Unhandled url: {url}")

    tools = build_longevity_tools(ITPHttp())
    out = _tool(tools, "longevity_itp_fetch_summary").handler(
        {"ids": ["https://www.nia.nih.gov/itp/example"], "mode": "precision"},
        None,
    )

    assert out["data"]["records"][0]["fallback_used"] is True
    assert out["data"]["records"][0]["blocked_by_waf"] is True
    assert out["data"]["records"][0]["source_host"] == "phenome.jax.org"
    assert out["warnings"]


def test_itp_summary_marks_non_waf_unavailable_when_fallback_succeeds() -> None:
    class ITPHttp:
        def get_text(self, *, url, params=None, headers=None):
            if "nia.nih.gov" in url:
                raise ToolExecutionError(code="UPSTREAM_ERROR", message="Network error while contacting upstream source")
            if "phenome.jax.org" in url:
                return ("<html><body>Median lifespan improved</body></html>", {})
            raise AssertionError(f"Unhandled url: {url}")

    tools = build_longevity_tools(ITPHttp())
    out = _tool(tools, "longevity_itp_fetch_summary").handler(
        {"ids": ["https://www.nia.nih.gov/itp/example"], "mode": "precision"},
        None,
    )

    assert out["data"]["records"][0]["fallback_used"] is True
    assert out["data"]["records"][0]["blocked_by_waf"] is False
    assert out["data"]["records"][0]["source_host"] == "phenome.jax.org"
    assert "unavailable" in out["warnings"][0].lower()


def test_itp_summary_uses_requested_url_when_not_blocked() -> None:
    class ITPHttp:
        def get_text(self, *, url, params=None, headers=None):
            assert url == "https://phenome.jax.org/itp/surv/MetRapa/C2011"
            return ("<html><body>p < 0.05</body></html>", {})

    tools = build_longevity_tools(ITPHttp())
    out = _tool(tools, "longevity_itp_fetch_summary").handler(
        {"ids": ["https://phenome.jax.org/itp/surv/MetRapa/C2011"], "mode": "precision"},
        None,
    )

    assert out["data"]["records"][0]["fallback_used"] is False
    assert out["data"]["records"][0]["blocked_by_waf"] is False
    assert out["warnings"] == []


def test_chebi_endpoints() -> None:
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
            raise AssertionError(f"Unhandled url: {url}")

    settings = replace(get_settings())
    tools = build_optional_source_tools(settings, OptionalHttp())

    chebi_search = _tool(tools, "chebi_search").handler({"query": "nmn", "mode": "balanced", "limit": 5}, None)
    assert chebi_search["data"]["records"][0]["chebi_id"] == "CHEBI:123"

    chebi_fetch = _tool(tools, "chebi_fetch").handler({"ids": ["CHEBI:16708"], "mode": "balanced"}, None)
    assert chebi_fetch["data"]["records"][0]["entity"]["chebi_accession"] == "CHEBI:16708"
    assert chebi_fetch["data"]["records"][0]["synonyms"][0] == "Ade"
