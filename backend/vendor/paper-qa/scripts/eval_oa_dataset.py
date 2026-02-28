#!/usr/bin/env python3
"""Evaluate DOI-based fetches for the OA messy links dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import time 

from paperqa.clients.open_access_resolver import OpenAccessResolver
from paperqa.clients.openalex_search import OpenAlexWorkHit
from paperqa.metrics import OpenAlexRunTracker
from paperqa.settings import Settings


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def _location_from_record(record: dict[str, Any], *, use_doi: bool) -> dict[str, Any]:
    return {
        "pdf_url": None if use_doi else record.get("pdf_url"),
        "landing_page_url": record.get("doi_url") if use_doi else record.get("landing_page_url"),
        "license": record.get("license"),
        "is_oa": True,
        "version": record.get("version"),
        "host_type": record.get("host_type"),
        "source": {
            "id": record.get("source_id"),
            "display_name": record.get("venue"),
            "host_organization_name": record.get("publisher"),
        },
    }


def _build_hit(record: dict[str, Any]) -> OpenAlexWorkHit:
    doi = record.get("doi")
    if isinstance(doi, str) and doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
    best = _location_from_record(record, use_doi=True)
    primary = _location_from_record(record, use_doi=False)
    locations = []
    if primary.get("landing_page_url") or primary.get("pdf_url"):
        locations.append(primary)
    if best.get("landing_page_url"):
        locations.append(best)
    return OpenAlexWorkHit(
        openalex_id=record.get("id") or "",
        doi=doi,
        title=record.get("title") or "",
        publication_year=record.get("year"),
        publication_date=None,
        relevance_score=None,
        cited_by_count=None,
        authors=tuple(),
        host_venue=record.get("venue"),
        publisher=record.get("publisher"),
        abstract=None,
        open_access={"oa_url": record.get("doi_url")} if record.get("doi_url") else {},
        best_oa_location=best,
        primary_location=primary,
        locations=tuple(locations),
        raw=record,
    )


async def _resolve_dataset(
    records: list[dict[str, Any]],
    *,
    settings: Settings,
    limit: int | None,
    tracker: OpenAlexRunTracker,
) -> None:
    async with httpx.AsyncClient(timeout=settings.agent.http_timeout_s) as http_client:
        resolver = OpenAccessResolver(http_client)
        for idx, record in enumerate(records, start=1):
            if limit is not None and idx > limit:
                break
            hit = _build_hit(record)
            failure_reason: str | None = None
            try:
                result = await resolver.fetch_fulltext(hit, settings=settings)
            except Exception as exc:  # noqa: BLE001
                failure_reason = f"exception:{exc}"
                result = None
            attempts = [
                {
                    "candidate_url": attempt.candidate_url,
                    "source": attempt.source,
                    "phase": attempt.phase,
                    "status": attempt.status,
                    "http_status": attempt.http_status,
                    "final_url": attempt.final_url,
                    "notes": attempt.notes,
                    "is_oa": attempt.is_oa,
                    "oa_status": attempt.oa_status,
                }
                for attempt in resolver.last_attempts
            ]
            tracker.record_hit(
                hit,
                result=result,
                attempts=attempts,
                failure_reason=failure_reason,
                artifact_source_path=(result.file_path if result else None),
                artifact_bytes=(result.content if result and result.content is not None else None),
            )


def _print_summary(tracker: OpenAlexRunTracker) -> None:
    total = len(tracker.records)
    resolved = sum(1 for o in tracker.records if o.success)
    ratio = (resolved / total * 100) if total else 0.0
    print(f"Total records: {total}")
    print(f"Resolved via DOI: {resolved} ({ratio:.1f}%)")
    if tracker.kinds:
        print("Resolved kinds:")
        for kind, count in tracker.kinds.most_common():
            pct = count / total * 100
            print(f"  - {kind}: {count} ({pct:.1f}%)")
    if tracker.sources:
        print("Chosen sources:")
        for src, count in tracker.sources.most_common():
            pct = count / max(resolved, 1) * 100
            print(f"  - {src}: {count} ({pct:.1f}%)")
    if tracker.resolution_reasons:
        print("Success reasons:")
        for reason, count in tracker.resolution_reasons.most_common():
            pct = count / max(resolved, 1) * 100
            print(f"  - {reason}: {count} ({pct:.1f}%)")
    if tracker.hosts:
        print("Top resolved hosts:")
        for host, count in tracker.hosts.most_common(5):
            pct = count / max(resolved, 1) * 100
            print(f"  - {host or '<unknown>'}: {count} ({pct:.1f}%)")
    if tracker.failures:
        print("Top failure reasons:")
        for reason, count in tracker.failures.most_common(5):
            pct = count / total * 100
            print(f"  - {reason}: {count} ({pct:.1f}%)")
    if tracker.attempts_success:
        avg_success = sum(tracker.attempts_success) / len(tracker.attempts_success)
    else:
        avg_success = 0.0
    if tracker.attempts_failure:
        avg_failure = sum(tracker.attempts_failure) / len(tracker.attempts_failure)
    else:
        avg_failure = 0.0
    print(f"Avg attempts (success): {avg_success:.2f}")
    print(f"Avg attempts (failure): {avg_failure:.2f}")


def _write_run_outputs(
    run_dir: Path,
    outcomes: list[dict[str, Any]],
    kinds: Counter[str],
    failures: Counter[str],
    hosts: Counter[str],
    sources: Counter[str],
    attempts_success: list[int],
    attempts_failure: list[int],
    overview_extra: dict[str, Any] | None = None,
) -> None:
    resolved = sum(1 for o in outcomes if o["success"])
    total = len(outcomes)
    overview = {
        "total": total,
        "resolved": resolved,
        "resolved_pct": (resolved / total * 100) if total else 0.0,
        "resolved_by_kind": dict(kinds),
        "resolved_by_source": dict(sources),
        "resolved_hosts": dict(hosts),
        "failures": dict(failures),
        "avg_attempts_success": (sum(attempts_success) / len(attempts_success)) if attempts_success else 0.0,
        "avg_attempts_failure": (sum(attempts_failure) / len(attempts_failure)) if attempts_failure else 0.0,
    }
    if overview_extra:
        overview.update(overview_extra)
    summary = {
        "overview": overview,
        "records": outcomes,
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _prepare_run_dirs(run_id: str | None) -> tuple[Path, Path]:
    if not run_id:
        run_id = time.strftime("run_%Y%m%d_%H%M%S")
    run_dir = Path("data") / run_id
    papers_dir = run_dir / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, papers_dir, run_id


async def _async_main(args: argparse.Namespace) -> None:
    records = _load_jsonl(Path(args.input))
    settings = Settings()
    settings.agent.respect_robots_txt = args.respect_robots
    settings.agent.allow_bronze = args.allow_bronze
    # settings.agent.headless_pdf_enabled = args.headless
    settings.agent.sink_to_file = args.sink_to_file

    tracker = OpenAlexRunTracker(base_dir="data", run_id=args.run_id, copy_artifacts=True)
    outcomes_name = args.output or "outcomes.jsonl"

    await _resolve_dataset(
        records,
        settings=settings,
        limit=args.limit,
        tracker=tracker,
    )
    tracker.write_outputs(outcomes_name=outcomes_name)
    _print_summary(tracker)
    print(f"Run artifacts saved under {tracker.run_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch OA messy links via DOI redirects and summarise results.",
    )
    parser.add_argument(
        "--input",
        default="data/oa_messy_links_50.jsonl",
        help="Path to the JSONL dataset.",
    )
    parser.add_argument(
        "--output",
        help="Name of the JSONL file (stored under the run directory) for per-record outcomes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional limit on the number of rows to evaluate.",
    )
    parser.add_argument(
        "--respect-robots",
        dest="respect_robots",
        action="store_true",
        default=True,
        help="Respect robots.txt directives.",
    )
    parser.add_argument(
        "--ignore-robots",
        dest="respect_robots",
        action="store_false",
        help="Ignore robots.txt directives.",
    )
    parser.add_argument(
        "--allow-bronze",
        action="store_true",
        default=True,
        help="Allow ingestion of bronze OA content.",
    )
    parser.add_argument(
        "--disallow-bronze",
        dest="allow_bronze",
        action="store_false",
        help="Reject bronze OA content.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Enable Playwright headless downloads.",
    )
    parser.add_argument(
        "--sink-to-file",
        action="store_true",
        help="Enable resolver file streaming for PDFs.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional run identifier (defaults to timestamp). Outputs stored in data/<run_id>/",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
