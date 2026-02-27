from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib import parse

from app.config import Settings
from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


_NCT_PATTERN = re.compile(r"\bNCT\d{8}\b", flags=re.IGNORECASE)


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid")


def _ensure_openalex_key(settings: Settings) -> str:
    if not settings.openalex_api_key:
        raise ToolExecutionError(
            code="UNCONFIGURED",
            message="OPENALEX_API_KEY is required for OpenAlex tools",
            details={"env": "OPENALEX_API_KEY"},
        )
    return settings.openalex_api_key


def _compact_openalex_record(work: dict[str, Any]) -> dict[str, Any]:
    ids = work.get("ids") or {}
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return {
        "id": work.get("id"),
        "display_name": work.get("display_name"),
        "publication_year": work.get("publication_year"),
        "type": work.get("type"),
        "doi": (ids.get("doi") or "").replace("https://doi.org/", "") if ids.get("doi") else None,
        "pmid": (ids.get("pmid") or "").replace("https://pubmed.ncbi.nlm.nih.gov/", "").strip("/") if ids.get("pmid") else None,
        "openalex": ids.get("openalex"),
        "cited_by_count": work.get("cited_by_count"),
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
        "journal": source.get("display_name"),
    }


def _as_pubmed_params(settings: Settings, base: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    if settings.pubmed_api_key:
        out["api_key"] = settings.pubmed_api_key
    return out


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _parse_pubmed_xml_records(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ToolExecutionError(
            code="UPSTREAM_ERROR",
            message="PubMed eFetch returned invalid XML payload",
            details={"source": "pubmed_efetch"},
        ) from exc

    records: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        pubmed_data = article.find("PubmedData")
        if medline is None:
            continue

        pmid = _text(medline.find("PMID"))
        art = medline.find("Article")

        title = _text(art.find("ArticleTitle")) if art is not None else None

        abstract_parts: list[str] = []
        if art is not None:
            for abs_node in art.findall("./Abstract/AbstractText"):
                abs_text = _text(abs_node)
                if abs_text:
                    abstract_parts.append(abs_text)
        abstract = "\n".join(abstract_parts) if abstract_parts else None

        journal = _text(art.find("./Journal/Title")) if art is not None else None
        pub_date = None
        if art is not None:
            pub_date_node = art.find("./Journal/JournalIssue/PubDate")
            if pub_date_node is not None:
                year = _text(pub_date_node.find("Year"))
                month = _text(pub_date_node.find("Month"))
                day = _text(pub_date_node.find("Day"))
                medline_date = _text(pub_date_node.find("MedlineDate"))
                pub_date = " ".join(part for part in [year, month, day] if part) or medline_date

        publication_types: list[str] = []
        if art is not None:
            for pub_type_node in art.findall("./PublicationTypeList/PublicationType"):
                pub_type = _text(pub_type_node)
                if pub_type:
                    publication_types.append(pub_type)

        mesh_terms: list[str] = []
        for mesh_node in medline.findall("./MeshHeadingList/MeshHeading/DescriptorName"):
            mesh_value = _text(mesh_node)
            if mesh_value:
                mesh_terms.append(mesh_value)

        doi = None
        nct_ids: list[str] = []
        if pubmed_data is not None:
            for article_id in pubmed_data.findall("./ArticleIdList/ArticleId"):
                value = _text(article_id)
                id_type = str(article_id.attrib.get("IdType") or "").lower()
                if not value:
                    continue
                if id_type == "doi" and doi is None:
                    doi = value
                if value.upper().startswith("NCT"):
                    nct_ids.append(value.upper())

        if abstract:
            nct_ids.extend(match.upper() for match in _NCT_PATTERN.findall(abstract))

        dedup_nct_ids: list[str] = []
        seen_nct: set[str] = set()
        for nct in nct_ids:
            key = nct.upper()
            if key in seen_nct:
                continue
            seen_nct.add(key)
            dedup_nct_ids.append(key)

        lower_mesh = {value.lower() for value in mesh_terms}
        humans = "humans" in lower_mesh
        animals = "animals" in lower_mesh

        records.append(
            {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "publication_types": publication_types,
                "mesh_terms": mesh_terms,
                "journal": journal,
                "pub_date": pub_date,
                "doi": doi,
                "nct_ids": dedup_nct_ids,
                "humans": humans,
                "animals": animals,
            }
        )

    return records


def build_literature_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def openalex_search_works(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")

        per_page = min(max(int(payload.get("per_page", 25)), 1), 200)
        page = max(int(payload.get("page", 1)), 1)
        filter_value = str(payload.get("filter", "")).strip() or None

        params = {
            "search": query,
            "per-page": per_page,
            "page": page,
            "api_key": key,
        }
        if filter_value:
            params["filter"] = filter_value

        data, headers = http.get_json(url="https://api.openalex.org/works", params=params)
        artifacts: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"openalex_search_page_{page}", data) if ctx else None
        if raw_ref:
            artifacts.append(raw_ref)

        works = list((data or {}).get("results") or [])
        compact = [_compact_openalex_record(item) for item in works]
        ids = [item.get("id") for item in compact if item.get("id")]

        meta = (data or {}).get("meta") or {}
        count = int(meta.get("count") or 0)
        has_more = bool(page * per_page < count)

        citations = [
            {
                "openalex_id": item.get("id"),
                "doi": item.get("doi"),
                "pmid": item.get("pmid"),
                "title": item.get("display_name"),
                "year": item.get("publication_year"),
            }
            for item in compact
        ]

        return make_tool_output(
            source="openalex",
            summary=f"Retrieved {len(compact)} OpenAlex work(s) for query '{query}'.",
            data={"query": query, "works": compact, "meta": meta},
            ids=ids,
            citations=citations,
            artifacts=artifacts,
            pagination={
                "next_page_token": str(page + 1) if has_more else None,
                "has_more": has_more,
            },
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def openalex_get_works(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []
        request_id: str | None = None

        for raw_id in ids:
            raw = str(raw_id).strip()
            if not raw:
                continue
            if raw.startswith("https://openalex.org/"):
                work_id = raw.rsplit("/", 1)[-1]
            else:
                work_id = raw
            url = f"https://api.openalex.org/works/{parse.quote(work_id)}"
            try:
                data, headers = http.get_json(url=url, params={"api_key": key})
                compact = _compact_openalex_record(data)
                records.append(compact)
                request_id = request_id or _request_id(headers)
                raw_ref = write_raw_json_artifact(ctx, f"openalex_work_{work_id}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{work_id}: {exc.message}")

        return make_tool_output(
            source="openalex",
            summary=f"Fetched {len(records)} OpenAlex work record(s).",
            data={"records": records},
            ids=[item.get("id") for item in records if item.get("id")],
            citations=[
                {
                    "openalex_id": item.get("id"),
                    "doi": item.get("doi"),
                    "pmid": item.get("pmid"),
                    "title": item.get("display_name"),
                    "year": item.get("publication_year"),
                }
                for item in records
            ],
            warnings=warnings,
            artifacts=artifacts,
            request_id=request_id,
            ctx=ctx,
        )

    def pubmed_esearch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        term = str(payload.get("term", "")).strip()
        if not term:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'term' is required")

        retmax = min(max(int(payload.get("retmax", 200)), 1), 5000)
        retstart = max(int(payload.get("retstart", 0)), 0)
        sort = str(payload.get("sort", "relevance")).strip() or "relevance"
        usehistory = bool(payload.get("usehistory", True))

        params = _as_pubmed_params(
            settings,
            {
                "db": "pubmed",
                "term": term,
                "retmax": retmax,
                "retstart": retstart,
                "retmode": "json",
                "sort": sort,
                "usehistory": "y" if usehistory else "n",
            },
        )

        data, headers = http.get_json(
            url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params,
        )

        raw_ref = write_raw_json_artifact(ctx, "pubmed_esearch", data) if ctx else None
        artifacts = [raw_ref] if raw_ref else []

        result = (data or {}).get("esearchresult") or {}
        pmids = list(result.get("idlist") or [])
        total_count = int(result.get("count") or 0)

        return make_tool_output(
            source="pubmed",
            summary=f"Retrieved {len(pmids)} PMID(s) for PubMed query.",
            data={
                "term": term,
                "count": total_count,
                "pmids": pmids,
                "query_translation": result.get("querytranslation"),
                "webenv": result.get("webenv"),
                "query_key": result.get("querykey"),
            },
            ids=pmids,
            artifacts=artifacts,
            request_id=_request_id(headers),
            pagination={
                "next_page_token": str(retstart + retmax) if (retstart + retmax) < total_count else None,
                "has_more": (retstart + retmax) < total_count,
            },
            ctx=ctx,
        )

    def pubmed_efetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        pmids = payload.get("pmids") or []
        if not isinstance(pmids, list) or not pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'pmids' must be a non-empty list")

        clean_pmids = [str(item).strip() for item in pmids if str(item).strip()]
        if not clean_pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid PMID values provided")

        params = _as_pubmed_params(
            settings,
            {
                "db": "pubmed",
                "id": ",".join(clean_pmids),
                "retmode": "xml",
            },
        )
        xml_text, headers = http.get_text(
            url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params=params,
        )
        records = _parse_pubmed_xml_records(xml_text)

        raw_ref = write_raw_json_artifact(ctx, "pubmed_efetch", {"xml": xml_text}) if ctx else None
        artifacts = [raw_ref] if raw_ref else []

        return make_tool_output(
            source="pubmed",
            summary=f"Fetched {len(records)} PubMed record(s) from eFetch.",
            data={"records": records},
            ids=[record.get("pmid") for record in records if record.get("pmid")],
            citations=[
                {
                    "pmid": rec.get("pmid"),
                    "doi": rec.get("doi"),
                    "title": rec.get("title"),
                    "year": rec.get("pub_date"),
                }
                for rec in records
            ],
            artifacts=artifacts,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def pubmed_enrich_pmids(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        pmids = payload.get("pmids") or []
        if not isinstance(pmids, list) or not pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'pmids' must be a non-empty list")

        clean_pmids = [str(item).strip() for item in pmids if str(item).strip()]
        if not clean_pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid PMID values provided")

        params = _as_pubmed_params(
            settings,
            {
                "db": "pubmed",
                "id": ",".join(clean_pmids),
                "retmode": "json",
            },
        )
        data, headers = http.get_json(
            url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params=params,
        )

        result = (data or {}).get("result") or {}
        uids = list(result.get("uids") or [])
        records: list[dict[str, Any]] = []
        for uid in uids:
            item = result.get(uid) or {}
            pub_types = list(item.get("pubtype") or [])
            article_ids = list(item.get("articleids") or [])
            records.append(
                {
                    "pmid": uid,
                    "title": item.get("title"),
                    "pubdate": item.get("pubdate"),
                    "source": item.get("source"),
                    "pub_types": pub_types,
                    "article_ids": article_ids,
                    "is_meta_or_systematic": any(
                        "meta" in str(pt).lower() or "systematic" in str(pt).lower() for pt in pub_types
                    ),
                    "is_rct_like": any("randomized" in str(pt).lower() or "clinical trial" in str(pt).lower() for pt in pub_types),
                }
            )

        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, "pubmed_esummary", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        return make_tool_output(
            source="pubmed",
            summary=f"Enriched {len(records)} PMID record(s) from PubMed metadata.",
            data={"records": records},
            ids=[record["pmid"] for record in records],
            citations=[{"pmid": rec["pmid"], "title": rec.get("title"), "year": rec.get("pubdate")} for rec in records],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    tools: list[ToolSpec] = []

    if settings.enable_openalex_tools and settings.openalex_api_key:
        tools.extend(
            [
                ToolSpec(
                    name="openalex_search_works",
                    description="Search OpenAlex works and return IDs + compact metadata.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "per_page": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
                            "page": {"type": "integer", "minimum": 1, "default": 1},
                            "filter": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                    handler=openalex_search_works,
                    source="openalex",
                ),
                ToolSpec(
                    name="openalex_get_works",
                    description="Fetch specific OpenAlex works by IDs.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["ids"],
                    },
                    handler=openalex_get_works,
                    source="openalex",
                ),
            ]
        )

    if settings.enable_pubmed_tools:
        tools.extend(
            [
                ToolSpec(
                    name="pubmed_esearch",
                    description="Search PubMed and return PMID IDs and retrieval metadata.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "term": {"type": "string"},
                            "retmax": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200},
                            "retstart": {"type": "integer", "minimum": 0, "default": 0},
                            "sort": {"type": "string", "default": "relevance"},
                            "usehistory": {"type": "boolean", "default": True},
                        },
                        "required": ["term"],
                    },
                    handler=pubmed_esearch,
                    source="pubmed",
                ),
                ToolSpec(
                    name="pubmed_efetch",
                    description="Fetch detailed PubMed records by PMID(s) via eFetch XML.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "pmids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["pmids"],
                    },
                    handler=pubmed_efetch,
                    source="pubmed",
                ),
                ToolSpec(
                    name="pubmed_enrich_pmids",
                    description="Enrich PMIDs using PubMed ESummary metadata for evidence classification.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "pmids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["pmids"],
                    },
                    handler=pubmed_enrich_pmids,
                    source="pubmed",
                ),
            ]
        )

    return tools
