#!/usr/bin/env python3
"""
Fetch a PDF using undetected-chromedriver (Selenium) to pass Cloudflare.

Usage:
  python scripts/fetch_pdf_uc.py --doi 10.1002/oby.21939 --out path/to/file.pdf
  python scripts/fetch_pdf_uc.py --url https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/oby.21939 --out path
"""
import argparse
import os
import re
import sys
import time
import tempfile
from pathlib import Path
from typing import Optional


def ensure_packages():
    try:
        import undetected_chromedriver  # noqa: F401
        import selenium  # noqa: F401
    except Exception:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'undetected-chromedriver', 'selenium'])


def fetch_pdf_uc(target_url: str, out_path: str, timeout: int = 180) -> None:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Use a temp download directory and then move the file into place
    tmpdir = tempfile.mkdtemp(prefix='dl_uc_')

    options = uc.ChromeOptions()
    prefs = {
        "download.default_directory": tmpdir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        # Disable PDF viewer to force direct download
        "plugins.plugins_list": [{"enabled": False, "name": "Chrome PDF Viewer"}],
    }
    options.add_experimental_option("prefs", prefs)
    # UA that looks like a normal desktop browser
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Headless new is closer to headful; try it first
    options.add_argument("--headless=new")

    driver = uc.Chrome(options=options)
    driver.set_page_load_timeout(timeout)

    def _wait_for_download(expected_suffix: str = ".pdf") -> Optional[Path]:
        end = time.time() + timeout
        last_size = None
        last_path = None
        while time.time() < end:
            pdfs = sorted(Path(tmpdir).glob("*" + expected_suffix))
            if pdfs:
                # Wait for file to stop growing
                p = pdfs[-1]
                size = p.stat().st_size
                if last_path == p and last_size == size and size > 0:
                    return p
                last_path, last_size = p, size
            time.sleep(0.5)
        return None

    try:
        # If URL is a Wiley pdf endpoint, also try landing (DOI) flow
        landing = None
        if "onlinelibrary.wiley.com" in target_url and "/doi/" in target_url:
            m = re.search(r"/doi/(?:pdf|pdfdirect|epdf)/(.+)$", target_url)
            if m:
                landing = f"https://doi.org/{m.group(1)}"

        # Start with the direct URL to maximize chance of triggering a download
        driver.get(target_url)
        # Give Cloudflare a chance to challenge and then redirect to download
        time.sleep(5)
        p = _wait_for_download()
        if not p and landing:
            # Use landing page, then click the PDF link
            driver.get(landing)
            try:
                WebDriverWait(driver, min(timeout, 60)).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            except Exception:
                pass
            # Look for PDF link candidates
            anchors = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/doi/pdfdirect/"], a[href*="/doi/pdf/"]')
            if not anchors:
                # Also search by link text
                anchors = driver.find_elements(By.PARTIAL_LINK_TEXT, 'PDF')
            if anchors:
                try:
                    anchors[0].click()
                except Exception:
                    # As fallback, navigate directly to href
                    href = anchors[0].get_attribute('href')
                    if href:
                        if href.startswith('/'):
                            href = 'https://onlinelibrary.wiley.com' + href
                        driver.get(href)
            # Wait for download
            p = _wait_for_download()

        if not p:
            raise RuntimeError("PDF download did not complete. Possibly blocked by Cloudflare.")

        # Move to requested output path
        os.replace(str(p), out_path)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"Saved: {out_path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch PDF via undetected-chromedriver.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--url', help='Direct PDF or publisher URL')
    g.add_argument('--doi', help='DOI string (e.g., 10.1002/oby.21939)')
    ap.add_argument('--out', required=True, help='Output path for PDF')
    ap.add_argument('--timeout', type=int, default=180, help='Timeout seconds')
    args = ap.parse_args(argv)

    url = args.url
    if not url and args.doi:
        url = f"https://onlinelibrary.wiley.com/doi/pdfdirect/{args.doi}"

    ensure_packages()
    fetch_pdf_uc(url, args.out, timeout=args.timeout)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

