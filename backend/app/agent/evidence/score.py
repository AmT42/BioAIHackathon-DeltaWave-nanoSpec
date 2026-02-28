from __future__ import annotations

from collections import Counter
from typing import Any

from app.agent.evidence.models import EvidenceGrade, EvidenceLedger, ScoreTrace


_LEVEL_POINTS = {
    1: 40.0,
    2: 28.0,
    3: 16.0,
    4: 8.0,
    5: 4.0,
    6: 2.0,
}

_QUALITY_PENALTIES = {
    "limited_metadata": 1.5,
    "population_unspecified": 1.5,
    "observational_risk_confounding": 1.5,
    "preclinical_translation_risk": 1.0,
    "small_n_or_unknown": 2.0,
    "not_completed": 2.0,
    "no_registry_results": 1.5,
}


def _label_for_score(score: float) -> tuple[str, str]:
    if score >= 85:
        return ("A", "high")
    if score >= 70:
        return ("B", "moderate_high")
    if score >= 55:
        return ("C", "moderate")
    if score >= 40:
        return ("D", "low")
    return ("E", "very_low")


def _record_hallmark_tags(record: Any) -> list[str]:
    metadata = record.metadata if hasattr(record, "metadata") else (record.get("metadata") if isinstance(record, dict) else {})
    tags = metadata.get("hallmark_tags") if isinstance(metadata, dict) else []
    out: list[str] = []
    for tag in tags or []:
        text = str(tag or "").strip()
        if text:
            out.append(text)
    return out


def grade_ledger(ledger: EvidenceLedger) -> EvidenceGrade:
    level_counts: Counter[int] = Counter()
    quality_flags: Counter[str] = Counter()
    endpoint_counts: Counter[str] = Counter()
    hallmark_tags: set[str] = set()

    human_count = 0
    for record in ledger.records:
        if record.evidence_level is not None:
            level_counts[int(record.evidence_level)] += 1
        endpoint_counts[str(record.endpoint_class or "unknown")] += 1
        for flag in record.quality_flags:
            quality_flags[str(flag)] += 1
        hallmark_tags.update(_record_hallmark_tags(record))
        if record.population_class in {"human", "human_registry"}:
            human_count += 1

    ces_components: dict[str, float] = {}
    ces = 0.0
    for level, base_points in _LEVEL_POINTS.items():
        count = level_counts.get(level, 0)
        if count <= 0:
            continue
        coverage_factor = min(1.0, 0.45 + 0.2 * float(min(count, 3)))
        contribution = round(base_points * coverage_factor, 3)
        ces += contribution
        ces_components[f"level_{level}"] = contribution
    ces = min(70.0, round(ces, 3))

    quality_penalty = 0.0
    penalties: list[dict[str, Any]] = []
    for flag, weight in _QUALITY_PENALTIES.items():
        count = int(quality_flags.get(flag, 0))
        if count <= 0:
            continue
        penalty = min(weight * count, weight * 4)
        quality_penalty += penalty
        penalties.append({"kind": "quality", "flag": flag, "count": count, "delta": -round(penalty, 3)})
    quality_penalty = round(quality_penalty, 3)

    consistency_bonus = 0.0
    bonuses: list[dict[str, Any]] = []
    if level_counts.get(1, 0) >= 1 and level_counts.get(2, 0) >= 1:
        consistency_bonus += 4.0
        bonuses.append({"kind": "consistency", "reason": "level1_plus_level2_present", "delta": 4.0})
    elif level_counts.get(2, 0) >= 2:
        consistency_bonus += 2.5
        bonuses.append({"kind": "consistency", "reason": "multiple_level2", "delta": 2.5})

    mp = 8.0 + min(18.0, float(len(hallmark_tags)) * 2.0)
    if endpoint_counts.get("clinical_hard", 0) > 0:
        mp += 3.0
    if endpoint_counts.get("surrogate_biomarker", 0) > endpoint_counts.get("clinical_hard", 0):
        mp -= 2.0
    mp = max(0.0, min(30.0, round(mp, 3)))

    raw = ces + mp + consistency_bonus - quality_penalty
    caps_applied: list[dict[str, Any]] = []

    has_level12 = level_counts.get(1, 0) > 0 or level_counts.get(2, 0) > 0
    if human_count == 0:
        raw = min(raw, 45.0)
        caps_applied.append({"cap": 45.0, "reason": "no_human_evidence"})
    elif not has_level12:
        raw = min(raw, 70.0)
        caps_applied.append({"cap": 70.0, "reason": "no_level1_level2"})

    if endpoint_counts.get("surrogate_biomarker", 0) > 0 and endpoint_counts.get("clinical_hard", 0) == 0:
        raw = min(raw, 60.0)
        caps_applied.append({"cap": 60.0, "reason": "surrogate_only_endpoints"})

    final = max(0.0, min(100.0, round(raw, 3)))
    label, confidence = _label_for_score(final)

    trace = ScoreTrace(
        ces=round(ces, 3),
        mp=round(mp, 3),
        final_confidence=final,
        penalties=penalties,
        bonuses=bonuses,
        caps_applied=caps_applied,
        components={
            "level_counts": {str(k): int(v) for k, v in sorted(level_counts.items())},
            "ces_components": ces_components,
            "endpoint_counts": dict(endpoint_counts),
            "quality_flags": dict(quality_flags),
            "hallmark_tag_count": len(hallmark_tags),
            "human_count": human_count,
            "quality_penalty": quality_penalty,
            "consistency_bonus": consistency_bonus,
        },
    )

    notes: list[str] = []
    if human_count == 0:
        notes.append("No human evidence detected; score is capped for translational uncertainty.")
    if endpoint_counts.get("clinical_hard", 0) == 0:
        notes.append("No hard clinical endpoints detected.")

    return EvidenceGrade(
        score=final,
        label=label,
        confidence=confidence,
        trace=trace,
        notes=notes,
    )
