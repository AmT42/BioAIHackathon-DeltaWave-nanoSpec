#!/usr/bin/env python3
"""
Build a messy OA link dataset to exercise PDF/HTML extraction.

What it does:
- Queries OpenAlex for OA works and their locations (publisher/repository/preprint).
- Heuristically labels links into your A/B/C/D/E cases based on URL patterns and host.
- Selects a diverse sample covering different publishers, host types, versions, and cases.
- Writes JSONL to data/oa_messy_links_50.jsonl

Notes:
- Requires network access. OpenAlex appreciates a mailto param.
- Heuristics are best-effort; they’re meant to stress parsers, not be perfect.
"""

import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request


OPENALEX_URL = (
    "https://api.openalex.org/works?"
    "filter=is_oa:true,from_publication_date:2015-01-01&"
    "per_page=200&"
    "sort=cited_by_count:desc"
)


def http_get(url: str, headers=None):
    req = Request(url, headers=headers or {"User-Agent": "paper-qa-oa-dataset/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_openalex_pages(pages=3, mailto="paper-qa@example.com", seed=None):
    if seed is not None:
        random.seed(seed)
    works = []
    page = 1
    while page <= pages:
        url = f"{OPENALEX_URL}&page={page}&mailto={mailto}"
        data = http_get(url)
        obj = json.loads(data)
        works.extend(obj.get("results", []))
        if not obj.get("results") or not obj.get("meta", {}).get("next_cursor"):
            # OpenAlex supports cursor pagination too; for now simple pages suffice.
            pass
        page += 1
        # be a good API citizen
        time.sleep(0.5)
    return works


REFERER_HOSTS = {
    # Commonly need Referer/cookies for direct PDF
    "dl.acm.org",
    "link.springer.com",
    "onlinelibrary.wiley.com",
    "www.tandfonline.com",
    "journals.sagepub.com",
    "www.nature.com",
    "www.cambridge.org",
    "academic.oup.com",
    "aacrjournals.org",
    "pnas.org",
    "www.science.org",
}

OCTET_STREAM_HOSTS = {
    # Often serve application/octet-stream for PDFs
    "zenodo.org",
    "osf.io",
    "figshare.com",
    "ieeexplore.ieee.org",  # stamp routes
}

VIEWER_HOST_HINTS = {
    # Often use viewer shells (pdf.js or stamped viewers)
    "www.science.org",
    "ieeexplore.ieee.org",
    "www.nature.com",
}

CLEAN_HTML_HOSTS = {
    # Typically have solid HTML full text
    "journals.plos.org",
    "elifesciences.org",
    "www.frontiersin.org",
    "bmcbioinformatics.biomedcentral.com",
    "biomedcentral.com",
    "www.hindawi.com",
    "www.mdpi.com",
    "peerj.com",
    "royalsocietypublishing.org",
    "www.bmj.com",
    "f1000research.com",
    "wellcomeopenresearch.org",
    "gatesopenresearch.org",
}

JATS_HOSTS = {
    "www.ncbi.nlm.nih.gov",  # PMC
    "europepmc.org",
}


def normalize_url(u):
    if not u:
        return None
    # Some OpenAlex fields can be protocol-relative or missing scheme
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "https://" + u


def classify_cases(landing_url: str | None, pdf_url: str | None):
    tags = set()
    lhost = urlparse(landing_url).netloc if landing_url else None
    phost = urlparse(pdf_url).netloc if pdf_url else None
    if pdf_url:
        pu = normalize_url(pdf_url)
        p = urlparse(pu)
        q = parse_qs(p.query)
        path = p.path or ""
        has_pdf_ext = path.lower().endswith(".pdf")
        has_html_ext = path.lower().endswith(".html")

        # A-cases
        if has_pdf_ext and not p.query:
            tags.add("A1")
        if p.query:
            # tokenized or pre-signed
            if any(k in q for k in ("token", "X-Amz-Signature", "Signature", "Expires", "AWSAccessKeyId", "sv")):
                tags.add("A2")
            else:
                tags.add("A2")
        if not has_pdf_ext and ("download" in path or "document" in path or "stamp" in path or "/pdf" in path):
            tags.add("A3")
        if has_html_ext and ("pdf" in path or "file" in q or any("pdf" in v for vs in q.values() for v in vs)):
            tags.add("A4")
        if any(h in p.netloc for h in OCTET_STREAM_HOSTS):
            tags.add("A5")
        if any(h in p.netloc for h in REFERER_HOSTS):
            tags.add("A6")
        # A7 via DOI resolver elsewhere

    # B-cases - from hosts and presence of links
    if landing_url or pdf_url:
        if pdf_url:
            tags.add("B1")
        if any(h and (h in (lhost or "") or h in (phost or "")) for h in VIEWER_HOST_HINTS):
            tags.add("B2")
        if (lhost and ("mdpi.com" in lhost or "frontiersin.org" in lhost)) or (phost and ("mdpi.com" in phost or "frontiersin.org" in phost)):
            tags.add("B3")
        if lhost == "doi.org":
            tags.add("B4")

    # C-cases
    if any(h in (lhost or "") for h in CLEAN_HTML_HOSTS) or any(h in (phost or "") for h in CLEAN_HTML_HOSTS):
        tags.add("C1")
    if any(h in (lhost or "") for h in JATS_HOSTS) or any(h in (phost or "") for h in JATS_HOSTS):
        tags.add("C2")

    # D/E hints
    if any(h in (lhost or "") for h in REFERER_HOSTS) or any(h in (phost or "") for h in REFERER_HOSTS):
        tags.add("D6")
    if any(h in (lhost or "") for h in ("www.nature.com", "ieeexplore.ieee.org", "dl.acm.org")) or any(h in (phost or "") for h in ("www.nature.com", "ieeexplore.ieee.org", "dl.acm.org")):
        tags.add("D2")

    return sorted(tags)


def to_record(work, loc):
    doi = work.get("doi")
    title = work.get("title")
    host_type = loc.get("host_type")  # publisher/repository
    version = loc.get("version")  # publishedVersion, acceptedVersion, submittedVersion
    license_ = loc.get("license")
    primary_location = work.get("primary_location") or {}
    landing_url = normalize_url(loc.get("landing_page_url") or primary_location.get("landing_page_url"))
    pdf_url = normalize_url(loc.get("pdf_url"))
    source = (loc.get("source") or {})
    publisher = source.get("host_organization_name") or source.get("display_name")
    venue = (primary_location.get("source") or {}).get("display_name")
    cases = classify_cases(landing_url, pdf_url)
    doi_url = f"https://doi.org/{doi.split('doi.org/')[-1]}" if doi else None
    if doi_url:
        # Using DOI resolver as an A7-like 302 chain to landing
        cases = sorted(set(cases) | {"A7", "B4"})
    return {
        "id": work.get("id"),
        "doi": doi,
        "title": title,
        "oa": True,
        "version": version,
        "host_type": host_type,
        "publisher": publisher,
        "venue": venue,
        "year": work.get("publication_year"),
        "landing_page_url": landing_url,
        "pdf_url": pdf_url,
        "doi_url": doi_url,
        "expected_cases": cases,
        "license": license_,
        "source_id": source.get("id"),
    }


def pick_diverse(records, target_n=50, seed=None):
    if seed is not None:
        random.seed(seed)

    # Aim to spread across host_type, versions, and publishers.
    by_publisher = {}
    for r in records:
        pub = (r.get("publisher") or "unknown").lower()
        by_publisher.setdefault(pub, []).append(r)

    chosen = []
    # First pass: pick from a curated list of hosts we want
    want_hosts = [
        "arxiv.org",
        "biorxiv.org",
        "medrxiv.org",
        "journals.plos.org",
        "elifesciences.org",
        "www.frontiersin.org",
        "biomedcentral.com",
        "www.hindawi.com",
        "www.mdpi.com",
        "peerj.com",
        "royalsocietypublishing.org",
        "www.nature.com",
        "academic.oup.com",
        "www.cambridge.org",
        "dl.acm.org",
        "ieeexplore.ieee.org",
        "link.springer.com",
        "www.tandfonline.com",
        "journals.sagepub.com",
        "www.bmj.com",
        "f1000research.com",
        "wellcomeopenresearch.org",
        "gatesopenresearch.org",
        "www.ncbi.nlm.nih.gov",
        "europepmc.org",
        "zenodo.org",
        "osf.io",
        "figshare.com",
        "openreview.net",
        "aclanthology.org",
        "jmlr.org",
    ]

    def host_in(u, host):
        try:
            return host in urlparse(u or "").netloc
        except Exception:
            return False

    remaining = records[:]
    random.shuffle(remaining)
    covered_hosts = set()

    # Ensure we include desired hosts if available
    for want in want_hosts:
        for r in list(remaining):
            if host_in(r.get("landing_page_url"), want) or host_in(r.get("pdf_url"), want):
                chosen.append(r)
                covered_hosts.add(want)
                remaining.remove(r)
                break

    # Coverage pass: ensure certain tricky cases appear at least N times
    by_case = {}
    for r in remaining:
        for t in r.get("expected_cases", []) or []:
            by_case.setdefault(t, []).append(r)

    coverage_targets = {
        "A1": 10,
        "A2": 4,
        "A3": 6,
        "A5": 3,
        "A6": 6,
        "B2": 3,
        "B3": 3,
        "C1": 8,
        "C2": 4,
        "D2": 3,
        "D6": 4,
    }

    def add_until(tag, n):
        pool = [r for r in by_case.get(tag, []) if r not in chosen]
        random.shuffle(pool)
        count_added = 0
        for r in pool:
            if len(chosen) >= target_n:
                break
            chosen.append(r)
            count_added += 1
            if count_added >= n:
                break

    for tag, n in coverage_targets.items():
        current = sum(1 for r in chosen if tag in (r.get("expected_cases") or []))
        need = max(0, n - current)
        if need > 0:
            add_until(tag, need)

    # Fill up with remaining, favoring new publishers and varied cases
    def score(rec):
        s = 0
        pub = (rec.get("publisher") or "").lower()
        if all(pub not in (c.get("publisher") or "").lower() for c in chosen):
            s += 3
        if rec.get("host_type") == "publisher":
            s += 1
        if rec.get("host_type") == "repository":
            s += 1
        if rec.get("version") in ("acceptedVersion", "submittedVersion"):
            s += 2
        s += min(3, len(rec.get("expected_cases") or []))
        return s

    remaining.sort(key=score, reverse=True)
    for r in remaining:
        if len(chosen) >= target_n:
            break
        chosen.append(r)

    # If we over-shot in the host pass, trim dedup by id
    uniq, seen = [], set()
    for r in chosen:
        rid = r.get("id")
        if rid in seen:
            continue
        uniq.append(r)
        seen.add(rid)
    if len(uniq) < target_n:
        # Top up with any remaining records not yet chosen
        pool = [r for r in records if r.get("id") not in seen]
        random.shuffle(pool)
        for r in pool:
            uniq.append(r)
            seen.add(r.get("id"))
            if len(uniq) >= target_n:
                break
    return uniq[:target_n]


def main():
    mailto = os.environ.get("OPENALEX_MAILTO", "paper-qa@example.com")
    pages = int(os.environ.get("OPENALEX_PAGES", "4"))
    seed = int(os.environ.get("DATASET_SEED", "42"))
    out_path = os.environ.get("OUT_PATH", "data/oa_messy_links_50.jsonl")

    print(f"Fetching OpenAlex works (pages={pages})…", file=sys.stderr)
    works = fetch_openalex_pages(pages=pages, mailto=mailto, seed=seed)
    print(f"Fetched {len(works)} works. Preparing locations…", file=sys.stderr)

    # Flatten OA locations
    candidates = []
    for w in works:
        locs = w.get("locations") or []
        for loc in locs:
            if not loc.get("is_oa"):
                continue
            if not (loc.get("landing_page_url") or loc.get("pdf_url")):
                continue
            rec = to_record(w, loc)
            # Filter obvious junk
            if not rec.get("landing_page_url") and not rec.get("pdf_url"):
                continue
            candidates.append(rec)

    # Deduplicate by (landing,pdf)
    deduped = []
    seen = set()
    for r in candidates:
        key = (r.get("landing_page_url"), r.get("pdf_url"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    print(f"Candidates after dedupe: {len(deduped)}", file=sys.stderr)

    sample = pick_diverse(deduped, target_n=50, seed=seed)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in sample:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {len(sample)} records to {out_path}")


if __name__ == "__main__":
    main()
