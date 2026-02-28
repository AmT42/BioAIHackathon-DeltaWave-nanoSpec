"""Base classes for tools, implemented in a functional manner."""

import asyncio
import csv
import inspect
import logging
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import ClassVar, Self, cast

from aviary.core import ToolRequestMessage
import httpx
from lmi import Embeddable, EmbeddingModel, LiteLLMModel
from pydantic import BaseModel, ConfigDict, Field, computed_field

from paperqa.clients.open_access_resolver import (
    FulltextFetchResult,
    OpenAccessResolver,
)
from paperqa.clients.openalex import get_openalex_mailto, parse_openalex_to_doc_details
from paperqa.clients.openalex_search import (
    OpenAlexSearchClient,
    OpenAlexWorkHit,
    deduplicate_hits,
)
from paperqa.metrics import OpenAlexRunTracker
from paperqa.docs import Docs
from paperqa.readers import read_doc
from paperqa.settings import Settings
from paperqa.sources.clinical_trials import add_clinical_trials_to_docs
from paperqa.types import Context, DocDetails, PQASession
from paperqa.utils import encode_id, maybe_is_text

from .search import get_directory_index

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CachedFulltext:
    path: Path
    kind: str
    license: str | None
    sha256: str | None
    url: str | None = None


@dataclass
class OpenAlexSessionCache:
    """Session-scoped cache for OpenAlex-backed searches."""

    seen_doc_ids: set[str] = field(default_factory=set)
    downloaded: dict[str, CachedFulltext] = field(default_factory=dict)
    parsed: set[str] = field(default_factory=set)
    search_offsets: dict[tuple[str, str | None], int] = field(default_factory=dict)
    search_locks: dict[tuple[str, str | None], asyncio.Lock] = field(
        default_factory=dict
    )
    artifact_dir: Path | None = None
    manifest_path: Path | None = None
    manifest_lock: asyncio.Lock | None = None


def make_status(
    total_paper_count: int, relevant_paper_count: int, evidence_count: int, cost: float
) -> str:
    return (
        f"Status: Paper Count={total_paper_count}"
        f" | Relevant Papers={relevant_paper_count} | Current Evidence={evidence_count}"
        f" | Current Cost=${cost:.4f}"
    )


def default_status(state: "EnvironmentState") -> str:
    relevant_contexts = state.get_relevant_contexts()
    return make_status(
        total_paper_count=len(state.docs.docs),
        relevant_paper_count=len({c.text.doc.dockey for c in relevant_contexts}),
        evidence_count=len(relevant_contexts),
        cost=state.session.cost,
    )


class EnvironmentState(BaseModel):
    """State here contains documents and answer being populated."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    docs: Docs
    session: PQASession = Field(..., alias="answer")
    status_fn: Callable[[Self], str] | None = Field(
        default=None,
        description=(
            "Function used to generate status,"
            " uses `paperqa.agents.tools.default_status` "
            "if not provided."
        ),
    )

    # SEE: https://regex101.com/r/RmuVdC/1
    STATUS_SEARCH_REGEX_PATTERN: ClassVar[str] = (
        r"Status: Paper Count=(\d+) \| Relevant Papers=(\d+) \| Current Evidence=(\d+)"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        if self.status_fn is not None:
            return self.status_fn(cast("Self", self))
        return default_status(self)

    def get_relevant_contexts(self, score_threshold: int | None = 0) -> list[Context]:
        """Get all contexts whose score is above (exclusive) of the input threshold."""
        return [
            c
            for c in self.session.contexts
            if score_threshold is None or c.score > score_threshold
        ]

    def record_action(self, action: ToolRequestMessage) -> None:
        self.session.add_tokens(action)
        self.session.tool_history.append([tc.function.name for tc in action.tool_calls])

    def query_tool_history(self, tool_name: str) -> bool:
        """Return true if the tool is has been called in history."""
        return tool_name in set(chain.from_iterable(self.session.tool_history))


class NamedTool(BaseModel):
    """Base class to make looking up tools easier."""

    TOOL_FN_NAME: ClassVar[str] = (
        "# unpopulated"  # Comment symbol ensures no collisions
    )

    # Whether the tool can be called concurrently with other tools.
    # Be careful when enabling.
    CONCURRENCY_SAFE: ClassVar[bool] = False

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class PaperSearch(NamedTool):
    TOOL_FN_NAME = "paper_search"

    # This tool is safe to run concurrently. The only stateful operation on the state
    # is docs.aadd_texts, which itself is concurrency safe.
    CONCURRENCY_SAFE = True

    settings: Settings
    embedding_model: EmbeddingModel
    previous_searches: dict[tuple[str, str | None], int] = Field(default_factory=dict)
    openalex_cache: OpenAlexSessionCache = Field(default_factory=OpenAlexSessionCache)

    async def paper_search(
        self,
        query: str,
        min_year: int | str | None,
        max_year: int | str | None,
        state: EnvironmentState,
    ) -> str:
        """
        Search for papers to increase the paper count.

        Repeat previous calls with the same query and years to continue a search. Only repeat a maximum of twice.
        This tool can be called concurrently.
        This tool introduces novel papers, so invoke this tool when just beginning or when unsatisfied with the current evidence.

        Args:
            query: A search query, which can be a specific phrase, complete sentence,
                or general keywords, e.g. 'machine learning for immunology'. Also can be
                given search operators.
            min_year: Filter for minimum publication year, or None for no minimum year.
                The current year is {current_year}.
            max_year: Filter for maximum publication year, or None for no maximum year.
                The current year is {current_year}.
            state: Current state.

        Returns:
            String describing searched papers and the current status.
        """  # noqa: E501,W505

        def clean(value: int | str | None) -> int | None:
            if isinstance(value, int | None):
                return value
            if value == "None":  # Claude Sonnet 4.5 has given "None" (str)
                return None
            return int(value)  # Confirm string year was an integer

        cleaned_min_year = clean(min_year)
        cleaned_max_year = clean(max_year)

        # Convert to date range (e.g. 2022-2022) if date is present
        year = (
            (
                f"{cleaned_min_year if cleaned_min_year is not None else ''}"
                f"-{cleaned_max_year if cleaned_max_year is not None else ''}"
            )
            if (cleaned_min_year is not None or cleaned_max_year is not None)
            else None
        )
        provider = (self.settings.agent.external_search_provider or "local").lower()
        search_key = query, year
        if provider == "openalex":
            logger.info("Using OpenAlex for paper search on %r", query)
            return await self._paper_search_openalex(
                query=query,
                min_year=cleaned_min_year,
                max_year=cleaned_max_year,
                search_key=search_key,
                state=state,
            )

        # get offset if we've done this search before (continuation of search)
        try:
            offset = self.previous_searches[search_key]
        except KeyError:
            offset = self.previous_searches[search_key] = 0

        logger.info(f"Starting paper search for {query!r}.")
        index = await get_directory_index(settings=self.settings, build=False)
        results: list[Docs] = await index.query(
            query,
            top_n=self.settings.agent.search_count,
            offset=offset,
            field_subset=[f for f in index.fields if f != "year"],
        )
        logger.info(
            f"{self.TOOL_FN_NAME} for query {query!r} and offset {offset} returned"
            f" {len(results)} papers."
        )

        # combine all the resulting doc objects into one and update the state
        all_doc_details: list[DocDetails] = []
        for r in results:
            # there's only one doc per result, so just take the first one
            this_doc_details = cast("DocDetails", next(iter(r.docs.values())))
            all_doc_details.append(this_doc_details)
            await state.docs.aadd_texts(
                texts=r.texts,
                doc=this_doc_details,
                settings=self.settings,
                embedding_model=self.embedding_model,
            )

        status = state.status
        logger.info(status)
        # mark how far we've searched so that continuation will start at the right place
        self.previous_searches[search_key] += self.settings.agent.search_count
        if self.settings.agent.return_paper_metadata:
            retrieved_papers = "\n".join(
                [f"{x.title} ({x.year})" for x in all_doc_details]
            )
            return f"Retrieved Papers:\n{retrieved_papers}\n\n{status}"
        return status

    def _build_openalex_filters(
        self, min_year: int | None, max_year: int | None
    ) -> dict[str, str]:
        filters: dict[str, str] = {}
        if min_year is not None and max_year is not None:
            filters["publication_year"] = f"{min_year}-{max_year}"
        elif min_year is not None:
            filters["publication_year"] = f">{min_year - 1}"
        elif max_year is not None:
            filters["publication_year"] = f"<{max_year + 1}"
        if self.settings.agent.require_open_access:
            filters["is_oa"] = "true"
        return filters

    def _seed_seen_doc_ids(self, state: EnvironmentState) -> None:
        cache = self.openalex_cache
        for doc in state.docs.docs.values():
            dockey = getattr(doc, "dockey", None)
            if dockey:
                cache.seen_doc_ids.add(str(dockey))

    def _make_doc_id(self, hit: OpenAlexWorkHit) -> str:
        if hit.doi:
            return encode_id(hit.doi.lower(), maxsize=24)
        if hit.openalex_id:
            return encode_id(hit.openalex_id.lower(), maxsize=24)
        fallback = (
            f"{hit.title}|{(hit.authors[0] if hit.authors else '').lower()}|"
            f"{hit.publication_year or ''}"
        )
        return encode_id(fallback, maxsize=24)

    async def _persist_fulltext(
        self, doc_id: str, fulltext: FulltextFetchResult
    ) -> Path:
        artifact_dir = self._ensure_artifact_dir()
        suffix = self._kind_suffix(fulltext.kind)
        dest_path = artifact_dir / f"{doc_id}{suffix}"
        if fulltext.file_path:
            src = Path(fulltext.file_path)
            if dest_path.exists():
                dest_path.unlink()
            shutil.move(src, dest_path)
            return dest_path
        if fulltext.content is not None:
            dest_path.write_bytes(fulltext.content)
            return dest_path
        raise RuntimeError("Full-text download missing both file_path and content.")

    def _ensure_artifact_dir(self) -> Path:
        cache = self.openalex_cache
        if cache.artifact_dir is not None:
            return cache.artifact_dir
        base = (
            self.settings.agent.fulltext_archive_dir
            or self.settings.agent.run_stats_dir
            or "data"
        )
        path = Path(base).expanduser()
        if self.settings.agent.run_id:
            path = path / self.settings.agent.run_id
        path.mkdir(parents=True, exist_ok=True)
        cache.artifact_dir = path
        return path

    @staticmethod
    def _kind_suffix(kind: str) -> str:
        return {"pdf": ".pdf", "html": ".html", "jats": ".xml"}.get(kind, ".bin")

    @staticmethod
    def _cleanup_fulltext_file(fulltext: FulltextFetchResult) -> None:
        if not fulltext.file_path:
            return
        try:
            Path(fulltext.file_path).unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to clean up temporary file %s", fulltext.file_path)

    def _relative_file_location(self, path: Path) -> str:
        base = self._ensure_artifact_dir()
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)

    def _should_ingest_kind(self, kind: str) -> bool:
        if kind == "pdf":
            return True
        if kind == "html":
            return self.settings.agent.html_ingest_enabled
        if kind == "jats":
            return self.settings.agent.jats_ingest_enabled
        return False

    def _build_doc_details(
        self, *, doc_id: str, hit: OpenAlexWorkHit, artifact: CachedFulltext
    ) -> DocDetails:
        doc_details = parse_openalex_to_doc_details(hit.raw)
        doc_details.doc_id = doc_id
        doc_details.dockey = doc_id
        if not doc_details.docname:
            doc_details.docname = doc_details.key or doc_details.title or doc_id
        if not doc_details.title:
            doc_details.title = hit.title or doc_id
        doc_details.file_location = self._relative_file_location(artifact.path)
        if artifact.license:
            doc_details.license = artifact.license
        if artifact.url:
            doc_details.pdf_url = artifact.url
        if not doc_details.year and hit.publication_year:
            doc_details.year = hit.publication_year
        if (not doc_details.authors) and hit.authors:
            doc_details.authors = list(hit.authors)
        if not doc_details.citation:
            doc_details.citation = (
                f"{doc_details.title or doc_id}, {doc_details.year or 'n.d.'}"
            )
        doc_details.other = doc_details.other or {}
        doc_details.other.update(
            {
                "openalex_id": hit.openalex_id,
                "openalex_relevance": hit.relevance_score,
                "openalex_cited_by_count": hit.cited_by_count,
                "host_venue": hit.host_venue,
            }
        )
        return doc_details

    async def _parse_local_fulltext(
        self, *, doc_details: DocDetails, local_path: Path
    ) -> list:
        parse_config = self.settings.parsing
        parse_media, enrich_media = parse_config.should_parse_and_enrich_media
        multimodal_kwargs: dict[str, object] = {"parse_media": parse_media}
        if enrich_media:
            multimodal_kwargs["multimodal_enricher"] = (
                self.settings.make_media_enricher()
            )
        texts, metadata = await read_doc(
            local_path,
            doc_details,
            page_size_limit=parse_config.page_size_limit,
            parse_pdf=parse_config.parse_pdf,
            include_metadata=True,
            **multimodal_kwargs,
            **parse_config.reader_config,
        )
        if metadata.name != "image" and (
            not texts
            or len(texts[0].text) < 10
            or (
                not parse_config.disable_doc_valid_check
                and (
                    sum(len(t.text.replace("\n", "")) for t in texts[:2]) < 20
                    or not maybe_is_text("".join(t.text for t in texts[:5]))
                )
            )
        ):
            raise ValueError(
                f"This does not look like a text document: {local_path}. "
                "Pass disable_doc_valid_check to ignore this error."
            )
        return texts

    async def _append_manifest_row(self, doc_details: DocDetails) -> None:
        if not self.settings.agent.write_manifest:
            return
        manifest_path = self._ensure_manifest_path()
        if manifest_path is None:
            return
        row = {
            "doc_id": doc_details.doc_id or doc_details.dockey,
            "file_location": str(doc_details.file_location or ""),
            "doi": doc_details.doi or "",
            "title": doc_details.title or "",
            "year": str(doc_details.year or ""),
            "openalex_id": (doc_details.other or {}).get("openalex_id", ""),
            "host_venue": (doc_details.other or {}).get("host_venue", ""),
            "first_author": (doc_details.authors or [""])[0],
        }
        lock = self._get_manifest_lock()
        async with lock:
            await asyncio.to_thread(
                self._write_manifest_row_sync,
                manifest_path,
                row,
            )

    def _ensure_manifest_path(self) -> Path | None:
        cache = self.openalex_cache
        if cache.manifest_path is not None:
            return cache.manifest_path
        base_dir = self.settings.agent.run_stats_dir or "data"
        path = Path(base_dir).expanduser()
        if self.settings.agent.run_id:
            path = path / self.settings.agent.run_id
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning("Failed to create manifest directory %s: %s", path, exc)
            return None
        cache.manifest_path = path / "manifest.csv"
        return cache.manifest_path

    def _get_manifest_lock(self) -> asyncio.Lock:
        cache = self.openalex_cache
        if cache.manifest_lock is None:
            cache.manifest_lock = asyncio.Lock()
        return cache.manifest_lock

    def _write_manifest_row_sync(self, manifest_path: Path, row: dict[str, str]) -> None:
        fieldnames = [
            "doc_id",
            "file_location",
            "doi",
            "title",
            "year",
            "openalex_id",
            "host_venue",
            "first_author",
        ]
        is_new = not manifest_path.exists()
        with manifest_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if is_new:
                writer.writeheader()
            writer.writerow(row)

    async def _paper_search_openalex(
        self,
        query: str,
        min_year: int | None,
        max_year: int | None,
        search_key: tuple[str, str | None],
        state: EnvironmentState,
    ) -> str:
        normalized_key = (query.strip(), search_key[1])
        cache = self.openalex_cache
        lock = cache.search_locks.get(normalized_key)
        if lock is None:
            lock = asyncio.Lock()
            cache.search_locks[normalized_key] = lock

        async with lock:
            offset = cache.search_offsets.get(normalized_key, 0)
            status = await self._run_openalex_search(
                query=normalized_key[0],
                min_year=min_year,
                max_year=max_year,
                search_key=normalized_key,
                offset=offset,
                state=state,
            )
            new_offset = offset + self.settings.agent.search_count
            cache.search_offsets[normalized_key] = new_offset
            self.previous_searches[normalized_key] = new_offset
            return status

    async def _run_openalex_search(
        self,
        *,
        query: str,
        min_year: int | None,
        max_year: int | None,
        search_key: tuple[str, str | None],
        offset: int,
        state: EnvironmentState,
    ) -> str:
        cache = self.openalex_cache
        self._seed_seen_doc_ids(state)
        candidate_cap = max(
            self.settings.agent.external_search_max_results,
            offset + self.settings.agent.search_count,
        )
        per_page = min(100, max(25, self.settings.agent.search_count * 2))
        filters = self._build_openalex_filters(min_year, max_year)

        existing_dois = {
            getattr(doc, "doi", "").lower()
            for doc in state.docs.docs.values()
            if getattr(doc, "doi", None)
        }
        existing_titles = {
            normalized
            for doc in state.docs.docs.values()
            if (normalized := self._normalize_title(getattr(doc, "title", None)))
        }
        seen_hashes = {
            cached.sha256
            for cached in cache.downloaded.values()
            if cached.sha256 is not None
        }

        tracker: OpenAlexRunTracker | None = None
        if self.settings.agent.collect_run_stats:
            tracker = OpenAlexRunTracker(
                base_dir=self.settings.agent.run_stats_dir,
                run_id=self.settings.agent.run_id,
                copy_artifacts=True,
            )

        mailto = get_openalex_mailto()
        headers = {
            "User-Agent": "PaperQA/oss (+https://github.com/whitead/paper-qa)",
        }
        if mailto:
            headers["User-Agent"] = f"PaperQA/oss (+mailto:{mailto})"

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as http_client:
            search_client = OpenAlexSearchClient(
                http_client=http_client,
                mailto=mailto,
            )
            try:
                raw_hits = await search_client.search(
                    query=query,
                    filters=filters,
                    max_results=candidate_cap,
                    per_page=per_page,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("OpenAlex search failed: %s", exc)
                raise

            deduped_hits = deduplicate_hits(raw_hits)
            hits = deduped_hits[offset:]
            logger.info(
                "OpenAlex search returned %s raw hits, %s after dedup, %s after offset for query %r",
                len(raw_hits),
                len(deduped_hits),
                len(hits),
                query,
            )

            resolver = OpenAccessResolver(http_client)
            download_cap = (
                self.settings.agent.external_pdf_max_downloads
                or self.settings.agent.search_count
            )
            added_docs: list[DocDetails] = []
            for hit in hits:
                if len(added_docs) >= download_cap:
                    break
                if self._is_duplicate_hit(
                    hit, existing_dois=existing_dois, existing_titles=existing_titles
                ):
                    logger.debug(
                        "Skipping OpenAlex hit %s due to existing DOI/title match",
                        hit.openalex_id,
                    )
                    continue
                doc_id = self._make_doc_id(hit)
                if doc_id in cache.parsed or doc_id in cache.seen_doc_ids:
                    logger.debug(
                        "Skipping OpenAlex hit %s because doc_id %s is already parsed",
                        hit.openalex_id,
                        doc_id,
                    )
                    continue

                cached_fulltext = cache.downloaded.get(doc_id)
                attempts: list[dict[str, str | int | None | bool]] = []
                if cached_fulltext is None:
                    logger.info(
                        "Resolving full-text for OpenAlex hit %s (doi=%s, title=%s)",
                        hit.openalex_id,
                        hit.doi,
                        hit.title,
                    )
                    fulltext_result = await resolver.fetch_fulltext(
                        hit, settings=self.settings
                    )
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
                    if fulltext_result is None:
                        if tracker:
                            tracker.record_hit(
                                hit,
                                result=None,
                                attempts=attempts,
                                artifact_source_path=None,
                                artifact_bytes=None,
                            )
                        logger.info(
                            "No full-text retrieved for %s; skipping ingestion",
                            hit.openalex_id,
                        )
                        continue
                    if not self._should_ingest_kind(fulltext_result.kind):
                        if tracker:
                            tracker.record_hit(
                                hit,
                                result=fulltext_result,
                                attempts=attempts,
                                artifact_source_path=fulltext_result.file_path,
                                artifact_bytes=fulltext_result.content,
                            )
                        self._cleanup_fulltext_file(fulltext_result)
                        logger.info(
                            "Skipping %s because %s ingestion is disabled",
                            hit.openalex_id,
                            fulltext_result.kind,
                        )
                        continue
                    if fulltext_result.sha256 in seen_hashes:
                        if tracker:
                            tracker.record_hit(
                                hit,
                                result=fulltext_result,
                                attempts=attempts,
                                artifact_source_path=fulltext_result.file_path,
                                artifact_bytes=None,
                            )
                        self._cleanup_fulltext_file(fulltext_result)
                        logger.debug(
                            "Skipping OpenAlex hit %s due to duplicate content hash",
                            hit.openalex_id,
                        )
                        continue
                    try:
                        persisted_path = await self._persist_fulltext(
                            doc_id, fulltext_result
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Failed to persist full-text for %s: %s",
                            hit.openalex_id,
                            exc,
                        )
                        if tracker:
                            tracker.record_hit(
                                hit,
                                result=fulltext_result,
                                attempts=attempts,
                                artifact_source_path=fulltext_result.file_path,
                                artifact_bytes=fulltext_result.content,
                            )
                        self._cleanup_fulltext_file(fulltext_result)
                        continue
                    if tracker:
                        tracker.record_hit(
                            hit,
                            result=fulltext_result,
                            attempts=attempts,
                            artifact_source_path=str(persisted_path),
                            artifact_bytes=(
                                fulltext_result.content
                                if fulltext_result.content is not None
                                else None
                            ),
                        )
                    cached_fulltext = CachedFulltext(
                        path=persisted_path,
                        kind=fulltext_result.kind,
                        license=fulltext_result.license,
                        sha256=fulltext_result.sha256,
                        url=fulltext_result.url,
                    )
                    cache.downloaded[doc_id] = cached_fulltext
                    if fulltext_result.sha256:
                        seen_hashes.add(fulltext_result.sha256)
                else:
                    logger.debug(
                        "Reusing cached artifact for OpenAlex hit %s (doc_id=%s)",
                        hit.openalex_id,
                        doc_id,
                    )

                try:
                    doc_details = self._build_doc_details(
                        doc_id=doc_id,
                        hit=hit,
                        artifact=cached_fulltext,
                    )
                    texts = await self._parse_local_fulltext(
                        doc_details=doc_details, local_path=cached_fulltext.path
                    )
                    added = await state.docs.aadd_texts(
                        texts=texts,
                        doc=doc_details,
                        settings=self.settings,
                        embedding_model=self.embedding_model,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to ingest OpenAlex hit %s (doc_id=%s): %s",
                        hit.openalex_id,
                        doc_id,
                        exc,
                    )
                    continue
                if not added:
                    logger.debug(
                        "Doc %s already present in state; skipping re-ingest", doc_id
                    )
                    cache.parsed.add(doc_id)
                    cache.seen_doc_ids.add(doc_id)
                    continue

                cache.parsed.add(doc_id)
                cache.seen_doc_ids.add(doc_id)
                added_docs.append(doc_details)
                logger.info(
                    "Ingested OpenAlex hit %s (doi=%s, kind=%s)",
                    hit.openalex_id,
                    hit.doi,
                    cached_fulltext.kind,
                )
                if hit.doi:
                    existing_dois.add(hit.doi.lower())
                title_key = self._normalize_title(hit.title)
                if title_key:
                    existing_titles.add(title_key)
                await self._append_manifest_row(doc_details)

            if tracker:
                tracker.write_outputs()
                logger.info(
                    "OpenAlex run stats saved to %s (records=%s)",
                    tracker.run_dir,
                    len(tracker.records),
                )

        logger.info(
            "OpenAlex ingestion complete: %s new papers added (limit=%s)",
            len(added_docs),
            download_cap,
        )

        status = state.status
        if self.settings.agent.return_paper_metadata and added_docs:
            retrieved = "\n".join(
                f"{doc.title or 'Untitled'} ({doc.year or 'n.d.'})"
                for doc in added_docs
            )
            return f"Retrieved Papers:\n{retrieved}\n\n{status}"
        return status

    @staticmethod
    def _normalize_title(title: str | None) -> str:
        if not title:
            return ""
        return " ".join(title.lower().split())

    def _is_duplicate_hit(
        self,
        hit: OpenAlexWorkHit,
        *,
        existing_dois: set[str],
        existing_titles: set[str],
    ) -> bool:
        doi = hit.doi.lower() if hit.doi else None
        if doi and doi in existing_dois:
            return True
        title_key = self._normalize_title(hit.title)
        if title_key and title_key in existing_titles:
            return True
        return False

class EmptyDocsError(RuntimeError):
    """Error to throw when we needed docs to be present."""


class GatherEvidence(NamedTool):
    TOOL_FN_NAME = "gather_evidence"

    settings: Settings
    summary_llm_model: LiteLLMModel
    embedding_model: EmbeddingModel
    partitioning_fn: Callable[[Embeddable], int] | None = None

    async def gather_evidence(self, question: str, state: EnvironmentState) -> str:
        """
        Gather evidence from previous papers given a specific question to increase evidence and relevant paper counts.

        A valuable time to invoke this tool is right after another tool increases paper count.
        Feel free to invoke this tool in parallel with other tools, but do not call this tool in parallel with itself.
        Only invoke this tool when the paper count is above zero, or this tool will be useless.

        Args:
            question: Specific question to gather evidence for.
            state: Current state.

        Returns:
            String describing gathered evidence and the current status.
        """
        if not state.docs.docs:
            raise EmptyDocsError("Not gathering evidence due to having no papers.")

        if f"{self.TOOL_FN_NAME}_initialized" in self.settings.agent.callbacks:
            await asyncio.gather(
                *(
                    c(state)
                    for c in self.settings.agent.callbacks[
                        f"{self.TOOL_FN_NAME}_initialized"
                    ]
                )
            )

        logger.info(f"{self.TOOL_FN_NAME} starting for question {question!r}.")
        original_question = state.session.question
        l1 = l0 = len(state.session.contexts)

        try:
            # Swap out the question with the more specific question
            # TODO: remove this swap, as it prevents us from supporting parallel calls
            state.session.question = question

            # TODO: refactor answer out of this...
            state.session = await state.docs.aget_evidence(
                query=state.session,
                settings=self.settings,
                embedding_model=self.embedding_model,
                summary_llm_model=self.summary_llm_model,
                partitioning_fn=self.partitioning_fn,
                callbacks=self.settings.agent.callbacks.get(
                    f"{self.TOOL_FN_NAME}_aget_evidence"
                ),
            )
            l1 = len(state.session.contexts)
        finally:
            state.session.question = original_question

        status = state.status
        logger.info(status)
        # only show top n contexts for this particular question to the agent
        sorted_contexts = sorted(
            (
                c
                for c in state.session.contexts
                if c.question is None or c.question == question
            ),
            key=lambda x: x.score,
            reverse=True,
        )

        top_contexts = "\n\n".join(
            f"- {sc.context}"
            for sc in sorted_contexts[: self.settings.agent.agent_evidence_n]
        )

        best_evidence = (
            f" Best evidence(s) for the current question:\n\n{top_contexts}"
            if top_contexts
            else ""
        )

        if f"{self.TOOL_FN_NAME}_completed" in self.settings.agent.callbacks:
            await asyncio.gather(
                *(
                    callback(state)
                    for callback in self.settings.agent.callbacks[
                        f"{self.TOOL_FN_NAME}_completed"
                    ]
                )
            )

        return f"Added {l1 - l0} pieces of evidence.{best_evidence}\n\n" + status


class GenerateAnswer(NamedTool):
    TOOL_FN_NAME = "gen_answer"

    settings: Settings
    llm_model: LiteLLMModel
    summary_llm_model: LiteLLMModel
    embedding_model: EmbeddingModel
    partitioning_fn: Callable[[Embeddable], int] | None = None

    async def gen_answer(self, state: EnvironmentState) -> str:
        """
        Generate an answer using current evidence.

        The tool may fail, indicating that better or different evidence should be found.
        Aim for at least five pieces of evidence from multiple sources before invoking this tool.
        Feel free to invoke this tool in parallel with other tools, but do not call this tool in parallel with itself.

        Args:
            state: Current state.
        """
        logger.info(f"Generating answer for '{state.session.question}'.")

        if f"{self.TOOL_FN_NAME}_initialized" in self.settings.agent.callbacks:
            await asyncio.gather(
                *(
                    callback(state)
                    for callback in self.settings.agent.callbacks[
                        f"{self.TOOL_FN_NAME}_initialized"
                    ]
                )
            )

        state.session = await state.docs.aquery(
            query=state.session,
            settings=self.settings,
            llm_model=self.llm_model,
            summary_llm_model=self.summary_llm_model,
            embedding_model=self.embedding_model,
            partitioning_fn=self.partitioning_fn,
            callbacks=self.settings.agent.callbacks.get(
                f"{self.TOOL_FN_NAME}_aget_query"
            ),
        )

        answer = state.session.answer
        status = state.status
        logger.info(status)

        if f"{self.TOOL_FN_NAME}_completed" in self.settings.agent.callbacks:
            await asyncio.gather(
                *(
                    callback(state)
                    for callback in self.settings.agent.callbacks[
                        f"{self.TOOL_FN_NAME}_completed"
                    ]
                )
            )

        return f"{answer} | {status}"

    # Use to separate answer from status
    # NOTE: can match failure to answer or an actual answer
    ANSWER_SPLIT_REGEX_PATTERN: ClassVar[str] = (
        r" \| " + EnvironmentState.STATUS_SEARCH_REGEX_PATTERN
    )

    @classmethod
    def extract_answer_from_message(cls, content: str) -> str:
        """Extract the answer from a message content."""
        answer, *rest = re.split(
            pattern=cls.ANSWER_SPLIT_REGEX_PATTERN, string=content, maxsplit=1
        )
        return answer if len(rest) == 4 else ""  # noqa: PLR2004


class Reset(NamedTool):
    TOOL_FN_NAME = "reset"

    async def reset(self, state: EnvironmentState) -> None:
        """
        Reset by clearing all current evidence from the system.

        This tool is useful when repeatedly failing to answer because the existing evidence may unsuitable for the question.
        It does not make sense to call this tool in parallel with other tools, as its resetting all state.
        Only invoke this tool when the current evidence is above zero, or this tool will be useless.
        """  # noqa: E501,W505
        logger.info(f"Resetting '{state.session.question}'.")
        state.session.contexts = []
        state.session.context = ""


class Complete(NamedTool):
    TOOL_FN_NAME = "complete"

    # Use to separate certainty from status
    CERTAINTY_SPLIT_REGEX_PATTERN: ClassVar[str] = (
        r" \| " + EnvironmentState.STATUS_SEARCH_REGEX_PATTERN
    )

    NO_ANSWER_PHRASE: ClassVar[str] = "No answer generated."

    async def complete(
        self, has_successful_answer: bool, state: EnvironmentState
    ) -> str:
        """
        Terminate using the last proposed answer.

        Do not invoke this tool in parallel with other tools or itself.

        Args:
            has_successful_answer: Set True if an answer that addresses all parts of the
                task has been generated, otherwise set False to indicate unsureness.
            state: Current state.
        """
        # TODO: eliminate race condition here if agent calls 2+ times in parallel
        # with opposite has_successful_answer values
        state.session.has_successful_answer = has_successful_answer

        if not state.session.answer:
            state.session.answer = self.NO_ANSWER_PHRASE

        logger.info(
            f"Completing '{state.session.question}' as"
            f" '{'certain' if has_successful_answer else 'unsure'}'."
        )
        # Return answer and status to simplify postprocessing of tool response
        return f"{'Certain' if has_successful_answer else 'Unsure'} | {state.status}"


class ClinicalTrialsSearch(NamedTool):
    TOOL_FN_NAME = "clinical_trials_search"

    # See PaperSearch for rationale.
    CONCURRENCY_SAFE = True

    model_config = ConfigDict(extra="forbid")

    search_count: int = 8
    previous_searches: dict[str, int] = Field(default_factory=dict)
    settings: Settings = Field(default_factory=Settings)

    # Gather evidence tool must be modified to understand the new evidence
    GATHER_EVIDENCE_TOOL_PROMPT_OVERRIDE: ClassVar[str] = (
        """Gather evidence from previous papers and clinical trials given a specific question.

        Will increase evidence, relevant paper counts, and relevant clinical trial counts.
        A valuable time to invoke this tool is right after another tool increases paper or clinical trials count.
        Feel free to invoke this tool in parallel with other tools, but do not call this tool in parallel with itself.
        Only invoke this tool when the paper count or clinical trial count is above zero, or this tool will be useless.

        Args:
            question: Specific question to gather evidence for.
            state: Current state.

        Returns:
            String describing gathered evidence and the current status.
        """
    )

    async def clinical_trials_search(self, query: str, state: EnvironmentState) -> str:
        r"""Search for clinical trials, with support for repeated calls and concurrent execution.

        Will add new clinical trials to the state, and return metadata about the number of trials found.

        Args:
            query: The search query string. Supports complex boolean expressions, field-specific
                searches, and query modifiers through operators. All configuration is done through
                operators in the query string.
                Query Syntax:
                    Basic Search:
                        Simple text automatically uses default EXPANSION[Relaxation] and COVERAGE[Contains]
                        >>> "heart attack"

                    Modified Search:
                        Use operators to modify search behavior:
                        >>> 'EXPANSION[None]COVERAGE[FullMatch]"exact phrase"'
                        >>> 'EXPANSION[Concept]heart attack'

                    Field Search:
                        Specify fields using AREA operator:
                        >>> 'AREA[InterventionName]aspirin'
                        >>> 'AREA[Phase]PHASE3'

                    Location Search:
                        Use SEARCH operator for compound location queries:
                        >>> 'cancer AND SEARCH[Location](AREA[LocationCity]Boston AND AREA[LocationState]Massachusetts)'

                    Complex Boolean:
                        Combine terms with AND, OR, NOT and parentheses:
                        >>> '(cancer OR tumor) AND NOT (EXPANSION[None]pediatric OR AREA[StdAge]CHILD)'

                    Date Ranges:
                        Use RANGE to specify date ranges with formats like "yyyy-MM" or "yyyy-MM-dd".
                        Note that MIN and MAX can be used for open-ended ranges:
                        >>> AREA[ResultsFirstPostDate]RANGE[2015-01-01, MAX]

                Operators:
                    EXPANSION[type]: Controls term expansion
                        - None: Exact match only, case and accent sensitive
                        - Term: Includes lexical variants (plurals, spellings)
                        - Concept: Includes UMLS synonyms
                        - Relaxation: Relaxes adjacency requirements (default)
                        - Lossy: Allows missing partial terms

                    COVERAGE[type]: Controls text matching
                        - FullMatch: Must match entire field
                        - StartsWith: Must match beginning of field
                        - EndsWith: Must match end of field
                        - Contains: Must match part of field (default)

                    AREA[field]: Specifies field to search
                        - See Field Reference for available fields

                    SEARCH[type]: Groups field searches
                        - Location: Groups location-related fields
                        - Study: Groups study-related fields

                Usage Notes:
                    - All search expressions are implicitly OR expressions
                    - Operator precedence (highest to lowest): terms/source operators, NOT/context operators, AND, OR
                    - Use quotes for exact phrase matching: "heart attack"
                    - Use parentheses for grouping: (heart OR cardiac) AND attack
                    - Use backslash to escape operators: \AND
                    - Default expansion is EXPANSION[Relaxation]
                    - Default coverage is COVERAGE[Contains]

                Field Reference:
                    High Priority Fields (weight >= 0.8):
                        - NCTId (1.0): Trial identifier
                        - Acronym (1.0): Study acronym
                        - BriefTitle (0.89): Short title
                        - OfficialTitle (0.85): Full official title
                        - Condition (0.81): Medical condition
                        - InterventionName (0.8): Primary intervention name
                        - OverallStatus: Trial status

                    Medium Priority Fields (0.5-0.79):
                        - InterventionOtherName (0.75): Alternative intervention names
                        - Phase (0.65): Trial phase
                        - StdAge (0.65): Standard age groups
                        - Keyword (0.6): Study keywords
                        - BriefSummary (0.6): Short description
                        - SecondaryOutcomeMeasure (0.5): Secondary outcomes

                    Low Priority Fields (< 0.5):
                        - DesignPrimaryPurpose (0.3): Primary purpose of study
                        - StudyType (0.3)
                        - Various descriptive, location, and administrative fields

                Supported Enums:
                    Phase:
                        - EARLY_PHASE1: Early Phase 1
                        - PHASE1: Phase 1
                        - PHASE2: Phase 2
                        - PHASE3: Phase 3
                        - PHASE4: Phase 4
                        - NA: Not Applicable

                    StandardAge:
                        - CHILD: Child
                        - ADULT: Adult
                        - OLDER_ADULT: Older Adult

                    Status:
                        - RECRUITING: Currently recruiting participants
                        - ACTIVE_NOT_RECRUITING: Active but not recruiting
                        - COMPLETED: Study completed
                        - ENROLLING_BY_INVITATION: Enrolling by invitation only
                        - NOT_YET_RECRUITING: Not yet recruiting
                        - SUSPENDED: Study suspended
                        - TERMINATED: Study terminated
                        - WITHDRAWN: Study withdrawn
                        - AVAILABLE: Available
                        - NO_LONGER_AVAILABLE: No longer available
                        - TEMPORARILY_NOT_AVAILABLE: Temporarily not available
                        - APPROVED_FOR_MARKETING: Approved for marketing
                        - WITHHELD: Withheld
                        - UNKNOWN: Unknown status

                    StudyType:
                        - INTERVENTIONAL: Interventional studies
                        - OBSERVATIONAL: Observational studies
                        - EXPANDED_ACCESS: Expanded access studies

                    PrimaryPurpose:
                        - TREATMENT: Treatment
                        - PREVENTION: Prevention
                        - DIAGNOSTIC: Diagnostic
                        - ECT: Educational/Counseling/Training
                        - SUPPORTIVE_CARE: Supportive Care
                        - SCREENING: Screening
                        - HEALTH_SERVICES_RESEARCH: Health Services Research
                        - BASIC_SCIENCE: Basic Science
                        - DEVICE_FEASIBILITY: Device Feasibility
                        - OTHER: Other

                    InterventionType:
                        - BEHAVIORAL: Behavioral interventions
                        - BIOLOGICAL: Biological interventions
                        - COMBINATION_PRODUCT: Combination product interventions
                        - DEVICE: Device interventions
                        - DIAGNOSTIC_TEST: Diagnostic test interventions
                        - DIETARY_SUPPLEMENT: Dietary supplement interventions
                        - DRUG: Drug interventions
                        - GENETIC: Genetic interventions
                        - PROCEDURE: Procedure interventions
                        - RADIATION: Radiation interventions
                        - OTHER: Other interventions

                    DesignAllocation:
                        - RANDOMIZED: Randomized allocation
                        - NON_RANDOMIZED: Non-randomized allocation
                        - NA: Not applicable

                    InterventionalAssignment:
                        - SINGLE_GROUP: Single group assignment
                        - PARALLEL: Parallel assignment
                        - CROSSOVER: Crossover assignment
                        - FACTORIAL: Factorial assignment
                        - SEQUENTIAL: Sequential assignment

                    ObservationalModel:
                        - COHORT: Cohort
                        - CASE_CONTROL: Case-Control
                        - CASE_ONLY: Case-Only
                        - CASE_CROSSOVER: Case-Crossover
                        - ECOLOGIC_OR_COMMUNITY: Ecologic or Community
                        - FAMILY_BASED: Family-Based
                        - DEFINED_POPULATION: Defined Population
                        - NATURAL_HISTORY: Natural History
                        - OTHER: Other

                    DesignMasking:
                        - NONE: None (Open Label)
                        - SINGLE: Single
                        - DOUBLE: Double
                        - TRIPLE: Triple
                        - QUADRUPLE: Quadruple

                    WhoMasked:
                        - PARTICIPANT: Participant
                        - CARE_PROVIDER: Care Provider
                        - INVESTIGATOR: Investigator
                        - OUTCOMES_ASSESSOR: Outcomes Assessor

            state: Current state

        Returns:
            String describing current status
        """
        # get offset if we've done this search before (continuation of search)
        # or mark this search as new (so offset 0)
        try:
            offset = self.previous_searches[query]
        except KeyError:
            offset = self.previous_searches[query] = 0

        total_result_count, new_result_count, error_message = (
            await add_clinical_trials_to_docs(
                query,
                state.docs,
                self.settings,
                limit=self.search_count,
                offset=offset,
            )
        )
        # mark how far we've searched so that continuation will start at the right place
        self.previous_searches[query] += self.search_count
        if error_message is None:
            return (
                f"Found clinical trial search results from search {offset} to"
                f" {offset + new_result_count} among {total_result_count} total"
                f" results. {state.status}"
            )
        return f"Error in clinical trial query syntax: {error_message}"


AVAILABLE_TOOL_NAME_TO_CLASS: dict[str, type[NamedTool]] = {
    cls.TOOL_FN_NAME: cls
    for _, cls in inspect.getmembers(
        sys.modules[__name__],
        predicate=lambda v: inspect.isclass(v)
        and issubclass(v, NamedTool)
        and v is not NamedTool,
    )
}


DEFAULT_TOOL_NAMES: list[str] = [
    name.strip()
    for name in os.environ.get("PAPERQA_DEFAULT_TOOL_NAMES", "").split(",")
    if name.strip()
] or [
    PaperSearch.TOOL_FN_NAME,
    GatherEvidence.TOOL_FN_NAME,
    GenerateAnswer.TOOL_FN_NAME,
    Reset.TOOL_FN_NAME,
    Complete.TOOL_FN_NAME,
]
