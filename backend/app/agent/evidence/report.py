from __future__ import annotations

from typing import Any


def _format_counts_by_level(records: list[dict[str, Any]]) -> dict[int, int]:
    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
    for record in records:
        level = int(record.get("evidence_level") or 0)
        if level in counts:
            counts[level] += 1
    return counts


def render_report_markdown(
    *,
    concept: dict[str, Any] | None,
    claim_context: dict[str, Any],
    ledger: dict[str, Any],
    score: dict[str, Any],
    gap_map: dict[str, Any],
) -> str:
    records = [item for item in (ledger.get("records") or []) if isinstance(item, dict)]
    counts = _format_counts_by_level(records)

    concept_label = ((concept or {}).get("label") if isinstance(concept, dict) else None) or claim_context.get("intervention")

    lines: list[str] = []
    lines.append(f"# Evidence Report: {concept_label}")
    lines.append("")
    lines.append("## Claim Context")
    lines.append(f"- Intervention: {claim_context.get('intervention')}")
    lines.append(f"- Population: {claim_context.get('population')}")
    lines.append(f"- Outcome: {claim_context.get('outcome')}")
    lines.append(f"- Comparator: {claim_context.get('comparator')}")
    if claim_context.get("directness_warnings"):
        lines.append(f"- Directness warnings: {', '.join(claim_context.get('directness_warnings') or [])}")

    lines.append("")
    lines.append("## Scores")
    lines.append(f"- CES: {score.get('ces')}")
    lines.append(f"- MP: {score.get('mp')}")
    lines.append(f"- Final confidence: {score.get('final_confidence')}")
    if score.get("caps_applied"):
        cap_labels = [str(item.get("cap")) for item in score.get("caps_applied") or []]
        lines.append(f"- Caps applied: {', '.join(cap_labels)}")

    lines.append("")
    lines.append("## Evidence Pyramid")
    lines.append("| Level | Count |")
    lines.append("|---|---:|")
    for level in [1, 2, 3, 4, 5, 6]:
        lines.append(f"| {level} | {counts[level]} |")

    lines.append("")
    lines.append("## Key Gaps")
    for item in gap_map.get("missing_evidence") or []:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("## What Would Change The Score")
    for item in gap_map.get("what_would_change_score") or []:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("## Top Evidence Items")
    top = sorted(
        records,
        key=lambda item: (int(item.get("evidence_level") or 99), str(item.get("title") or "")),
    )[:12]
    for item in top:
        ids = item.get("ids") or {}
        refs = [f"PMID:{ids.get('pmid')}" if ids.get("pmid") else "", f"NCT:{ids.get('nct')}" if ids.get("nct") else ""]
        ref_text = ", ".join(part for part in refs if part)
        lines.append(
            f"- L{item.get('evidence_level')} | {item.get('study_type')} | {item.get('title') or 'Untitled'}"
            + (f" ({ref_text})" if ref_text else "")
        )

    return "\n".join(lines).strip() + "\n"


def render_report_json(
    *,
    concept: dict[str, Any] | None,
    claim_context: dict[str, Any],
    ledger: dict[str, Any],
    score: dict[str, Any],
    gap_map: dict[str, Any],
) -> dict[str, Any]:
    return {
        "intervention": concept or {"label": claim_context.get("intervention")},
        "claim_context": claim_context,
        "evidence_summary": {
            "ces": score.get("ces"),
            "mp": score.get("mp"),
            "final_confidence": score.get("final_confidence"),
            "caps_applied": score.get("caps_applied") or [],
        },
        "ledger": ledger,
        "score": score,
        "gaps": gap_map,
    }
