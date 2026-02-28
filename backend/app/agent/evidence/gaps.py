from __future__ import annotations

from typing import Any


def build_gap_map(*, ledger: dict[str, Any], claim_context: dict[str, Any] | None = None, score: dict[str, Any] | None = None) -> dict[str, Any]:
    records = [item for item in (ledger.get("records") or []) if isinstance(item, dict)]

    levels_present = {int(item.get("evidence_level")) for item in records if item.get("evidence_level") is not None}
    missing_levels = [level for level in [1, 2, 3, 4, 5, 6] if level not in levels_present]

    human_records = [item for item in records if int(item.get("evidence_level") or 0) in {1, 2, 3}]
    hard_endpoint_human = [
        item
        for item in human_records
        if str(item.get("endpoint_class") or "") in {"clinical_hard", "clinical_intermediate"}
    ]

    mismatch_flags: list[str] = []
    for record in records:
        metadata = record.get("metadata") or {}
        severity = str(metadata.get("mismatch_severity") or "").strip().lower()
        if severity:
            mismatch_flags.append(severity)

    missing: list[str] = []
    if 1 in missing_levels:
        missing.append("No systematic review/meta-analysis evidence in scope.")
    if 2 in missing_levels:
        missing.append("No randomized/interventional human trial evidence in scope.")
    if not hard_endpoint_human:
        missing.append("Human evidence lacks hard/intermediate clinical endpoints.")
    if mismatch_flags:
        missing.append("Registry-publication mismatch signals detected.")

    what_changes: list[str] = []
    if 2 in missing_levels:
        what_changes.append(
            "A preregistered interventional trial in the target population with >=12 months follow-up and functional endpoints."
        )
    if not hard_endpoint_human:
        what_changes.append(
            "At least one replicated human study with clinical outcomes (frailty/function/morbidity), not biomarker-only endpoints."
        )
    if 1 in missing_levels:
        what_changes.append(
            "A high-quality systematic review/meta-analysis synthesizing the intervention evidence in comparable populations."
        )

    claim = claim_context or {}
    outcome = str(claim.get("outcome") or "").strip()
    population = str(claim.get("population") or "").strip()

    next_best_studies = [
        {
            "name": "Definitive human RCT",
            "design": "Interventional randomized controlled trial",
            "population": population or "target older adult population",
            "outcome": outcome or "healthspan-oriented clinical endpoints",
            "minimum_specs": ["n>=200", "follow-up>=12 months", "pre-registered outcomes", "adverse events reporting"],
        }
    ]

    if score and float(score.get("final_confidence") or 0.0) < 50.0:
        next_best_studies.append(
            {
                "name": "Independent replication cohort",
                "design": "Prospective independent cohort/RCT replication",
                "population": population or "similar target population",
                "outcome": outcome or "same clinical endpoint set",
                "minimum_specs": ["independent site", "comparable intervention protocol", "transparent data release"],
            }
        )

    return {
        "missing_evidence": missing,
        "missing_levels": missing_levels,
        "mismatch_signals": mismatch_flags,
        "what_would_change_score": what_changes,
        "next_best_studies": next_best_studies,
    }
