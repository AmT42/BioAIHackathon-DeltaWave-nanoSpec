from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from paperqa.clients.open_access_resolver import FulltextFetchResult
from paperqa.clients.openalex_search import OpenAlexWorkHit


@dataclass
class RunRecord:
    """Per-hit telemetry for OpenAlex resolver runs."""

    openalex_id: str
    doi: str | None
    title: str | None
    success: bool
    kind: str | None
    final_url: str | None
    sha256: str | None
    license: str | None
    failure_reason: str | None
    resolution_reason: str | None
    failure_details: str | None
    resolution_details: str | None
    chosen_source: str | None
    artifact_path: str | None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.openalex_id,
            "doi": self.doi,
            "title": self.title,
            "success": self.success,
            "kind": self.kind,
            "final_url": self.final_url,
            "sha256": self.sha256,
            "license": self.license,
            "failure_reason": self.failure_reason,
            "resolution_reason": self.resolution_reason,
            "failure_details": self.failure_details,
            "resolution_details": self.resolution_details,
            "chosen_source": self.chosen_source,
            "artifact_path": self.artifact_path,
            "attempts": self.attempts,
        }


@dataclass
class SearchLog:
    """Summary of a single search invocation."""

    provider: str
    query: str
    min_year: int | None
    max_year: int | None
    offset: int
    search_count: int
    external_pdf_max_downloads: int | None
    per_page: int
    max_results: int
    raw_hits: int
    deduped_hits: int
    considered_hits: int
    ingested_hits: int
    started_at: float
    finished_at: float

    def as_dict(self) -> dict[str, Any]:
        started = datetime.fromtimestamp(self.started_at, tz=timezone.utc)
        finished = datetime.fromtimestamp(self.finished_at, tz=timezone.utc)
        return {
            "provider": self.provider,
            "query": self.query,
            "min_year": self.min_year,
            "max_year": self.max_year,
            "offset": self.offset,
            "search_count": self.search_count,
            "external_pdf_max_downloads": self.external_pdf_max_downloads,
            "per_page": self.per_page,
            "max_results": self.max_results,
            "raw_hits": self.raw_hits,
            "deduped_hits": self.deduped_hits,
            "considered_hits": self.considered_hits,
            "ingested_hits": self.ingested_hits,
            "duration_s": max(self.finished_at - self.started_at, 0.0),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
        }


class OpenAlexRunTracker:
    """Aggregate resolver telemetry for both scripts and interactive runs."""

    def __init__(
        self,
        *,
        base_dir: str | Path | None = "data",
        run_id: str | None = None,
        copy_artifacts: bool = True,
    ) -> None:
        self.base_dir = Path(base_dir or "data")
        self.run_id = run_id or time.strftime("run_%Y%m%d_%H%M%S")
        self.run_dir = self.base_dir / self.run_id
        self.papers_dir = self.run_dir / "papers"
        self.copy_artifacts = copy_artifacts

        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.copy_artifacts:
            self.papers_dir.mkdir(parents=True, exist_ok=True)

        self.records: list[RunRecord] = []
        self.kinds: Counter[str] = Counter()
        self.failures: Counter[str] = Counter()
        self.hosts: Counter[str] = Counter()
        self.sources: Counter[str] = Counter()
        self.resolution_reasons: Counter[str] = Counter()
        self.attempts_success: list[int] = []
        self.attempts_failure: list[int] = []
        self._saved_names: set[str] = set()
        self._finalized = False
        self.search_records: list[SearchLog] = []

    def record_hit(
        self,
        hit: OpenAlexWorkHit,
        *,
        result: FulltextFetchResult | None,
        attempts: list[dict[str, Any]],
        failure_reason: str | None = None,
        artifact_source_path: str | Path | None = None,
        artifact_bytes: bytes | None = None,
    ) -> None:
        success = result is not None
        kind = result.kind if result else None
        final_url = result.url if result else None
        sha256 = result.sha256 if result else None
        license_value = result.license if result else None
        chosen_source = self._derive_chosen_source(attempts, final_url)
        resolution_reason = self._success_reason(attempts, final_url) if success else None
        if success:
            if kind:
                self.kinds[kind] += 1
            if chosen_source:
                self.sources[chosen_source] += 1
            if resolution_reason:
                self.resolution_reasons[resolution_reason] += 1
            host = urlparse(final_url).netloc.lower() if final_url else ""
            if host:
                self.hosts[host] += 1
            self.attempts_success.append(len(attempts))
        else:
            failure_reason = failure_reason or self._classify_failure(attempts)
            self.failures[failure_reason] += 1
            self.attempts_failure.append(len(attempts))

        artifact_path = None
        if success and self.copy_artifacts:
            artifact_path = self._persist_artifact(
                hit=hit,
                result=result,
                source_path=Path(artifact_source_path) if artifact_source_path else None,
                content=artifact_bytes or (result.content if result else None),
            )

        record = RunRecord(
            openalex_id=hit.openalex_id,
            doi=hit.doi,
            title=hit.title,
            success=success,
            kind=kind,
            final_url=final_url,
            sha256=sha256,
            license=license_value,
            failure_reason=None if success else failure_reason,
            resolution_reason=resolution_reason if success else None,
            failure_details=None if success else self._failure_details(attempts),
            resolution_details=self._success_details(attempts, final_url) if success else None,
            chosen_source=chosen_source,
            artifact_path=str(artifact_path) if artifact_path else None,
            attempts=attempts,
        )
        self.records.append(record)

    def record_search_metadata(
        self,
        *,
        provider: str,
        query: str,
        min_year: int | None,
        max_year: int | None,
        offset: int,
        search_count: int,
        external_pdf_max_downloads: int | None,
        per_page: int,
        max_results: int,
        raw_hits: int,
        deduped_hits: int,
        considered_hits: int,
        ingested_hits: int,
        started_at: float,
        finished_at: float,
    ) -> None:
        self.search_records.append(
            SearchLog(
                provider=provider,
                query=query,
                min_year=min_year,
                max_year=max_year,
                offset=offset,
                search_count=search_count,
                external_pdf_max_downloads=external_pdf_max_downloads,
                per_page=per_page,
                max_results=max_results,
                raw_hits=raw_hits,
                deduped_hits=deduped_hits,
                considered_hits=considered_hits,
                ingested_hits=ingested_hits,
                started_at=started_at,
                finished_at=finished_at,
            )
        )

    def write_outputs(self, outcomes_name: str = "outcomes.jsonl") -> None:
        if self._finalized:
            return
        outcomes_path = self.run_dir / outcomes_name
        with outcomes_path.open("w", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(record.as_dict()) + "\n")

        summary = {
            "overview": self._overview(outcomes_path.name),
            "search": [record.as_dict() for record in self.search_records],
            "records": [record.as_dict() for record in self.records],
        }
        summary_path = self.run_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        self._finalized = True

    def _overview(self, outcomes_name: str) -> dict[str, Any]:
        total = len(self.records)
        resolved = sum(1 for r in self.records if r.success)
        overview = {
            "run_id": self.run_id,
            "outcomes_file": outcomes_name,
            "total": total,
            "resolved": resolved,
            "resolved_pct": (resolved / total * 100) if total else 0.0,
            "resolved_by_kind": dict(self.kinds),
            "resolved_by_source": dict(self.sources),
            "resolved_hosts": dict(self.hosts),
            "resolved_by_reason": dict(self.resolution_reasons),
            "failures": dict(self.failures),
            "avg_attempts_success": (sum(self.attempts_success) / len(self.attempts_success))
            if self.attempts_success
            else 0.0,
            "avg_attempts_failure": (sum(self.attempts_failure) / len(self.attempts_failure))
            if self.attempts_failure
            else 0.0,
        }
        return overview

    @staticmethod
    def _normalize_source(source: str | None) -> str | None:
        if not source:
            return None
        source = source.lower()
        if "openalex_best" in source:
            return "best"
        if "openalex_primary" in source:
            return "primary"
        if "openalex_locations" in source or "openalex_location" in source:
            return "locations"
        if "repo_transform" in source:
            return "repo_transform"
        if "openalex_oa_url" in source:
            return "oa_url"
        return "other"

    @classmethod
    def _derive_chosen_source(cls, attempts: list[dict[str, Any]], final_url: str | None) -> str | None:
        if not final_url:
            return None
        for attempt in attempts:
            if attempt.get("status") == "ok" and attempt.get("final_url") == final_url:
                return cls._normalize_source(attempt.get("source"))
        for attempt in attempts:
            if attempt.get("status") == "ok":
                return cls._normalize_source(attempt.get("source"))
        return None

    @staticmethod
    def _classify_failure(attempts: list[dict[str, Any]]) -> str:
        if not attempts:
            return "no_candidates"
        for attempt in attempts:
            phase = (attempt.get("phase") or "").lower()
            notes = (attempt.get("notes") or "").lower()
            status = attempt.get("http_status")
            if phase == "robots_block":
                return "robots_blocked"
            if phase == "license_reject":
                return "license_reject"
            if phase == "budget_exceeded":
                return "budget_exceeded"
            if "license" in notes and "reject" in notes:
                return "license_reject"
            if status in (401, 403):
                return "auth_required"
            if status in (404, 410):
                return "dead_link"
            if status == 429:
                return "rate_limited"
            if status is not None and 500 <= status < 600:
                return "server_error"
            if "no pdf/jats" in notes or phase.endswith("_fallback"):
                return "no_usable_fulltext"
        last_phase = (attempts[-1].get("phase") or "").lower()
        if last_phase:
            return last_phase
        last_status = attempts[-1].get("http_status")
        if last_status:
            return f"http_{last_status}"
        return "unknown_failure"

    @staticmethod
    def _success_reason(attempts: list[dict[str, Any]], final_url: str | None) -> str | None:
        if not final_url:
            return None
        for attempt in attempts:
            if attempt.get("status") == "ok" and attempt.get("final_url") == final_url:
                return attempt.get("phase")
        for attempt in attempts:
            if attempt.get("status") == "ok":
                return attempt.get("phase")
        return None

    @staticmethod
    def _success_details(attempts: list[dict[str, Any]], final_url: str | None) -> str | None:
        if not final_url:
            return None
        for attempt in attempts:
            if attempt.get("status") == "ok" and attempt.get("final_url") == final_url:
                return OpenAlexRunTracker._format_attempt_detail(attempt)
        for attempt in attempts:
            if attempt.get("status") == "ok":
                return OpenAlexRunTracker._format_attempt_detail(attempt)
        return None

    @staticmethod
    def _failure_details(attempts: list[dict[str, Any]]) -> str | None:
        if not attempts:
            return None
        for attempt in reversed(attempts):
            if attempt.get("status") == "err":
                return OpenAlexRunTracker._format_attempt_detail(attempt)
        return OpenAlexRunTracker._format_attempt_detail(attempts[-1])

    @staticmethod
    def _format_attempt_detail(attempt: dict[str, Any]) -> str:
        phase = attempt.get("phase") or "unknown"
        note = attempt.get("notes")
        status = attempt.get("status")
        http_status = attempt.get("http_status")
        parts = [phase]
        if status:
            parts.append(f"status={status}")
        if http_status is not None:
            parts.append(f"http={http_status}")
        if note:
            parts.append(f"notes={note}")
        return "; ".join(parts)

    def _persist_artifact(
        self,
        *,
        hit: OpenAlexWorkHit,
        result: FulltextFetchResult | None,
        source_path: Path | None,
        content: bytes | None,
    ) -> Path | None:
        if not result:
            return None
        suffix_map = {"pdf": ".pdf", "html": ".html", "jats": ".xml"}
        suffix = suffix_map.get(result.kind, ".bin")
        base = (hit.doi or hit.openalex_id or "work").replace("/", "_")
        filename = base + suffix
        counter = 1
        while filename in self._saved_names:
            filename = f"{base}_{counter}{suffix}"
            counter += 1
        self._saved_names.add(filename)
        dest = self.papers_dir / filename
        if content is not None:
            dest.write_bytes(content)
        elif source_path and source_path.exists():
            shutil.copy2(source_path, dest)
        else:
            return None
        return dest.relative_to(self.run_dir)
