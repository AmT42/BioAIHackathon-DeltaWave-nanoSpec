#!/usr/bin/env python3
"""
Fetch a PDF behind a Cloudflare challenge using Playwright.

Usage:
  python scripts/fetch_pdf_playwright.py --url <pdf_or_landing_url> --out <output_path>
  python scripts/fetch_pdf_playwright.py --doi <doi> --out <output_path>
  python scripts/fetch_pdf_playwright.py --openalex <work_id> --out <output_path>

Notes:
  - For --doi or --openalex, the script resolves the direct PDF URL (if available)
    via the OpenAlex Works API.
  - Requires `pip install playwright` and running `playwright install chromium` once.
"""

import argparse
import json
import os
import re
import sys
from typing import Optional

import urllib.parse
import urllib.request


OPENALEX_WORKS_API = "https://api.openalex.org/works/"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_pdf_url_from_openalex(work_or_doi: str) -> Optional[str]:
    """
    Accepts an OpenAlex work id (e.g., W2766980350 or https://openalex.org/W2766980350)
    or a DOI (e.g., 10.1002/oby.21939 or https://doi.org/10.1002/oby.21939), and tries
    to find a PDF URL via the OpenAlex API.
    """

    # Normalize input
    w = work_or_doi.strip()
    if w.startswith("https://openalex.org/"):
        w = w.rsplit("/", 1)[-1]
    if w.lower().startswith("https://doi.org/"):
        w = w.rsplit("/", 1)[-1]

    # If it looks like a DOI, hit the works API by DOI
    if "/" in w and not w.startswith("W"):
        # DOI path
        encoded_doi = urllib.parse.quote(w, safe="")
        works_url = f"{OPENALEX_WORKS_API}doi:{encoded_doi}"
    else:
        # OpenAlex work id
        works_url = f"{OPENALEX_WORKS_API}{w}"

    data = fetch_json(works_url)

    # Prefer primary_location.pdf_url, fallback to open_access.oa_url
    candidate_urls = []
    try:
        pl = data.get("primary_location") or {}
        if pl.get("pdf_url"):
            candidate_urls.append(pl["pdf_url"])
        if pl.get("landing_page_url"):
            candidate_urls.append(pl["landing_page_url"])
    except Exception:
        pass
    try:
        oa = data.get("open_access") or {}
        if oa.get("oa_url"):
            candidate_urls.append(oa["oa_url"])
    except Exception:
        pass

    # De-duplicate while preserving order
    seen = set()
    ordered = []
    for u in candidate_urls:
        if u and u not in seen:
            seen.add(u)
            ordered.append(u)

    return ordered[0] if ordered else None


def save_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def fetch_pdf_with_playwright(url: str, out_path: str, timeout_ms: int = 120000) -> None:
    from playwright.sync_api import sync_playwright

    # Some Wiley endpoints serve the PDF in the main frame; we hook into responses
    # and capture the first with Content-Type application/pdf.
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/118.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
            accept_downloads=True,
        )
        page = context.new_page()

        pdf_bytes: Optional[bytes] = None

        def handle_response(resp):
            nonlocal pdf_bytes
            try:
                ctype = (resp.headers.get("content-type") or "").lower()
            except Exception:
                ctype = ""
            if pdf_bytes is None and ("application/pdf" in ctype or resp.url.lower().endswith(".pdf")):
                try:
                    pdf_bytes = resp.body()
                except Exception:
                    pass

        page.on("response", handle_response)

        # Try handling as a direct download first. Some publishers trigger a download
        # immediately for /pdf or /pdfdirect URLs. If this doesn't happen within the
        # timeout, fall back to response sniffing below.
        try:
            with page.expect_download(timeout=timeout_ms) as dl_info:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            download = dl_info.value
            # Save directly to requested path
            download.save_as(out_path)
            browser.close()
            return
        except Exception:
            # If no download happened, continue with response-based capture.
            pass

        # If direct download didn't happen, try via DOI landing page -> click PDF
        # Derive a DOI-based landing page if possible
        landing_url = None
        if "onlinelibrary.wiley.com" in url and "/doi/" in url:
            m = re.search(r"/doi/(?:pdf|pdfdirect|epdf)/(.+)$", url)
            if m:
                landing_url = f"https://doi.org/{m.group(1)}"
        if landing_url:
            try:
                page.goto(landing_url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Allow any CF challenge to complete
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception:
                    pass
                # Locate a PDF link
                selectors = [
                    'a[href*="/doi/pdfdirect/"]',
                    'a[href*="/doi/pdf/"]',
                    'a:has-text("PDF")',
                    'text=PDF',
                ]
                found = None
                for sel in selectors:
                    try:
                        element = page.wait_for_selector(sel, timeout=timeout_ms)
                        if element:
                            found = element
                            break
                    except Exception:
                        continue
                if found:
                    href = found.get_attribute("href")
                    # Make absolute if needed
                    if href and href.startswith("/"):
                        base = "https://onlinelibrary.wiley.com"
                        href = base + href
                    if href:
                        try:
                            with page.expect_download(timeout=timeout_ms) as dl_info:
                                found.click()
                            download = dl_info.value
                            download.save_as(out_path)
                            browser.close()
                            return
                        except Exception:
                            # As a final push, go directly
                            try:
                                with page.expect_download(timeout=timeout_ms) as dl_info:
                                    page.goto(href, wait_until="domcontentloaded", timeout=timeout_ms)
                                download = dl_info.value
                                download.save_as(out_path)
                                browser.close()
                                return
                            except Exception:
                                pass
            except Exception:
                pass

        # In some cases, the PDF is loaded after challenge completes; wait for it.
        # First try quick wait via predicate on responses.
        if pdf_bytes is None:
            try:
                page.wait_for_response(lambda r: "application/pdf" in (r.headers.get("content-type") or "").lower(), timeout=timeout_ms)
            except Exception:
                pass
        # Fallback: wait for network idle to allow any redirects/challenges to finish
        if pdf_bytes is None:
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass

        if pdf_bytes is None:
            # Sometimes sites embed a viewer; try to find an <embed> or <iframe> with pdf
            try:
                # Grab any iframe srcs that look like PDFs and fetch one directly via page.request
                frames = [f for f in page.frames]
                for fr in frames:
                    try:
                        ct = (fr.url or "").lower()
                    except Exception:
                        ct = ""
                    if ct.endswith(".pdf"):
                        resp = page.request.get(fr.url, timeout=timeout_ms)
                        if resp.ok and "application/pdf" in (resp.headers.get("content-type") or "").lower():
                            pdf_bytes = resp.body()
                            break
            except Exception:
                pass

        if pdf_bytes is None:
            # As a last attempt, try direct GET via page.request to the original URL
            try:
                resp = page.request.get(url, timeout=timeout_ms)
                if resp.ok and "application/pdf" in (resp.headers.get("content-type") or "").lower():
                    pdf_bytes = resp.body()
            except Exception:
                pass

        browser.close()

        if not pdf_bytes:
            raise RuntimeError("Failed to retrieve PDF bytes; site may still be blocking automation.")

        save_bytes(out_path, pdf_bytes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a PDF using Playwright, handling JS challenges.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="Direct PDF or landing URL")
    g.add_argument("--doi", help="DOI string or DOI URL")
    g.add_argument("--openalex", help="OpenAlex work id or URL")
    parser.add_argument("--out", required=True, help="Output file path (will be overwritten)")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds")
    args = parser.parse_args(argv)

    url: Optional[str] = None
    if args.url:
        url = args.url
    elif args.doi:
        url = resolve_pdf_url_from_openalex(args.doi)
    elif args.openalex:
        url = resolve_pdf_url_from_openalex(args.openalex)

    if not url:
        print("Could not resolve a candidate PDF URL.", file=sys.stderr)
        return 2

    # Prefer explicit PDF endpoints if we got a landing page URL
    # For Wiley, if we have a DOI landing page, try to build pdfdirect link.
    if "onlinelibrary.wiley.com/doi/" in url and "/pdf" not in url:
        m = re.search(r"onlinelibrary\.wiley\.com/doi/(?:full|abs|epdf|pdfdirect)?/([\w\./-]+)", url)
        if m:
            doi_tail = m.group(1)
            url = f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi_tail}"

    timeout_ms = int(args.timeout * 1000)
    fetch_pdf_with_playwright(url, args.out, timeout_ms=timeout_ms)
    print(f"Saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
