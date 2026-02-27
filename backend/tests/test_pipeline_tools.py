from __future__ import annotations

from dataclasses import replace

from app.config import get_settings
from app.agent.tools.sources.pipeline import build_pipeline_tools


class PipelineHttp:
    def get_json(self, *, url, params=None, headers=None):
        if "rxnav.nlm.nih.gov/REST/rxcui.json" in url:
            return ({"idGroup": {"rxnormId": ["111"]}}, {})
        if "rxnav.nlm.nih.gov/REST/rxcui/" in url and "properties.json" in url:
            return ({"properties": {"name": "Sirolimus", "tty": "IN"}}, {})
        if "pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" in url and "/cids/JSON" in url:
            return ({"IdentifierList": {"CID": []}}, {})
        if "ebi.ac.uk/ols4/api/search" in url:
            return ({"response": {"docs": []}}, {})
        if "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in url:
            term = str((params or {}).get("term") or "")
            if "NCT" in term:
                return ({"esearchresult": {"idlist": []}}, {})
            return (
                {
                    "esearchresult": {
                        "count": "1",
                        "idlist": ["12345"],
                        "querytranslation": term,
                    }
                },
                {},
            )
        if "clinicaltrials.gov/api/v2/studies" in url:
            return (
                {
                    "studies": [
                        {
                            "protocolSection": {
                                "identificationModule": {
                                    "nctId": "NCT01234567",
                                    "briefTitle": "Rapamycin aging study",
                                },
                                "statusModule": {
                                    "overallStatus": "COMPLETED",
                                    "completionDateStruct": {"date": "2020-01-01"},
                                    "primaryCompletionDateStruct": {"date": "2019-06-01"},
                                },
                            },
                            "hasResults": False,
                        }
                    ]
                },
                {},
            )
        raise AssertionError(f"Unhandled URL {url}")

    def get_text(self, *, url, params=None, headers=None):
        if "efetch.fcgi" in url:
            xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <ArticleTitle>Rapamycin RCT in older adults</ArticleTitle>
        <Abstract>
          <AbstractText>Randomized controlled trial measured frailty and hospitalization.</AbstractText>
        </Abstract>
        <Journal><Title>Aging Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Humans</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType=\"doi\">10.1000/test</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""
            return (xml, {})
        raise AssertionError(f"Unhandled URL {url}")


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_pipeline_tools_retrieve_grade_and_report() -> None:
    settings = replace(get_settings(), pubmed_api_key=None, openalex_api_key=None)
    tools = build_pipeline_tools(settings, PipelineHttp())

    bundle_out = _tool(tools, "evidence_retrieve_bundle").handler(
        {
            "intervention": "rapamycin",
            "include_safety": False,
            "include_longevity": False,
        },
        None,
    )
    bundle = bundle_out["data"]
    assert bundle["concept"]["type"] == "drug"
    assert bundle["source_counts"]["study_count"] == 1
    assert bundle["trials"][0]["nct_id"] == "NCT01234567"

    grade_out = _tool(tools, "evidence_grade_bundle").handler({"bundle": bundle}, None)
    grade = grade_out["data"]
    assert 0 <= grade["score"] <= 100
    assert grade["label"] in {"A", "B", "C", "D", "E"}
    assert grade["breakdown"]["total"] == grade["score"]

    report_out = _tool(tools, "evidence_generate_report").handler(
        {"bundle": bundle, "grade": grade},
        None,
    )
    assert "# Evidence Report" in report_out["data"]["report_markdown"]
    assert report_out["data"]["report_json"]["evidence_summary"]["score"] == grade["score"]
