from __future__ import annotations

from typing import Any
from urllib import parse

from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


def _request_id(headers: dict[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("x-amzn-requestid") or headers.get("x-openai-request-id")


def _require_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ToolExecutionError(code="VALIDATION_ERROR", message=f"'{key}' is required")
    return value


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


def build_normalization_tools(http: SimpleHttpClient) -> list[ToolSpec]:
    def rxnorm_resolve(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        term = _require_text(payload, "term")
        max_candidates = int(payload.get("max_candidates", 10))

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
        synonym_entries = [{"text": text, "source": "pubchem", "weight": 0.7} for text in synonyms[:30]]

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
            ids=[compound["cid"], compound.get("inchikey")],
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
                "synonyms": [{"text": text, "source": "pubchem", "weight": 0.7} for text in synonyms[:50]],
            },
            ids=[compound["cid"], compound.get("inchikey")],
            ctx=ctx,
        )

    def ols_search_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        q = _require_text(payload, "q")
        rows = int(payload.get("rows", 10))
        ontologies = payload.get("ontologies") or []
        ontology_param = ",".join(str(item).strip().lower() for item in ontologies if str(item).strip()) if ontologies else None

        data, headers = http.get_json(
            url="https://www.ebi.ac.uk/ols4/api/search",
            params={
                "q": q,
                "rows": rows,
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
                    "synonyms": list(doc.get("synonym") or [])[:15],
                    "xrefs": list(doc.get("database_cross_reference") or [])[:10],
                }
            )

        return make_tool_output(
            source="ols",
            summary=f"Found {len(hits)} ontology term candidate(s) for '{q}'.",
            data={"query": q, "hits": hits, "best": hits[0] if hits else None},
            ids=[hit.get("obo_id") for hit in hits if hit.get("obo_id")],
            request_id=_request_id(headers),
            ctx=ctx,
        )

    def ols_get_term(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        iri = str(payload.get("iri", "")).strip()
        obo_id = str(payload.get("obo_id", "")).strip()
        ontology = str(payload.get("ontology", "")).strip().lower()

        if iri and ontology:
            encoded = parse.quote(iri, safe="")
            url = f"https://www.ebi.ac.uk/ols4/api/ontologies/{parse.quote(ontology)}/terms/{encoded}"
            data, headers = http.get_json(url=url)
            term = {
                "obo_id": data.get("obo_id"),
                "label": data.get("label"),
                "iri": data.get("iri"),
                "ontology": ontology,
                "synonyms": list(data.get("synonyms") or []),
                "xrefs": list(data.get("annotation", {}).get("database_cross_reference", []) or []),
            }
            return make_tool_output(
                source="ols",
                summary="Fetched ontology term.",
                data={"term": term},
                ids=[term.get("obo_id")],
                request_id=_request_id(headers),
                ctx=ctx,
            )

        if not obo_id:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Provide either ('iri' and 'ontology') or 'obo_id'")

        search_payload = ols_search_terms({"q": obo_id, "rows": 5, "ontologies": [ontology] if ontology else []}, ctx)
        hits = search_payload.get("data", {}).get("hits") or []
        exact = next((item for item in hits if (item.get("obo_id") or "").lower() == obo_id.lower()), hits[0] if hits else None)
        return make_tool_output(
            source="ols",
            summary="Fetched ontology term by OBO ID." if exact else "No matching ontology term found.",
            data={"term": exact},
            ids=[exact.get("obo_id")] if isinstance(exact, dict) and exact.get("obo_id") else [],
            warnings=[f"No exact match for '{obo_id}'"] if not exact else [],
            ctx=ctx,
        )

    def concept_merge_candidates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        user_text = str(payload.get("user_text", "")).strip()
        rx_data = _unwrap_tool_data(payload.get("rxnorm") or {})
        pub_data = _unwrap_tool_data(payload.get("pubchem") or {})
        ols_data = _unwrap_tool_data(payload.get("ols") or {})

        warnings: list[str] = []
        concept_type = "free_text"
        pivot: dict[str, Any] = {"source": "free_text", "id": user_text or None}
        preferred_label = user_text
        synonyms: list[dict[str, Any]] = []
        xrefs: list[dict[str, Any]] = []

        ingredient_rxcui = rx_data.get("ingredient_rxcui")
        rx_candidates = rx_data.get("candidates") or []
        if ingredient_rxcui:
            concept_type = "drug"
            pivot = {"source": "rxnorm", "id": str(ingredient_rxcui)}
            if rx_candidates:
                preferred_label = str(rx_candidates[0].get("name") or preferred_label or user_text)
            xrefs.append({"source": "rxnorm", "id": f"RxCUI:{ingredient_rxcui}"})

        if concept_type == "free_text" and pub_data.get("inchikey"):
            concept_type = "chemical"
            pivot = {"source": "pubchem", "id": str(pub_data.get("inchikey"))}
            preferred_label = str(pub_data.get("preferred_name") or preferred_label or user_text)
            if pub_data.get("cid"):
                xrefs.append({"source": "pubchem", "id": f"CID:{pub_data.get('cid')}"})

        if concept_type == "free_text":
            hits = ols_data.get("hits") or []
            best = ols_data.get("best") or (hits[0] if hits else None)
            if isinstance(best, dict):
                obo_id = best.get("obo_id")
                onto = str(best.get("ontology") or "").lower()
                preferred_label = str(best.get("label") or preferred_label or user_text)
                if obo_id and onto == "efo":
                    concept_type = "procedure"
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

        rx_related = rx_data.get("related") or []
        for rel in rx_related[:20]:
            if rel.get("name"):
                synonyms.append({"text": rel["name"], "source": "rxnorm", "weight": 0.8})

        pub_synonyms = pub_data.get("synonyms") or []
        for syn in pub_synonyms[:30]:
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

    def build_search_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        max_synonyms = int(payload.get("max_synonyms", 10))
        concept = _unwrap_tool_data(payload.get("concept") or {})
        if "concept" in concept and isinstance(concept["concept"], dict):
            concept = concept["concept"]

        label = str(concept.get("label") or payload.get("label") or "").strip()
        syn_items = concept.get("synonyms") or payload.get("synonyms") or []
        related_terms = payload.get("related_terms") or []

        candidates: list[str] = []
        if label:
            candidates.append(label)
        for item in syn_items:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
            else:
                text = str(item).strip()
            if not text:
                continue
            if len(text) <= 2:
                continue
            candidates.append(text)

        deduped: list[str] = []
        seen = set()
        for term in candidates:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
            if len(deduped) >= max_synonyms:
                break

        openalex_terms = deduped[:]
        ctgov_terms = deduped[: min(5, len(deduped))]
        safety_terms = deduped[:1]
        exclude_terms = [str(t).strip() for t in related_terms if str(t).strip() and len(str(t).strip()) <= 2]

        return make_tool_output(
            source="internal",
            summary=f"Built controlled search term set with {len(deduped)} term(s).",
            data={
                "terms": {
                    "openalex": openalex_terms,
                    "clinicaltrials": ctgov_terms,
                    "safety": safety_terms,
                    "exclude": exclude_terms,
                },
                "label": label,
            },
            ids=deduped,
            ctx=ctx,
        )

    return [
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
                "properties": {"rxcui": {"type": "string"}},
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
            },
            handler=concept_merge_candidates,
            source="internal",
        ),
        ToolSpec(
            name="build_search_terms",
            description="Build controlled source-specific search terms from a normalized concept.",
            input_schema={
                "type": "object",
                "properties": {
                    "concept": {"type": "object"},
                    "max_synonyms": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "related_terms": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["concept"],
            },
            handler=build_search_terms,
            source="internal",
        ),
    ]
