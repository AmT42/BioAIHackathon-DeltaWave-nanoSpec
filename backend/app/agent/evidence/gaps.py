from __future__ import annotations

from collections import Counter
from typing import Any

from app.agent.evidence.models import EvidenceGrade, EvidenceLedger


def build_gap_map(ledger: EvidenceLedger, grade: EvidenceGrade | None = None) -> dict[str, Any]:
    level_counts: Counter[int] = Counter()
    endpoint_counts: Counter[str] = Counter()

    for record in ledger.records:
        if record.evidence_level is not None:
            level_counts[int(record.evidence_level)] += 1
        endpoint_counts[str(record.endpoint_class or "unknown")] += 1

    missing_levels: list[str] = []
    if level_counts.get(1, 0) == 0:
        missing_levels.append("No systematic review or meta-analysis evidence (Level 1).")
    if level_counts.get(2, 0) == 0:
        missing_levels.append("No randomized trial evidence (Level 2).")
    if (level_counts.get(1, 0) + level_counts.get(2, 0) + level_counts.get(3, 0)) == 0:
        missing_levels.append("No human evidence (Levels 1-3).")

    missing_endpoints: list[str] = []
    if endpoint_counts.get("clinical_hard", 0) == 0:
        missing_endpoints.append("No hard clinical endpoints identified.")
    if endpoint_counts.get("surrogate_biomarker", 0) > 0 and endpoint_counts.get("clinical_hard", 0) == 0:
        missing_endpoints.append("Evidence is surrogate-heavy; direct healthspan outcomes are missing.")

    next_best_studies: list[str] = []
    if level_counts.get(2, 0) == 0:
        next_best_studies.append(
            "Run a preregistered randomized trial in older adults with functional outcomes (frailty, hospitalization, mobility)."
        )
    if endpoint_counts.get("clinical_hard", 0) == 0:
        next_best_studies.append(
            "Add hard endpoint follow-up (e.g., morbidity, hospitalization, or validated functional decline endpoints)."
        )

    mismatch_flags: Counter[str] = Counter()
    for row in ledger.optional_source_status:
        flag = str((row or {}).get("flag") or "")
        if flag:
            mismatch_flags[flag] += 1

    cautions: list[str] = []
    if mismatch_flags.get("completed_but_unpublished_possible", 0) > 0 or mismatch_flags.get("possible_unpublished_completed_trial", 0) > 0:
        cautions.append("Completed trials without linked publications detected.")
    if mismatch_flags.get("registry_results_without_publication", 0) > 0:
        cautions.append("Registry-posted results without peer-reviewed publication detected.")

    if grade and grade.label in {"D", "E"} and not next_best_studies:
        next_best_studies.append("Increase human evidence depth before making efficacy claims.")

    return {
        "missing_levels": missing_levels,
        "missing_endpoints": missing_endpoints,
        "next_best_studies": next_best_studies,
        "mismatch_cautions": cautions,
        "level_counts": {str(k): int(v) for k, v in sorted(level_counts.items())},
        "endpoint_counts": dict(endpoint_counts),
    }
