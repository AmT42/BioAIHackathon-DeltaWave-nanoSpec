from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any

from app.agent.evidence.models import ClaimContext


_HARD_ENDPOINT_MARKERS = [
    "mortality",
    "all-cause mortality",
    "death",
    "hospitalization",
    "adverse event",
    "frailty",
    "functional",
    "function",
    "mobility",
    "disability",
    "infection",
    "falls",
    "grip strength",
    "vo2",
]

_INTERMEDIATE_MARKERS = [
    "blood pressure",
    "hba1c",
    "insulin",
    "insulin sensitivity",
    "lipid",
    "cholesterol",
    "crp",
    "inflammation",
    "glucose",
]

_SURROGATE_MARKERS = [
    "biomarker",
    "epigenetic clock",
    "methylation age",
    "clock",
    "transcriptomic",
    "proteomic",
    "metabolomic",
    "sasp",
    "nad+",
    "nad",
]

_MECHANISTIC_MARKERS = [
    "mtor",
    "ampk",
    "igf",
    "autophagy",
    "senescence",
    "epigenetic",
    "mitochond",
    "proteostasis",
    "telomere",
    "genomic instability",
    "in silico",
]

_IN_SILICO_MARKERS = ["in silico", "computational", "network pharmacology", "simulation", "docking", "modeling"]
_IN_VITRO_MARKERS = ["in vitro", "cell line", "cellular", "organoid"]

_HALLMARK_MARKERS: dict[str, list[str]] = {
    "genomic_instability": ["genomic instability", "dna damage", "dna repair"],
    "telomere_attrition": ["telomere"],
    "epigenetic_alterations": ["epigenetic", "methylation"],
    "proteostasis": ["proteostasis", "autophagy", "protein quality"],
    "nutrient_sensing": ["mtor", "ampk", "igf", "insulin"],
    "mitochondrial_dysfunction": ["mitochond"],
    "cellular_senescence": ["senescence", "sasp"],
    "stem_cell_exhaustion": ["stem cell"],
    "intercellular_communication": ["inflammation", "immune", "microbiome"],
}


def _normalize_text(parts: list[str]) -> str:
    return " ".join(str(p or "") for p in parts).strip().lower()


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def extract_hallmark_tags(text: str) -> list[str]:
    tags: list[str] = []
    for hallmark, markers in _HALLMARK_MARKERS.items():
        if _has_any(text, markers):
            tags.append(hallmark)
    return tags


def classify_endpoint_class(text: str) -> str:
    if _has_any(text, _HARD_ENDPOINT_MARKERS):
        return "clinical_hard"
    if _has_any(text, _INTERMEDIATE_MARKERS):
        return "clinical_intermediate"
    if _has_any(text, _SURROGATE_MARKERS):
        return "surrogate_biomarker"
    return "mechanistic_only"


def _study_type_from_pub_types(pub_types: list[str], text: str) -> str:
    pts = [str(item or "").lower() for item in pub_types]
    if any("meta-analysis" in pt or "systematic review" in pt for pt in pts):
        return "meta_analysis"
    if any("randomized controlled trial" in pt for pt in pts):
        return "rct"
    if any("clinical trial" in pt for pt in pts):
        return "clinical_trial"
    if any("cohort" in text for _ in [0]):
        return "observational"
    if _has_any(text, _IN_VITRO_MARKERS):
        return "in_vitro"
    if _has_any(text, _IN_SILICO_MARKERS):
        return "in_silico"
    return "observational"


def classify_pubmed_record(record: dict[str, Any], claim_context: ClaimContext | None = None) -> dict[str, Any]:
    title = str(record.get("title") or "")
    abstract = str(record.get("abstract") or "")
    text = _normalize_text([title, abstract])
    mesh_terms = [str(item or "") for item in (record.get("mesh_terms") or [])]
    mesh_text = _normalize_text(mesh_terms)

    pub_types = [str(item or "") for item in (record.get("pub_types") or record.get("publication_types") or [])]
    study_type = _study_type_from_pub_types(pub_types, text)

    humans = "human" in mesh_text or "humans" in mesh_text
    animals = "animal" in mesh_text or "animals" in mesh_text
    in_vitro = _has_any(text + " " + mesh_text, _IN_VITRO_MARKERS)
    in_silico = _has_any(text + " " + mesh_text, _IN_SILICO_MARKERS)

    if study_type == "meta_analysis":
        evidence_level = 1
    elif study_type in {"rct", "clinical_trial"} and humans:
        evidence_level = 2
    elif humans:
        evidence_level = 3
    elif animals:
        evidence_level = 4
    elif in_vitro:
        evidence_level = 5
    elif in_silico:
        evidence_level = 6
    else:
        evidence_level = 3 if study_type == "observational" else None

    if humans:
        population_class = "human"
    elif animals:
        population_class = "animal"
    elif in_vitro:
        population_class = "cell"
    elif in_silico:
        population_class = "computational"
    else:
        population_class = "unknown"

    endpoint_class = classify_endpoint_class(text)

    quality_flags: list[str] = []
    if evidence_level in {2, 3} and not abstract:
        quality_flags.append("limited_metadata")
    if evidence_level == 3:
        quality_flags.append("observational_risk_confounding")
    if evidence_level in {4, 5}:
        quality_flags.append("preclinical_translation_risk")

    directness_flags = infer_directness_flags(
        claim_context=claim_context,
        population_class=population_class,
        endpoint_class=endpoint_class,
        text=text,
    )

    effect_direction = infer_effect_direction(text)

    study_key = f"pmid:{record.get('pmid')}" if record.get("pmid") else f"doi:{record.get('doi')}"

    return {
        "study_key": study_key,
        "source": "pubmed",
        "ids": {
            "pmid": str(record.get("pmid") or ""),
            "doi": str(record.get("doi") or ""),
        },
        "title": title or None,
        "year": _extract_year(record.get("pubdate") or record.get("year")),
        "evidence_level": evidence_level,
        "study_type": study_type,
        "population_class": population_class,
        "endpoint_class": endpoint_class,
        "quality_flags": quality_flags,
        "directness_flags": directness_flags,
        "effect_direction": effect_direction,
        "citations": [{"pmid": record.get("pmid"), "doi": record.get("doi"), "title": title or None}],
        "metadata": {
            "pub_types": pub_types,
            "mesh_terms": mesh_terms,
            "hallmark_tags": extract_hallmark_tags(text),
        },
    }


def classify_trial_record(record: dict[str, Any], claim_context: ClaimContext | None = None) -> dict[str, Any]:
    nct = str(record.get("nct_id") or record.get("nctId") or "").strip().upper()
    study_type_raw = str(record.get("study_type") or "")
    outcomes = record.get("primary_outcomes") or []
    outcome_text = " ".join(str((item or {}).get("measure") or item or "") for item in outcomes)
    title = str(record.get("brief_title") or record.get("official_title") or "")
    text = _normalize_text([title, outcome_text])

    interventional = "interventional" in study_type_raw.lower()
    status = str(record.get("overall_status") or "")

    if interventional:
        evidence_level = 2
        typed_study = "registry_interventional"
    else:
        evidence_level = 3
        typed_study = "registry_observational"

    endpoint_class = classify_endpoint_class(text)
    quality_flags: list[str] = []

    enrollment_raw = record.get("enrollment")
    enrollment = None
    try:
        enrollment = int(enrollment_raw) if enrollment_raw is not None else None
    except Exception:
        enrollment = None

    if enrollment is None or enrollment < 50:
        quality_flags.append("small_n_or_unknown")
    if status and status.upper() != "COMPLETED":
        quality_flags.append("not_completed")
    if not bool(record.get("has_results")):
        quality_flags.append("no_registry_results")

    directness_flags = infer_directness_flags(
        claim_context=claim_context,
        population_class="human_registry",
        endpoint_class=endpoint_class,
        text=text,
    )

    return {
        "study_key": f"nct:{nct}" if nct else "nct:unknown",
        "source": "clinicaltrials",
        "ids": {"nct": nct},
        "title": title or None,
        "year": _extract_year(record.get("completion_date") or record.get("primary_completion_date")),
        "evidence_level": evidence_level,
        "study_type": typed_study,
        "population_class": "human_registry",
        "endpoint_class": endpoint_class,
        "quality_flags": quality_flags,
        "directness_flags": directness_flags,
        "effect_direction": "unknown",
        "citations": [{"nct": nct, "title": title or None}],
        "metadata": {
            "overall_status": status,
            "has_results": bool(record.get("has_results")),
            "primary_completion_date": record.get("primary_completion_date"),
            "completion_date": record.get("completion_date"),
            "results_first_posted_date": record.get("results_first_posted_date"),
            "primary_outcomes": outcomes,
            "hallmark_tags": extract_hallmark_tags(text),
        },
    }


def infer_effect_direction(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in ["no significant", "not significant", "null effect"]):
        return "null"
    if any(marker in lowered for marker in ["improved", "increase", "extended", "reduced", "benefit"]):
        return "benefit"
    if any(marker in lowered for marker in ["worse", "harm", "adverse", "increased risk"]):
        return "harm"
    return "unknown"


def infer_directness_flags(
    *,
    claim_context: ClaimContext | None,
    population_class: str,
    endpoint_class: str,
    text: str,
) -> list[str]:
    flags: list[str] = []
    if claim_context is None:
        return flags

    population = claim_context.population.lower()
    outcome = claim_context.outcome.lower()

    if "healthy" in population and population_class not in {"human", "human_registry"}:
        flags.append("indirect_population")

    if population_class in {"human", "human_registry"} and "healthy" in population:
        if "healthy" not in text and "older" not in text and "aged" not in text:
            flags.append("indirect_population")

    if "healthspan" in outcome or "aging" in outcome or "longevity" in outcome:
        if endpoint_class in {"surrogate_biomarker", "mechanistic_only"}:
            flags.append("indirect_endpoint")

    return flags


def _extract_year(raw: Any) -> int | None:
    text = str(raw or "").strip()
    match = re.search(r"(19|20)\d{2}", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


def parse_possible_date(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%Y":
                return date(parsed.year, 1, 1)
            if fmt in {"%Y-%m", "%B %Y", "%b %Y"}:
                return date(parsed.year, parsed.month, 1)
            return parsed.date()
        except Exception:
            continue
    return None


def months_since(raw_date: Any, *, now: date | None = None) -> int | None:
    current = now or datetime.now(timezone.utc).date()
    parsed = parse_possible_date(raw_date)
    if parsed is None:
        return None
    months = (current.year - parsed.year) * 12 + (current.month - parsed.month)
    if current.day < parsed.day:
        months -= 1
    return max(months, 0)
