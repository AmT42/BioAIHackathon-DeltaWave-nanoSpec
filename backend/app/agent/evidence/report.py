from __future__ import annotations

import json
from typing import Any

from app.agent.evidence.models import ClaimContext, EvidenceGrade, EvidenceLedger


def _record_primary_id(record: dict[str, Any]) -> str:
    ids = record.get("ids")
    if not isinstance(ids, dict):
        return str(record.get("study_key") or "unknown")

    pmid = str(ids.get("pmid") or "").strip()
    if pmid:
        return f"PMID:{pmid}"

    nct = str(ids.get("nct") or "").strip()
    if nct:
        return nct.upper()

    doi = str(ids.get("doi") or "").strip()
    if doi:
        return f"DOI:{doi}"

    return str(record.get("study_key") or "unknown")


def _format_or_none(items: list[str], *, limit: int = 6) -> list[str]:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return ["None identified."]
    return cleaned[:limit]


def _format_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return "Unknown"


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
                "metadata": dict(record.metadata),
            }
        )

    top_human_studies = [
        row
        for row in top_records
        if str(row.get("population_class") or "").strip() in {"human", "human_registry"}
    ][:8]
    trial_registry_rows = [row for row in top_records if str(row.get("source") or "").strip() == "clinicaltrials"]
    preclinical_anchors = [
        row
        for row in top_records
        if isinstance(row.get("evidence_level"), int) and int(row.get("evidence_level")) in {4, 5, 6}
    ][:8]

    key_flags: list[str] = []
    for row in top_records:
        for field in ("quality_flags", "directness_flags"):
            values = row.get(field) if isinstance(row.get(field), list) else []
            for item in values:
                text = str(item).strip()
                if text and text not in key_flags:
                    key_flags.append(text)

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
        "top_human_studies": top_human_studies,
        "trial_registry_rows": trial_registry_rows,
        "preclinical_anchors": preclinical_anchors,
        "key_flags": key_flags,
        "optional_source_status": list(ledger.optional_source_status),
    }


def render_report_markdown(report_json: dict[str, Any]) -> str:
    intervention = report_json.get("intervention") or {}
    claim_context = report_json.get("claim_context") or {}
    summary = report_json.get("evidence_summary") or {}
    pyramid = report_json.get("evidence_pyramid") or {}
    trace = report_json.get("scoring_trace") or {}
    gap_map = report_json.get("gap_map") or {}
    records = report_json.get("records") if isinstance(report_json.get("records"), list) else []
    top_human = report_json.get("top_human_studies") if isinstance(report_json.get("top_human_studies"), list) else []
    trial_rows = report_json.get("trial_registry_rows") if isinstance(report_json.get("trial_registry_rows"), list) else []
    preclinical = report_json.get("preclinical_anchors") if isinstance(report_json.get("preclinical_anchors"), list) else []
    optional_source_status = (
        report_json.get("optional_source_status") if isinstance(report_json.get("optional_source_status"), list) else []
    )

    ambiguity_warnings = (
        claim_context.get("ambiguity_warnings")
        if isinstance(claim_context, dict) and isinstance(claim_context.get("ambiguity_warnings"), list)
        else []
    )
    directness_warnings = (
        claim_context.get("directness_warnings")
        if isinstance(claim_context, dict) and isinstance(claim_context.get("directness_warnings"), list)
        else []
    )

    lines = [
        f"# Evidence Report: {intervention.get('label') or 'Intervention'}",
        "",
        "## 1) Intervention Identity",
        f"- Type: {intervention.get('type') or 'unknown'}",
        f"- Pivot: {intervention.get('pivot') or 'None identified'}",
        f"- Query: {claim_context.get('query') or intervention.get('label') or 'unspecified'}",
        f"- Population: {claim_context.get('population') or 'unspecified'}",
        f"- Outcome: {claim_context.get('outcome') or 'unspecified'}",
        "- Ambiguity notes:",
    ]
    for item in _format_or_none([str(value) for value in ambiguity_warnings], limit=4):
        lines.append(f"  - {item}")
    lines.append("- Directness warnings:")
    for item in _format_or_none([str(value) for value in directness_warnings], limit=4):
        lines.append(f"  - {item}")

    lines.extend(
        [
            "",
            "## 2) Evidence Pyramid",
            f"- Level 1 (systematic/meta): {pyramid.get('level_1', 0)}",
            f"- Level 2 (human interventional): {pyramid.get('level_2', 0)}",
            f"- Level 3 (human observational): {pyramid.get('level_3', 0)}",
            f"- Level 4 (animal in vivo): {pyramid.get('level_4', 0)}",
            f"- Level 5 (in vitro): {pyramid.get('level_5', 0)}",
            f"- Level 6 (in silico): {pyramid.get('level_6', 0)}",
            "",
            "## 3) Key Human Evidence",
        ]
    )

    if top_human:
        for row in top_human[:8]:
            if not isinstance(row, dict):
                continue
            flags = []
            flags.extend([str(item) for item in (row.get("quality_flags") or []) if str(item).strip()])
            flags.extend([str(item) for item in (row.get("directness_flags") or []) if str(item).strip()])
            flag_text = ", ".join(flags) if flags else "none"
            lines.append(
                "- "
                + " | ".join(
                    [
                        _record_primary_id(row),
                        str(row.get("year") or "year-unknown"),
                        str(row.get("study_type") or "unknown"),
                        str(row.get("endpoint_class") or "unknown"),
                        str(row.get("effect_direction") or "unknown"),
                        f"flags={flag_text}",
                    ]
                )
            )
            if row.get("title"):
                lines.append(f"  title: {row.get('title')}")
    else:
        lines.append("- None identified.")

    lines.extend(["", "## 4) Trial Registry Audit"])
    if trial_rows:
        lines.extend(
            [
                "| NCT ID | Status | Results Posted | Linked PMIDs | Flags |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in trial_rows[:12]:
            if not isinstance(row, dict):
                continue
            ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            citations = row.get("citations") if isinstance(row.get("citations"), list) else []
            linked_pmids = sorted(
                {
                    str(item.get("pmid")).strip()
                    for item in citations
                    if isinstance(item, dict) and str(item.get("pmid") or "").strip()
                }
            )
            flags = []
            flags.extend([str(item) for item in (row.get("quality_flags") or []) if str(item).strip()])
            flags.extend([str(item) for item in (row.get("directness_flags") or []) if str(item).strip()])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(ids.get("nct") or "unknown").upper(),
                        str(metadata.get("overall_status") or "unknown"),
                        _format_bool(metadata.get("has_results")),
                        ", ".join(linked_pmids) if linked_pmids else "None",
                        ", ".join(flags) if flags else "none",
                    ]
                )
                + " |"
            )
    else:
        lines.append("- None identified.")

    lines.extend(["", "## 5) Preclinical Longevity Evidence"])
    if preclinical:
        for row in preclinical[:8]:
            if not isinstance(row, dict):
                continue
            lines.append(
                "- "
                + " | ".join(
                    [
                        _record_primary_id(row),
                        f"level={row.get('evidence_level')}",
                        str(row.get("study_type") or "unknown"),
                        str(row.get("endpoint_class") or "unknown"),
                    ]
                )
            )
            if row.get("title"):
                lines.append(f"  title: {row.get('title')}")
    else:
        lines.append("- None identified.")

    components = trace.get("components") if isinstance(trace.get("components"), dict) else {}
    hallmark_tag_count = components.get("hallmark_tag_count")
    lines.extend(
        [
            "",
            "## 6) Mechanistic Plausibility",
            f"- MP score: {trace.get('mp')} / 30",
            f"- Hallmark tag count (observed in classified records): {hallmark_tag_count if hallmark_tag_count is not None else 'unknown'}",
            "- Interpretation: plausibility can support prioritization but cannot override weak human evidence.",
        ]
    )

    lines.extend(["", "## 7) Safety Summary"])
    safety_rows: list[str] = []
    for item in optional_source_status:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("name") or "").strip().lower()
        if source in {"dailymed", "openfda", "faers"} or "safety" in source:
            flag = str(item.get("flag") or "status").strip() or "status"
            details = str(item.get("detail") or item.get("message") or "").strip()
            safety_rows.append(f"{source}: {flag}" + (f" ({details})" if details else ""))
    for row in _format_or_none(safety_rows, limit=8):
        lines.append(f"- {row}")

    lines.extend(
        [
            "",
            "## 8) Confidence Score + Trace",
            f"- Overall score: {summary.get('score')} ({summary.get('label')}, {summary.get('confidence')})",
            f"- CES: {trace.get('ces')}",
            f"- MP: {trace.get('mp')}",
            f"- Final confidence (trace): {trace.get('final_confidence')}",
            "- Penalties:",
        ]
    )
    penalties = trace.get("penalties") if isinstance(trace.get("penalties"), list) else []
    if penalties:
        for item in penalties[:8]:
            lines.append(f"  - {item}")
    else:
        lines.append("  - None identified.")

    lines.append("- Bonuses:")
    bonuses = trace.get("bonuses") if isinstance(trace.get("bonuses"), list) else []
    if bonuses:
        for item in bonuses[:8]:
            lines.append(f"  - {item}")
    else:
        lines.append("  - None identified.")

    lines.append("- Caps applied:")
    caps_applied = trace.get("caps_applied") if isinstance(trace.get("caps_applied"), list) else []
    if caps_applied:
        for item in caps_applied[:8]:
            lines.append(f"  - {item}")
    else:
        lines.append("  - None identified.")

    notes = summary.get("notes") if isinstance(summary.get("notes"), list) else []
    if notes:
        lines.append("- Notes:")
        for note in notes[:8]:
            lines.append(f"  - {note}")

    lines.extend(["", "## 9) Evidence Gaps + What Would Change the Score", "- Missing evidence levels:"])
    for item in _format_or_none([str(value) for value in (gap_map.get("missing_levels") or [])]):
        lines.append(f"  - {item}")

    lines.append("- Missing endpoint classes:")
    for item in _format_or_none([str(value) for value in (gap_map.get("missing_endpoints") or [])]):
        lines.append(f"  - {item}")

    lines.append("- Next best studies:")
    for item in _format_or_none([str(value) for value in (gap_map.get("next_best_studies") or [])]):
        lines.append(f"  - {item}")

    lines.append("- Registry/publication mismatch cautions:")
    for item in _format_or_none([str(value) for value in (gap_map.get("mismatch_cautions") or [])]):
        lines.append(f"  - {item}")

    lines.extend(
        [
            "",
            "## 10) Limitations of this Automated Review",
            "- Classification is metadata-driven and may miss details only available in full text.",
            "- Registry/publication linkage can be incomplete for recently completed trials.",
            "- The report reflects retrieved records only; hidden or unpublished studies may change conclusions.",
            f"- Records summarized: {len(records)}",
            "",
            "```json",
            json.dumps(report_json, ensure_ascii=True, indent=2, sort_keys=True),
            "```",
        ]
    )

    return "\n".join(lines).strip() + "\n"
