#!/usr/bin/env python3
"""Evaluate the OpenAlex resolver against a JSONL of OA links."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from paperqa.clients.open_access_resolver import OpenAccessResolver
from paperqa.clients.openalex_search import OpenAlexWorkHit
from paperqa.settings import Settings


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _build_hit(record: dict[str, Any]) -> OpenAlexWorkHit:
    location = {
        "pdf_url": record.get("pdf_url"),
        "landing_page_url": record.get("landing_page_url"),
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
    oa_url = record.get("doi_url") or record.get("landing_page_url")
    doi = record.get("doi")
    if isinstance(doi, str) and doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
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
        open_access={"oa_url": oa_url} if oa_url else {},
        best_oa_location=location,
        primary_location=location,
        locations=(location,),
        raw=record,
    )


async def _resolve_dataset(
    records: list[dict[str, Any]],
    *,
    settings: Settings,
    limit: int | None,
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    outcomes: list[dict[str, Any]] = []
    kind_counter: Counter[str] = Counter()
    failure_counter: Counter[str] = Counter()
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
            success = result is not None
            if success and result:
                kind_counter[result.kind] += 1
            else:
                failure_counter[failure_reason or "no_result"] += 1
            outcomes.append(
                {
                    "id": record.get("id"),
                    "title": record.get("title"),
                    "doi": record.get("doi"),
                    "success": success,
                    "kind": result.kind if result else None,
                    "final_url": result.url if result else None,
                    "sha256": result.sha256 if result else None,
                    "failure_reason": failure_reason,
                    "attempts": [
                        {
                            "candidate_url": attempt.candidate_url,
                            "source": attempt.source,
                            "phase": attempt.phase,
                            "status": attempt.status,
                            "http_status": attempt.http_status,
                            "final_url": attempt.final_url,
                            "notes": attempt.notes,
                        }
                        for attempt in resolver.last_attempts
                    ],
                }
            )
    return outcomes, kind_counter, failure_counter


def _print_summary(outcomes: list[dict[str, Any]], kinds: Counter[str], failures: Counter[str]) -> None:
    total = len(outcomes)
    resolved = sum(1 for o in outcomes if o["success"])
    ratio = (resolved / total * 100) if total else 0.0
    print(f"Total records: {total}")
    print(f"Resolved: {resolved} ({ratio:.1f}%)")
    if kinds:
        print("Kinds:")
        for kind, count in kinds.most_common():
            pct = count / total * 100
            print(f"  - {kind}: {count} ({pct:.1f}%)")
    if failures:
        print("Top failure reasons:")
        for reason, count in failures.most_common(5):
            pct = count / total * 100
            print(f"  - {reason}: {count} ({pct:.1f}%)")


async def _async_main(args: argparse.Namespace) -> None:
    records = _load_records(Path(args.input))
    settings = Settings()
    settings.agent.respect_robots_txt = args.respect_robots
    # settings.agent.headless_pdf_enabled = args.headless
    outcomes, kinds, failures = await _resolve_dataset(
        records,
        settings=settings,
        limit=args.limit,
    )
    _print_summary(outcomes, kinds, failures)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            for row in outcomes:
                handle.write(json.dumps(row) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OpenAlex resolver against a JSONL dataset.")
    parser.add_argument(
        "--input",
        default="data/oa_messy_links_50.jsonl",
        help="Path to the JSONL file with OA link records.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write per-record resolver outcomes (JSONL).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of records to evaluate.",
    )
    parser.add_argument(
        "--respect-robots",
        dest="respect_robots",
        action="store_true",
        default=True,
        help="Respect robots.txt (default: true).",
    )
    parser.add_argument(
        "--ignore-robots",
        dest="respect_robots",
        action="store_false",
        help="Ignore robots.txt for evaluation.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Enable headless Playwright fallback (requires playwright installed).",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
