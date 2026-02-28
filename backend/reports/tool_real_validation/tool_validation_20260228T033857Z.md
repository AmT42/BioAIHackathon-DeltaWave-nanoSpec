# Real Tool Validation Report

- Generated: 2026-02-28T03:39:20.146834Z
- JSON: `/Users/amt42/projects/hackathon-agent-core/backend/reports/tool_real_validation/tool_validation_20260228T033857Z.json`

## Status Summary

- `error`: 1
- `skipped`: 11
- `success`: 27
- `strict_mode`: True
- `strict_science_tools_checked`: 28
- `strict_science_tools_failed`: 1
- `core_checked`: 28
- `core_failed`: 1
- `core_passed`: False
- `optional_checked`: 0
- `optional_failed`: 0
- `strict_passed`: False

## Tool Results

| Tool | Status | Latency (ms) | Endpoint | Notes |
|---|---|---:|---|---|
| `rxnorm_resolve` | `success` | `1471.43` | `https://rxnav.nlm.nih.gov/REST/rxcui.json` |  |
| `rxnorm_get_related_terms` | `success` | `1737.4` | `https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/allrelated.json` |  |
| `pubchem_resolve` | `success` | `1772.85` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON` |  |
| `pubchem_get_compound` | `success` | `1023.08` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/JSON` |  |
| `ols_search_terms` | `success` | `390.74` | `https://www.ebi.ac.uk/ols4/api/search` |  |
| `ols_get_term` | `success` | `191.42` | `https://www.ebi.ac.uk/ols4/api/terms?iri={iri}` |  |
| `concept_merge_candidates` | `success` | `3.91` | `` |  |
| `build_search_terms` | `success` | `2.31` | `` |  |
| `normalize_mesh_expand` | `success` | `2426.4` | `https://id.nlm.nih.gov/mesh/lookup/descriptor` |  |
| `normalize_expand_terms_llm` | `success` | `2.9` | `internal` |  |
| `pubmed_search` | `success` | `1054.2` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` |  |
| `pubmed_fetch` | `success` | `1207.66` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi` |  |
| `pubmed_enrich_pmids` | `success` | `582.19` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi` | PMID enrichment only |
| `europmc_search` | `success` | `277.45` | `https://www.ebi.ac.uk/europepmc/webservices/rest/search` |  |
| `clinicaltrials_search_studies` | `success` | `331.57` | `https://clinicaltrials.gov/api/v2/studies` |  |
| `clinicaltrials_get_studies` | `success` | `762.03` | `https://clinicaltrials.gov/api/v2/studies/{nct_id}` |  |
| `clinicaltrials_fetch` | `success` | `765.85` | `https://clinicaltrials.gov/api/v2/studies/{nct_id}` |  |
| `trial_publication_linker` | `success` | `2795.38` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` |  |
| `pubmed_fetch` | `success` | `680.33` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi` | best-effort classification sample |
| `clinicaltrials_get_studies` | `success` | `419.23` | `https://clinicaltrials.gov/api/v2/studies/{nct_id}` | classification sample |
| `evidence_classify_pubmed_records` | `success` | `4.91` | `internal` |  |
| `evidence_classify_trial_records` | `success` | `0.93` | `internal` |  |
| `trial_publication_linker` | `success` | `4694.52` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` |  |
| `evidence_build_ledger` | `success` | `4.04` | `internal` |  |
| `evidence_grade` | `success` | `2.27` | `internal` |  |
| `evidence_gap_map` | `success` | `1.61` | `internal` |  |
| `evidence_render_report` | `success` | `2.15` | `internal` |  |
| `dailymed_search_labels` | `skipped` | `0` | `https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json` |  |
| `dailymed_get_label_sections` | `skipped` | `0` | `https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.json` | fallback setid |
| `openfda_faers_aggregate` | `error` | `0.59` | `https://api.fda.gov/drug/event.json` | VALIDATION_ERROR: 'query' is required |
| `hagr_drugage_refresh` | `skipped` | `0` | `https://genomics.senescence.info/drugs/dataset.zip` |  |
| `hagr_drugage_query` | `skipped` | `0` | `local_cache:hagr_drugage` |  |
| `itp_fetch_survival_summary` | `skipped` | `0` | `https://phenome.jax.org/itp/surv/MetRapa/C2011` |  |
| `chembl_search_molecules` | `skipped` | `0` | `https://www.ebi.ac.uk/chembl/api/data/molecule/search.json` |  |
| `chembl_get_molecule` | `skipped` | `0` | `https://www.ebi.ac.uk/chembl/api/data/molecule/{chembl_id}.json` | fallback ChEMBL |
| `chebi_search_entities` | `skipped` | `0` | `https://www.ebi.ac.uk/chebi/backend/api/public/es_search/` |  |
| `chebi_get_entity` | `skipped` | `0` | `https://www.ebi.ac.uk/chebi/backend/api/public/compound/{chebi_id}/` | fallback ChEBI |
| `openalex_search_works` | `skipped` | `0` | `https://api.openalex.org/works` | user requested skip |
| `openalex_get_works` | `skipped` | `0` | `https://api.openalex.org/works/{id}` | user requested skip |

## Strict Mode Failure

Strict validation failed for: `openfda_faers_aggregate`
