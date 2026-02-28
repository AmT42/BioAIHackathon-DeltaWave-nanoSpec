from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from typing import Any

from app.config import Settings


_PRELOAD_PROFILE_PACKAGES: dict[str, tuple[str, ...]] = {
    "bio_data_full": (
        "pydantic",
        "pyyaml",
        "requests",
        "httpx",
        "aiohttp",
        "numpy",
        "pandas",
        "scipy",
        "scikit-learn",
        "statsmodels",
        "matplotlib",
        "seaborn",
        "plotly",
        "biopython",
        "rdkit-pypi",
        "networkx",
    ),
    "data_first": (
        "pydantic",
        "pyyaml",
        "requests",
        "httpx",
        "aiohttp",
        "numpy",
        "pandas",
        "scipy",
        "scikit-learn",
        "statsmodels",
        "matplotlib",
        "seaborn",
        "plotly",
    ),
    "minimal_core": (
        "pydantic",
        "pyyaml",
        "requests",
        "httpx",
        "aiohttp",
        "numpy",
        "pandas",
    ),
}

_PACKAGE_IMPORT_HINTS: dict[str, str] = {
    "pyyaml": "yaml",
    "rdkit-pypi": "rdkit",
    "scikit-learn": "sklearn",
    "biopython": "Bio",
}


def _normalize_package_name(value: str) -> str:
    return str(value or "").strip().lower()


def _import_name_for_package(package_name: str) -> str:
    normalized = _normalize_package_name(package_name)
    if normalized in _PACKAGE_IMPORT_HINTS:
        return _PACKAGE_IMPORT_HINTS[normalized]
    return normalized.replace("-", "_")


def _is_import_available(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:
        return False


def resolve_preload_package_set(settings: Settings) -> list[str]:
    if settings.repl_preload_packages:
        out: list[str] = []
        seen: set[str] = set()
        for item in settings.repl_preload_packages:
            package_name = str(item).strip()
            key = _normalize_package_name(package_name)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(package_name)
        return out

    profile = str(settings.repl_preload_profile or "bio_data_full").strip().lower()
    base = _PRELOAD_PROFILE_PACKAGES.get(profile, _PRELOAD_PROFILE_PACKAGES["bio_data_full"])
    return list(base)


def _install_packages(
    packages: list[str],
    *,
    timeout_seconds: int,
    index_url: str | None = None,
) -> tuple[bool, str]:
    if not packages:
        return True, ""

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--quiet",
        *packages,
    ]
    if index_url:
        cmd.extend(["--index-url", index_url])

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(15, int(timeout_seconds)),
            check=False,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if completed.returncode == 0:
        return True, ""

    stderr = str(completed.stderr or "").strip()
    stdout = str(completed.stdout or "").strip()
    details = stderr or stdout or f"pip exited with code {completed.returncode}"
    if len(details) > 1000:
        details = details[:997] + "..."
    return False, details


def ensure_repl_preload(settings: Settings) -> dict[str, Any]:
    started = time.monotonic()
    if not settings.repl_preload_enabled:
        return {
            "status": "disabled",
            "profile": settings.repl_preload_profile,
            "packages": [],
            "missing_before": [],
            "missing_after": [],
            "installed": [],
            "failed": [],
            "duration_s": round(time.monotonic() - started, 3),
        }

    packages = resolve_preload_package_set(settings)
    missing_before: list[str] = []
    for package_name in packages:
        import_name = _import_name_for_package(package_name)
        if not _is_import_available(import_name):
            missing_before.append(package_name)

    installed: list[str] = []
    failed: list[str] = []
    install_error = ""
    if missing_before:
        ok, install_error = _install_packages(
            missing_before,
            timeout_seconds=settings.repl_preload_timeout_seconds,
            index_url=settings.repl_lazy_install_index_url,
        )
        if ok:
            installed = list(missing_before)
        else:
            failed = list(missing_before)

    missing_after: list[str] = []
    for package_name in packages:
        import_name = _import_name_for_package(package_name)
        if not _is_import_available(import_name):
            missing_after.append(package_name)

    status = "ready" if not missing_after else "degraded"
    report: dict[str, Any] = {
        "status": status,
        "profile": settings.repl_preload_profile,
        "packages": packages,
        "missing_before": missing_before,
        "missing_after": missing_after,
        "installed": installed,
        "failed": failed,
        "install_error": install_error or None,
        "duration_s": round(time.monotonic() - started, 3),
    }

    if missing_after and settings.repl_preload_fail_mode == "fail_fast":
        raise RuntimeError(
            "REPL preload failed in fail-fast mode. "
            f"Missing packages after preload: {missing_after}. "
            f"Install details: {install_error or 'n/a'}"
        )

    return report
