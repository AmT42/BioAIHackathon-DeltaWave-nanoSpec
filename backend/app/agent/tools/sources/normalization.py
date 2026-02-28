from __future__ import annotations

from typing import Any
from urllib import parse

from app.config import Settings
from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


_GENERIC_EXCLUDED_TERMS = {
    "drug",
    "compound",
    "therapy",
    "treatment",
    "intervention",
    "aging",
    "ageing",
}


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid") or headers.get("x-openai-request-id")


def _require_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ToolExecutionError(code="VALIDATION_ERROR", message=f"'{key}' is required")
    return value


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)


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


def _term_allowed(term: str, *, allowed_short: set[str], excluded: set[str]) -> bool:
    clean = " ".join(str(term or "").split()).strip()
    if not clean:
        return False
    lower = clean.lower()
    if lower in excluded:
        return False
    if len(clean) <= 2:
        return clean.upper() in allowed_short
    return True


def _dedupe_terms(terms: list[str], *, max_terms: int, allowed_short: set[str], excluded: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        text = " ".join(str(raw or "").split()).strip()
        if not _term_allowed(text, allowed_short=allowed_short, excluded=excluded):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_terms:
            break
    return out


def build_normalization_tools(http: SimpleHttpClient, settings: Settings | None = None) -> list[ToolSpec]:
    def rxnorm_resolve(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        term = _require_text(payload, "term")
        max_candidates = _safe_int(payload.get("max_candidates", 10), default=10, minimum=1, maximum=50)

        base = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
        data, headers = http.get_json(url=base, params={"name": term, "search": "2"})
        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, "rxnorm_resolve", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        rxcui_ids = ((data or {}).get("idGroup") or {}).get("rxnormId") or []
        candidates: list[dict[str, Any]] = []
        ingredient_rxcui: str | None = None

        for rxcui in rxcui_ids[:max_candidates]:
            props_url = f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json"
            props_data, _ = http.get_json(url=props_url)
            props = ((props_data or {}).get("properties") or {})
            name = props.get("name")
            tty = props.get("tty")
            candidate = {
                "rxcui": str(rxcui),
                "name": name,
                "tty": tty,
            }
            candidates.append(candidate)
            if tty == "IN" and ingredient_rxcui is None:
                ingredient_rxcui = str(rxcui)

        if ingredient_rxcui is None and candidates:
            ingredient_rxcui = str(candidates[0]["rxcui"])

        best = candidates[0] if candidates else None
        return make_tool_output(
            source="rxnorm",
            summary=f"Resolved {len(candidates)} RxNorm candidate(s) for '{term}'.",
            data={
                "query": term,
                "candidates": candidates,
                "best": best,
                "ingredient_rxcui": ingredient_rxcui,
            },
            ids=[c["rxcui"] for c in candidates],
            warnings=["No RxNorm match found."] if not candidates else [],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def rxnorm_get_related_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        rxcui = _require_text(payload, "rxcui")
        limit = _safe_int(payload.get("limit", 75), default=75, minimum=1, maximum=200)
        url = f"https://rxnav.nlm.nih.gov/REST/rxcui/{parse.quote(rxcui)}/allrelated.json"
        data, headers = http.get_json(url=url)
        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"rxnorm_allrelated_{rxcui}", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        related: list[dict[str, Any]] = []
        groups = ((data or {}).get("allRelatedGroup") or {}).get("conceptGroup") or []
        for group in groups:
            tty = group.get("tty")
            for concept in group.get("conceptProperties") or []:
                related.append(
                    {
                        "rxcui": concept.get("rxcui"),
                        "name": concept.get("name"),
                        "tty": tty,
                    }
                )
                if len(related) >= limit:
                    break
            if len(related) >= limit:
                break

        return make_tool_output(
            source="rxnorm",
            summary=f"Fetched {len(related)} related RxNorm concept(s) for {rxcui}.",
            data={"rxcui": rxcui, "related": related},
            ids=[item["rxcui"] for item in related if item.get("rxcui")],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
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

    def pubchem_resolve(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        name = _require_text(payload, "name")
        cids_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{parse.quote(name)}/cids/JSON"
        data, headers = http.get_json(url=cids_url)

        cids = ((data or {}).get("IdentifierList") or {}).get("CID") or []
        if not cids:
            raise ToolExecutionError(code="NOT_FOUND", message=f"No PubChem CID found for '{name}'")

        first_cid = str(cids[0])
        compound, synonyms = _pubchem_compound(first_cid)
        synonym_entries = [{"text": text, "source": "pubchem", "weight": 0.7} for text in synonyms[:60]]

        artifact_refs: list[dict[str, Any]] = []
        raw_ref = write_raw_json_artifact(ctx, f"pubchem_cids_{name}", data) if ctx else None
        if raw_ref:
            artifact_refs.append(raw_ref)

        return make_tool_output(
            source="pubchem",
            summary=f"Resolved PubChem compound for '{name}'.",
            data={
                "query": name,
                **compound,
                "synonyms": synonym_entries,
            },
            ids=[item for item in [compound["cid"], compound.get("inchikey")] if item],
            artifacts=artifact_refs,
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def pubchem_get_compound(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        cid = str(payload.get("cid", "")).strip()
        inchikey = str(payload.get("inchikey", "")).strip()

        resolved_cid = cid
        if not resolved_cid and inchikey:
            cids_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{parse.quote(inchikey)}/cids/JSON"
            cids_data, _ = http.get_json(url=cids_url)
            cids = ((cids_data or {}).get("IdentifierList") or {}).get("CID") or []
            if not cids:
                raise ToolExecutionError(code="NOT_FOUND", message=f"No PubChem CID found for InChIKey '{inchikey}'")
            resolved_cid = str(cids[0])
        if not resolved_cid:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Either 'cid' or 'inchikey' is required")

        compound, synonyms = _pubchem_compound(resolved_cid)
        return make_tool_output(
            source="pubchem",
            summary=f"Fetched PubChem compound {resolved_cid}.",
            data={
                **compound,
                "synonyms": [{"text": text, "source": "pubchem", "weight": 0.7} for text in synonyms[:100]],
            },
            ids=[item for item in [compound["cid"], compound.get("inchikey")] if item],
            ctx=ctx,
        )

    def ols_search_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        q = _require_text(payload, "q")
        rows = _safe_int(payload.get("rows", 10), default=10, minimum=1, maximum=100)
        page = _safe_int(payload.get("page", 1), default=1, minimum=1, maximum=10_000)
        ontologies = payload.get("ontologies") or []
        ontology_param = ",".join(str(item).strip().lower() for item in ontologies if str(item).strip()) if ontologies else None

        data, headers = http.get_json(
            url="https://www.ebi.ac.uk/ols4/api/search",
            params={
                "q": q,
                "rows": rows,
                "start": (page - 1) * rows,
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
            summary=f"Found {len(hits)} ontology term candidate(s) for '{q}'.",
            data={"query": q, "page": page, "hits": hits, "best": hits[0] if hits else None},
            ids=[hit.get("obo_id") for hit in hits if hit.get("obo_id")],
            pagination={"next_page_token": str(page + 1) if len(hits) >= rows else None, "has_more": len(hits) >= rows},
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def ols_get_term(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        iri = str(payload.get("iri", "")).strip()
        obo_id = str(payload.get("obo_id", "")).strip()
        ontology = str(payload.get("ontology", "")).strip().lower()

        if not iri and not obo_id:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Provide either ('iri' and 'ontology') or 'obo_id'")

        request_headers: dict[str, str] = {}
        warnings: list[str] = []
        term: dict[str, Any] | None = None
        tried_paths: list[str] = []

        if iri and ontology:
            tried_paths.append("ontology_terms_by_iri")
            try:
                data, headers = http.get_json(
                    url=f"https://www.ebi.ac.uk/ols4/api/ontologies/{parse.quote(ontology)}/terms",
                    params={"iri": iri},
                )
                request_headers = headers or {}
                first = _first_ols_term(data or {})
                if isinstance(first, dict):
                    term = _compact_ols_term(first, ontology_hint=ontology)
            except ToolExecutionError:
                warnings.append(f"Ontology scoped IRI lookup failed for '{ontology}'.")

        if term is None and iri:
            tried_paths.append("global_terms_by_iri")
            try:
                data, headers = http.get_json(
                    url="https://www.ebi.ac.uk/ols4/api/terms",
                    params={"iri": iri},
                )
                request_headers = headers or request_headers
                first = _first_ols_term(data or {})
                if isinstance(first, dict):
                    term = _compact_ols_term(first, ontology_hint=ontology or None)
            except ToolExecutionError:
                warnings.append("Global IRI lookup failed.")

        if term is None and obo_id:
            tried_paths.append("search_by_obo_id")
            search_data, headers = http.get_json(
                url="https://www.ebi.ac.uk/ols4/api/search",
                params={
                    "q": obo_id,
                    "rows": 10,
                    "ontology": ontology or None,
                },
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
                    "ontology": str(best.get("ontology_name") or ontology).strip().lower() or None,
                    "synonyms": [str(item) for item in _as_list(best.get("synonym")) if str(item).strip()],
                    "xrefs": [str(item) for item in _as_list(best.get("database_cross_reference")) if str(item).strip()],
                }

        if term is None:
            raise ToolExecutionError(
                code="NOT_FOUND",
                message="No ontology term found for provided identifier.",
                details={"iri": iri or None, "obo_id": obo_id or None, "ontology": ontology or None, "tried_paths": tried_paths},
            )

        return make_tool_output(
            source="ols",
            summary="Fetched ontology term.",
            data={"term": term, "tried_paths": tried_paths},
            ids=[term.get("obo_id")] if term.get("obo_id") else [],
            warnings=warnings,
            request_id=_request_id(request_headers),
            ctx=ctx,
        )

    def normalize_mesh_expand(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_text(payload, "query")
        limit = _safe_int(payload.get("limit", 10), default=10, minimum=1, maximum=50)

        search_data, headers = http.get_json(
            url="https://id.nlm.nih.gov/mesh/lookup/descriptor",
            params={"label": query, "match": "contains", "limit": limit},
        )
        candidates = list(search_data or [])

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for item in candidates:
            resource = str((item or {}).get("resource") or "").strip()
            label = str((item or {}).get("label") or "").strip() or None
            if not resource:
                continue
            mesh_id = resource.rsplit("/", 1)[-1]
            try:
                details, _ = http.get_json(
                    url="https://id.nlm.nih.gov/mesh/lookup/details",
                    params={"descriptor": mesh_id},
                )
                terms = details.get("terms") if isinstance(details, dict) else []
                entry_terms = []
                for term_node in terms or []:
                    text = str((term_node or {}).get("label") or "").strip()
                    if text:
                        entry_terms.append(text)

                scope_note = details.get("scopeNote") if isinstance(details, dict) else None
                if scope_note:
                    scope_note = " ".join(str(scope_note).split())[:400]

                records.append(
                    {
                        "mesh_id": mesh_id,
                        "label": label,
                        "entry_terms": _dedupe_terms(entry_terms, max_terms=40, allowed_short=set(), excluded=set()),
                        "scope_note": scope_note,
                    }
                )
                raw_ref = write_raw_json_artifact(ctx, f"mesh_details_{mesh_id}", details) if ctx else None
                if raw_ref:
                    artifacts.append(raw_ref)
            except ToolExecutionError as exc:
                warnings.append(f"{mesh_id}: {exc.message}")

        return make_tool_output(
            source="mesh",
            summary=f"Resolved {len(records)} MeSH descriptor candidate(s) for '{query}'.",
            data={"query": query, "records": records},
            ids=[record.get("mesh_id") for record in records if record.get("mesh_id")],
            warnings=warnings,
            artifacts=artifacts,
            request_id=_request_id(headers),
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def concept_merge_candidates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        user_text = str(payload.get("user_text", "")).strip()
        if not user_text:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'user_text' is required")
        rx_data = _unwrap_tool_data(payload.get("rxnorm") or payload.get("drug_candidates") or {})
        pub_data = _unwrap_tool_data(payload.get("pubchem") or payload.get("compound_candidates") or {})
        ols_data = _unwrap_tool_data(payload.get("ols") or payload.get("ontology_candidates") or {})

        warnings: list[str] = []
        concept_type = "free_text"
        pivot: dict[str, Any] = {"source": "free_text", "id": user_text}
        preferred_label = user_text
        synonyms: list[dict[str, Any]] = []
        xrefs: list[dict[str, Any]] = []

        ingredient_rxcui = rx_data.get("ingredient_rxcui")
        rx_candidates = rx_data.get("candidates") or []
        if ingredient_rxcui:
            concept_type = "drug"
            pivot = {"source": "rxnorm", "id": str(ingredient_rxcui)}
            if rx_candidates:
                preferred_label = str(rx_candidates[0].get("name") or preferred_label)
            xrefs.append({"source": "rxnorm", "id": f"RxCUI:{ingredient_rxcui}"})

        if concept_type == "free_text" and pub_data.get("inchikey"):
            concept_type = "chemical"
            pivot = {"source": "pubchem", "id": str(pub_data.get("inchikey"))}
            preferred_label = str(pub_data.get("preferred_name") or preferred_label)
            if pub_data.get("cid"):
                xrefs.append({"source": "pubchem", "id": f"CID:{pub_data.get('cid')}"})

        if concept_type == "free_text":
            hits = ols_data.get("hits") or []
            best = ols_data.get("best") or (hits[0] if hits else None)
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

        if concept_type == "free_text":
            warnings.append("AMBIGUOUS_CONCEPT")

        rx_related = rx_data.get("related") or rx_data.get("records") or []
        for rel in rx_related[:40]:
            if isinstance(rel, dict) and rel.get("name"):
                synonyms.append({"text": rel["name"], "source": "rxnorm", "weight": 0.8})

        pub_synonyms = pub_data.get("synonyms") or []
        for syn in pub_synonyms[:80]:
            if isinstance(syn, dict) and syn.get("text"):
                synonyms.append(syn)
            elif isinstance(syn, str):
                synonyms.append({"text": syn, "source": "pubchem", "weight": 0.7})

        best_ols = ols_data.get("best")
        if isinstance(best_ols, dict):
            for syn in best_ols.get("synonyms") or []:
                synonyms.append({"text": str(syn), "source": "ols", "weight": 0.6})

        seen: set[str] = set()
        deduped_synonyms: list[dict[str, Any]] = []
        for item in synonyms:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped_synonyms.append(item)

        concept = {
            "label": preferred_label or user_text,
            "type": concept_type,
            "pivot": pivot,
            "synonyms": deduped_synonyms,
            "xrefs": xrefs,
            "warnings": warnings,
        }

        return make_tool_output(
            source="internal",
            summary=f"Merged concept candidates into '{concept['type']}' pivot.",
            data={"concept": concept},
            ids=[pivot.get("id")],
            warnings=warnings,
            ctx=ctx,
        )

    def normalize_expand_terms_llm(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        concept = _unwrap_tool_data(payload.get("concept") or {})
        if "concept" in concept and isinstance(concept["concept"], dict):
            concept = concept["concept"]
        label = str(concept.get("label") or "").strip()
        if not label:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'concept.label' is required")

        mode = str(payload.get("mode", "balanced")).strip().lower() or "balanced"
        if mode not in {"precision", "balanced", "recall"}:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'mode' must be one of precision|balanced|recall")

        max_exact = _safe_int(payload.get("max_exact_synonyms", 12), default=12, minimum=1, maximum=50)
        max_related = _safe_int(payload.get("max_related_terms", 8), default=8, minimum=1, maximum=50)

        llm = payload.get("llm_suggestions") if isinstance(payload.get("llm_suggestions"), dict) else {}
        input_disambiguators = _as_list(payload.get("disambiguators"))
        llm_disambiguators = _as_list(llm.get("disambiguators"))
        disambiguators = [str(item).strip() for item in [*input_disambiguators, *llm_disambiguators] if str(item).strip()]

        short_allowed: set[str] = set()
        for token in disambiguators:
            if len(token) <= 2:
                short_allowed.add(token.upper())

        concept_synonyms = []
        for item in concept.get("synonyms") or []:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
            else:
                text = str(item).strip()
            if text:
                concept_synonyms.append(text)

        llm_exact = [str(item).strip() for item in _as_list(llm.get("exact_synonyms")) if str(item).strip()]
        llm_related = [str(item).strip() for item in _as_list(llm.get("related_terms")) if str(item).strip()]
        excluded_terms = {
            str(item).strip().lower()
            for item in [*(_as_list(payload.get("excluded_terms"))), *(_as_list(llm.get("excluded_terms")))]
            if str(item).strip()
        }

        exact_terms = _dedupe_terms(
            [label, *concept_synonyms, *llm_exact],
            max_terms=max_exact,
            allowed_short=short_allowed,
            excluded=_GENERIC_EXCLUDED_TERMS | excluded_terms,
        )
        related_terms = _dedupe_terms(
            llm_related,
            max_terms=max_related,
            allowed_short=short_allowed,
            excluded=_GENERIC_EXCLUDED_TERMS | excluded_terms,
        )

        # Keep canonical concept first.
        if exact_terms and exact_terms[0].lower() != label.lower():
            exact_terms = [label, *[term for term in exact_terms if term.lower() != label.lower()]]
            exact_terms = exact_terms[:max_exact]

        exact_with_conf = [
            {
                "term": term,
                "confidence": 0.9 if term.lower() == label.lower() else (0.8 if term in concept_synonyms else 0.65),
            }
            for term in exact_terms
        ]
        related_with_conf = [{"term": term, "confidence": 0.6} for term in related_terms]

        reasoning_notes = str(llm.get("reasoning_notes") or payload.get("reasoning_notes") or "").strip()
        if not reasoning_notes:
            if settings and settings.gemini_api_key:
                reasoning_notes = "LLM suggestions unavailable in tool payload; deterministic fallback expansion used."
            else:
                reasoning_notes = "No model suggestions provided; deterministic fallback expansion used."

        return make_tool_output(
            source="internal",
            summary=f"Expanded normalized terms with guarded synonym logic ({mode}).",
            data={
                "concept_label": label,
                "mode": mode,
                "exact_synonyms": exact_with_conf,
                "related_terms": related_with_conf,
                "disambiguators": disambiguators,
                "excluded_terms": sorted(excluded_terms),
                "reasoning_notes": reasoning_notes,
            },
            ids=[item["term"] for item in exact_with_conf],
            warnings=[
                "Short acronyms (1-2 chars) are filtered unless explicitly disambiguated.",
            ],
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def build_search_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        max_synonyms = _safe_int(payload.get("max_synonyms", 10), default=10, minimum=1, maximum=50)
        concept = _unwrap_tool_data(payload.get("concept") or {})
        if "concept" in concept and isinstance(concept["concept"], dict):
            concept = concept["concept"]

        label = str(concept.get("label") or payload.get("label") or "").strip()
        if not label:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'concept.label' (or 'label') is required")

        syn_items = concept.get("synonyms") or payload.get("synonyms") or []
        related_terms = payload.get("related_terms") or []
        disambiguators = payload.get("disambiguators") or []
        excluded_terms = payload.get("excluded_terms") or []

        expanded = _unwrap_tool_data(payload.get("expanded_terms") or {})
        if expanded:
            syn_items = [*syn_items, *(expanded.get("exact_synonyms") or [])]
            related_terms = [*related_terms, *(expanded.get("related_terms") or [])]
            disambiguators = [*disambiguators, *(expanded.get("disambiguators") or [])]
            excluded_terms = [*excluded_terms, *(expanded.get("excluded_terms") or [])]

        short_allowed: set[str] = set()
        for item in disambiguators:
            token = str(item).strip()
            if len(token) <= 2:
                short_allowed.add(token.upper())

        excluded = _GENERIC_EXCLUDED_TERMS | {str(item).strip().lower() for item in excluded_terms if str(item).strip()}

        candidates: list[str] = [label]
        for item in syn_items:
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("term") or "").strip()
            else:
                text = str(item).strip()
            if text:
                candidates.append(text)

        deduped = _dedupe_terms(candidates, max_terms=max_synonyms, allowed_short=short_allowed, excluded=excluded)
        related_clean = _dedupe_terms([str(item) for item in related_terms], max_terms=max_synonyms, allowed_short=short_allowed, excluded=excluded)

        ctgov_terms = deduped[: min(6, len(deduped))]
        safety_terms = deduped[:1]

        return make_tool_output(
            source="internal",
            summary=f"Built controlled search term set with {len(deduped)} primary term(s).",
            data={
                "terms": {
                    "pubmed": deduped,
                    "clinicaltrials": ctgov_terms,
                    "safety": safety_terms,
                    "expansion": related_clean,
                    "exclude": sorted(excluded),
                },
                "label": label,
                "disambiguators": list(short_allowed),
            },
            ids=deduped,
            ctx=ctx,
        )

    # Backward-compatible aliases for the previous/parallel contract.
    def normalize_drug(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        mapped = {
            "term": payload.get("query"),
            "max_candidates": payload.get("limit", 10),
        }
        return rxnorm_resolve(mapped, ctx)

    def normalize_drug_related(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")
        merged_records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for item in ids[:10]:
            try:
                out = rxnorm_get_related_terms({"rxcui": str(item), "limit": payload.get("limit", 75)}, ctx)
                merged_records.extend(out.get("data", {}).get("related") or [])
            except ToolExecutionError as exc:
                warnings.append(f"{item}: {exc.message}")
        return make_tool_output(
            source="rxnorm",
            summary=f"Fetched {len(merged_records)} related RxNorm concept(s).",
            data={"records": merged_records},
            ids=[row.get("rxcui") for row in merged_records if row.get("rxcui")],
            warnings=warnings,
            ctx=ctx,
        )

    def normalize_compound(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        out = pubchem_resolve({"name": payload.get("query")}, ctx)
        record = out.get("data") or {}
        return make_tool_output(
            source="pubchem",
            summary=out.get("summary") or "Resolved PubChem compound.",
            data={"query": record.get("query"), "records": [record], "best": record},
            ids=out.get("ids") or [],
            warnings=out.get("warnings") or [],
            artifacts=out.get("artifacts") or [],
            request_id=((out.get("source_meta") or {}).get("request_id") if isinstance(out.get("source_meta"), dict) else None),
            ctx=ctx,
        )

    def normalize_compound_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for raw in ids[:20]:
            try:
                item = str(raw).strip()
                if not item:
                    continue
                if item.isdigit():
                    out = pubchem_get_compound({"cid": item}, ctx)
                else:
                    out = pubchem_get_compound({"inchikey": item}, ctx)
                records.append(out.get("data") or {})
            except ToolExecutionError as exc:
                warnings.append(f"{raw}: {exc.message}")

        return make_tool_output(
            source="pubchem",
            summary=f"Fetched {len(records)} PubChem compound record(s).",
            data={"records": records},
            ids=[record.get("cid") for record in records if record.get("cid")],
            warnings=warnings,
            ctx=ctx,
        )

    def normalize_ontology(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return ols_search_terms(
            {
                "q": payload.get("query"),
                "rows": payload.get("limit", 10),
                "page": payload.get("page", 1),
                "ontologies": payload.get("ontologies"),
            },
            ctx,
        )

    def normalize_ontology_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = payload.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        ontology = payload.get("ontology")
        for item in ids[:20]:
            text = str(item).strip()
            if not text:
                continue
            try:
                if text.startswith("http"):
                    out = ols_get_term({"iri": text, "ontology": ontology}, ctx)
                else:
                    out = ols_get_term({"obo_id": text, "ontology": ontology}, ctx)
                record = (out.get("data") or {}).get("term")
                if isinstance(record, dict):
                    records.append(record)
            except ToolExecutionError as exc:
                warnings.append(f"{text}: {exc.message}")
        return make_tool_output(
            source="ols",
            summary=f"Fetched {len(records)} ontology term record(s).",
            data={"records": records},
            ids=[record.get("obo_id") for record in records if record.get("obo_id")],
            warnings=warnings,
            ctx=ctx,
        )

    def normalize_merge_candidates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        remapped = {
            "user_text": payload.get("user_text"),
            "rxnorm": payload.get("drug_candidates") or payload.get("rxnorm"),
            "pubchem": payload.get("compound_candidates") or payload.get("pubchem"),
            "ols": payload.get("ontology_candidates") or payload.get("ols"),
        }
        return concept_merge_candidates(remapped, ctx)

    def retrieval_build_query_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        remapped = {
            "concept": payload.get("concept"),
            "label": payload.get("label"),
            "synonyms": payload.get("synonyms"),
            "max_synonyms": payload.get("max_synonyms", 10),
            "related_terms": payload.get("related_terms"),
            "disambiguators": payload.get("disambiguators"),
            "excluded_terms": payload.get("excluded_terms"),
            "expanded_terms": payload.get("expanded_terms"),
        }
        return build_search_terms(remapped, ctx)

    tools: list[ToolSpec] = [
        ToolSpec(
            name="rxnorm_resolve",
            description="Resolve a drug term to RxNorm candidates and ingredient RxCUI.",
            input_schema={
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "max_candidates": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "required": ["term"],
            },
            handler=rxnorm_resolve,
            source="rxnorm",
        ),
        ToolSpec(
            name="rxnorm_get_related_terms",
            description="Fetch related RxNorm terms for a given RxCUI.",
            input_schema={
                "type": "object",
                "properties": {
                    "rxcui": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 75},
                },
                "required": ["rxcui"],
            },
            handler=rxnorm_get_related_terms,
            source="rxnorm",
        ),
        ToolSpec(
            name="pubchem_resolve",
            description="Resolve a chemical/supplement name to PubChem CID and InChIKey.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            handler=pubchem_resolve,
            source="pubchem",
        ),
        ToolSpec(
            name="pubchem_get_compound",
            description="Fetch PubChem compound details by CID or InChIKey.",
            input_schema={
                "type": "object",
                "properties": {
                    "cid": {"type": "string"},
                    "inchikey": {"type": "string"},
                },
            },
            handler=pubchem_get_compound,
            source="pubchem",
        ),
        ToolSpec(
            name="ols_search_terms",
            description="Search ontology terms in OLS4 (EFO/MONDO/HPO etc.).",
            input_schema={
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "ontologies": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                },
                "required": ["q"],
            },
            handler=ols_search_terms,
            source="ols",
        ),
        ToolSpec(
            name="ols_get_term",
            description="Fetch one ontology term by (iri+ontology) or obo_id.",
            input_schema={
                "type": "object",
                "properties": {
                    "iri": {"type": "string"},
                    "ontology": {"type": "string"},
                    "obo_id": {"type": "string"},
                },
            },
            handler=ols_get_term,
            source="ols",
        ),
        ToolSpec(
            name="normalize_mesh_expand",
            description=(
                "WHEN: Expand query terms with MeSH descriptor candidates and entry terms.\n"
                "AVOID: Treating MeSH expansion as canonical concept normalization.\n"
                "CRITICAL_ARGS: query, optional limit/mode.\n"
                "RETURNS: MeSH candidates with mesh_id, label, entry_terms, and scope_note.\n"
                "FAILS_IF: query is missing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["query"],
            },
            handler=normalize_mesh_expand,
            source="mesh",
        ),
        ToolSpec(
            name="concept_merge_candidates",
            description="Deterministically merge normalization candidates into one canonical concept.",
            input_schema={
                "type": "object",
                "properties": {
                    "user_text": {"type": "string"},
                    "rxnorm": {"type": "object"},
                    "pubchem": {"type": "object"},
                    "ols": {"type": "object"},
                },
                "required": ["user_text"],
            },
            handler=concept_merge_candidates,
            source="internal",
        ),
        ToolSpec(
            name="normalize_expand_terms_llm",
            description=(
                "WHEN: Generate guarded synonym/related expansions from normalized concept context.\n"
                "AVOID: Replacing the canonical pivot or sending unconstrained raw LLM lists directly to retrieval.\n"
                "CRITICAL_ARGS: concept, mode, max_exact_synonyms, max_related_terms.\n"
                "RETURNS: exact_synonyms, related_terms, disambiguators, excluded_terms, reasoning_notes.\n"
                "FAILS_IF: concept.label is missing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "concept": {"type": "object"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "max_exact_synonyms": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                    "max_related_terms": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                    "llm_suggestions": {"type": "object"},
                    "disambiguators": {"type": "array", "items": {"type": "string"}},
                    "excluded_terms": {"type": "array", "items": {"type": "string"}},
                    "reasoning_notes": {"type": "string"},
                },
                "required": ["concept"],
            },
            handler=normalize_expand_terms_llm,
            source="internal",
        ),
        ToolSpec(
            name="build_search_terms",
            description="Build controlled source-specific search terms from a normalized concept.",
            input_schema={
                "type": "object",
                "properties": {
                    "concept": {"type": "object"},
                    "label": {"type": "string"},
                    "synonyms": {"type": "array", "items": {"type": "string"}},
                    "max_synonyms": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "related_terms": {"type": "array", "items": {"type": "string"}},
                    "disambiguators": {"type": "array", "items": {"type": "string"}},
                    "excluded_terms": {"type": "array", "items": {"type": "string"}},
                    "expanded_terms": {"type": "object"},
                },
                "required": ["concept"],
            },
            handler=build_search_terms,
            source="internal",
        ),
        ToolSpec(
            name="normalize_drug",
            description="Alias: resolve drug concept via RxNorm.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["query"],
            },
            handler=normalize_drug,
            source="rxnorm",
        ),
        ToolSpec(
            name="normalize_drug_related",
            description="Alias: fetch RxNorm related concepts by ID list.",
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 75},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["ids"],
            },
            handler=normalize_drug_related,
            source="rxnorm",
        ),
        ToolSpec(
            name="normalize_compound",
            description="Alias: resolve compound concept via PubChem.",
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
            description="Alias: fetch PubChem compound details by CID/InChIKey IDs.",
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "id_type": {"type": "string", "enum": ["auto", "cid", "inchikey"], "default": "auto"},
                },
                "required": ["ids"],
            },
            handler=normalize_compound_fetch,
            source="pubchem",
        ),
        ToolSpec(
            name="normalize_ontology",
            description="Alias: search OLS ontology concepts.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "ontologies": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["query"],
            },
            handler=normalize_ontology,
            source="ols",
        ),
        ToolSpec(
            name="normalize_ontology_fetch",
            description="Alias: fetch OLS terms by OBO IDs or IRIs.",
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
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
            description="Alias: merge normalization candidates into canonical concept.",
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
            description="Alias: build source-specific search terms from normalized concept.",
            input_schema={
                "type": "object",
                "properties": {
                    "concept": {"type": "object"},
                    "label": {"type": "string"},
                    "synonyms": {"type": "array", "items": {"type": "string"}},
                    "max_synonyms": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "related_terms": {"type": "array", "items": {"type": "string"}},
                    "disambiguators": {"type": "array", "items": {"type": "string"}},
                    "excluded_terms": {"type": "array", "items": {"type": "string"}},
                    "expanded_terms": {"type": "object"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                },
                "required": ["concept"],
            },
            handler=retrieval_build_query_terms,
            source="internal",
        ),
    ]

    return tools
