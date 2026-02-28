"""Base classes for tools, implemented in a functional manner."""

import asyncio
import inspect
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from itertools import chain
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from aviary.core import ToolRequestMessage
import httpx
from lmi import Embeddable, EmbeddingModel, LiteLLMModel
from pydantic import BaseModel, ConfigDict, Field, computed_field

from paperqa.clients.open_access_resolver import (
    FulltextFetchResult,
    OpenAccessResolver,
)
from paperqa.clients.openalex import get_openalex_mailto
from paperqa.clients.openalex_search import (
    OpenAlexSearchClient,
    OpenAlexWorkHit,
    deduplicate_hits,
)
from paperqa.metrics import (
    OpenAlexRunTracker,
    capture_session_snapshot,
    compute_session_delta,
    get_agent_run_logger,
    list_citation_ids,
    summarize_contexts,
    summarize_doc_chunks,
)
from paperqa.docs import Docs
from paperqa.settings import Settings
from paperqa.sources.clinical_trials import add_clinical_trials_to_docs
from paperqa.types import Context, Doc, DocDetails, PQASession

from .search import get_directory_index

logger = logging.getLogger(__name__)


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

    async def paper_search(
        self,
        query: str,
        min_year: int | str | None,
        max_year: int | str | None,
        state: EnvironmentState,
    ) -> str:
        """
        Search for papers from OpenAlex to increase the paper count.

        This tool forwards the `query` verbatim to OpenAlex `works?search=` (lexical, BM25-style ranking).
        The goal is to construct a high‑recall, high‑precision search string that captures
        the key scientific entities and relationships in the current question.

        CRITICAL BEHAVIOR
        -----------------
        • OpenAlex `search` is **LEXICAL** (no automatic MeSH/synonym expansion).
        You must expand synonyms and variants **inside the query string**. OpenAlex does NOT automatically expand biomedical synonyms or MeSH terms.
        • Free-text behaves OR-like; use **AND/OR/NOT** (UPPERCASE) with **parentheses** to control logic.
        • Use **quoted phrases** for multi-word terms.
        • Repeating the same (query, min_year, max_year) continues the same search via offset/pagination.
        
        WHEN TO USE
        -----------
        • At the start of a session to seed the corpus.
        • When current evidence is insufficient, off-target, or too narrow.
        • To explore alternative hypotheses (use distinct queries concurrently).
        • To paginate additional results: repeat with the EXACT SAME `(query, min_year, max_year)`.
        • You may call this tool concurrently with different queries to explore
        alternative hypotheses or subtopics.

        Pagination / repeated calls
        ---------------------------
        - If the initial results are promising but clearly incomplete, you may repeat
        this tool call with the *exact same* `query`, `min_year`, and `max_year` to
        fetch additional pages.
        - Repeat at most **two** additional times per unique `(query, min_year, max_year)`
        combination.  
        - If you want a *different* slice of the literature (e.g., new synonyms, new
        population, new intervention), construct a new `query` instead of repeating.
        
        Repeat previous calls with the same query and years to continue a search. Only repeat a maximum of twice.
        This tool can be called concurrently.
        This tool introduces novel papers, so invoke this tool when just beginning or when unsatisfied with the current evidence.

        Args:
            query:
                A compact, high-precision **boolean/phrase** query sent as-is to OpenAlex.
                Build it using this algorithm and rules:

                A) Identify key entities (only what matters):
                • Diseases/phenotypes (e.g., "type 2 diabetes", T2DM)
                • Genes/proteins/targets (e.g., GLP1, "GLP-1 receptor")
                • Drugs/classes (generic + major brand names; key analogs)
                • Methods/modalities/endpoints (e.g., "clinical trial", randomized, phase terms)
                • Populations/constraints (human/adult/older adult)

                B) Expand **only high-confidence** synonyms/variants:
                • Canonical name + common abbreviations and spellings
                • Hyphen/space/no-space variants (GLP-1 / GLP1 / "glucagon like peptide 1")
                • Greek/ASCII variants where common (α/alpha)
                • For drugs: generic + well-known brand names; key class exemplars
                • You may include MeSH preferred terms **as plain text** (not IDs) when they’re common in titles/abstracts
                • **Prefer quality over quantity** — include only synonyms you are **very sure** are correct
                • **Do NOT** include obscure lab codes, internal catalog numbers, or obviously wrong names

                C) Assemble boolean logic:
                • Group synonyms for the same entity with **OR** inside parentheses:
                    ("glucagon-like peptide 1" OR "glucagon like peptide 1" OR "GLP-1" OR GLP1)
                • Combine different entities with **AND** when the question links them (e.g., drug AND disease)
                • Use **NOT** to exclude dominant false positives (e.g., NOT (mouse OR murine OR "in vitro" OR preclinical))
                • Quote multi-word phrases: "clinical trial", "phase 2", "phase 3"

                D) Keep it concise and targeted:
                • Aim for 2–4 concept groups joined by AND; each group 1–8 carefully chosen OR terms
                • Avoid long natural-language sentences; produce a single, readable boolean string

                Examples:
                • GLP-1 human clinical trials (avoid preclinical):
                    (
                    "GLP-1" OR GLP1 OR "glucagon like peptide 1" OR "glucagon-like peptide-1"
                    ) AND (
                    semaglutide OR liraglutide OR exenatide OR dulaglutide OR albiglutide
                    OR "GLP-1 agonist" OR "GLP-1 analogue"
                    ) AND (
                    "clinical trial" OR randomized OR "phase 2" OR "phase 3"
                    ) AND (human OR adults)
                    NOT (mouse OR murine OR "in vitro" OR preclinical)

                • ALS + TDP-43 + riluzole:
                    ("amyotrophic lateral sclerosis" OR ALS OR "lou gehrig disease")
                    AND ("tar dna-binding protein 43" OR "tdp-43" OR TARDBP)
                    AND (riluzole OR Rilutek)

                DO / DON’T
                ----------
                ✓ Use AND/OR/NOT + parentheses; quote multi-word terms
                ✓ Include only high-confidence synonyms; enumerate key analogs/brand/generics when helpful
                ✓ Add NOT terms to suppress preclinical or off-topic hits
                ✗ Do NOT include obscure lab codes or obviously wrong names
                ✗ Do NOT send paragraph-style natural language — always a targeted boolean query
                
            min_year:
                Minimum publication year (inclusive) or None. Use when the task needs recency or the user specifies a window.
                The current year is {current_year}.

            max_year:
                Maximum publication year (inclusive) or None. Use when the task limits the timeframe.
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
        # get offset if we've done this search before (continuation of search)
        # or mark this search as new (so offset 0)
        search_key = query, year
        try:
            offset = self.previous_searches[search_key]
        except KeyError:
            offset = self.previous_searches[search_key] = 0

        provider = (self.settings.agent.external_search_provider or "local").lower()
        run_logger = get_agent_run_logger(self.settings)

        if provider == "openalex":
            logger.info("Using OpenAlex for paper search on %r", query)
            status, search_metrics = await self._paper_search_openalex(
                query=query,
                min_year=cleaned_min_year,
                max_year=cleaned_max_year,
                search_key=search_key,
                offset=offset,
                state=state,
            )
            if run_logger and search_metrics:
                run_logger.log_event(step="paper_search", **search_metrics)
            return status

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
        if run_logger:
            run_logger.log_event(
                step="paper_search",
                inputs={
                    "provider": "local",
                    "query": query,
                    "min_year": cleaned_min_year,
                    "max_year": cleaned_max_year,
                    "offset": offset,
                    "search_count": self.settings.agent.search_count,
                },
                outputs={
                    "result_count": len(all_doc_details),
                    "doc_samples": [
                        {
                            "title": getattr(doc, "title", None),
                            "year": getattr(doc, "year", None),
                            "dockey": getattr(doc, "dockey", None),
                        }
                        for doc in all_doc_details[:10]
                    ],
                    "status": status,
                },
            )
        if self.settings.agent.return_paper_metadata:
            retrieved_papers = "\n".join(
                [f"{x.title} ({x.year})" for x in all_doc_details]
            )
            return f"Retrieved Papers:\n{retrieved_papers}\n\n{status}"
        return status

    async def _paper_search_openalex(
        self,
        query: str,
        min_year: int | None,
        max_year: int | None,
        search_key: tuple[str, str | None],
        offset: int,
        state: EnvironmentState,
    ) -> tuple[str, dict[str, Any]]:
        candidate_cap = max(
            self.settings.agent.external_search_max_results,
            offset + self.settings.agent.search_count,
        )  #OA hp
        per_page = min(100, max(25, self.settings.agent.search_count * 2))
        search_started = time.time()

        filters: dict[str, str] = {}
        if min_year is not None and max_year is not None:
            filters["publication_year"] = f"{min_year}-{max_year}"
        elif min_year is not None:
            filters["publication_year"] = f">{min_year - 1}"
        elif max_year is not None:
            filters["publication_year"] = f"<{max_year + 1}"
        if self.settings.agent.require_open_access:
            filters["is_oa"] = "true"

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
        existing_doc_by_doi = {
            getattr(doc, "doi", "").lower(): doc
            for doc in state.docs.docs.values()
            if getattr(doc, "doi", None)
        }
        existing_doc_by_title = {
            normalized: doc
            for doc in state.docs.docs.values()
            if (normalized := self._normalize_title(getattr(doc, "title", None)))
        }

        def _doc_summary(doc: DocDetails | Doc | None) -> dict[str, Any] | None:
            if doc is None:
                return None
            return {
                "docname": getattr(doc, "docname", None),
                "dockey": getattr(doc, "dockey", None),
                "title": getattr(doc, "title", None),
                "doi": getattr(doc, "doi", None),
            }

        tracker: OpenAlexRunTracker | None = None
        if self.settings.agent.collect_run_stats:
            should_copy_artifacts = not bool(self.settings.agent.fulltext_archive_dir)
            tracker = OpenAlexRunTracker(
                base_dir=self.settings.agent.run_stats_dir,
                run_id=self.settings.agent.run_id,
                copy_artifacts=should_copy_artifacts,
            )
        hit_attempt_logs: list[dict[str, Any]] = []

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
            limit = (
                self.settings.agent.external_pdf_max_downloads
                or self.settings.agent.search_count
            )  #OA hp
            added_hits: list[OpenAlexWorkHit] = []
            seen_hashes: set[str] = set()
            for hit in hits:
                if len(added_hits) >= limit:
                    break
                is_duplicate, duplicate_reason, duplicate_key = self._is_duplicate_hit(
                    hit, existing_dois=existing_dois, existing_titles=existing_titles
                )
                hit_log_entry: dict[str, Any] = {
                    "openalex_id": hit.openalex_id,
                    "title": hit.title,
                    "doi": hit.doi,
                    "year": hit.publication_year,
                    "success": False,
                    "reason": None,
                }
                if is_duplicate:
                    duplicate_doc = None
                    if duplicate_reason == "duplicate_doi" and duplicate_key:
                        duplicate_doc = _doc_summary(
                            existing_doc_by_doi.get(duplicate_key)
                        )
                    elif duplicate_reason == "duplicate_title" and duplicate_key:
                        duplicate_doc = _doc_summary(
                            existing_doc_by_title.get(duplicate_key)
                        )
                    hit_log_entry.update(
                        {
                            "reason": duplicate_reason,
                            "duplicate_key": duplicate_key,
                            "existing_doc": duplicate_doc,
                        }
                    )
                    hit_attempt_logs.append(hit_log_entry)
                    logger.debug(
                        "Skipping OpenAlex hit %s due to existing DOI/title match",
                        hit.openalex_id,
                    )
                    continue
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
                artifact_path: str | None = None
                ingestion_info: dict[str, Any] | None = None
                if tracker:
                    tracker.record_hit(
                        hit,
                        result=fulltext_result,
                        attempts=attempts,
                        artifact_source_path=(
                            fulltext_result.file_path if fulltext_result else None
                        ),
                        artifact_bytes=(
                            fulltext_result.content
                            if fulltext_result and fulltext_result.content is not None
                            else None
                        ),
                    )
                    artifact_path = (
                        tracker.records[-1].artifact_path if tracker.records else None
                    )
                if fulltext_result is None:
                    logger.info(
                        "No full-text retrieved for %s; skipping ingestion",
                        hit.openalex_id,
                    )
                    hit_log_entry.update({"reason": "no_fulltext"})
                    hit_attempt_logs.append(hit_log_entry)
                    continue
                if fulltext_result.sha256 in seen_hashes:
                    logger.debug(
                        "Skipping OpenAlex hit %s due to duplicate content hash",
                        hit.openalex_id,
                    )
                    hit_log_entry.update(
                        {
                            "reason": "duplicate_content_hash",
                            "sha256": fulltext_result.sha256,
                        }
                    )
                    hit_attempt_logs.append(hit_log_entry)
                    continue
                try:
                    logger.info(
                        "Ingesting %s (%s) into Docs collection",
                        hit.openalex_id,
                        fulltext_result.kind,
                    )
                    ingestion_info = await self._ingest_fulltext_hit(
                        hit=hit,
                        fulltext=fulltext_result,
                        state=state,
                        http_client=http_client,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to ingest OpenAlex hit %s: %s", hit.openalex_id, exc
                    )
                    hit_log_entry.update(
                        {"reason": "ingest_error", "error": str(exc)}
                    )
                    hit_attempt_logs.append(hit_log_entry)
                    continue
                added_hits.append(hit)
                seen_hashes.add(fulltext_result.sha256)
                logger.info(
                    "Ingested OpenAlex hit %s (doi=%s, kind=%s)",
                    hit.openalex_id,
                    hit.doi,
                    fulltext_result.kind,
                )
                doc_model = ingestion_info.get("doc_model") if ingestion_info else None
                doi_lower: str | None = None
                if doc_model and getattr(doc_model, "doi", None):
                    doi_lower = getattr(doc_model, "doi").lower()
                    existing_doc_by_doi[doi_lower] = doc_model
                elif hit.doi:
                    doi_lower = hit.doi.lower()
                if doi_lower:
                    existing_dois.add(doi_lower)
                title_source = (
                    getattr(doc_model, "title", None) if doc_model else hit.title
                )
                title_key = self._normalize_title(title_source)
                if title_key:
                    existing_titles.add(title_key)
                    if doc_model:
                        existing_doc_by_title[title_key] = doc_model
                if ingestion_info:
                    hit_log_entry.update(
                        {
                            "docname": ingestion_info.get("docname"),
                            "dockey": ingestion_info.get("dockey"),
                            "archive_path": ingestion_info.get("archive_path"),
                        }
                    )
                hit_log_entry.update(
                    {
                        "success": True,
                        "artifact_path": artifact_path,
                        "sha256": fulltext_result.sha256,
                        "resolved_url": fulltext_result.url,
                        "kind": fulltext_result.kind,
                    }
                )
                hit_attempt_logs.append(hit_log_entry)

            if tracker:
                tracker.write_outputs()
                logger.info(
                    "OpenAlex run stats saved to %s (records=%s)",
                    tracker.run_dir,
                    len(tracker.records),
                )

        status = state.status
        self.previous_searches[search_key] += self.settings.agent.search_count
        logger.info(
            "OpenAlex ingestion complete: %s new papers added (limit=%s)",
            len(added_hits),
            limit,
        )
        tracker_run_dir = str(tracker.run_dir) if tracker else None
        if self.settings.agent.return_paper_metadata:
            retrieved = "\n".join(
                f"{hit.title} ({hit.publication_year or 'n.d.'})"
                for hit in added_hits
            )
            status_message = f"Retrieved Papers:\n{retrieved}\n\n{status}"
        else:
            status_message = status

        search_finished = time.time()
        if tracker:
            tracker.record_search_metadata(
                provider="openalex",
                query=query,
                min_year=min_year,
                max_year=max_year,
                offset=offset,
                search_count=self.settings.agent.search_count,
                external_pdf_max_downloads=self.settings.agent.external_pdf_max_downloads,
                per_page=per_page,
                max_results=candidate_cap,
                raw_hits=len(raw_hits),
                deduped_hits=len(deduped_hits),
                considered_hits=len(hits),
                ingested_hits=len(added_hits),
                started_at=search_started,
                finished_at=search_finished,
            )

        search_inputs = {
            "provider": "openalex",
            "query": query,
            "min_year": min_year,
            "max_year": max_year,
            "offset": offset,
            "search_count": self.settings.agent.search_count,
            "external_pdf_max_downloads": self.settings.agent.external_pdf_max_downloads,
            "per_page": per_page,
            "max_results": candidate_cap,
        }
        search_outputs = {
            "raw_hits": len(raw_hits),
            "deduped_hits": len(deduped_hits),
            "considered_hits": len(hits),
            "ingested_hits": len(added_hits),
            "status": status_message,
            "tracker_run_dir": tracker_run_dir,
            "retrieved_samples": [
                {
                    "title": hit.title,
                    "doi": hit.doi,
                    "year": hit.publication_year,
                }
                for hit in added_hits[:10]
            ],
            "tried_samples": hit_attempt_logs,
            "duplicate_samples": [
                sample
                for sample in hit_attempt_logs
                if isinstance(sample.get("reason"), str)
                and sample["reason"].startswith("duplicate")
            ],
        }
        return status_message, {"inputs": search_inputs, "outputs": search_outputs}

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
    ) -> tuple[bool, str | None, str | None]:
        doi = hit.doi.lower() if hit.doi else None
        if doi and doi in existing_dois:
            return True, "duplicate_doi", doi
        title_key = self._normalize_title(hit.title)
        if title_key and title_key in existing_titles:
            return True, "duplicate_title", title_key
        return False, None, None

    async def _ingest_fulltext_hit(
        self,
        *,
        hit: OpenAlexWorkHit,
        fulltext: FulltextFetchResult,
        state: EnvironmentState,
        http_client: httpx.AsyncClient,
    ) -> dict[str, Any] | None:
        citation_year = hit.publication_year or "n.d."
        citation_title = hit.title or hit.doi or "Unknown title"
        citation = f"{citation_title}, {citation_year}"
        suffix_map = {"pdf": ".pdf", "html": ".html", "jats": ".xml"}
        tmp_path: Path | None = None
        source_path: Path
        archive_dest_path: Path | None = None
        ingested_docname: str | None = None
        if fulltext.file_path:
            source_path = Path(fulltext.file_path)
        else:
            if fulltext.content is None:
                raise RuntimeError("Full-text download is missing in-memory content.")
            suffix = suffix_map.get(fulltext.kind, ".bin")
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
                tmp.write(fulltext.content)
                tmp_path = Path(tmp.name)
            source_path = tmp_path
        try:
            logger.info(
                "Persisting temporary %s (%s bytes) for OpenAlex hit %s",
                source_path,
                len(fulltext.content) if fulltext.content is not None else "streamed",
                hit.openalex_id,
            )
            archive_dir = self.settings.agent.fulltext_archive_dir
            if archive_dir:
                try:
                    archive_path = Path(archive_dir).expanduser()
                    archive_path.mkdir(parents=True, exist_ok=True)
                    identifier = (hit.doi or hit.openalex_id).replace("/", "_")
                    suffix = suffix_map.get(fulltext.kind, ".bin")
                    dest_path = archive_path / f"{identifier}{suffix}"
                    shutil.copy2(source_path, dest_path)
                    logger.info("Archived full-text for %s to %s", hit.openalex_id, dest_path)
                    archive_dest_path = dest_path
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to archive full-text for %s to %s: %s",
                        hit.openalex_id,
                        archive_dir,
                        exc,
                    )
            ingested_docname = await state.docs.aadd(
                source_path,
                citation=citation,
                title=hit.title or None,
                doi=hit.doi,
                authors=list(hit.authors) or None,
                settings=self.settings,
                embedding_model=self.embedding_model,
                http_client=http_client,
                license=fulltext.license,
            )
        finally:
            try:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
                elif fulltext.file_path:
                    Path(fulltext.file_path).unlink(missing_ok=True)
            except OSError:
                logger.debug(
                    "Failed to remove temporary file %s",
                    tmp_path or fulltext.file_path,
                )
        ingested_doc = None
        if ingested_docname:
            ingested_doc = next(
                (
                    doc
                    for doc in state.docs.docs.values()
                    if getattr(doc, "docname", None) == ingested_docname
                ),
                None,
            )
        return {
            "docname": ingested_docname,
            "dockey": getattr(ingested_doc, "dockey", None) if ingested_doc else None,
            "archive_path": str(archive_dest_path) if archive_dest_path else None,
            "doc_model": ingested_doc,
        }


class EmptyDocsError(RuntimeError):
    """Error to throw when we needed docs to be present."""


class GatherEvidence(NamedTool):
    TOOL_FN_NAME = "gather_evidence"

    settings: Settings
    summary_llm_model: LiteLLMModel
    embedding_model: EmbeddingModel
    partitioning_fn: Callable[[Embeddable], int] | None = None
    call_counter: int = 0

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
        run_logger = get_agent_run_logger(self.settings)
        session_snapshot = (
            capture_session_snapshot(state.session) if run_logger else None
        )
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

        self.call_counter += 1
        iteration = self.call_counter

        logger.info(
            f"{self.TOOL_FN_NAME} starting for question {question!r} (iteration={iteration})."
        )
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

        contexts_added = l1 - l0
        if run_logger:
            run_logger.log_event(
                step="gather_evidence",
                inputs={"question": question},
                outputs={
                    "contexts_added": contexts_added,
                    "iteration": iteration,
                    "new_contexts": summarize_contexts(
                        state.session.contexts[l0:l1],
                        question=question,
                        limit=25,
                    ),
                    "total_contexts": l1,
                    "docs_summary": summarize_doc_chunks(state.docs),
                    "status": status,
                },
                metadata={"iteration": iteration},
                session_delta=compute_session_delta(session_snapshot, state.session),
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
        run_logger = get_agent_run_logger(self.settings)
        session_snapshot = (
            capture_session_snapshot(state.session) if run_logger else None
        )

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

        if run_logger:
            run_logger.log_event(
                step="gen_answer",
                outputs={
                    "answer_length": len(state.session.answer or ""),
                    "raw_answer_length": len(state.session.raw_answer or ""),
                    "contexts_used": len(state.session.contexts),
                    "citations_used": list_citation_ids(
                        state.session.raw_answer or state.session.answer
                    ),
                    "status": status,
                },
                session_delta=compute_session_delta(session_snapshot, state.session),
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
