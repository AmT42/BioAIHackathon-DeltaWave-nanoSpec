from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import httpx

from paperqa.clients.openalex import get_openalex_mailto

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DEFAULT_TIMEOUT = 30.0
MAX_PER_PAGE = 200
DEFAULT_PER_PAGE = 50


def _decode_abstract(inverted_index: dict[str, Sequence[int]] | None) -> str | None:
    """Decode OpenAlex inverted index abstract into plain text."""
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for token, indices in inverted_index.items():
        for idx in indices:
            positions[idx] = token
    return " ".join(positions[index] for index in sorted(positions))


def _select_fields() -> str | None:
    """Fields to request from OpenAlex to keep payloads small.

    NOTE: OpenAlex currently rejects `select` values that include nested resources
    such as `host_venue`. Rather than risk a 403 from an invalid combination,
    default to `None` (use the full payload) until they expose a stable subset.
    """
    return None


@dataclass(slots=True)
class OpenAlexWorkHit:
    openalex_id: str
    doi: str | None
    title: str
    publication_year: int | None
    publication_date: str | None
    relevance_score: float | None
    cited_by_count: int | None
    authors: tuple[str, ...]
    host_venue: str | None
    publisher: str | None
    abstract: str | None
    open_access: dict[str, Any]
    best_oa_location: dict[str, Any]
    primary_location: dict[str, Any]
    locations: tuple[dict[str, Any], ...]
    raw: dict[str, Any]


class OpenAlexSearchClient:
    """Lightweight async client for OpenAlex works search endpoint."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_per_page: int = MAX_PER_PAGE,
        http_client: httpx.AsyncClient | None = None,
        mailto: str | None = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout)
        self._max_per_page = max(1, min(max_per_page, MAX_PER_PAGE))
        self._provided_client = http_client
        self._mailto = mailto if mailto is not None else get_openalex_mailto()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._provided_client is not None:
            return self._provided_client
        headers = {
            "User-Agent": "PaperQA/oss (+https://github.com/whitead/paper-qa)",
        }
        if self._mailto:
            headers["User-Agent"] = (
                f"PaperQA/oss (+mailto:{self._mailto})"
            )
        return httpx.AsyncClient(timeout=self._timeout, headers=headers)

    async def search(
        self,
        query: str,
        *,
        filters: dict[str, str] | None = None,
        max_results: int = 50,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[OpenAlexWorkHit]:
        """Execute a works search query and return ranked hits."""
        per_page = max(1, min(per_page, self._max_per_page))
        client = await self._get_client()
        should_close = client is not self._provided_client
        cursor = "*"
        results: list[OpenAlexWorkHit] = []
        params = {
            "search": query,
            "per-page": per_page,
            "cursor": cursor,
            "sort": "relevance_score:desc",
        }
        select_fields = _select_fields()
        if select_fields:
            params["select"] = select_fields
        if self._mailto:
            params["mailto"] = self._mailto
        if filters:
            filter_clause = ",".join(f"{key}:{value}" for key, value in filters.items())
            if filter_clause:
                params["filter"] = filter_clause
        try:
            while len(results) < max_results and cursor is not None:
                params["cursor"] = cursor
                try:
                    response = await client.get(OPENALEX_WORKS_URL, params=params)
                except httpx.HTTPError as exc:
                    logger.warning("OpenAlex request failed: %s", exc)
                    break
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    sleep_for = float(retry_after) if retry_after else 1.0
                    await asyncio.sleep(min(sleep_for, 30.0))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.warning("OpenAlex returned error: %s", exc)
                    break
                body = response.json()
                for item in body.get("results", []):
                    results.append(self._map_hit(item))
                    if len(results) >= max_results:
                        break
                cursor = (body.get("meta") or {}).get("next_cursor")
        finally:
            if should_close:
                await client.aclose()
        return results

    def _map_hit(self, raw: dict[str, Any]) -> OpenAlexWorkHit:
        authors = tuple(
            (
                authorship.get("author", {}) or {}
            ).get("display_name", "")
            for authorship in raw.get("authorships") or []
        )
        host = raw.get("host_venue") or {}
        doi = raw.get("doi")
        if isinstance(doi, str):
            doi = doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
        return OpenAlexWorkHit(
            openalex_id=raw["id"],
            doi=doi,
            title=raw.get("display_name") or "",
            publication_year=raw.get("publication_year"),
            publication_date=raw.get("publication_date"),
            relevance_score=raw.get("relevance_score"),
            cited_by_count=raw.get("cited_by_count"),
            authors=tuple(a for a in authors if a),
            host_venue=host.get("display_name"),
            publisher=host.get("publisher"),
            abstract=_decode_abstract(raw.get("abstract_inverted_index")),
            open_access=raw.get("open_access") or {},
            best_oa_location=raw.get("best_oa_location") or {},
            primary_location=raw.get("primary_location") or {},
            locations=tuple(raw.get("locations") or ()),
            raw=raw,
        )


def deduplicate_hits(
    hits: Iterable[OpenAlexWorkHit],
) -> list[OpenAlexWorkHit]:
    """Deduplicate hits by DOI and title."""
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    deduped: list[OpenAlexWorkHit] = []
    for hit in hits:
        doi = hit.doi.lower() if hit.doi else None
        title_key = " ".join(hit.title.lower().split())
        if doi and doi in seen_dois:
            continue
        if title_key in seen_titles:
            continue
        if doi:
            seen_dois.add(doi)
        seen_titles.add(title_key)
        deduped.append(hit)
    return deduped
