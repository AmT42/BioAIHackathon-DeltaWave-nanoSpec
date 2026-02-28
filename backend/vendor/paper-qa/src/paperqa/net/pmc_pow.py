"""Utilities for solving the PMC proof-of-work (POW) download gate."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

__all__ = [
    "PmcPowParams",
    "parse_pow_page",
    "solve_pow",
    "build_cookie_value",
]


@dataclass(frozen=True, slots=True)
class PmcPowParams:
    """Structured POW metadata extracted from the PMC gate HTML."""

    challenge: str
    difficulty: int
    cookie_name: str
    cookie_path: str


_POW_CHALLENGE_RE = re.compile(r'POW_CHALLENGE\s*=\s*"([^"]+)"')
_POW_DIFFICULTY_RE = re.compile(r'POW_DIFFICULTY\s*=\s*"(\d+)"')
_POW_COOKIE_NAME_RE = re.compile(r'POW_COOKIE_NAME\s*=\s*"([^"]+)"')
_POW_COOKIE_PATH_RE = re.compile(r'POW_COOKIE_PATH\s*=\s*"([^"]+)"')


def parse_pow_page(html: str) -> PmcPowParams | None:
    """Return POW parameters if the HTML corresponds to the PMC POW gate."""
    if "POW_CHALLENGE" not in html:
        return None
    challenge_match = _POW_CHALLENGE_RE.search(html)
    difficulty_match = _POW_DIFFICULTY_RE.search(html)
    cookie_name_match = _POW_COOKIE_NAME_RE.search(html)
    cookie_path_match = _POW_COOKIE_PATH_RE.search(html)
    if not all((challenge_match, difficulty_match, cookie_name_match, cookie_path_match)):
        return None
    return PmcPowParams(
        challenge=challenge_match.group(1),
        difficulty=int(difficulty_match.group(1)),
        cookie_name=cookie_name_match.group(1),
        cookie_path=cookie_path_match.group(1),
    )


def solve_pow(challenge: str, difficulty: int) -> tuple[int, str]:
    """Find a nonce whose SHA256 hash matches the PMC difficulty requirement."""
    prefix = "0" * max(difficulty, 1)
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}{nonce}".encode()).hexdigest()
        if digest.startswith(prefix):
            return nonce, digest
        nonce += 1


def build_cookie_value(challenge: str, nonce: int) -> str:
    """Format the cookie value expected by PMC."""
    return f"{challenge},{nonce}"
