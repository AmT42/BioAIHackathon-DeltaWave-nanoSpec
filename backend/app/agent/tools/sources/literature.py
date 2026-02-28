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
_YEAR_PATTERN = re.compile(r"(19|20)\d{2}")


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


def _as_pubmed_params(settings: Settings, base: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    if settings.pubmed_api_key:
        out["api_key"] = settings.pubmed_api_key
    return out


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _extract_year(text: Any) -> int | None:
    match = _YEAR_PATTERN.search(str(text or ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


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
                label = str(abs_node.attrib.get("Label") or "").strip() if isinstance(abs_node.attrib, dict) else ""
                abs_text = _text(abs_node)
                if not abs_text:
                    continue
                abstract_parts.append(f"{label}: {abs_text}" if label else abs_text)
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
                "publication_year": _extract_year(pub_date),
                "doi": doi,
                "nct_ids": dedup_nct_ids,
                "humans": humans,
                "animals": animals,
            }
        )

    return records


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


def _compact_europmc_record(record: dict[str, Any]) -> dict[str, Any]:
    pmid = str(record.get("pmid") or "").strip() or None
    pmcid = str(record.get("pmcid") or "").strip() or None
    doi = str(record.get("doi") or "").strip() or None
    year = _extract_year(record.get("pubYear") or record.get("firstPublicationDate"))
    source = str(record.get("source") or "").strip().upper() or None
    if source == "MED" and pmid:
        record_id = f"PMID:{pmid}"
    elif source == "PMC" and pmcid:
        record_id = f"PMCID:{pmcid}"
    elif doi:
        record_id = f"DOI:{doi}"
    else:
        record_id = str(record.get("id") or "").strip() or None

    return {
        "id": record_id,
        "title": record.get("title"),
        "publication_year": year,
        "source": source,
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": doi,
        "journal": record.get("journalTitle"),
        "author_string": record.get("authorString"),
        "is_open_access": str(record.get("isOpenAccess") or "N").upper() == "Y",
        "abstract_snippet": (str(record.get("abstractText") or "")[:400] or None),
    }


def _europepmc_record_query_from_id(raw_id: str, id_type: str) -> str:
    clean = str(raw_id or "").strip()
    if not clean:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="Empty Europe PMC record ID")

    inferred = id_type
    if inferred == "auto":
        upper = clean.upper()
        if upper.startswith("PMID:"):
            inferred = "pmid"
            clean = clean.split(":", 1)[1]
        elif upper.startswith("PMCID:"):
            inferred = "pmcid"
            clean = clean.split(":", 1)[1]
        elif upper.startswith("DOI:"):
            inferred = "doi"
            clean = clean.split(":", 1)[1]
        elif upper.startswith("PMC"):
            inferred = "pmcid"
        elif clean.isdigit():
            inferred = "pmid"
        elif "/" in clean:
            inferred = "doi"
        else:
            inferred = "pmid"

    if inferred == "pmid":
        return f"EXT_ID:{clean} AND SRC:MED"
    if inferred == "pmcid":
        return f"EXT_ID:{clean} AND SRC:PMC"
    if inferred == "doi":
        return f'DOI:"{clean}"'
    raise ToolExecutionError(code="VALIDATION_ERROR", message="'id_type' must be one of auto|pmid|pmcid|doi")


def build_literature_tools(settings: Settings, http: SimpleHttpClient) -> list[ToolSpec]:
    def openalex_search_works(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        key = _ensure_openalex_key(settings)
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")

        per_page = _safe_int(payload.get("per_page", 25), default=25, minimum=1, maximum=200)
        page = _safe_int(payload.get("page", 1), default=1, minimum=1, maximum=10_000)
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

        retmax = _safe_int(payload.get("retmax", 200), default=200, minimum=1, maximum=5000)
        retstart = _safe_int(payload.get("retstart", 0), default=0, minimum=0, maximum=1_000_000)
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
        pmids = [str(item) for item in (result.get("idlist") or []) if str(item).strip()]
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
            data_schema_version="v2.1",
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
                    "year": rec.get("publication_year"),
                }
                for rec in records
            ],
            artifacts=artifacts,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def pubmed_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = payload.get("ids")
        if ids is None:
            ids = payload.get("pmids")
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

        clean_pmids = [str(item).strip() for item in ids if str(item).strip()]
        if not clean_pmids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid PMID values provided")

        include_abstract = bool(payload.get("include_abstract", True))
        include_classification_fields = bool(payload.get("include_classification_fields", True))
        fields = payload.get("fields") or []
        if fields is not None and not isinstance(fields, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'fields' must be a list")
        selected_fields = {str(item).strip() for item in fields if str(item).strip()}

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

        processed: list[dict[str, Any]] = []
        for record in records:
            out = dict(record)
            if not include_abstract:
                out.pop("abstract", None)
            if not include_classification_fields:
                out.pop("publication_types", None)
                out.pop("mesh_terms", None)
                out.pop("humans", None)
                out.pop("animals", None)
            if selected_fields:
                out = {k: v for k, v in out.items() if k in selected_fields or k == "pmid"}
            processed.append(out)

        raw_ref = write_raw_json_artifact(ctx, "pubmed_fetch", {"xml": xml_text}) if ctx else None
        artifacts = [raw_ref] if raw_ref else []

        return make_tool_output(
            source="pubmed",
            summary=f"Fetched {len(processed)} PubMed record(s).",
            data={"records": processed},
            ids=[record.get("pmid") for record in processed if record.get("pmid")],
            citations=[
                {
                    "pmid": rec.get("pmid"),
                    "doi": rec.get("doi"),
                    "title": rec.get("title"),
                    "year": rec.get("publication_year") or _extract_year(rec.get("pub_date")),
                }
                for rec in processed
            ],
            artifacts=artifacts,
            request_id=_request_id(headers),
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def pubmed_enrich_pmids(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        pmids = payload.get("pmids") or payload.get("ids") or []
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
        uids = [str(item) for item in (result.get("uids") or []) if str(item).strip()]
        records: list[dict[str, Any]] = []
        for uid in uids:
            item = result.get(uid) or {}
            pub_types = list(item.get("pubtype") or [])
            article_ids = list(item.get("articleids") or [])
            doi = None
            for aid in article_ids:
                if isinstance(aid, dict) and str(aid.get("idtype") or "").lower() == "doi":
                    doi = aid.get("value")
                    break
            records.append(
                {
                    "pmid": uid,
                    "title": item.get("title"),
                    "pubdate": item.get("pubdate"),
                    "source": item.get("source"),
                    "pub_types": pub_types,
                    "publication_types": pub_types,
                    "article_ids": article_ids,
                    "doi": doi,
                    "publication_year": _extract_year(item.get("pubdate")),
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
            citations=[
                {
                    "pmid": rec["pmid"],
                    "doi": rec.get("doi"),
                    "title": rec.get("title"),
                    "year": rec.get("publication_year"),
                }
                for rec in records
            ],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def pubmed_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")
        limit = _safe_int(payload.get("limit", 100), default=100, minimum=1, maximum=500)
        page_token = str(payload.get("page_token", "")).strip() or "0"
        try:
            retstart = max(int(page_token), 0)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer offset string") from exc

        out = pubmed_esearch(
            {
                "term": query,
                "retmax": limit,
                "retstart": retstart,
                "sort": payload.get("sort", "relevance"),
                "usehistory": payload.get("usehistory", True),
            },
            ctx,
        )
        data = out.get("data") if isinstance(out, dict) else {}
        if isinstance(data, dict):
            data = dict(data)
            data["query"] = query
            out["data"] = data
        return out

    def europmc_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")
        page_size = _safe_int(payload.get("page_size", 25), default=25, minimum=1, maximum=100)
        page_token = str(payload.get("page_token", "")).strip() or "1"
        try:
            page = max(int(page_token), 1)
        except Exception as exc:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'page_token' must be an integer page number") from exc

        data, headers = http.get_json(
            url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": page_size,
                "page": page,
            },
        )
        result_list = (data or {}).get("resultList") or {}
        raw_results = list(result_list.get("result") or [])
        records = [_compact_europmc_record(item) for item in raw_results]

        hit_count = _safe_int((data or {}).get("hitCount", 0), default=0, minimum=0, maximum=10_000_000)
        has_more = page * page_size < hit_count

        raw_ref = write_raw_json_artifact(ctx, f"europmc_search_page_{page}", data) if ctx else None
        artifacts = [raw_ref] if raw_ref else []

        return make_tool_output(
            source="europmc",
            summary=f"Retrieved {len(records)} Europe PMC record(s).",
            data={"query": query, "records": records, "hit_count": hit_count, "page": page},
            ids=[record.get("id") for record in records if record.get("id")],
            citations=[
                {
                    "pmid": rec.get("pmid"),
                    "pmcid": rec.get("pmcid"),
                    "doi": rec.get("doi"),
                    "title": rec.get("title"),
                    "year": rec.get("publication_year"),
                }
                for rec in records
            ],
            artifacts=artifacts,
            pagination={"next_page_token": str(page + 1) if has_more else None, "has_more": has_more},
            request_id=_request_id(headers),
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def europmc_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")
        id_type = str(payload.get("id_type", "auto")).strip().lower() or "auto"

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []
        request_id: str | None = None

        for raw_id in ids[:100]:
            clean_id = str(raw_id).strip()
            if not clean_id:
                continue
            try:
                query = _europepmc_record_query_from_id(clean_id, id_type)
                data, headers = http.get_json(
                    url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": 1,
                        "page": 1,
                    },
                )
                request_id = request_id or _request_id(headers)
                results = list((((data or {}).get("resultList") or {}).get("result") or []))
                if not results:
                    warnings.append(f"{clean_id}: no Europe PMC record found")
                    continue
                compact = _compact_europmc_record(results[0])
                records.append(compact)
                raw_ref = write_raw_json_artifact(ctx, f"europmc_fetch_{parse.quote(clean_id, safe='')}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{clean_id}: {exc.message}")

        return make_tool_output(
            source="europmc",
            summary=f"Fetched {len(records)} Europe PMC record(s).",
            data={"records": records},
            ids=[record.get("id") for record in records if record.get("id")],
            citations=[
                {
                    "pmid": rec.get("pmid"),
                    "pmcid": rec.get("pmcid"),
                    "doi": rec.get("doi"),
                    "title": rec.get("title"),
                    "year": rec.get("publication_year"),
                }
                for rec in records
            ],
            warnings=warnings,
            artifacts=artifacts,
            request_id=request_id,
            data_schema_version="v2.1",
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
                    name="pubmed_search",
                    description=(
                        "WHEN: Retrieve PMID IDs for a biomedical query before fetching details.\n"
                        "AVOID: Using as a final evidence record source without pubmed_fetch.\n"
                        "CRITICAL_ARGS: query, optional limit/page_token.\n"
                        "RETURNS: ID-first PubMed contract with PMID list and pagination metadata.\n"
                        "FAILS_IF: query is missing."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                            "page_token": {"type": "string"},
                            "sort": {"type": "string", "default": "relevance"},
                            "usehistory": {"type": "boolean", "default": True},
                        },
                        "required": ["query"],
                    },
                    handler=pubmed_search,
                    source="pubmed",
                ),
                ToolSpec(
                    name="pubmed_fetch",
                    description=(
                        "WHEN: Fetch detailed PubMed records for known PMID IDs.\n"
                        "AVOID: Passing non-PMID identifiers.\n"
                        "CRITICAL_ARGS: ids, include_classification_fields.\n"
                        "RETURNS: Compact records with optional publication_types/mesh/humans/animals fields.\n"
                        "FAILS_IF: ids is missing or empty."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "ids": {"type": "array", "items": {"type": "string"}},
                            "include_abstract": {"type": "boolean", "default": True},
                            "include_classification_fields": {"type": "boolean", "default": True},
                            "fields": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["ids"],
                    },
                    handler=pubmed_fetch,
                    source="pubmed",
                ),
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
                ToolSpec(
                    name="europmc_search",
                    description=(
                        "WHEN: Expand literature retrieval with Europe PMC coverage and OA signals.\n"
                        "AVOID: Treating Europe PMC as a replacement for PubMed trial typing.\n"
                        "CRITICAL_ARGS: query, optional page_size/page_token.\n"
                        "RETURNS: ID-first compact Europe PMC records + pagination.\n"
                        "FAILS_IF: query is missing."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                            "page_token": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                    handler=europmc_search,
                    source="europmc",
                ),
                ToolSpec(
                    name="europmc_fetch",
                    description=(
                        "WHEN: Fetch Europe PMC records for known PMID/PMCID/DOI identifiers.\n"
                        "AVOID: Passing unsupported ID types without id_type hints.\n"
                        "CRITICAL_ARGS: ids, optional id_type (auto|pmid|pmcid|doi).\n"
                        "RETURNS: Compact Europe PMC records with resolved IDs and OA flags.\n"
                        "FAILS_IF: ids is missing or empty."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "ids": {"type": "array", "items": {"type": "string"}},
                            "id_type": {"type": "string", "enum": ["auto", "pmid", "pmcid", "doi"], "default": "auto"},
                        },
                        "required": ["ids"],
                    },
                    handler=europmc_fetch,
                    source="europmc",
                ),
            ]
        )

    return tools
