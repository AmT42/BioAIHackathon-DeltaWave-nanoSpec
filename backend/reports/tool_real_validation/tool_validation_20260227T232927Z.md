# Real Tool Validation Report

- Generated: 2026-02-27T23:29:47.268421Z
- JSON: `/Users/amt42/projects/hackathon-agent-core/backend/reports/tool_real_validation/tool_validation_20260227T232927Z.json`

## Status Summary

- `error`: 1
- `success`: 23
- `strict_mode`: True
- `strict_science_tools_checked`: 24
- `strict_science_tools_failed`: 1

## Tool Results

| Tool | Status | Latency (ms) | Endpoint | Notes |
|---|---|---:|---|---|
| `normalize_drug` | `success` | `1790.49` | `https://rxnav.nlm.nih.gov/REST/rxcui.json` |  |
| `normalize_drug_related` | `success` | `779.24` | `https://rxnav.nlm.nih.gov/REST/rxcui/{id}/allrelated.json` |  |
| `normalize_compound` | `success` | `1789.27` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/cids/JSON` |  |
| `normalize_compound_fetch` | `success` | `1299.38` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/JSON` |  |
| `normalize_ontology` | `success` | `368.68` | `https://www.ebi.ac.uk/ols4/api/search` |  |
| `normalize_ontology_fetch` | `success` | `373.36` | `https://www.ebi.ac.uk/ols4/api/terms` |  |
| `normalize_merge_candidates` | `success` | `3.2` | `internal` |  |
| `retrieval_build_query_terms` | `success` | `2.96` | `internal` |  |
| `retrieval_build_pubmed_templates` | `success` | `1.62` | `internal` |  |
| `pubmed_search` | `success` | `696.46` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` |  |
| `pubmed_fetch` | `success` | `870.07` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi` |  |
| `clinicaltrials_search` | `success` | `331.29` | `https://clinicaltrials.gov/api/v2/studies` |  |
| `clinicaltrials_fetch` | `success` | `734.57` | `https://clinicaltrials.gov/api/v2/studies/{nct}` |  |
| `retrieval_should_run_trial_audit` | `success` | `4.4` | `internal` |  |
| `trial_publication_linker` | `success` | `1791.8` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` |  |
| `dailymed_search` | `success` | `891.32` | `https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json` |  |
| `dailymed_fetch_sections` | `success` | `987.85` | `https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml` |  |
| `openfda_faers_aggregate` | `success` | `1023.1` | `https://api.fda.gov/drug/event.json` |  |
| `longevity_drugage_refresh` | `success` | `682.23` | `https://genomics.senescence.info/drugs/dataset.zip` |  |
| `longevity_drugage_query` | `success` | `21.27` | `local_cache:hagr_drugage` |  |
| `longevity_itp_fetch_summary` | `success` | `1306.12` | `https://phenome.jax.org/itp/surv/MetRapa/C2011` |  |
| `chembl_search` | `success` | `332.11` | `https://www.ebi.ac.uk/chembl/api/data/molecule/search.json` |  |
| `chebi_search` | `success` | `352.15` | `https://www.ebi.ac.uk/chebi/backend/api/public/es_search/` |  |
| `semanticscholar_search` | `error` | `3089.25` | `https://api.semanticscholar.org/graph/v1/paper/search` | RATE_LIMIT: Rate limited by upstream source: https://api.semanticscholar.org/graph/v1/paper/search?query=rapamycin+aging+trial&limit=5&fields=title%2Cyear%2CpaperId%2CexternalIds%2CcitationCount |