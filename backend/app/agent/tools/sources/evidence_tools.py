from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.evidence import (
    ClaimContext,
    EvidenceLedger,
    StudyRecord,
    build_gap_map,
    classify_pubmed_record,
    classify_trial_record,
    grade_ledger,
    render_report_json,
    render_report_markdown,
)
from app.agent.tools.artifacts import write_raw_json_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.registry import ToolSpec


def _to_claim_context(payload: dict[str, Any] | None) -> ClaimContext | None:
    if not isinstance(payload, dict):
        return None
    return ClaimContext(
        query=str(payload.get("query") or payload.get("intervention") or "").strip(),
        intervention=str(payload.get("intervention") or "").strip(),
        population=str(payload.get("population") or "unspecified").strip() or "unspecified",
        outcome=str(payload.get("outcome") or "aging-related outcome").strip() or "aging-related outcome",
        comparator=str(payload.get("comparator") or "unspecified").strip() or "unspecified",
        claim_mode=str(payload.get("claim_mode") or "explicit").strip() or "explicit",
        ask_clarify=bool(payload.get("ask_clarify", False)),
        directness_warnings=[str(item) for item in (payload.get("directness_warnings") or []) if str(item).strip()],
        ambiguity_warnings=[str(item) for item in (payload.get("ambiguity_warnings") or []) if str(item).strip()],
    )


def _to_study_record(item: dict[str, Any]) -> StudyRecord:
    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    return StudyRecord(
        study_key=str(item.get("study_key") or "").strip() or "unknown",
        source=str(item.get("source") or "unknown").strip() or "unknown",
        title=item.get("title"),
        year=item.get("year") if isinstance(item.get("year"), int) else None,
        ids={str(k): str(v) for k, v in ids.items() if str(v).strip()},
        evidence_level=(int(item.get("evidence_level")) if str(item.get("evidence_level") or "").isdigit() else None),
        study_type=str(item.get("study_type") or "unknown"),
        population_class=str(item.get("population_class") or "unknown"),
        endpoint_class=str(item.get("endpoint_class") or "mechanistic_only"),
        quality_flags=[str(v) for v in (item.get("quality_flags") or []) if str(v).strip()],
        directness_flags=[str(v) for v in (item.get("directness_flags") or []) if str(v).strip()],
        effect_direction=str(item.get("effect_direction") or "unknown"),
        citations=list(item.get("citations") or []),
        metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
    )


def _ledger_from_payload(payload: dict[str, Any]) -> EvidenceLedger:
    if "ledger" in payload and isinstance(payload.get("ledger"), dict):
        payload = payload.get("ledger")
    if not isinstance(payload, dict):
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ledger' must be an object")

    records = [_to_study_record(item) for item in (payload.get("records") or []) if isinstance(item, dict)]
    return EvidenceLedger(
        records=records,
        dedupe_stats=payload.get("dedupe_stats") if isinstance(payload.get("dedupe_stats"), dict) else {},
        counts_by_level=payload.get("counts_by_level") if isinstance(payload.get("counts_by_level"), dict) else {},
        counts_by_endpoint=payload.get("counts_by_endpoint") if isinstance(payload.get("counts_by_endpoint"), dict) else {},
        counts_by_source=payload.get("counts_by_source") if isinstance(payload.get("counts_by_source"), dict) else {},
        coverage_gaps=list(payload.get("coverage_gaps") or []),
        optional_source_status=list(payload.get("optional_source_status") or []),
    )


def _maybe_write_artifact(ctx: ToolContext | None, name: str, payload: dict[str, Any], threshold: int = 80) -> list[dict[str, Any]]:
    records = payload.get("records")
    if not isinstance(records, list) or len(records) < threshold or ctx is None:
        return []
    ref = write_raw_json_artifact(ctx, name, payload)
    return [ref] if ref else []


def build_evidence_tools() -> list[ToolSpec]:
    def evidence_classify_pubmed_records(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        records = payload.get("records")
        if not isinstance(records, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'records' must be a list of PubMed records")

        claim_context = _to_claim_context(payload.get("claim_context") if isinstance(payload.get("claim_context"), dict) else payload)

        classified: list[dict[str, Any]] = []
        warnings: list[str] = []
        for idx, item in enumerate(records):
            if not isinstance(item, dict):
                warnings.append(f"record[{idx}]: skipped non-object")
                continue
            classified.append(classify_pubmed_record(item, claim_context=claim_context))

        artifacts = _maybe_write_artifact(ctx, "evidence_classify_pubmed_records", {"records": classified})
        return make_tool_output(
            source="evidence",
            summary=f"Classified {len(classified)} PubMed record(s) into evidence tiers.",
            data={"records": classified},
            ids=[row.get("study_key") for row in classified if row.get("study_key")],
            warnings=warnings,
            artifacts=artifacts,
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def evidence_classify_trial_records(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        records = payload.get("records") or payload.get("studies")
        if not isinstance(records, list):
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'records' (or 'studies') must be a list of trial records")

        claim_context = _to_claim_context(payload.get("claim_context") if isinstance(payload.get("claim_context"), dict) else payload)

        classified: list[dict[str, Any]] = []
        warnings: list[str] = []
        for idx, item in enumerate(records):
            if not isinstance(item, dict):
                warnings.append(f"record[{idx}]: skipped non-object")
                continue
            classified.append(classify_trial_record(item, claim_context=claim_context))

        artifacts = _maybe_write_artifact(ctx, "evidence_classify_trial_records", {"records": classified})
        return make_tool_output(
            source="evidence",
            summary=f"Classified {len(classified)} ClinicalTrials record(s).",
            data={"records": classified},
            ids=[row.get("study_key") for row in classified if row.get("study_key")],
            warnings=warnings,
            artifacts=artifacts,
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def evidence_build_ledger(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        pubmed_records = payload.get("pubmed_records") or payload.get("records") or []
        trial_records = payload.get("trial_records") or payload.get("trials") or []
        optional_source_status = payload.get("optional_source_status") or []

        all_rows: list[dict[str, Any]] = []
        for row in pubmed_records:
            if isinstance(row, dict):
                all_rows.append(row)
        for row in trial_records:
            if isinstance(row, dict):
                all_rows.append(row)

        deduped: list[StudyRecord] = []
        seen: set[str] = set()
        duplicate_count = 0

        counts_by_level: dict[str, int] = {}
        counts_by_endpoint: dict[str, int] = {}
        counts_by_source: dict[str, int] = {}

        for row in all_rows:
            record = _to_study_record(row)
            key = record.study_key.strip().lower()
            if not key or key == "unknown":
                key = f"{record.source}:{record.ids.get('pmid') or record.ids.get('doi') or record.ids.get('nct') or len(deduped)}"
                record.study_key = key
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            deduped.append(record)

            level_key = str(record.evidence_level) if record.evidence_level is not None else "unknown"
            counts_by_level[level_key] = counts_by_level.get(level_key, 0) + 1
            counts_by_endpoint[record.endpoint_class] = counts_by_endpoint.get(record.endpoint_class, 0) + 1
            counts_by_source[record.source] = counts_by_source.get(record.source, 0) + 1

        coverage_gaps: list[str] = []
        if counts_by_level.get("1", 0) == 0:
            coverage_gaps.append("No Level 1 evidence detected.")
        if counts_by_level.get("2", 0) == 0:
            coverage_gaps.append("No Level 2 evidence detected.")
        if sum(counts_by_level.get(level, 0) for level in ("1", "2", "3")) == 0:
            coverage_gaps.append("No human evidence detected (Levels 1-3).")

        ledger = EvidenceLedger(
            records=deduped,
            dedupe_stats={"input_records": len(all_rows), "unique_records": len(deduped), "duplicates_removed": duplicate_count},
            counts_by_level=counts_by_level,
            counts_by_endpoint=counts_by_endpoint,
            counts_by_source=counts_by_source,
            coverage_gaps=coverage_gaps,
            optional_source_status=[row for row in optional_source_status if isinstance(row, dict)],
        )

        ledger_dict = ledger.to_dict()
        artifacts = _maybe_write_artifact(ctx, "evidence_ledger", ledger_dict)
        return make_tool_output(
            source="evidence",
            summary=f"Built evidence ledger with {len(deduped)} unique record(s).",
            data=ledger_dict,
            ids=[record.study_key for record in deduped[:200]],
            artifacts=artifacts,
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def evidence_grade(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ledger = _ledger_from_payload(payload)
        grade = grade_ledger(ledger)
        out = grade.to_dict()
        return make_tool_output(
            source="evidence",
            summary=f"Graded evidence ledger: {grade.score} ({grade.label}).",
            data=out,
            ids=[str(grade.score)],
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def evidence_gap_map(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ledger = _ledger_from_payload(payload)
        grade_payload = payload.get("grade") if isinstance(payload.get("grade"), dict) else None
        grade = None
        if isinstance(grade_payload, dict):
            # Use deterministic recompute if trace object is not fully present.
            try:
                grade = grade_ledger(ledger)
            except Exception:
                grade = None

        gap_map = build_gap_map(ledger, grade)
        return make_tool_output(
            source="evidence",
            summary="Computed evidence gap map.",
            data=gap_map,
            ids=[str(item) for item in gap_map.get("missing_levels") or []],
            data_schema_version="v2.1",
            ctx=ctx,
        )

    def evidence_render_report(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        intervention = payload.get("intervention")
        if not isinstance(intervention, dict):
            intervention = {"label": str(payload.get("intervention") or "Intervention").strip() or "Intervention"}

        ledger = _ledger_from_payload(payload)

        grade_payload = payload.get("grade") if isinstance(payload.get("grade"), dict) else None
        grade = grade_ledger(ledger)
        if isinstance(grade_payload, dict):
            # Keep deterministic grade but allow caller-provided notes append.
            extra_notes = [str(item) for item in (grade_payload.get("notes") or []) if str(item).strip()]
            if extra_notes:
                grade.notes.extend(extra_notes)

        gap_payload = payload.get("gap_map") if isinstance(payload.get("gap_map"), dict) else None
        gap_map = gap_payload or build_gap_map(ledger, grade)
        claim_context = _to_claim_context(payload.get("claim_context") if isinstance(payload.get("claim_context"), dict) else None)

        report_json = render_report_json(
            intervention=intervention,
            ledger=ledger,
            grade=grade,
            gap_map=gap_map,
            claim_context=claim_context,
        )
        report_markdown = render_report_markdown(report_json)

        schema_path = Path(__file__).resolve().parents[5] / "schemas" / "evidence_report.schema.json"
        warnings: list[str] = []
        if not schema_path.exists():
            warnings.append("evidence_report.schema.json is missing; report emitted without schema validation.")

        artifacts: list[dict[str, Any]] = []
        if ctx is not None:
            json_ref = write_raw_json_artifact(ctx, "evidence_report_json", report_json)
            if json_ref:
                artifacts.append(json_ref)

        return make_tool_output(
            source="evidence",
            summary="Rendered evidence report markdown and JSON payload.",
            data={
                "report_markdown": report_markdown,
                "report_json": report_json,
                "schema_path": str(schema_path),
            },
            ids=[str(intervention.get("label") or "intervention")],
            warnings=warnings,
            artifacts=artifacts,
            data_schema_version="v2.1",
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="evidence_classify_pubmed_records",
            description=(
                "WHEN: Classify fetched PubMed records into evidence levels before grading.\n"
                "AVOID: Passing unparsed PMIDs or empty records.\n"
                "CRITICAL_ARGS: records (PubMed records), optional claim_context.\n"
                "RETURNS: Contract v2.1 output with classified records and study keys.\n"
                "FAILS_IF: records is missing or not a list."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "records": {"type": "array", "items": {"type": "object"}},
                    "claim_context": {"type": "object"},
                },
                "required": ["records"],
            },
            handler=evidence_classify_pubmed_records,
            source="evidence",
        ),
        ToolSpec(
            name="evidence_classify_trial_records",
            description=(
                "WHEN: Classify ClinicalTrials compact records into evidence-ready rows.\n"
                "AVOID: Passing registry search IDs without fetched fields.\n"
                "CRITICAL_ARGS: records (or studies), optional claim_context.\n"
                "RETURNS: Contract v2.1 output with classified trial rows.\n"
                "FAILS_IF: records/studies is missing or not a list."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "records": {"type": "array", "items": {"type": "object"}},
                    "studies": {"type": "array", "items": {"type": "object"}},
                    "claim_context": {"type": "object"},
                },
            },
            handler=evidence_classify_trial_records,
            source="evidence",
        ),
        ToolSpec(
            name="evidence_build_ledger",
            description=(
                "WHEN: Merge classified literature/trial rows into a deduplicated evidence ledger.\n"
                "AVOID: Mixing raw upstream payloads with classified rows.\n"
                "CRITICAL_ARGS: pubmed_records and/or trial_records.\n"
                "RETURNS: Contract v2.1 output with records, counts, and coverage gaps.\n"
                "FAILS_IF: payload cannot be interpreted as record collections."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pubmed_records": {"type": "array", "items": {"type": "object"}},
                    "trial_records": {"type": "array", "items": {"type": "object"}},
                    "records": {"type": "array", "items": {"type": "object"}},
                    "trials": {"type": "array", "items": {"type": "object"}},
                    "optional_source_status": {"type": "array", "items": {"type": "object"}},
                },
            },
            handler=evidence_build_ledger,
            source="evidence",
        ),
        ToolSpec(
            name="evidence_grade",
            description=(
                "WHEN: Compute deterministic confidence and trace from an evidence ledger.\n"
                "AVOID: Grading before classification/dedupe.\n"
                "CRITICAL_ARGS: ledger (records + counts).\n"
                "RETURNS: Contract v2.1 score, label, confidence, and trace.\n"
                "FAILS_IF: ledger is missing or invalid."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ledger": {"type": "object"},
                    "records": {"type": "array", "items": {"type": "object"}},
                },
            },
            handler=evidence_grade,
            source="evidence",
        ),
        ToolSpec(
            name="evidence_gap_map",
            description=(
                "WHEN: Generate missing-evidence and next-study recommendations from a ledger.\n"
                "AVOID: Using it as a substitute for grading.\n"
                "CRITICAL_ARGS: ledger (or records).\n"
                "RETURNS: Contract v2.1 gap map with missing tiers/endpoints.\n"
                "FAILS_IF: ledger is missing or invalid."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ledger": {"type": "object"},
                    "records": {"type": "array", "items": {"type": "object"}},
                    "grade": {"type": "object"},
                },
            },
            handler=evidence_gap_map,
            source="evidence",
        ),
        ToolSpec(
            name="evidence_render_report",
            description=(
                "WHEN: Render final markdown + JSON report after grading/gap analysis.\n"
                "AVOID: Calling without ledger inputs.\n"
                "CRITICAL_ARGS: intervention and ledger (or records).\n"
                "RETURNS: Contract v2.1 report_markdown + report_json aligned to schema.\n"
                "FAILS_IF: ledger cannot be constructed from input."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "intervention": {"type": ["object", "string"]},
                    "ledger": {"type": "object"},
                    "records": {"type": "array", "items": {"type": "object"}},
                    "grade": {"type": "object"},
                    "gap_map": {"type": "object"},
                    "claim_context": {"type": "object"},
                },
            },
            handler=evidence_render_report,
            source="evidence",
        ),
    ]
