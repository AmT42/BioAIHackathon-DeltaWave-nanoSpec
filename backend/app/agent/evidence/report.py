from __future__ import annotations

from typing import Any

from app.agent.evidence.models import ClaimContext, EvidenceGrade, EvidenceLedger


def render_report_json(
    *,
    intervention: dict[str, Any],
    ledger: EvidenceLedger,
    grade: EvidenceGrade,
    gap_map: dict[str, Any],
    claim_context: ClaimContext | None = None,
) -> dict[str, Any]:
    counts_by_level = dict(ledger.counts_by_level)
    evidence_pyramid = {
        "level_1": counts_by_level.get("1", 0),
        "level_2": counts_by_level.get("2", 0),
        "level_3": counts_by_level.get("3", 0),
        "level_4": counts_by_level.get("4", 0),
        "level_5": counts_by_level.get("5", 0),
        "level_6": counts_by_level.get("6", 0),
    }

    top_records: list[dict[str, Any]] = []
    for record in ledger.records[:20]:
        top_records.append(
            {
                "study_key": record.study_key,
                "source": record.source,
                "title": record.title,
                "year": record.year,
                "evidence_level": record.evidence_level,
                "study_type": record.study_type,
                "population_class": record.population_class,
                "endpoint_class": record.endpoint_class,
                "effect_direction": record.effect_direction,
                "quality_flags": list(record.quality_flags),
                "directness_flags": list(record.directness_flags),
                "ids": dict(record.ids),
                "citations": list(record.citations),
            }
        )

    return {
        "intervention": intervention,
        "claim_context": claim_context.to_dict() if claim_context else None,
        "evidence_summary": {
            "score": grade.score,
            "label": grade.label,
            "confidence": grade.confidence,
            "notes": list(grade.notes),
        },
        "evidence_pyramid": evidence_pyramid,
        "counts_by_source": dict(ledger.counts_by_source),
        "counts_by_endpoint": dict(ledger.counts_by_endpoint),
        "scoring_trace": grade.trace.to_dict(),
        "coverage_gaps": list(ledger.coverage_gaps),
        "gap_map": gap_map,
        "records": top_records,
        "optional_source_status": list(ledger.optional_source_status),
    }


def render_report_markdown(report_json: dict[str, Any]) -> str:
    intervention = report_json.get("intervention") or {}
    summary = report_json.get("evidence_summary") or {}
    pyramid = report_json.get("evidence_pyramid") or {}
    gap_map = report_json.get("gap_map") or {}

    lines = [
        f"# Evidence Report: {intervention.get('label') or 'Intervention'}",
        "",
        f"- Type: {intervention.get('type') or 'unknown'}",
        f"- Pivot: {intervention.get('pivot') or {}}",
        f"- Confidence score: {summary.get('score')} ({summary.get('label')}, {summary.get('confidence')})",
        "",
        "## Evidence Pyramid",
        f"- Level 1 (systematic/meta): {pyramid.get('level_1', 0)}",
        f"- Level 2 (RCT): {pyramid.get('level_2', 0)}",
        f"- Level 3 (observational): {pyramid.get('level_3', 0)}",
        f"- Level 4 (animal): {pyramid.get('level_4', 0)}",
        f"- Level 5 (in vitro): {pyramid.get('level_5', 0)}",
        f"- Level 6 (in silico): {pyramid.get('level_6', 0)}",
        "",
        "## Main Gaps",
    ]

    for item in (gap_map.get("missing_levels") or [])[:6]:
        lines.append(f"- {item}")
    for item in (gap_map.get("missing_endpoints") or [])[:6]:
        lines.append(f"- {item}")

    next_steps = gap_map.get("next_best_studies") or []
    if next_steps:
        lines.extend(["", "## What Would Raise Confidence"]) 
        for item in next_steps[:6]:
            lines.append(f"- {item}")

    notes = summary.get("notes") or []
    if notes:
        lines.extend(["", "## Notes"])
        for note in notes[:6]:
            lines.append(f"- {note}")

    return "\n".join(lines).strip() + "\n"
