#!/usr/bin/env python3
"""Download PDF files from PMC by solving their proof-of-work gate automatically."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from paperqa.net.pmc_pow import build_cookie_value, parse_pow_page, solve_pow
except ModuleNotFoundError:  # pragma: no cover - fallback when package deps missing
    from dataclasses import dataclass
    import re
    from typing import Optional

    @dataclass(frozen=True)
    class _Params:
        challenge: str
        difficulty: int
        cookie_name: str
        cookie_path: str

    _POW_CHALLENGE_RE = re.compile(r'POW_CHALLENGE\s*=\s*"([^"]+)"')
    _POW_DIFFICULTY_RE = re.compile(r'POW_DIFFICULTY\s*=\s*"(\d+)"')
    _POW_COOKIE_NAME_RE = re.compile(r'POW_COOKIE_NAME\s*=\s*"([^"]+)"')
    _POW_COOKIE_PATH_RE = re.compile(r'POW_COOKIE_PATH\s*=\s*"([^"]+)"')

    def parse_pow_page(html: str) -> Optional[_Params]:
        if "POW_CHALLENGE" not in html:
            return None
        m1 = _POW_CHALLENGE_RE.search(html)
        m2 = _POW_DIFFICULTY_RE.search(html)
        m3 = _POW_COOKIE_NAME_RE.search(html)
        m4 = _POW_COOKIE_PATH_RE.search(html)
        if not all((m1, m2, m3, m4)):
            return None
        return _Params(
            challenge=m1.group(1),
            difficulty=int(m2.group(1)),
            cookie_name=m3.group(1),
            cookie_path=m4.group(1),
        )

    def solve_pow(challenge: str, difficulty: int):
        prefix = "0" * max(difficulty, 1)
        nonce = 0
        while True:
            digest = __import__("hashlib").sha256(f"{challenge}{nonce}".encode()).hexdigest()
            if digest.startswith(prefix):
                return nonce, digest
            nonce += 1

    def build_cookie_value(challenge: str, nonce: int) -> str:
        return f"{challenge},{nonce}"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Safari/537.36"
)

def fetch_pdf(url: str, destination: Path) -> None:
    """Download a PDF from PMC, solving the proof-of-work gate if necessary."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    response = session.get(url, allow_redirects=True)
    response.raise_for_status()

    if "pdf" in response.headers.get("Content-Type", "").lower():
        destination.write_bytes(response.content)
        return

    # If the response is HTML, assume it's the POW gate and extract the parameters.
    params = parse_pow_page(response.text)
    if params is None:
        raise RuntimeError("Response was HTML but did not match the PMC POW gate.")

    nonce, _ = solve_pow(params.challenge, params.difficulty)
    cookie_value = build_cookie_value(params.challenge, nonce)
    domain = urlparse(response.url).hostname
    session.cookies.set(params.cookie_name, cookie_value, path=params.cookie_path, domain=domain)

    proofed_response = session.get(url, allow_redirects=True)
    proofed_response.raise_for_status()

    content_type = proofed_response.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type:
        raise RuntimeError(f"Expected PDF content, received '{content_type}' instead.")

    destination.write_bytes(proofed_response.content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="PMC PDF URL (e.g., https://pmc.ncbi.nlm.nih.gov/.../file.pdf)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path for the PDF (defaults to the basename from the URL).",
    )
    args = parser.parse_args(argv)

    dest = args.output or Path(urlparse(args.url).path.split("/")[-1] or "pmc.pdf")
    dest.parent.mkdir(parents=True, exist_ok=True)

    fetch_pdf(args.url, dest)
    print(f"Downloaded {args.url} -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
