from __future__ import annotations

from typing import Any
from urllib import parse

from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.descriptions import render_tool_description
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.policy import (
    build_pubmed_evidence_queries,
    build_source_query_terms,
    recommend_initial_tools_for_query,
    should_run_trial_publication_audit,
)
from app.agent.tools.registry import ToolSpec


MODES = {"precision", "balanced", "recall"}


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid") or headers.get("x-openai-request-id")


def _require_query(payload: dict[str, Any], key: str = "query") -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ToolExecutionError(code="VALIDATION_ERROR", message=f"'{key}' is required")
    return value


def _require_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode", "balanced")).strip().lower()
    if mode not in MODES:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message="'mode' must be one of: precision, balanced, recall",
            details={"allowed": sorted(MODES)},
        )
    return mode


def _limit_for_mode(payload: dict[str, Any], *, default_precision: int, default_balanced: int, default_recall: int, maximum: int) -> int:
    mode = _require_mode(payload)
    default = {
        "precision": default_precision,
        "balanced": default_balanced,
        "recall": default_recall,
    }[mode]
    raw = payload.get("limit", default)
    try:
        value = int(raw)
    except Exception as exc:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'limit' must be an integer") from exc
    if value < 1 or value > maximum:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"'limit' must be between 1 and {maximum}",
            details={"limit": value, "max": maximum},
        )
    return value


def _require_ids(payload: dict[str, Any], *, max_size: int = 25) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in ids:
        value = str(item or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        cleaned.append(value)

    if not cleaned:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid IDs provided in 'ids'")
    if len(cleaned) > max_size:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"Too many IDs. Maximum is {max_size}",
            details={"provided": len(cleaned), "max": max_size},
        )
    return cleaned


def _unwrap_tool_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if "data" in value and isinstance(value.get("data"), dict):
        return value["data"]
    if "output" in value and isinstance(value["output"], dict):
        maybe_output = value["output"]
        if isinstance(maybe_output.get("data"), dict):
            return maybe_output["data"]
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _compact_ols_term(term: dict[str, Any], *, ontology_hint: str | None = None) -> dict[str, Any]:
    annotation = term.get("annotation") if isinstance(term.get("annotation"), dict) else {}
    xrefs = _as_list(annotation.get("database_cross_reference")) if annotation else []
    synonyms = _as_list(term.get("synonyms"))
    ontology_name = str(term.get("ontology_name") or ontology_hint or "").strip().lower() or None
    return {
        "obo_id": term.get("obo_id"),
        "label": term.get("label"),
        "iri": term.get("iri"),
        "ontology": ontology_name,
        "synonyms": [str(item) for item in synonyms if str(item).strip()],
        "xrefs": [str(item) for item in xrefs if str(item).strip()],
    }


def _first_ols_term(payload: dict[str, Any]) -> dict[str, Any] | None:
    embedded = payload.get("_embedded") if isinstance(payload, dict) else None
    if isinstance(embedded, dict):
        terms = embedded.get("terms")
        if isinstance(terms, list) and terms:
            first = terms[0]
            if isinstance(first, dict):
                return first
    if isinstance(payload, dict) and payload.get("iri") and payload.get("label"):
        return payload
    return None


def build_normalization_tools(http: SimpleHttpClient) -> list[ToolSpec]:
    def normalize_drug(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=5, default_balanced=10, default_recall=20, maximum=30)

        data, headers = http.get_json(
            url="https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": query, "search": "2"},
        )
        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"normalize_drug_{query}", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        rxcui_ids = ((data or {}).get("idGroup") or {}).get("rxnormId") or []
        candidates: list[dict[str, Any]] = []
        ingredient_rxcui: str | None = None

        for rxcui in rxcui_ids[:limit]:
            props_url = f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json"
            props_data, _ = http.get_json(url=props_url)
            props = ((props_data or {}).get("properties") or {})
            name = props.get("name")
            tty = props.get("tty")
            candidates.append(
                {
                    "rxcui": str(rxcui),
                    "name": name,
                    "tty": tty,
                }
            )
            if tty == "IN" and ingredient_rxcui is None:
                ingredient_rxcui = str(rxcui)

        if ingredient_rxcui is None and candidates:
            ingredient_rxcui = str(candidates[0]["rxcui"])

        best = candidates[0] if candidates else None
        return make_tool_output(
            source="rxnorm",
            summary=f"Resolved {len(candidates)} RxNorm candidate(s) for '{query}'.",
            result_kind="id_list",
            data={
                "query": query,
                "mode": mode,
                "candidates": candidates,
                "best": best,
                "ingredient_rxcui": ingredient_rxcui,
            },
            ids=[c["rxcui"] for c in candidates],
            warnings=["No RxNorm match found."] if not candidates else [],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            next_recommended_tools=["normalize_drug_related", "normalize_merge_candidates"],
            ctx=ctx,
        )

    def normalize_drug_related(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=10)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=15, default_balanced=30, default_recall=50, maximum=75)

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for rxcui in ids:
            url = f"https://rxnav.nlm.nih.gov/REST/rxcui/{parse.quote(rxcui)}/allrelated.json"
            try:
                data, headers = http.get_json(url=url)
                raw_ref = write_raw_json_artifact(ctx, f"normalize_drug_related_{rxcui}", data) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
                groups = ((data or {}).get("allRelatedGroup") or {}).get("conceptGroup") or []
                for group in groups:
                    tty = group.get("tty")
                    for concept in group.get("conceptProperties") or []:
                        rows.append(
                            {
                                "input_rxcui": rxcui,
                                "rxcui": concept.get("rxcui"),
                                "name": concept.get("name"),
                                "tty": tty,
                            }
                        )
                        if len(rows) >= limit:
                            break
                    if len(rows) >= limit:
                        break
            except ToolExecutionError as exc:
                warnings.append(f"{rxcui}: {exc.message}")

        return make_tool_output(
            source="rxnorm",
            summary=f"Fetched {len(rows)} related RxNorm concept(s).",
            result_kind="record_list",
            data={"mode": mode, "records": rows},
            ids=[str(item.get("rxcui")) for item in rows if item.get("rxcui")],
            warnings=warnings,
            artifacts=artifacts,
            next_recommended_tools=["normalize_merge_candidates", "retrieval_build_query_terms"],
            request_id=_request_id(headers) if "headers" in locals() else None,
            ctx=ctx,
        )

    def _pubchem_compound(cid: str) -> tuple[dict[str, Any], list[str]]:
        prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{parse.quote(cid)}/property/Title,InChIKey,CanonicalSMILES/JSON"
        prop_data, _ = http.get_json(url=prop_url)
        synonym_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{parse.quote(cid)}/synonyms/JSON"
        syn_data, _ = http.get_json(url=synonym_url)

        props = ((prop_data or {}).get("PropertyTable") or {}).get("Properties") or []
        props_entry = props[0] if props else {}
        syn_info = ((syn_data or {}).get("InformationList") or {}).get("Information") or []
        synonyms = []
        if syn_info:
            synonyms = list(syn_info[0].get("Synonym") or [])

        compound = {
            "cid": str(cid),
            "preferred_name": props_entry.get("Title"),
            "inchikey": props_entry.get("InChIKey"),
            "canonical_smiles": props_entry.get("CanonicalSMILES"),
        }
        return compound, synonyms

    def normalize_compound(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=3, default_balanced=8, default_recall=15, maximum=20)

        cids_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{parse.quote(query)}/cids/JSON"
        data, headers = http.get_json(url=cids_url)

        cids = ((data or {}).get("IdentifierList") or {}).get("CID") or []
        if not cids:
            raise ToolExecutionError(code="NOT_FOUND", message=f"No PubChem CID found for '{query}'")

        records: list[dict[str, Any]] = []
        for cid in cids[:limit]:
            compound, synonyms = _pubchem_compound(str(cid))
            records.append(
                {
                    **compound,
                    "synonyms": [{"text": text, "source": "pubchem", "weight": 0.7} for text in synonyms[:30]],
                }
            )

        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"normalize_compound_{query}", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        ids: list[str] = []
        for record in records:
            if record.get("cid"):
                ids.append(str(record["cid"]))
            if record.get("inchikey"):
                ids.append(str(record["inchikey"]))

        return make_tool_output(
            source="pubchem",
            summary=f"Resolved {len(records)} PubChem compound candidate(s) for '{query}'.",
            result_kind="record_list",
            data={
                "query": query,
                "mode": mode,
                "records": records,
                "best": records[0] if records else None,
            },
            ids=ids,
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            next_recommended_tools=["normalize_compound_fetch", "normalize_merge_candidates"],
            ctx=ctx,
        )

    def normalize_compound_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=20)
        mode = _require_mode(payload)
        id_type = str(payload.get("id_type", "auto")).strip().lower()
        if id_type not in {"auto", "cid", "inchikey"}:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'id_type' must be one of: auto, cid, inchikey")

        records: list[dict[str, Any]] = []
        warnings: list[str] = []

        for raw_id in ids:
            cid: str | None = None
            if id_type in {"auto", "cid"} and raw_id.isdigit():
                cid = raw_id
            if id_type in {"auto", "inchikey"} and cid is None:
                cids_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{parse.quote(raw_id)}/cids/JSON"
                try:
                    cids_data, _ = http.get_json(url=cids_url)
                    cids = ((cids_data or {}).get("IdentifierList") or {}).get("CID") or []
                    if cids:
                        cid = str(cids[0])
                except ToolExecutionError as exc:
                    warnings.append(f"{raw_id}: {exc.message}")

            if cid is None:
                warnings.append(f"{raw_id}: could not resolve to PubChem CID")
                continue

            compound, synonyms = _pubchem_compound(cid)
            records.append(
                {
                    **compound,
                    "synonyms": [{"text": text, "source": "pubchem", "weight": 0.7} for text in synonyms[:50]],
                }
            )

        return make_tool_output(
            source="pubchem",
            summary=f"Fetched {len(records)} PubChem compound record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[str(item.get("cid")) for item in records if item.get("cid")],
            warnings=warnings,
            next_recommended_tools=["normalize_merge_candidates", "retrieval_build_query_terms"],
            ctx=ctx,
        )

    def normalize_ontology(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=5, default_balanced=10, default_recall=20, maximum=50)
        page = max(int(payload.get("page", 1)), 1)
        ontologies = payload.get("ontologies") or []
        if ontologies is not None and not isinstance(ontologies, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ontologies' must be a list of ontology names")

        ontology_param = ",".join(str(item).strip().lower() for item in ontologies if str(item).strip()) if ontologies else None

        data, headers = http.get_json(
            url="https://www.ebi.ac.uk/ols4/api/search",
            params={
                "q": query,
                "rows": limit,
                "start": (page - 1) * limit,
                "ontology": ontology_param,
            },
        )
        docs = (((data or {}).get("response") or {}).get("docs") or [])
        hits: list[dict[str, Any]] = []
        for doc in docs:
            hits.append(
                {
                    "obo_id": doc.get("obo_id"),
                    "label": doc.get("label"),
                    "iri": doc.get("iri"),
                    "ontology": doc.get("ontology_name"),
                    "synonyms": list(doc.get("synonym") or [])[:20],
                    "xrefs": list(doc.get("database_cross_reference") or [])[:20],
                }
            )

        return make_tool_output(
            source="ols",
            summary=f"Found {len(hits)} ontology candidate(s) for '{query}'.",
            result_kind="record_list",
            data={"query": query, "mode": mode, "page": page, "hits": hits, "best": hits[0] if hits else None},
            ids=[hit.get("obo_id") for hit in hits if hit.get("obo_id")],
            pagination={"next_page_token": str(page + 1) if len(hits) >= limit else None, "has_more": len(hits) >= limit},
            request_id=_request_id(headers),
            next_recommended_tools=["normalize_ontology_fetch", "normalize_merge_candidates"],
            ctx=ctx,
        )

    def normalize_ontology_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=20)
        mode = _require_mode(payload)
        ontology_hint = str(payload.get("ontology", "")).strip().lower() or None

        terms: list[dict[str, Any]] = []
        warnings: list[str] = []
        request_headers: dict[str, str] = {}

        for raw in ids:
            iri = raw if raw.startswith("http") else ""
            obo_id = "" if iri else raw
            term: dict[str, Any] | None = None

            if iri and ontology_hint:
                try:
                    data, headers = http.get_json(
                        url=f"https://www.ebi.ac.uk/ols4/api/ontologies/{parse.quote(ontology_hint)}/terms",
                        params={"iri": iri},
                    )
                    request_headers = headers or request_headers
                    first = _first_ols_term(data or {})
                    if isinstance(first, dict):
                        term = _compact_ols_term(first, ontology_hint=ontology_hint)
                except ToolExecutionError:
                    pass

            if term is None and iri:
                try:
                    data, headers = http.get_json(url="https://www.ebi.ac.uk/ols4/api/terms", params={"iri": iri})
                    request_headers = headers or request_headers
                    first = _first_ols_term(data or {})
                    if isinstance(first, dict):
                        term = _compact_ols_term(first, ontology_hint=ontology_hint)
                except ToolExecutionError as exc:
                    warnings.append(f"{raw}: {exc.message}")

            if term is None and obo_id:
                try:
                    search_data, headers = http.get_json(
                        url="https://www.ebi.ac.uk/ols4/api/search",
                        params={"q": obo_id, "rows": 10, "ontology": ontology_hint or None},
                    )
                    request_headers = headers or request_headers
                    docs = (((search_data or {}).get("response") or {}).get("docs") or [])
                    exact = next((item for item in docs if str(item.get("obo_id") or "").lower() == obo_id.lower()), None)
                    best = exact or (docs[0] if docs else None)
                    if isinstance(best, dict):
                        term = {
                            "obo_id": best.get("obo_id"),
                            "label": best.get("label"),
                            "iri": best.get("iri"),
                            "ontology": str(best.get("ontology_name") or ontology_hint or "").strip().lower() or None,
                            "synonyms": [str(item) for item in _as_list(best.get("synonym")) if str(item).strip()],
                            "xrefs": [str(item) for item in _as_list(best.get("database_cross_reference")) if str(item).strip()],
                        }
                except ToolExecutionError as exc:
                    warnings.append(f"{raw}: {exc.message}")

            if term is None:
                warnings.append(f"{raw}: no ontology term found")
                continue
            terms.append(term)

        return make_tool_output(
            source="ols",
            summary=f"Fetched {len(terms)} ontology term record(s).",
            result_kind="record_list",
            data={"mode": mode, "records": terms},
            ids=[term.get("obo_id") for term in terms if term.get("obo_id")],
            warnings=warnings,
            request_id=_request_id(request_headers),
            next_recommended_tools=["normalize_merge_candidates", "retrieval_build_query_terms"],
            ctx=ctx,
        )

    def normalize_merge_candidates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        user_text = str(payload.get("user_text", "")).strip()
        if not user_text:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'user_text' is required")

        drug_data = _unwrap_tool_data(payload.get("drug_candidates") or payload.get("rxnorm") or {})
        compound_data = _unwrap_tool_data(payload.get("compound_candidates") or payload.get("pubchem") or {})
        ontology_data = _unwrap_tool_data(payload.get("ontology_candidates") or payload.get("ols") or {})

        warnings: list[str] = []
        concept_type = "free_text"
        pivot: dict[str, Any] = {"source": "free_text", "id": user_text}
        preferred_label = user_text
        synonyms: list[dict[str, Any]] = []
        xrefs: list[dict[str, Any]] = []

        ingredient_rxcui = drug_data.get("ingredient_rxcui")
        drug_candidates = drug_data.get("candidates") or []
        if ingredient_rxcui:
            concept_type = "drug"
            pivot = {"source": "rxnorm", "id": str(ingredient_rxcui)}
            if drug_candidates:
                preferred_label = str(drug_candidates[0].get("name") or preferred_label)
            xrefs.append({"source": "rxnorm", "id": f"RxCUI:{ingredient_rxcui}"})

        if concept_type == "free_text":
            compound_records = compound_data.get("records") or []
            first_compound = compound_records[0] if compound_records else compound_data
            if isinstance(first_compound, dict) and first_compound.get("inchikey"):
                concept_type = "chemical"
                pivot = {"source": "pubchem", "id": str(first_compound.get("inchikey"))}
                preferred_label = str(first_compound.get("preferred_name") or preferred_label)
                if first_compound.get("cid"):
                    xrefs.append({"source": "pubchem", "id": f"CID:{first_compound.get('cid')}"})

        if concept_type == "free_text":
            hits = ontology_data.get("hits") or ontology_data.get("records") or []
            best = ontology_data.get("best") or (hits[0] if hits else None)
            if isinstance(best, dict):
                obo_id = best.get("obo_id")
                onto = str(best.get("ontology") or "").lower()
                preferred_label = str(best.get("label") or preferred_label)
                if obo_id and onto == "efo":
                    concept_type = "procedure_or_lifestyle"
                    pivot = {"source": "ols", "id": str(obo_id)}
                elif obo_id and onto in {"mondo"}:
                    concept_type = "disease"
                    pivot = {"source": "ols", "id": str(obo_id)}
                elif obo_id and onto in {"hp", "hpo"}:
                    concept_type = "phenotype"
                    pivot = {"source": "ols", "id": str(obo_id)}
                elif obo_id:
                    concept_type = "ontology_term"
                    pivot = {"source": "ols", "id": str(obo_id)}
                if obo_id:
                    xrefs.append({"source": "ols", "id": str(obo_id)})

        for rel in (drug_data.get("records") or drug_data.get("related") or [])[:40]:
            if isinstance(rel, dict) and rel.get("name"):
                synonyms.append({"text": str(rel["name"]), "source": "rxnorm", "weight": 0.8})

        compound_records = compound_data.get("records") or []
        first_compound = compound_records[0] if compound_records else compound_data
        for syn in (first_compound.get("synonyms") if isinstance(first_compound, dict) else []) or []:
            if isinstance(syn, dict) and syn.get("text"):
                synonyms.append(syn)
            elif isinstance(syn, str):
                synonyms.append({"text": syn, "source": "pubchem", "weight": 0.7})

        best_ontology = ontology_data.get("best")
        if isinstance(best_ontology, dict):
            for syn in best_ontology.get("synonyms") or []:
                synonyms.append({"text": str(syn), "source": "ols", "weight": 0.6})

        deduped: list[dict[str, Any]] = []
        seen_syn: set[str] = set()
        for item in synonyms:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen_syn:
                continue
            seen_syn.add(key)
            deduped.append(item)

        if concept_type == "free_text":
            warnings.append("AMBIGUOUS_CONCEPT")

        concept = {
            "label": preferred_label,
            "type": concept_type,
            "pivot": pivot,
            "synonyms": deduped,
            "xrefs": xrefs,
            "warnings": warnings,
            "recommended_initial_tools": recommend_initial_tools_for_query(user_text),
        }

        return make_tool_output(
            source="internal",
            summary=f"Merged candidates into concept type '{concept_type}'.",
            result_kind="document",
            data={"concept": concept},
            ids=[str(pivot.get("id"))],
            warnings=warnings,
            next_recommended_tools=["kg_query", "retrieval_build_query_terms", "retrieval_build_pubmed_templates"],
            ctx=ctx,
        )

    def retrieval_build_query_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        mode = _require_mode(payload)
        concept = _unwrap_tool_data(payload.get("concept") or {})
        if "concept" in concept and isinstance(concept["concept"], dict):
            concept = concept["concept"]

        label = str(concept.get("label") or payload.get("label") or "").strip()
        if not label:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'concept.label' (or 'label') is required")

        synonyms = []
        for item in (concept.get("synonyms") or payload.get("synonyms") or []):
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                synonyms.append(text)

        source_terms = build_source_query_terms(label=label, synonyms=synonyms, mode=mode)

        return make_tool_output(
            source="internal",
            summary="Built source-specific retrieval terms.",
            result_kind="document",
            data={"label": label, "mode": mode, "terms": source_terms},
            ids=source_terms.get("pubmed", []),
            next_recommended_tools=["retrieval_build_pubmed_templates", "pubmed_search", "clinicaltrials_search"],
            ctx=ctx,
        )

    def retrieval_build_pubmed_templates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        intervention_terms = payload.get("intervention_terms")
        if not isinstance(intervention_terms, list):
            terms_from_source = _unwrap_tool_data(payload.get("terms") or {}).get("terms", {})
            intervention_terms = terms_from_source.get("pubmed") if isinstance(terms_from_source, dict) else None

        if not isinstance(intervention_terms, list) or not intervention_terms:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'intervention_terms' must be a non-empty list")

        outcomes = payload.get("outcome_terms") or ["aging", "healthspan", "lifespan", "frailty"]
        if not isinstance(outcomes, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'outcome_terms' must be a list")

        queries = build_pubmed_evidence_queries(
            intervention_terms=[str(item) for item in intervention_terms],
            outcome_terms=[str(item) for item in outcomes],
        )

        return make_tool_output(
            source="internal",
            summary="Built high-evidence-first PubMed query templates.",
            result_kind="document",
            data={"queries": queries},
            ids=["systematic_reviews", "rcts", "observational", "broad"],
            next_recommended_tools=["pubmed_search"],
            ctx=ctx,
        )

    def retrieval_should_run_trial_audit(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        trials = payload.get("trials")
        if not isinstance(trials, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'trials' must be a list")

        run_audit = should_run_trial_publication_audit([item for item in trials if isinstance(item, dict)])
        return make_tool_output(
            source="internal",
            summary="Evaluated whether trial-publication audit should run.",
            result_kind="status",
            data={"should_run": run_audit},
            ids=[],
            next_recommended_tools=["trial_publication_linker"] if run_audit else [],
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="normalize_drug",
            description=render_tool_description(
                purpose="Resolve a potential pharmacologic intervention into RxNorm canonical IDs.",
                when=["input looks like a drug name", "you need canonical ingredient normalization"],
                avoid=["input is a procedure/lifestyle", "input is clearly non-drug disease phenotype"],
                critical_args=["query: user intervention text", "mode: precision/balanced/recall", "limit: candidate cap"],
                returns="ID-first candidate list with best ingredient RxCUI.",
                fails_if=["query missing", "limit out of range", "upstream RxNorm unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "required": ["query"],
            },
            handler=normalize_drug,
            source="rxnorm",
        ),
        ToolSpec(
            name="normalize_drug_related",
            description=render_tool_description(
                purpose="Fetch related RxNorm concepts for known RxCUI IDs.",
                when=["you already have RxCUI IDs", "you need brand/formulation synonym expansion"],
                avoid=["you have no normalized drug ID", "you are normalizing non-drug concepts"],
                critical_args=["ids: list of RxCUI values", "mode: controls expansion breadth", "limit: cap returned related terms"],
                returns="Record list with related RxNorm concepts and term types.",
                fails_if=["ids missing or too many", "invalid mode", "upstream RxNorm unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 75, "default": 30},
                },
                "required": ["ids"],
            },
            handler=normalize_drug_related,
            source="rxnorm",
        ),
        ToolSpec(
            name="normalize_compound",
            description=render_tool_description(
                purpose="Resolve supplement/chemical concepts using PubChem CID/InChIKey mappings.",
                when=["input is likely supplement/chemical", "you need structure-level normalization"],
                avoid=["input is a clinical diagnosis", "input is a pure procedure"],
                critical_args=["query: compound text", "mode: precision/balanced/recall", "limit: candidate cap"],
                returns="Record list with CID, InChIKey, and selected synonyms.",
                fails_if=["query missing", "limit invalid", "no PubChem match"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "required": ["query"],
            },
            handler=normalize_compound,
            source="pubchem",
        ),
        ToolSpec(
            name="normalize_compound_fetch",
            description=render_tool_description(
                purpose="Fetch detailed PubChem compound records by IDs (CID or InChIKey).",
                when=["you already have normalized IDs", "you need expanded synonym and structure details"],
                avoid=["you only have raw query text", "you exceed batch limits"],
                critical_args=["ids: CID/InChIKey list", "id_type: auto/cid/inchikey", "mode: retrieval breadth control"],
                returns="Record list for each resolved compound ID.",
                fails_if=["ids missing", "too many IDs", "unresolvable IDs"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                    "id_type": {"type": "string", "enum": ["auto", "cid", "inchikey"], "default": "auto"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=normalize_compound_fetch,
            source="pubchem",
        ),
        ToolSpec(
            name="normalize_ontology",
            description=render_tool_description(
                purpose="Search ontology concepts (EFO/MONDO/HPO) for procedures, lifestyle, diseases, and phenotypes.",
                when=["input is not clearly a drug", "you need disease/procedure/phenotype canonical IDs"],
                avoid=["you already have exact ontology IDs", "you need direct ID fetch only"],
                critical_args=["query: concept text", "mode: precision/balanced/recall", "limit/page: pagination controls"],
                returns="Record list with OBO IDs, ontology, synonyms, and xrefs.",
                fails_if=["query missing", "invalid mode", "OLS upstream unavailable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "ontologies": {"type": "array", "items": {"type": "string"}},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                },
                "required": ["query"],
            },
            handler=normalize_ontology,
            source="ols",
        ),
        ToolSpec(
            name="normalize_ontology_fetch",
            description=render_tool_description(
                purpose="Fetch ontology term records from OLS by OBO ID or IRI.",
                when=["you already have ontology IDs", "you need canonical term metadata"],
                avoid=["you only have raw text query", "batch size exceeds limits"],
                critical_args=["ids: OBO IDs or IRIs", "ontology: optional hint", "mode: retained for consistency"],
                returns="Record list of normalized ontology terms.",
                fails_if=["ids missing", "invalid mode", "all IDs unresolved"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                    "ontology": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=normalize_ontology_fetch,
            source="ols",
        ),
        ToolSpec(
            name="normalize_merge_candidates",
            description=render_tool_description(
                purpose="Merge normalization outputs into one canonical concept object for retrieval.",
                when=["you have outputs from normalize_* tools", "you need one pivot concept"],
                avoid=["you skipped normalization", "user_text is missing"],
                critical_args=["user_text: original query", "drug_candidates/compound_candidates/ontology_candidates: tool outputs"],
                returns="Single concept document with pivot, type, synonyms, and xrefs.",
                fails_if=["user_text missing", "all candidates empty and no fallback"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "user_text": {"type": "string"},
                    "drug_candidates": {"type": "object"},
                    "compound_candidates": {"type": "object"},
                    "ontology_candidates": {"type": "object"},
                },
                "required": ["user_text"],
            },
            handler=normalize_merge_candidates,
            source="internal",
        ),
        ToolSpec(
            name="retrieval_build_query_terms",
            description=render_tool_description(
                purpose="Build source-specific search terms from a normalized concept.",
                when=["normalization is complete", "you need deterministic query terms per source"],
                avoid=["you have no concept label", "you want direct raw searching"],
                critical_args=["concept: merged concept object", "mode: precision/balanced/recall"],
                returns="Document with curated terms for PubMed, ClinicalTrials, safety, and expansion.",
                fails_if=["concept label missing", "invalid mode"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "concept": {"type": "object"},
                    "label": {"type": "string"},
                    "synonyms": {"type": "array", "items": {"type": "string"}},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["concept"],
            },
            handler=retrieval_build_query_terms,
            source="internal",
        ),
        ToolSpec(
            name="retrieval_build_pubmed_templates",
            description=render_tool_description(
                purpose="Create high-evidence-first PubMed query templates (reviews -> RCTs -> observational -> broad).",
                when=["you have intervention terms", "you need tiered retrieval order"],
                avoid=["empty intervention term set", "fully manual query authoring"],
                critical_args=["intervention_terms: required list", "outcome_terms: optional override"],
                returns="Document containing deterministic PubMed query templates by evidence tier.",
                fails_if=["intervention_terms missing", "outcome_terms invalid"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "intervention_terms": {"type": "array", "items": {"type": "string"}},
                    "outcome_terms": {"type": "array", "items": {"type": "string"}},
                    "terms": {"type": "object"},
                },
                "required": ["intervention_terms"],
            },
            handler=retrieval_build_pubmed_templates,
            source="internal",
        ),
        ToolSpec(
            name="retrieval_should_run_trial_audit",
            description=render_tool_description(
                purpose="Decide if trial-publication mismatch audit should run based on trial status/results.",
                when=["you have ClinicalTrials records", "you want deterministic audit trigger"],
                avoid=["you have no trials payload", "you are scoring without trial metadata"],
                critical_args=["trials: list of trial records"],
                returns="Status document with boolean 'should_run'.",
                fails_if=["trials is not a list"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "trials": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["trials"],
            },
            handler=retrieval_should_run_trial_audit,
            source="internal",
        ),
    ]
