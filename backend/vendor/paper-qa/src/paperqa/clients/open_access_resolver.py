from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, unquote

import httpx

from paperqa.clients.openalex_search import OpenAlexWorkHit
from paperqa.net.policy import license_ok, resolve_license, robots_allows
from paperqa.net.pmc_pow import build_cookie_value, parse_pow_page, solve_pow

logger = logging.getLogger(__name__)

_LOG_FILE_INITIALIZED = False
DEFAULT_UA = "PaperQA/oss (+https://github.com/whitead/paper-qa)"
HEADLESS_UA_CHAIN = (
    DEFAULT_UA,
    # Chromium UA (matches Playwright default) to bypass vendor bot filters when needed.
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    # Firefox-style UA provides another fingerprint if Chromium is filtered.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
)

# Hosts that frequently require a Referer header for direct PDF downloads
NEEDS_REFERER_HOSTS = {
    "link.springer.com",
    "onlinelibrary.wiley.com",
    "dl.acm.org",
    "tandfonline.com",
    "journals.sagepub.com",
    "nature.com",
    "cambridge.org",
    "academic.oup.com",
    "science.org",
    "sciencedirect.com",
    "ieeexplore.ieee.org",
    "cell.com",
    "royalsocietypublishing.org",
    "asm.org",
}

PDF_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/acrobat",
}
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
XML_CONTENT_TYPES = {
    "application/xml",
    "text/xml",
    "application/jats+xml",
    "application/x-xml",
}
OCTET_CONTENT_TYPES = {"application/octet-stream", "binary/octet-stream"}

_SUPP_TOKENS = (
    "supplement",
    "supplementary",
    "supp",
    "si",
    "esm",
    "appendix",
    "suppl",
    "additional-file",
    "additional",
    "supporting",
)

_PDF_ALT_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/pdf["\'][^>]+href=["\']([^"\']+)["\']',
    re.I,
)
_META_CITATION_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_A_PDF_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\'][^>]*>([^<]{0,160})</a>',
    re.I,
)
_A_HREF_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{0,160})</a>',
    re.I,
)
_IFRAME_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
_EMBED_RE = re.compile(r'<embed[^>]+src=["\']([^"\']+)["\']', re.I)
_OBJECT_RE = re.compile(r'<object[^>]+data=["\']([^"\']+)["\']', re.I)
_VIEWER_FILE_RE = re.compile(r'viewer\.html\?file=([^"&]+)', re.I)
_XML_ALT_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/(?:xml|x-xml|jats\+xml|nxml)["\'][^>]+href=["\']([^"\']+)["\']',
    re.I,
)
_A_XML_RE = re.compile(r'<a[^>]+href=["\']([^"\']+\.xml(?:\?[^"\']*)?)["\']', re.I)


def _ensure_file_logging() -> None:
    """Attach a verbose log handler once per process for resolver debug traces."""
    global _LOG_FILE_INITIALIZED
    if _LOG_FILE_INITIALIZED:
        return
    log_path = Path(os.environ.get("PAPERQA_OPENALEX_LOG", "paperqa_openalex.log")).expanduser()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s")
        handler.setFormatter(fmt)
        logging.getLogger().addHandler(handler)
        logger.info("Detailed OpenAlex logs will be written to %s", log_path)
        _LOG_FILE_INITIALIZED = True
    except OSError as exc:  # noqa: BLE001
        logger.warning("Failed to initialise OpenAlex log file %s: %s", log_path, exc)


@dataclass(slots=True)
class FulltextCandidate:
    url: str
    kind_hint: str | None
    source: str
    license_hint: str | None
    referer: str | None = None
    is_oa: bool | None = None
    oa_status: str | None = None


@dataclass(slots=True)
class FulltextFetchResult:
    url: str
    kind: str  # "pdf" | "html" | "jats"
    content: bytes | None
    license: str | None
    sha256: str
    filename: str | None = None
    file_path: str | None = None


@dataclass(slots=True)
class _StreamedContent:
    final_url: str
    headers: httpx.Headers
    head: bytes
    content: bytes
    status_code: int
    truncated: bool = False


@dataclass(slots=True)
class FetchAttempt:
    candidate_url: str
    source: str
    phase: str
    status: str  # "ok" | "err"
    http_status: int | None
    final_url: str | None
    notes: str | None
    is_oa: bool | None
    oa_status: str | None


class OpenAccessResolver:
    """Resolve and download open access full-text artifacts for OpenAlex hits."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        _ensure_file_logging()
        self._http_client = http_client
        self._doi_locks: dict[str, asyncio.Lock] = {}
        self._download_semaphore: asyncio.Semaphore | None = None
        self._download_limit: int | None = None
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last_request: dict[str, float] = {}
        self.last_attempts: list[FetchAttempt] = []
        self._last_stream_error: str | None = None

    async def fetch_fulltext(self, hit: OpenAlexWorkHit, *, settings) -> FulltextFetchResult | None:
        """Resolve the best available full-text resource for a given OpenAlex hit."""
        if not hit:
            return None
        key = (hit.doi or hit.openalex_id).lower()
        lock = self._doi_locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._fetch_fulltext_locked(hit, settings=settings)

    async def _fetch_fulltext_locked(self, hit: OpenAlexWorkHit, *, settings) -> FulltextFetchResult | None:
        deadline = time.monotonic() + settings.agent.per_work_resolution_budget_s
        self.last_attempts = []
        candidates = await self.enumerate_candidates(hit)
        if not candidates:
            logger.info("No full-text candidates enumerated for %s", hit.openalex_id)
            return None
        candidates = await self._rescore_and_sort(candidates, settings=settings)
        best_html: FulltextFetchResult | None = None
        best_html_source: FulltextCandidate | None = None
        for index, candidate in enumerate(candidates, start=1):
            logger.info(
                "Trying candidate %s/%s for %s: url=%s source=%s hint=%s license_hint=%s referer=%s",
                index,
                len(candidates),
                hit.openalex_id,
                candidate.url,
                candidate.source,
                candidate.kind_hint,
                candidate.license_hint,
                candidate.referer,
            )
            if time.monotonic() > deadline:
                logger.info("Full-text resolution budget exceeded for %s", hit.openalex_id)
                self._log_attempt(
                    FulltextCandidate(url=hit.openalex_id, kind_hint=None, source="work", license_hint=None),
                    "budget_exceeded",
                    "err",
                    None,
                    None,
                    None,
                )
                break
            if settings.agent.respect_robots_txt:
                try:
                    allowed = await robots_allows(candidate.url, user_agent=DEFAULT_UA)
                except Exception as exc:  # noqa: BLE001
                    logger.info("robots.txt check failed for %s: %s", candidate.url, exc)
                    allowed = False
                if not allowed:
                    logger.info("robots.txt disallows %s", candidate.url)
                    self._log_attempt(
                        candidate,
                        "robots_block",
                        "err",
                        None,
                        None,
                        "robots.txt disallows",
                    )
                    continue
            fetch_result = await self._probe_and_fetch(candidate, settings=settings)
            if fetch_result is None:
                logger.info("Candidate %s yielded no usable content", candidate.url)
                continue
            license_value = candidate.license_hint or self._license_from_sources(hit, fetch_result.url)
            if (
                not settings.agent.ignore_license_filter
                and not license_ok(license_value, settings.agent.allow_bronze)
            ):
                logger.info("Rejected candidate %s due to license=%s", fetch_result.url, license_value)
                self._log_attempt(
                    candidate,
                    "license_reject",
                    "err",
                    None,
                    fetch_result.url,
                    f"license={license_value}",
                )
                continue
            fetch_result.license = license_value
            logger.info(
                "Resolved %s full-text for %s via %s (kind=%s license=%s)",
                fetch_result.url,
                hit.openalex_id,
                candidate.source,
                fetch_result.kind,
                license_value,
            )
            if fetch_result.kind in {"pdf", "jats"}:
                return fetch_result
            if fetch_result.kind == "html" and best_html is None:
                best_html = fetch_result
                best_html_source = candidate
                logger.info(
                    "Deferring HTML fallback from %s while continuing to search for PDF/JATS",
                    candidate.source,
                )
                continue
        if best_html:
            logger.info(
                "Returning deferred HTML fallback for %s via %s",
                hit.openalex_id,
                best_html_source.source if best_html_source else "unknown",
            )
            return best_html
        logger.info(
            "Failed to resolve full-text for %s after %s candidates",
            hit.openalex_id,
            len(candidates),
        )
        return None

    async def enumerate_candidates(self, hit: OpenAlexWorkHit) -> list[FulltextCandidate]:
        candidates: list[FulltextCandidate] = []
        seen: set[str] = set()

        def add(
            url: str | None,
            kind_hint: str | None,
            source: str,
            *,
            referer: str | None = None,
            license_hint: str | None = None,
            is_oa: bool | None = None,
            oa_status: str | None = None,
        ) -> None:
            if not url:
                return
            normalized = url.strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(
                FulltextCandidate(
                    url=normalized,
                    kind_hint=kind_hint,
                    source=source,
                    license_hint=license_hint,
                    referer=referer,
                    is_oa=is_oa,
                    oa_status=oa_status,
                )
            )

        def add_location(location: dict[str, Any], tag: str) -> None:
            if not isinstance(location, dict):
                return
            lic = location.get("license")
            pdf = location.get("pdf_url")
            landing = location.get("landing_page_url")
            add(
                pdf,
                "pdf",
                f"openalex_{tag}",
                referer=landing,
                license_hint=lic,
                is_oa=location.get("is_oa"),
                oa_status=location.get("oa_status"),
            )
            add(
                landing,
                "html",
                f"openalex_{tag}",
                referer=None,
                license_hint=lic,
                is_oa=location.get("is_oa"),
                oa_status=location.get("oa_status"),
            )

        if hit.best_oa_location:
            add_location(hit.best_oa_location, "best")
        if hit.primary_location:
            add_location(hit.primary_location, "primary")
        for loc in hit.locations:
            add_location(loc, "locations")
        if isinstance(hit.open_access, dict):
            add(
                hit.open_access.get("oa_url"),
                "html",
                "openalex_oa_url",
                is_oa=hit.open_access.get("is_oa"),
                oa_status=hit.open_access.get("oa_status"),
            )

        transformed: list[FulltextCandidate] = []
        for cand in candidates:
            transformed.append(cand)
            turl = self._transform_repo_pdf(cand.url)
            if turl and turl not in seen:
                seen.add(turl)
                transformed.append(
                    FulltextCandidate(
                        url=turl,
                        kind_hint="pdf",
                        source="repo_transform",
                        license_hint=cand.license_hint,
                        referer=cand.referer or cand.url,
                        is_oa=cand.is_oa,
                        oa_status=cand.oa_status,
                    )
                )
        logger.info("Enumerated %s candidate URLs for %s", len(transformed), hit.openalex_id)
        return transformed

    async def _rescore_and_sort(self, candidates: list[FulltextCandidate], *, settings) -> list[FulltextCandidate]:
        best_oa: list[FulltextCandidate] = []
        other_oa: list[FulltextCandidate] = []
        non_oa: list[FulltextCandidate] = []

        for candidate in candidates:
            if candidate.is_oa is False:
                non_oa.append(candidate)
            elif "openalex_best" in candidate.source:
                best_oa.append(candidate)
            else:
                other_oa.append(candidate)

        preferred = [k.lower() for k in settings.agent.prefer_fulltext_order]
        fmt_rank = {kind: idx for idx, kind in enumerate(preferred)}

        def bucket_key(candidate: FulltextCandidate) -> tuple[int, int]:
            kind = (candidate.kind_hint or "html").lower()
            repo_bonus = 0 if "repo_transform" in candidate.source else 1
            return fmt_rank.get(kind, len(preferred)), repo_bonus

        best_oa_sorted = sorted(best_oa, key=bucket_key)
        other_oa_sorted = sorted(other_oa, key=bucket_key)
        non_oa_sorted = sorted(non_oa, key=bucket_key)

        return best_oa_sorted + other_oa_sorted + non_oa_sorted

    async def _probe_and_fetch(self, candidate: FulltextCandidate, *, settings) -> FulltextFetchResult | None:
        timeout = settings.agent.per_link_timeout_s
        max_pdf_mb = settings.agent.max_pdf_mb
        max_html_mb = settings.agent.max_html_mb
        kind_hint = (candidate.kind_hint or "").lower()

        if kind_hint == "pdf":
            pdf = await self._fetch_pdf_robust(
                candidate.url,
                referer=candidate.referer,
                timeout=timeout,
                max_mb=max_pdf_mb,
                settings=settings,
            )
            if pdf:
                self._log_attempt(candidate, "pdf_hinted", "ok", 200, pdf.url, "robust PDF succeeded")
                return pdf

        jats_url = await self._discover_jats_on_landing(candidate.referer or candidate.url)
        if jats_url and settings.agent.jats_ingest_enabled:
            j = await self._stream_fetch(jats_url, timeout=timeout, max_mb=max_html_mb, settings=settings)
            if j and self._looks_like_jats(j.head, j.content):
                digest = hashlib.sha256(j.content).hexdigest()
                self._log_attempt(candidate, "jats_shortcut", "ok", j.status_code, j.final_url, "PMC JATS via shortcut")
                return FulltextFetchResult(url=j.final_url, kind="jats", content=j.content, license=None, sha256=digest)

        stream = await self._stream_fetch(
            candidate.url,
            timeout=timeout,
            max_mb=max_pdf_mb if kind_hint == "pdf" else max_html_mb,
            settings=settings,
        )
        if stream is None:
            error_note = self._last_stream_error or "stream fetch failed"
            self._log_attempt(candidate, "probe", "err", None, None, error_note)
            if settings.agent.headless_pdf_enabled and self._should_try_headless_on_probe(candidate, error_note):
                headless_target = self._pick_headless_target(candidate)
                pdf = await self._fetch_pdf_headless(headless_target, settings=settings)
                if pdf:
                    self._log_attempt(
                        candidate,
                        "headless_pdf_probe",
                        "ok",
                        200,
                        pdf.url,
                        f"playwright captured from {headless_target}",
                    )
                    return pdf
                else:
                    self._log_attempt(
                        candidate,
                        "headless_pdf_probe",
                        "err",
                        None,
                        None,
                        f"headless_failed target={headless_target}",
                    )
            return None

        ct = (stream.headers.get("Content-Type") or "").split(";")[0].lower()
        cd = stream.headers.get("Content-Disposition")
        head = stream.head
        body = stream.content
        final_url = stream.final_url
        status = stream.status_code

        if self._looks_like_pdf(ct, head, content_disposition=cd):
            digest = hashlib.sha256(body).hexdigest()
            filename = self._filename_from_disposition(cd)
            note = "ephemeral url" if self._looks_ephemeral(final_url) else None
            notes = f"ct={ct}, disp={cd or '-'}"
            if note:
                notes = f"{notes}; {note}"
            self._log_attempt(candidate, "probe_pdf", "ok", status, final_url, notes)
            return FulltextFetchResult(
                url=final_url,
                kind="pdf",
                content=body,
                license=None,
                sha256=digest,
                filename=filename,
            )

        if self._looks_like_jats(head, body) and settings.agent.jats_ingest_enabled:
            digest = hashlib.sha256(body).hexdigest()
            self._log_attempt(candidate, "probe_jats", "ok", status, final_url, f"ct={ct}")
            return FulltextFetchResult(url=final_url, kind="jats", content=body, license=None, sha256=digest)

        if self._looks_like_html(ct, head):
            html_text = body.decode("utf-8", "ignore")

            pdf_from_viewer = self._extract_pdf_from_viewer_html(html_text, base_url=final_url)
            if pdf_from_viewer:
                pdf = await self._fetch_pdf_robust(
                    pdf_from_viewer,
                    referer=final_url,
                    timeout=timeout,
                    max_mb=max_pdf_mb,
                    settings=settings,
                )
                if pdf:
                    self._log_attempt(candidate, "viewer_pdf", "ok", 200, pdf.url, "extracted from viewer")
                    return pdf

            viewer_src = self._find_viewer_src(html_text, base_url=final_url)
            if viewer_src:
                v = await self._stream_fetch(viewer_src, timeout=timeout, max_mb=max_html_mb, settings=settings)
                if v and self._looks_like_html((v.headers.get("Content-Type") or "").split(";")[0].lower(), v.head):
                    vhtml = v.content.decode("utf-8", "ignore")
                    v_pdf_link, v_ranked = self._extract_pdf_links(vhtml, base_url=v.final_url)
                    v_first_pdf = (
                        v_pdf_link
                        or next((u for u in v_ranked if not self._is_supplement_url(u)), None)
                        or (v_ranked[0] if v_ranked else None)
                    )
                    if v_first_pdf:
                        pdf2 = await self._fetch_pdf_robust(
                            v_first_pdf,
                            referer=v.final_url,
                            timeout=timeout,
                            max_mb=max_pdf_mb,
                            settings=settings,
                        )
                        if pdf2:
                            self._log_attempt(candidate, "viewer_html_pdf_link", "ok", 200, pdf2.url, "from viewer page")
                            return pdf2
                    if settings.agent.jats_ingest_enabled:
                        for xurl in self._extract_xml_candidates(vhtml, base_url=v.final_url)[:2]:
                            xres = await self._stream_fetch(xurl, timeout=timeout, max_mb=max_html_mb, settings=settings)
                            if xres and self._looks_like_jats(xres.head, xres.content):
                                digest = hashlib.sha256(xres.content).hexdigest()
                                self._log_attempt(candidate, "viewer_alt_xml", "ok", xres.status_code, xres.final_url, "JATS via viewer")
                                return FulltextFetchResult(
                                    url=xres.final_url,
                                    kind="jats",
                                    content=xres.content,
                                    license=None,
                                    sha256=digest,
                                )
            # Base page PDF surfaces

            pdf_link, ranked_pdf = self._extract_pdf_links(html_text, base_url=final_url)
            first_pdf = (
                pdf_link
                or next((u for u in ranked_pdf if not self._is_supplement_url(u)), None)
                or (ranked_pdf[0] if ranked_pdf else None)
            )
            if first_pdf:
                pdf = await self._fetch_pdf_robust(
                    first_pdf,
                    referer=final_url,
                    timeout=timeout,
                    max_mb=max_pdf_mb,
                    settings=settings,
                )
                if pdf:
                    note = "ephemeral url" if self._looks_ephemeral(first_pdf) else None
                    extra = "from citation/anchor"
                    if note:
                        extra = f"{extra}; {note}"
                    self._log_attempt(candidate, "html_pdf_link", "ok", 200, pdf.url, extra)
                    return pdf

            for u in self._extract_pdfish_candidates(html_text, base_url=final_url, limit=3):
                if self._is_supplement_url(u):
                    continue
                pdf = await self._fetch_pdf_robust(
                    u,
                    referer=final_url,
                    timeout=timeout,
                    max_mb=max_pdf_mb,
                    settings=settings,
                )
                if pdf:
                    note = "ephemeral url" if self._looks_ephemeral(u) else None
                    extra = "disguised download"
                    if note:
                        extra = f"{extra}; {note}"
                    self._log_attempt(candidate, "html_pdfish_link", "ok", 200, pdf.url, extra)
                    return pdf

            for alt in self._derive_print_like_endpoints(final_url):
                alt_stream = await self._stream_fetch(alt, timeout=timeout, max_mb=max_html_mb, settings=settings)
                if not alt_stream:
                    continue
                alt_ct = (alt_stream.headers.get("Content-Type") or "").split(";")[0].lower()
                alt_cd = alt_stream.headers.get("Content-Disposition")
                if self._looks_like_pdf(alt_ct, alt_stream.head, content_disposition=alt_cd):
                    digest = hashlib.sha256(alt_stream.content).hexdigest()
                    filename = self._filename_from_disposition(alt_cd)
                    self._log_attempt(candidate, "print_endpoint_pdf", "ok", alt_stream.status_code, alt_stream.final_url, "print/pdf endpoint")
                    return FulltextFetchResult(
                        url=alt_stream.final_url,
                        kind="pdf",
                        content=alt_stream.content,
                        license=None,
                        sha256=digest,
                        filename=filename,
                    )

            if settings.agent.headless_pdf_enabled:
                pdf = await self._fetch_pdf_headless(final_url, settings=settings)
                if pdf:
                    self._log_attempt(candidate, "headless_pdf", "ok", 200, pdf.url, "playwright captured")
                    return pdf

            if settings.agent.jats_ingest_enabled:
                for xurl in self._extract_xml_candidates(html_text, base_url=final_url)[:2]:
                    xres = await self._stream_fetch(xurl, timeout=timeout, max_mb=max_html_mb, settings=settings)
                    if xres and self._looks_like_jats(xres.head, xres.content):
                        digest = hashlib.sha256(xres.content).hexdigest()
                        self._log_attempt(candidate, "html_alt_xml", "ok", xres.status_code, xres.final_url, "JATS via rel=alternate")
                        return FulltextFetchResult(url=xres.final_url, kind="jats", content=xres.content, license=None, sha256=digest)

            accepted_html, html_signals = self._html_is_good_article(body, return_report=True)
            if settings.agent.html_ingest_enabled and accepted_html:
                if stream.truncated:
                    full = await self._stream_fetch(
                        final_url,
                        timeout=timeout,
                        max_mb=max_html_mb,
                        settings=settings,
                        headers=self._build_headers(accept_pdf=False, referer=None),
                    )
                    if full and self._looks_like_html((full.headers.get("Content-Type") or "").split(";")[0].lower(), full.head):
                        body = full.content
                        final_url = full.final_url
                        status = full.status_code
                digest = hashlib.sha256(body).hexdigest()
                html_signals["host"] = urlparse(final_url).netloc.lower()
                note = (
                    "accepted html; meta={meta_ok} p={p_count} h2h3={h_count} "
                    "article={has_article_tag} words≈{word_count} host={host}"
                ).format(**html_signals)
                self._log_attempt(candidate, "html_accept", "ok", status, final_url, note)
                return FulltextFetchResult(url=final_url, kind="html", content=body, license=None, sha256=digest)

            self._log_attempt(candidate, "html_fallback", "err", status, final_url, "no PDF/JATS and not a good article")
            return None

        self._log_attempt(candidate, "unknown", "err", status, final_url, f"ct={ct}")
        return None

    async def _fetch_pdf_robust(self, url: str, *, referer: str | None, timeout: float, max_mb: int, settings) -> FulltextFetchResult | None:
        max_bytes = max_mb * 1024 * 1024
        host = urlparse(url).netloc.lower()
        prefer_referer = referer and any(host.endswith(h) for h in NEEDS_REFERER_HOSTS)
        headers1 = self._build_headers(accept_pdf=True, referer=referer if prefer_referer else None)
        res = await self._download_pdf_stream(url, headers=headers1, timeout=timeout, max_bytes=max_bytes, settings=settings)
        if res:
            return res
        s1 = await self._stream_fetch(url, timeout=timeout, max_mb=max_mb, settings=settings, headers=headers1)
        _, pow_result = await self._maybe_handle_pmc_pow_gate(
            s1,
            url=url,
            headers=headers1,
            timeout=timeout,
            max_mb=max_mb,
            settings=settings,
        )
        if pow_result:
            return pow_result
        if s1 and self._looks_like_pdf(
            (s1.headers.get("Content-Type") or "").split(";")[0].lower(),
            s1.head,
            content_disposition=s1.headers.get("Content-Disposition"),
        ):
            filename = self._filename_from_disposition(s1.headers.get("Content-Disposition"))
            digest = hashlib.sha256(s1.content).hexdigest()
            return FulltextFetchResult(
                url=s1.final_url,
                kind="pdf",
                content=s1.content,
                license=None,
                sha256=digest,
                filename=filename,
            )
        if referer and not prefer_referer:
            headers2 = self._build_headers(accept_pdf=True, referer=referer)
            res2 = await self._download_pdf_stream(url, headers=headers2, timeout=timeout, max_bytes=max_bytes, settings=settings)
            if res2:
                return res2
            s2 = await self._stream_fetch(url, timeout=timeout, max_mb=max_mb, settings=settings, headers=headers2)
            _, pow_result = await self._maybe_handle_pmc_pow_gate(
                s2,
                url=url,
                headers=headers2,
                timeout=timeout,
                max_mb=max_mb,
                settings=settings,
            )
            if pow_result:
                return pow_result
            if s2 and self._looks_like_pdf(
                (s2.headers.get("Content-Type") or "").split(";")[0].lower(),
                s2.head,
                content_disposition=s2.headers.get("Content-Disposition"),
            ):
                filename = self._filename_from_disposition(s2.headers.get("Content-Disposition"))
                digest = hashlib.sha256(s2.content).hexdigest()
                return FulltextFetchResult(
                    url=s2.final_url,
                    kind="pdf",
                    content=s2.content,
                    license=None,
                    sha256=digest,
                    filename=filename,
                )
        return None

    async def _maybe_handle_pmc_pow_gate(
        self,
        stream: _StreamedContent | None,
        *,
        url: str,
        headers: dict[str, str],
        timeout: float,
        max_mb: int,
        settings,
    ) -> tuple[bool, FulltextFetchResult | None]:
        """Detect and solve PMC POW pages, returning a PDF result if successful."""
        if stream is None:
            return False, None
        final_url = stream.final_url or url
        host = urlparse(final_url).netloc.lower()
        if not host or "pmc" not in host or "ncbi.nlm.nih.gov" not in host:
            return False, None
        try:
            html = stream.content.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            return False, None
        params = parse_pow_page(html)
        if params is None:
            return False, None

        nonce, _ = solve_pow(params.challenge, params.difficulty)
        cookie_value = build_cookie_value(params.challenge, nonce)
        cookie_domain = host or urlparse(url).netloc.lower()
        try:
            self._http_client.cookies.set(
                params.cookie_name,
                cookie_value,
                domain=cookie_domain or None,
                path=params.cookie_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to set PMC POW cookie for %s: %s", cookie_domain, exc)
            return True, None

        logger.info(
            "Solved PMC POW gate for %s (difficulty=%s nonce=%s)",
            cookie_domain,
            params.difficulty,
            nonce,
        )

        max_bytes = max_mb * 1024 * 1024
        res = await self._download_pdf_stream(
            url,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
            settings=settings,
        )
        if res:
            return True, res

        retry_stream = await self._stream_fetch(
            url,
            timeout=timeout,
            max_mb=max_mb,
            settings=settings,
            headers=headers,
        )
        if retry_stream and self._looks_like_pdf(
            (retry_stream.headers.get("Content-Type") or "").split(";")[0].lower(),
            retry_stream.head,
            content_disposition=retry_stream.headers.get("Content-Disposition"),
        ):
            filename = self._filename_from_disposition(retry_stream.headers.get("Content-Disposition"))
            digest = hashlib.sha256(retry_stream.content).hexdigest()
            return True, FulltextFetchResult(
                url=retry_stream.final_url,
                kind="pdf",
                content=retry_stream.content,
                license=None,
                sha256=digest,
                filename=filename,
            )

        return True, None

    async def _download_pdf_stream(self, url: str, *, headers: dict[str, str], timeout: float, max_bytes: int, settings) -> FulltextFetchResult | None:
        """Stream a confirmed-PDF response to disk when sink_to_file is enabled."""
        if not settings.agent.sink_to_file:
            return None
        semaphore = self._ensure_download_semaphore(settings.agent.http_max_concurrent_downloads)
        async with semaphore:
            for attempt in range(2):
                await self._respect_rps(url, settings=settings)
                try:
                    async with self._http_client.stream(
                        "GET",
                        url,
                        timeout=min(timeout, settings.agent.http_timeout_s),
                        follow_redirects=True,
                        headers=headers,
                    ) as resp:
                        status = resp.status_code
                        if status == 429 and attempt == 0:
                            delay = self._retry_delay(resp.headers.get("Retry-After"))
                            await asyncio.sleep(delay)
                            continue
                        if 500 <= status < 600 and attempt == 0:
                            await asyncio.sleep(1.0)
                            continue
                        if status >= 400:
                            return None

                        ct_lc = (resp.headers.get("Content-Type") or "").split(";")[0].lower()
                        cd = resp.headers.get("Content-Disposition")
                        header_says_pdf = (ct_lc in PDF_CONTENT_TYPES) or (ct_lc in OCTET_CONTENT_TYPES and cd and ".pdf" in cd.lower())
                        if not header_says_pdf:
                            return None

                        cl = resp.headers.get("Content-Length")
                        if cl is not None:
                            try:
                                if int(cl) > max_bytes:
                                    logger.info("Skipping %s: Content-Length %s > cap %s", url, cl, max_bytes)
                                    return None
                            except ValueError:
                                pass

                        stream_result = await self._stream_pdf_to_file(resp, max_bytes=max_bytes)
                        if not stream_result:
                            return None
                        file_path, sha256_hex = stream_result
                        filename = self._filename_from_disposition(cd)
                        note = "ephemeral url" if self._looks_ephemeral(str(resp.url)) else None
                        if note:
                            logger.info("PDF appears ephemeral at %s", resp.url)
                        return FulltextFetchResult(
                            url=str(resp.url),
                            kind="pdf",
                            content=None,
                            license=None,
                            sha256=sha256_hex,
                            filename=filename,
                            file_path=file_path,
                        )
                except (httpx.HTTPError, asyncio.CancelledError):
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    return None
        return None

    async def _stream_pdf_to_file(self, response: httpx.Response, *, max_bytes: int) -> tuple[str, str] | None:
        """Stream a PDF to a temp file, returning (path, sha256) or None if invalid."""
        h = hashlib.sha256()
        ct = (response.headers.get("Content-Type") or "").split(";")[0].lower()
        cd = (response.headers.get("Content-Disposition") or "")
        first = True
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            written = 0
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                if first:
                    first = False
                    if (ct in OCTET_CONTENT_TYPES and ".pdf" not in cd.lower()) and not chunk.startswith(b"%PDF-"):
                        tmp.close()
                        try:
                            os.unlink(tmp.name)
                        except OSError:
                            pass
                        return None
                written += len(chunk)
                if written > max_bytes:
                    tmp.close()
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
                    return None
                h.update(chunk)
                tmp.write(chunk)
            return tmp.name, h.hexdigest()

    async def _stream_fetch(
        self,
        url: str,
        *,
        timeout: float,
        max_mb: int,
        settings,
        headers: dict[str, str] | None = None,
    ) -> _StreamedContent | None:
        peek_bytes = settings.agent.http_stream_peek_bytes
        max_bytes = max_mb * 1024 * 1024
        html_peek_limit = 512 * 1024
        semaphore = self._ensure_download_semaphore(settings.agent.http_max_concurrent_downloads)
        self._last_stream_error = None
        async with semaphore:
            for attempt in range(2):
                await self._respect_rps(url, settings=settings)
                try:
                    request_timeout = min(timeout, settings.agent.http_timeout_s)
                    hdrs = headers or {
                        "User-Agent": DEFAULT_UA,
                        "Accept": "text/html,application/xhtml+xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
                        "Accept-Language": "en;q=0.9",
                    }
                    async with self._http_client.stream(
                        "GET",
                        url,
                        timeout=request_timeout,
                        follow_redirects=True,
                        headers=hdrs,
                    ) as response:
                        status = response.status_code
                        if status == 429 and attempt == 0:
                            delay = self._retry_delay(response.headers.get("Retry-After"))
                            await asyncio.sleep(delay)
                            continue
                        if 500 <= status < 600 and attempt == 0:
                            await asyncio.sleep(1.0)
                            continue
                        if status >= 400:
                            self._last_stream_error = f"http_status={status}"
                            return None
                        head = bytearray()
                        content = bytearray()
                        truncated = False
                        ct_lc = (response.headers.get("Content-Type") or "").split(";")[0].lower()
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            if len(head) < peek_bytes:
                                head.extend(chunk[: peek_bytes - len(head)])
                            content.extend(chunk)
                            if len(content) > max_bytes:
                                self._last_stream_error = f"payload_exceeds_{max_mb}mb"
                                return None
                            if ct_lc in HTML_CONTENT_TYPES and len(content) > html_peek_limit:
                                truncated = True
                                break
                        return _StreamedContent(
                            final_url=str(response.url),
                            headers=response.headers,
                            head=bytes(head),
                            content=bytes(content),
                            status_code=status,
                            truncated=truncated,
                        )
                except httpx.HTTPError as exc:
                    self._last_stream_error = f"httpx_error: {exc}"
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    return None
                except asyncio.CancelledError as exc:
                    self._last_stream_error = f"cancelled: {exc}"
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    return None
        if self._last_stream_error is None:
            self._last_stream_error = "max_attempts_exhausted"
        return None

    async def _respect_rps(self, url: str, *, settings) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return
        interval = 1.0 / max(settings.agent.http_per_host_rps, 0.1)
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._host_last_request.get(host)
            if last is not None:
                sleep_for = interval - (now - last)
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._host_last_request[host] = time.monotonic()

    def _ensure_download_semaphore(self, limit: int) -> asyncio.Semaphore:
        limit = max(1, limit)
        if self._download_semaphore is None or self._download_limit != limit:
            self._download_semaphore = asyncio.Semaphore(limit)
            self._download_limit = limit
        return self._download_semaphore

    def _retry_delay(self, retry_after: str | None) -> float:
        try:
            if retry_after is None:
                raise ValueError
            return min(float(retry_after), 30.0)
        except (TypeError, ValueError):
            return 1.5

    def _looks_like_pdf(self, content_type: str, head: bytes, *, content_disposition: str | None = None) -> bool:
        ct = (content_type or "").lower()
        if ct in PDF_CONTENT_TYPES:
            return True
        if ct in OCTET_CONTENT_TYPES:
            if head.startswith(b"%PDF-"):
                return True
            if content_disposition and ".pdf" in content_disposition.lower():
                return True
            return False
        return head.startswith(b"%PDF-")

    def _looks_like_html(self, content_type: str, head: bytes) -> bool:
        if content_type in HTML_CONTENT_TYPES:
            return True
        cleaned = head.lstrip().lower()
        return cleaned.startswith(b"<!doctype html") or b"<html" in cleaned[:512]

    def _looks_like_jats(self, head: bytes, body: bytes) -> bool:
        sample = (head or body[:4096]).lower()
        if b"<?xml" in sample or b"<article" in sample:
            chunk = body[:8192].lower()
            return b"<article" in chunk and (b"<body" in chunk or b"<front" in chunk)
        return False

    def _html_is_good_article(self, body: bytes, *, return_report: bool = False):
        sample = body[:250_000].decode("utf-8", "ignore")
        lower = sample.lower()
        p_count = lower.count("<p")
        h_count = lower.count("<h2") + lower.count("<h3")
        has_meta = any(t in lower for t in ("schema.org/scholarlyarticle", "citation_title", "citation_doi"))
        has_article_tag = any(tag in lower for tag in ("<article", "<section", 'role="article"'))
        words = len(
            re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", sample, flags=re.I))
            .split()
        )
        score = 0
        if has_meta:
            score += 2
        if p_count > 30:
            score += 2
        if has_article_tag:
            score += 1
        if h_count > 6:
            score += 1
        accepted = score >= 3
        if not return_report:
            return accepted
        signals = {
            "meta_ok": has_meta,
            "p_count": p_count,
            "h_count": h_count,
            "has_article_tag": has_article_tag,
            "word_count": words,
        }
        return accepted, signals

    def _is_supplement_url(self, url: str) -> bool:
        low = url.lower()
        return any(token in low for token in _SUPP_TOKENS)

    def _looks_ephemeral(self, url: str) -> bool:
        low = url.lower()
        return any(tok in low for tok in ("token=", "x-amz-signature", "x-amz-security-token", "expires=", "signature=", "sig="))

    def _should_try_headless_on_probe(self, candidate: FulltextCandidate, error_note: str | None) -> bool:
        if not error_note:
            return False
        note = error_note.lower()
        if "http_status=403" in note or "http_status=401" in note:
            return True
        return False

    def _pick_headless_target(self, candidate: FulltextCandidate) -> str:
        url = candidate.url
        doi_like = url.startswith("https://doi.org/") or url.startswith("http://doi.org/")
        if doi_like and candidate.referer:
            return candidate.referer
        return url

    def _find_viewer_src(self, html: str, *, base_url: str) -> str | None:
        match = _VIEWER_FILE_RE.search(html)
        if match:
            return urljoin(base_url, match.group(0))
        for rex in (_IFRAME_RE, _EMBED_RE, _OBJECT_RE):
            maybe = rex.search(html)
            if maybe:
                return urljoin(base_url, maybe.group(1))
        return None

    def _extract_pdf_from_viewer_html(self, html: str, *, base_url: str) -> str | None:
        match = _VIEWER_FILE_RE.search(html)
        if match:
            raw = match.group(1)
            try:
                raw = unquote(raw)
            except Exception:  # noqa: BLE001
                pass
            if "%25" in raw:
                try:
                    raw = unquote(raw)
                except Exception:  # noqa: BLE001
                    pass
            return urljoin(base_url, raw)
        for rex in (_IFRAME_RE, _EMBED_RE, _OBJECT_RE):
            maybe = rex.search(html)
            if maybe:
                src = urljoin(base_url, maybe.group(1))
                if src.lower().endswith(".pdf") or "format=pdf" in src.lower() or "pdf=1" in src.lower():
                    return src
                return src
        return None

    def _extract_xml_candidates(self, html: str, *, base_url: str) -> list[str]:
        out: list[str] = []
        for match in _XML_ALT_LINK_RE.finditer(html):
            out.append(urljoin(base_url, match.group(1).strip()))
        for match in _A_XML_RE.finditer(html):
            out.append(urljoin(base_url, match.group(1).strip()))
        return out

    def _extract_pdf_links(self, html: str, *, base_url: str) -> tuple[str | None, list[str]]:
        strong_pdf = None
        ranked: list[tuple[int, str]] = []
        for rex in (_PDF_ALT_LINK_RE, _META_CITATION_PDF_RE):
            match = rex.search(html)
            if match:
                strong_pdf = urljoin(base_url, match.group(1).strip())
                break
        for link in _A_PDF_RE.finditer(html):
            href = urljoin(base_url, link.group(1).strip())
            text = (link.group(2) or "").lower()
            score = 10
            if "full text" in text or "article" in text or "download" in text:
                score += 10
            if any(key in text for key in ("supp", "appendix", "supporting")) or self._is_supplement_url(href):
                score -= 10
            ranked.append((score, href))
        ranked.sort(reverse=True)
        return strong_pdf, [u for _, u in ranked]

    def _extract_pdfish_candidates(self, html: str, *, base_url: str, limit: int = 3) -> list[str]:
        scored: list[tuple[int, str]] = []
        for link in _A_HREF_RE.finditer(html):
            href = urljoin(base_url, (link.group(1) or "").strip())
            parsed = urlparse(href)
            if parsed.scheme and parsed.scheme not in ("http", "https"):
                continue
            text = (link.group(2) or "").lower()
            url_l = href.lower()
            if any(t in url_l for t in ("#", "/cite", "/references", "/ref", "/metrics", "/share", "/login", "/register")):
                continue
            score = 0
            if any(t in text for t in ("download pdf", "pdf", "article pdf", "full text pdf",'view pdf')):
                score += 25
            if "download" in text:
                score += 10
            if "full text" in text or "article" in text:
                score += 6
            if any(t in url_l for t in ("viewcontent", "cgi", "download", "action=download", "format=pdf", "pdf=1")):
                score += 20
            if url_l.endswith(".pdf"):
                score += 30
            if self._is_supplement_url(href) or any(t in text for t in ("supplement", "appendix", "supporting")):
                score -= 20
            if score > 0:
                scored.append((score, href))
        scored.sort(reverse=True)
        out: list[str] = []
        seen: set[str] = set()
        for _, url in scored:
            if url not in seen:
                out.append(url)
                seen.add(url)
            if len(out) >= limit:
                break
        return out

    def _derive_print_like_endpoints(self, url: str) -> list[str]:
        return [
            url + ("&" if "?" in url else "?") + "format=print",
            url + ("&" if "?" in url else "?") + "print=1",
            url.rstrip("/") + "/print",
            url.rstrip("/") + "/pdf",
        ]

    async def _discover_jats_on_landing(self, url: str) -> str | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if "ncbi.nlm.nih.gov" in host and "/pmc/articles/" in parsed.path:
            return url.rstrip("/") + "?format=flat"
        return None

    def _transform_repo_pdf(self, url: str) -> str | None:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
        netloc = parsed.netloc.lower()
        path = parsed.path
        if "arxiv.org" in netloc:
            match = re.search(r"/abs/(?P<id>[^/?#]+)", path)
            if match:
                return f"https://arxiv.org/pdf/{match.group('id')}.pdf"
            if "/pdf/" in path and not path.lower().endswith(".pdf"):
                return f"https://arxiv.org{path}.pdf"
        if "biorxiv.org" in netloc or "medrxiv.org" in netloc:
            if not path.endswith(".full.pdf"):
                return f"https://{netloc}{path}.full.pdf"
        if "ncbi.nlm.nih.gov" in netloc and "/pmc/articles/" in path:
            base = url.rstrip("/")
            if base.endswith("/pdf"):
                return f"{base}.pdf"
            if not base.endswith(".pdf"):
                return f"{base}/pdf"
        if "europepmc.org" in netloc:
            if "/article/PMC/" in path and not path.endswith("/pdf"):
                return f"https://europepmc.org{path}/pdf"
        return None

    async def _fetch_pdf_headless(self, landing_url: str, *, settings) -> FulltextFetchResult | None:
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception:  # noqa: BLE001
            return None
        async with async_playwright() as playwright:
            browser = None
            try:
                browser = await playwright.chromium.launch(headless=settings.agent.headless)
                selectors = [
                    "a:has-text('Download PDF'):visible",
                    "button:has-text('Download PDF'):visible",
                    "[role=button]:has-text('Download PDF'):visible",
                    "a:has-text('View PDF'):visible",
                    "button:has-text('View PDF'):visible",
                    "[role=button]:has-text('View PDF'):visible",
                    "a:has-text('PDF'):visible",
                    "button:has-text('PDF'):visible",
                    "[role=button]:has-text('PDF'):visible",
                    "a[href$='.pdf']:visible",
                    "a[href*='/pdf']:visible",
                    "a[href$='.pdf']",
                ]
                for attempt, ua in enumerate(HEADLESS_UA_CHAIN, start=1):
                    logger.debug("Headless attempt %s for %s with UA=%s", attempt, landing_url, ua.split(" (", 1)[0])
                    ctx = await browser.new_context(
                        user_agent=ua,
                        extra_http_headers={"Accept-Language": "en;q=0.9"},
                    )
                    page = await ctx.new_page()
                    holder: dict[str, Any] = {"resp": None}

                    def _capture(resp) -> None:
                        ct = (resp.headers.get("content-type") or "").lower()
                        cd = resp.headers.get("content-disposition") or ""
                        if ("application/pdf" in ct) or ("application/octet-stream" in ct and ".pdf" in cd.lower()):
                            if holder["resp"] is None:
                                holder["resp"] = resp

                    page.on("response", _capture)
                    try:
                        await page.goto(landing_url, wait_until="domcontentloaded", timeout=int(settings.agent.http_timeout_s * 1000))
                    except Exception as exc:  # noqa: BLE001
                        logger.info("Headless goto failed for %s (UA=%s): %s", landing_url, ua, exc)
                        await ctx.close()
                        continue
                    clicked = False
                    for sel in selectors:
                        try:
                            loc = page.locator(sel).first
                            if await loc.count() == 0:
                                continue
                            await loc.click(timeout=1500)
                            clicked = True
                            break
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("Headless click failed (sel=%s, UA=%s) for %s: %s", sel, ua, landing_url, exc)
                            continue
                    if not clicked:
                        logger.info("Headless fetch saw no clickable PDF elements for %s (UA=%s)", landing_url, ua)
                    for _ in range(20):
                        if holder["resp"] is not None:
                            break
                        await asyncio.sleep(0.2)
                    resp = holder["resp"]
                    body = await resp.body() if resp else None
                    await ctx.close()
                    if not resp or body is None:
                        logger.debug("Headless attempt (UA=%s) for %s saw no PDF response", ua, landing_url)
                        continue
                    ct = (resp.headers.get("content-type") or "").split(";")[0].lower()
                    cd = resp.headers.get("content-disposition")
                    if not self._looks_like_pdf(ct, body[:2048], content_disposition=cd):
                        logger.debug("Headless attempt (UA=%s) for %s yielded non-PDF content-type=%s", ua, landing_url, ct)
                        continue
                    filename = self._filename_from_disposition(cd)
                    digest = hashlib.sha256(body).hexdigest()
                    final_url = str(resp.url)
                    logger.info("Headless PDF fetch succeeded for %s via UA=%s url=%s", landing_url, ua, final_url)
                    await browser.close()
                    return FulltextFetchResult(url=final_url, kind="pdf", content=body, license=None, sha256=digest, filename=filename)
                await browser.close()
                return None
            except Exception as exc:  # noqa: BLE001
                logger.info("Playwright fallback failed for %s: %s", landing_url, exc)
                try:
                    if browser:
                        await browser.close()
                except Exception:  # noqa: BLE001
                    pass
                return None

    def _license_from_sources(self, hit: OpenAlexWorkHit, url: str) -> str | None:
        matched_location: dict[str, Any] | None = None
        for location in self._iter_locations(hit):
            if not isinstance(location, dict):
                continue
            values = [
                location.get("pdf_url"),
                location.get("landing_page_url"),
                location.get("url"),
            ]
            if any(self._urls_equivalent(url, candidate) for candidate in values if candidate):
                matched_location = location
                break
        return resolve_license(matched_location)

    def _iter_locations(self, hit: OpenAlexWorkHit) -> Iterable[dict[str, Any]]:
        if hit.best_oa_location:
            yield hit.best_oa_location
        if hit.primary_location:
            yield hit.primary_location
        for loc in hit.locations:
            yield loc

    def _urls_equivalent(self, left: str, right: str | None) -> bool:
        return self._normalize_for_compare(left) == self._normalize_for_compare(right or "")

    def _normalize_for_compare(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{(parsed.scheme or 'https')}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"

    def _filename_from_disposition(self, content_disposition: str | None) -> str | None:
        if not content_disposition:
            return None
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition, re.I)
        if not match:
            return None
        name = match.group(1)
        try:
            return unquote(name)
        except Exception:  # noqa: BLE001
            return name

    def _build_headers(self, *, accept_pdf: bool, referer: str | None) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": DEFAULT_UA, "Accept-Language": "en;q=0.9"}
        headers["Accept"] = "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8" if accept_pdf else "text/html,application/xhtml+xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7"
        if referer:
            headers["Referer"] = referer
        return headers

    def _log_attempt(
        self,
        cand: FulltextCandidate,
        phase: str,
        status: str,
        http_status: int | None,
        final_url: str | None,
        notes: str | None,
    ) -> None:
        self.last_attempts.append(
            FetchAttempt(
                candidate_url=cand.url,
                source=cand.source,
                phase=phase,
                status=status,
                http_status=http_status,
                final_url=final_url,
                notes=notes,
                is_oa=cand.is_oa,
                oa_status=cand.oa_status,
            )
        )
