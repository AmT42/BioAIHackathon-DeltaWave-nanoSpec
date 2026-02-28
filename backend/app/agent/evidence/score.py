from __future__ import annotations

from collections import Counter
from typing import Any


_LEVEL_WEIGHTS = {
    1: 100.0,
    2: 85.0,
    3: 65.0,
    4: 40.0,
    5: 20.0,
    6: 10.0,
}

_QUALITY_PENALTIES = {
    "small_n_or_unknown": 6.0,
    "observational_risk_confounding": 8.0,
    "preclinical_translation_risk": 5.0,
    "limited_metadata": 4.0,
    "not_completed": 4.0,
    "no_registry_results": 5.0,
    "high_risk_bias": 10.0,
    "severe_safety_signal": 18.0,
}

_DIRECTNESS_PENALTIES = {
    "indirect_population": 8.0,
    "indirect_endpoint": 10.0,
}


def _as_records(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    records = ledger.get("records") or []
    return [item for item in records if isinstance(item, dict)]


def _record_score(record: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    level = int(record.get("evidence_level") or 0)
    base = _LEVEL_WEIGHTS.get(level, 35.0)
    penalties: list[dict[str, Any]] = []

    for flag in record.get("quality_flags") or []:
        penalty = _QUALITY_PENALTIES.get(str(flag), 0.0)
        if penalty:
            penalties.append({"code": f"quality:{flag}", "value": penalty})
            base -= penalty

    for flag in record.get("directness_flags") or []:
        penalty = _DIRECTNESS_PENALTIES.get(str(flag), 0.0)
        if penalty:
            penalties.append({"code": f"directness:{flag}", "value": penalty})
            base -= penalty

    return max(base, 0.0), penalties


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def grade_hybrid(
    *,
    ledger: dict[str, Any],
    claim_context: dict[str, Any] | None = None,
    severe_safety_unresolved: bool = False,
) -> dict[str, Any]:
    records = _as_records(ledger)

    by_record_score: list[dict[str, Any]] = []
    all_scores: list[float] = []
    human_scores: list[float] = []
    preclinical_scores: list[float] = []
    all_penalties: list[dict[str, Any]] = []
    bonuses: list[dict[str, Any]] = []

    effect_counter: Counter[str] = Counter()
    hallmark_tags: set[str] = set()

    for record in records:
        score, penalties = _record_score(record)
        key = str(record.get("study_key") or "unknown")
        by_record_score.append({"study_key": key, "score": round(score, 3), "penalties": penalties})
        all_penalties.extend(penalties)
        all_scores.append(score)

        level = int(record.get("evidence_level") or 0)
        if level in {1, 2, 3}:
            human_scores.append(score)
        elif level in {4, 5, 6}:
            preclinical_scores.append(score)

        direction = str(record.get("effect_direction") or "unknown")
        effect_counter[direction] += 1

        metadata = record.get("metadata") or {}
        for tag in metadata.get("hallmark_tags") or []:
            hallmark_tags.add(str(tag))

    top_human = sorted(human_scores, reverse=True)[:3]
    top_all = sorted(all_scores, reverse=True)[:5]
    top_preclinical = sorted(preclinical_scores, reverse=True)[:4]

    if top_human:
        ces = 0.7 * _mean(top_human) + 0.3 * _mean(top_all)
    elif top_preclinical:
        ces = 0.8 * _mean(top_preclinical)
    else:
        ces = 0.0

    if effect_counter.get("benefit", 0) > 0 and effect_counter.get("harm", 0) == 0:
        ces += 2.0
        bonuses.append({"code": "consistency_positive_direction", "value": 2.0})
    if effect_counter.get("harm", 0) > 0 and effect_counter.get("benefit", 0) > 0:
        ces -= 5.0
        all_penalties.append({"code": "inconsistency_mixed_effects", "value": 5.0})

    if len(top_human) >= 3:
        ces += 3.0
        bonuses.append({"code": "human_replication_signal", "value": 3.0})

    ces = max(0.0, min(100.0, ces))

    # Mechanistic plausibility axis.
    mp = 30.0
    hallmark_bonus = min(30.0, len(hallmark_tags) * 5.0)
    mp += hallmark_bonus
    if hallmark_bonus:
        bonuses.append({"code": "hallmark_coverage", "value": hallmark_bonus})

    has_human = bool(top_human)
    has_preclinical = bool(top_preclinical)
    if has_human and has_preclinical:
        mp += 10.0
        bonuses.append({"code": "cross_species_support", "value": 10.0})

    human_endpoints = {
        str(record.get("endpoint_class") or "")
        for record in records
        if int(record.get("evidence_level") or 0) in {1, 2, 3}
    }

    if "surrogate_biomarker" in human_endpoints and "clinical_hard" not in human_endpoints:
        mp -= 5.0
        all_penalties.append({"code": "surrogate_heavy_human_evidence", "value": 5.0})

    if severe_safety_unresolved:
        mp -= 15.0
        all_penalties.append({"code": "severe_safety_signal", "value": 15.0})

    mp = max(0.0, min(100.0, mp))

    raw_final = 0.7 * ces + 0.3 * mp
    final_confidence = raw_final
    caps_applied: list[dict[str, Any]] = []

    if not has_human:
        if final_confidence > 40.0:
            caps_applied.append({"cap": "no_human_evidence", "max": 40.0, "before": round(final_confidence, 3)})
        final_confidence = min(final_confidence, 40.0)

    surrogate_only_human = bool(human_endpoints) and human_endpoints.issubset({"surrogate_biomarker", "mechanistic_only"})
    if surrogate_only_human:
        if final_confidence > 55.0:
            caps_applied.append({"cap": "human_surrogate_only", "max": 55.0, "before": round(final_confidence, 3)})
        final_confidence = min(final_confidence, 55.0)

    if severe_safety_unresolved:
        if final_confidence > 50.0:
            caps_applied.append({"cap": "severe_safety_unresolved", "max": 50.0, "before": round(final_confidence, 3)})
        final_confidence = min(final_confidence, 50.0)

    return {
        "ces": round(ces, 3),
        "mp": round(mp, 3),
        "final_confidence": round(max(0.0, min(100.0, final_confidence)), 3),
        "penalties": all_penalties,
        "bonuses": bonuses,
        "caps_applied": caps_applied,
        "score_trace": {
            "claim_context": claim_context or {},
            "record_scores": by_record_score,
            "human_record_count": len(top_human),
            "preclinical_record_count": len(top_preclinical),
            "effect_direction_counts": dict(effect_counter),
            "hallmark_tags": sorted(hallmark_tags),
            "raw_final": round(raw_final, 3),
        },
    }
