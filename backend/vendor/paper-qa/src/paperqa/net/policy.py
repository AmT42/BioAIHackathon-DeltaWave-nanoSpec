"""Download policy helpers for PaperQA full-text resolution."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

__all__ = [
    "domain_allowed",
    "license_ok",
    "resolve_license",
    "robots_allows",
]


_ROBOTS_TTL = 60 * 60  # seconds
_ROBOTS_CACHE: dict[str, "RobotCacheEntry"] = {}
_ROBOTS_LOCK = asyncio.Lock()
_HTTP_CLIENT: httpx.AsyncClient | None = None


@dataclass(slots=True)
class RobotCacheEntry:
    parser: RobotFileParser
    expires_at: float


def domain_allowed(url: str, allow: set[str], block: set[str]) -> bool:
    """Return True when URL passes allow/block domain policy."""
    host = urlparse(url).netloc.lower()
    if not host:
        return False
    if any(host.endswith(banned) for banned in block if banned):
        return False
    if not allow:
        return True
    return any(host.endswith(permitted) for permitted in allow if permitted)


def license_ok(license_str: str | None, allow_bronze: bool) -> bool:
    """Check whether a license string is acceptable under current policy."""
    if not license_str:
        return allow_bronze
    lic = license_str.lower()
    approved = ("cc-by", "cc0", "cc-by-sa", "cc-by-nc", "cc-by-nd")
    return any(token in lic for token in approved)


def resolve_license(openalex_location: dict[str, Any] | None) -> str | None:
    """Return the best available license string from OpenAlex metadata."""
    for src in (openalex_location,):
        if not src:
            continue
        license_val = (
            src.get("license")
            or src.get("license_url")
            or src.get("license_type")
            or ""
        )
        if isinstance(license_val, str):
            cleaned = license_val.strip()
            if cleaned:
                return cleaned
    return None


async def robots_allows(url: str, user_agent: str = "PaperQA/oss") -> bool:
    """Check whether robots.txt allows fetching the provided URL."""
    parsed = urlparse(url if "://" in url else f"https://{url.lstrip('/')}")
    if not parsed.netloc:
        return False
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    async with _ROBOTS_LOCK:
        entry = _ROBOTS_CACHE.get(parsed.netloc)
        if entry and entry.expires_at > time.monotonic():
            return entry.parser.can_fetch(user_agent, url)

    client = await _get_http_client()
    try:
        response = await client.get(robots_url, timeout=10.0)
    except httpx.HTTPError:
        return False
    if response.status_code >= 400:
        return False

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    async with _ROBOTS_LOCK:
        _ROBOTS_CACHE[parsed.netloc] = RobotCacheEntry(
            parser=parser, expires_at=time.monotonic() + _ROBOTS_TTL
        )
    return parser.can_fetch(user_agent, url)


async def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=10.0)
    return _HTTP_CLIENT
