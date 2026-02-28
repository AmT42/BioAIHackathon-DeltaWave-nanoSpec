from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.agent.tools.artifacts import (
    source_cache_dir,
    write_binary_file_artifact,
    write_text_file_artifact,
)
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.descriptions import render_tool_description
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


DEFAULT_ITP_FALLBACK_URL = "https://phenome.jax.org/itp/surv/MetRapa/C2011"
MODES = {"precision", "balanced", "recall"}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _candidate_drugage_urls() -> list[str]:
    return [
        "https://genomics.senescence.info/drugs/dataset.zip",
        "https://hagr.ageing-map.org/drugs/dataset.zip",
    ]


def _require_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode", "balanced")).strip().lower()
    if mode not in MODES:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message="'mode' must be one of: precision, balanced, recall",
            details={"allowed": sorted(MODES)},
        )
    return mode


def _limit_for_mode(payload: dict[str, Any], *, default_precision: int, default_balanced: int, default_recall: int, maximum: int) -> int:
    mode = _require_mode(payload)
    default = {
        "precision": default_precision,
        "balanced": default_balanced,
        "recall": default_recall,
    }[mode]
    raw = payload.get("limit", default)
    try:
        value = int(raw)
    except Exception as exc:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'limit' must be an integer") from exc
    if value < 1 or value > maximum:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"'limit' must be between 1 and {maximum}",
            details={"limit": value, "max": maximum},
        )
    return value


def _require_query(payload: dict[str, Any], key: str = "query") -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ToolExecutionError(code="VALIDATION_ERROR", message=f"'{key}' is required")
    return value


def _require_ids(payload: dict[str, Any], *, max_size: int = 10) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="'ids' must be a non-empty list")

    out: list[str] = []
    seen: set[str] = set()
    for item in ids:
        value = str(item or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)

    if not out:
        raise ToolExecutionError(code="VALIDATION_ERROR", message="No valid IDs provided in 'ids'")
    if len(out) > max_size:
        raise ToolExecutionError(
            code="VALIDATION_ERROR",
            message=f"Too many IDs. Maximum is {max_size}",
            details={"provided": len(out), "max": max_size},
        )
    return out


def _detect_extension(url: str, headers: dict[str, str]) -> str:
    content_type = (headers.get("content-type") or "").lower()
    if "zip" in content_type:
        return "zip"
    if "csv" in content_type:
        return "csv"
    if url.lower().endswith(".zip"):
        return "zip"
    return "csv"


def _latest_file(cache_dir: Path, prefix: str) -> Path | None:
    files = sorted(cache_dir.glob(f"{prefix}_*"), reverse=True)
    return files[0] if files else None


def _rows_from_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                return []
            with zf.open(csv_names[0], "r") as handle:
                text = handle.read().decode("utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    normalized_rows: list[dict[str, Any]] = []
    for row in reader:
        normalized_rows.append({str(k).strip().lower(): v for k, v in dict(row).items()})
    return normalized_rows


def _find_value(row: dict[str, Any], candidates: list[str]) -> str | None:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for key in candidates:
        normalized_key = str(key).strip().lower()
        if normalized_key in lower and str(lower[normalized_key]).strip():
            return str(lower[normalized_key]).strip()
    return None


def _is_nia_host(host: str) -> bool:
    return host == "nia.nih.gov" or host.endswith(".nia.nih.gov")


def _looks_like_waf_block(html: str) -> bool:
    html_lower = html.lower()
    return "captcha" in html_lower or "cloudfront" in html_lower


def _looks_like_waf_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ["http 403", "http 405", "forbidden", "captcha", "cloudfront", "waf"])


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def build_longevity_tools(http: SimpleHttpClient) -> list[ToolSpec]:
    def longevity_drugage_refresh(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        mode = _require_mode(payload)
        forced_url = str(payload.get("download_url", "")).strip() or None
        urls = [forced_url] if forced_url else _candidate_drugage_urls()

        if ctx is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Tool context is required for source cache routing")

        cache_root = source_cache_dir(ctx, "hagr_drugage")
        if cache_root is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Source cache root is not configured")

        last_error: str | None = None
        saved_path: Path | None = None
        source_url: str | None = None
        for url in urls:
            try:
                data, headers = http.get_bytes(url=url)
                ext = _detect_extension(url, headers)
                file_name = f"drugage_{_utc_stamp()}.{ext}"
                saved_path = cache_root / file_name
                saved_path.write_bytes(data)
                source_url = url
                break
            except Exception as exc:  # pragma: no cover
                last_error = str(exc)

        if saved_path is None:
            stale = _latest_file(cache_root, "drugage")
            if stale and stale.exists():
                stale_rows = _rows_from_file(stale)
                return make_tool_output(
                    source="hagr_drugage",
                    summary=f"Refresh failed; using stale DrugAge cache with {len(stale_rows)} row(s).",
                    result_kind="status",
                    data={
                        "mode": mode,
                        "local_path": str(stale),
                        "rows": len(stale_rows),
                        "stale_cache": True,
                        "source_url": None,
                    },
                    ids=[str(stale)],
                    warnings=["Refresh failed; served stale cache snapshot.", f"Last refresh error: {last_error or 'unknown'}"],
                    ctx=ctx,
                )
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message="Failed to refresh DrugAge dataset from all candidate URLs",
                details={"last_error": last_error, "candidate_urls": urls},
            )

        rows = _rows_from_file(saved_path)
        artifacts: list[dict[str, Any]] = []
        artifact = write_binary_file_artifact(ctx, saved_path.name, saved_path.read_bytes(), subdir="files")
        if artifact:
            artifacts.append(artifact)

        return make_tool_output(
            source="hagr_drugage",
            summary=f"Refreshed DrugAge cache with {len(rows)} row(s).",
            result_kind="status",
            data={
                "mode": mode,
                "local_path": str(saved_path),
                "rows": len(rows),
                "stale_cache": False,
                "source_url": source_url,
            },
            ids=[str(saved_path)],
            artifacts=artifacts,
            ctx=ctx,
        )

    def longevity_drugage_query(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = _require_query(payload)
        mode = _require_mode(payload)
        limit = _limit_for_mode(payload, default_precision=10, default_balanced=25, default_recall=50, maximum=200)
        species_filter = str(payload.get("species", "")).strip().lower() or None
        auto_refresh = _coerce_bool(payload.get("auto_refresh"), default=True)

        if ctx is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Tool context is required for source cache routing")
        cache_root = source_cache_dir(ctx, "hagr_drugage")
        if cache_root is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Source cache root is not configured")

        path = _latest_file(cache_root, "drugage")
        if path is None and auto_refresh:
            refresh_result = longevity_drugage_refresh({"mode": mode}, ctx)
            local_path = str(refresh_result.get("data", {}).get("local_path", "")).strip()
            path = Path(local_path) if local_path else None

        if path is None or not path.exists():
            raise ToolExecutionError(
                code="NOT_FOUND",
                message="No DrugAge cache file available. Run longevity_drugage_refresh first.",
            )

        rows = _rows_from_file(path)
        q = query.lower()
        matches: list[dict[str, Any]] = []
        for row in rows:
            compound_name = _find_value(row, ["compound_name", "compound", "drug", "name", "intervention"]) or ""
            species = _find_value(row, ["species", "organism", "model"]) or ""
            if q not in compound_name.lower():
                continue
            if species_filter and species_filter not in species.lower():
                continue
            avg_change = _find_value(
                row,
                [
                    "avg_lifespan_change_percent",
                    "average lifespan change (%)",
                    "avg lifespan change (%)",
                    "median lifespan change (%)",
                ],
            )
            max_change = _find_value(
                row,
                [
                    "max_lifespan_change_percent",
                    "max lifespan change (%)",
                    "maximum lifespan change (%)",
                ],
            )
            pubmed_id = _find_value(row, ["pubmed_id", "pmid"])
            reference = _find_value(row, ["pubmed_id", "pmid", "reference", "citation"])
            matches.append(
                {
                    "compound": compound_name,
                    "compound_name": compound_name,
                    "species": species,
                    "strain": _find_value(row, ["strain", "background"]),
                    "sex": _find_value(row, ["sex", "gender"]),
                    "dose": _find_value(row, ["dose", "dosage"]),
                    "avg_median_lifespan_change_pct": avg_change,
                    "max_lifespan_change_pct": max_change,
                    "avg_lifespan_change_percent": avg_change,
                    "max_lifespan_change_percent": max_change,
                    # Compatibility aliases commonly assumed by model code.
                    "avg_lifespan_change": avg_change,
                    "max_lifespan_change": max_change,
                    "significance": _find_value(
                        row,
                        [
                            "avg_lifespan_significance",
                            "max_lifespan_significance",
                            "significance",
                        ],
                    ),
                    "pubmed_id": pubmed_id,
                    "reference": reference,
                    "raw": row,
                }
            )
            if len(matches) >= limit:
                break

        return make_tool_output(
            source="hagr_drugage",
            summary=f"Found {len(matches)} DrugAge row(s) matching '{query}'.",
            result_kind="record_list",
            data={
                "query": query,
                "mode": mode,
                "species_filter": species_filter,
                "records": matches,
                "entries": matches,
                "cache_path": str(path),
            },
            ids=[entry.get("reference") for entry in matches if entry.get("reference")],
            warnings=["No matching rows found."] if not matches else [],
            ctx=ctx,
        )

    def longevity_itp_fetch_summary(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = _require_ids(payload, max_size=10)
        mode = _require_mode(payload)
        configured_fallback = str(payload.get("fallback_url", "")).strip()
        fallback_url = configured_fallback or DEFAULT_ITP_FALLBACK_URL

        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        artifacts: list[dict[str, Any]] = []

        for url in ids:
            requested_host = str(urlparse(url).hostname or "").lower()
            attempt_urls = [url]
            if _is_nia_host(requested_host) and fallback_url and fallback_url != url:
                attempt_urls.append(fallback_url)

            blocked_hint = (
                "NIA host appears blocked by anti-bot controls. "
                f"Using JAX fallback URL ({fallback_url}) if needed."
            )
            unavailable_hint = f"Primary NIA source was unavailable. Using JAX fallback URL ({fallback_url}) if needed."
            attempted_errors: list[dict[str, str]] = []
            primary_nia_issue: dict[str, Any] | None = None
            html: str | None = None
            resolved_url: str | None = None
            resolved_host: str | None = None

            for idx, attempt_url in enumerate(attempt_urls):
                attempt_host = str(urlparse(attempt_url).hostname or "").lower()
                has_next_attempt = idx < len(attempt_urls) - 1
                try:
                    candidate_html, _ = http.get_text(url=attempt_url)
                except ToolExecutionError as exc:
                    attempted_errors.append(
                        {
                            "url": attempt_url,
                            "source_host": attempt_host,
                            "error": exc.message,
                        }
                    )
                    if _is_nia_host(attempt_host):
                        primary_nia_blocked = _looks_like_waf_error(exc.message)
                        primary_nia_issue = {
                            "source_host": attempt_host,
                            "blocked_by_waf": primary_nia_blocked,
                        }
                        if has_next_attempt:
                            continue
                        warnings.append(f"{url}: {blocked_hint if primary_nia_blocked else unavailable_hint}")
                        continue
                    if primary_nia_issue is not None:
                        primary_nia_blocked = bool(primary_nia_issue.get("blocked_by_waf"))
                        warnings.append(
                            f"{url}: "
                            + (
                                "Primary NIA source appears blocked and fallback retrieval failed."
                                if primary_nia_blocked
                                else "Primary NIA source was unavailable and fallback retrieval failed."
                            )
                        )
                        continue
                    warnings.append(f"{url}: {exc.message}")
                    continue

                if _is_nia_host(attempt_host) and _looks_like_waf_block(candidate_html):
                    primary_nia_issue = {
                        "source_host": attempt_host,
                        "blocked_by_waf": True,
                    }
                    attempted_errors.append(
                        {
                            "url": attempt_url,
                            "source_host": attempt_host,
                            "error": "WAF-style response content detected",
                        }
                    )
                    if has_next_attempt:
                        continue
                    warnings.append(f"{url}: {blocked_hint}")
                    continue

                html = candidate_html
                resolved_url = attempt_url
                resolved_host = attempt_host
                break

            if html is None or resolved_url is None or resolved_host is None:
                warnings.append(f"{url}: unable to fetch ITP page")
                continue

            raw_html_artifact = write_text_file_artifact(ctx, f"itp_{_utc_stamp()}.html", html, subdir="raw") if ctx else None
            if raw_html_artifact:
                artifacts.append(raw_html_artifact)

            text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
            text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            preview = text[:2500]
            p_values = re.findall(r"p\s*[=<>]\s*([0-9]*\.?[0-9]+)", text, flags=re.IGNORECASE)
            fallback_used = resolved_url != url
            blocked_by_waf = bool(primary_nia_issue and primary_nia_issue.get("blocked_by_waf"))
            fallback_warning = blocked_hint if blocked_by_waf else unavailable_hint

            if fallback_used:
                warnings.append(fallback_warning)

            records.append(
                {
                    "requested_url": url,
                    "url": resolved_url,
                    "source_host": resolved_host,
                    "blocked_by_waf": blocked_by_waf,
                    "fallback_used": fallback_used,
                    "fallback_url": fallback_url if _is_nia_host(requested_host) else None,
                    "text_preview": preview,
                    "p_values": p_values[:20],
                }
            )

        return make_tool_output(
            source="itp",
            summary=f"Fetched {len(records)} ITP survival summary page(s).",
            result_kind="record_list",
            data={"mode": mode, "records": records},
            ids=[record.get("url") for record in records if record.get("url")],
            warnings=warnings,
            artifacts=artifacts,
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="longevity_drugage_refresh",
            description=render_tool_description(
                purpose="Refresh local DrugAge cache snapshot from public HAGR mirrors.",
                when=["cache missing or stale", "you need latest curated preclinical longevity rows"],
                avoid=["running on every turn without need", "tool context cache path unavailable"],
                critical_args=["mode: precision/balanced/recall (policy consistency)", "download_url: optional mirror override"],
                returns="Status document with cache path, row count, and stale-cache fallback info.",
                fails_if=["cache root unavailable", "all mirrors fail with no stale snapshot"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "download_url": {"type": "string"},
                },
            },
            handler=longevity_drugage_refresh,
            source="hagr_drugage",
        ),
        ToolSpec(
            name="longevity_drugage_query",
            description=render_tool_description(
                purpose="Query cached DrugAge rows by intervention name and optional species filter.",
                when=["you need curated animal lifespan evidence anchors", "compound-level preclinical scan"],
                avoid=["cache not available and refresh disabled", "expecting human clinical endpoints"],
                critical_args=["query: intervention name", "mode/limit: recall depth", "species/auto_refresh: filtering and cache behavior"],
                returns="Record list of matching DrugAge entries with lifespan effect fields.",
                fails_if=["query missing", "invalid limit/mode", "no cache and refresh disabled"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "species": {"type": "string"},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
                    "auto_refresh": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
            handler=longevity_drugage_query,
            source="hagr_drugage",
        ),
        ToolSpec(
            name="longevity_itp_fetch_summary",
            description=render_tool_description(
                purpose="Fetch ITP survival summary pages and extract compact significance previews.",
                when=["you have ITP summary URLs", "you need quick multi-site mouse-study signal extraction"],
                avoid=["using this as sole efficacy evidence", "passing non-URL IDs"],
                critical_args=["ids: ITP summary URL list", "mode: policy consistency", "fallback_url: alternate source for NIA-blocked pages"],
                returns="Record list with resolved URL, fallback state, preview text, and parsed p-values.",
                fails_if=["ids missing", "too many ids", "all URLs unreachable"],
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
                    "mode": {"type": "string", "enum": ["precision", "balanced", "recall"], "default": "balanced"},
                    "fallback_url": {"type": "string"},
                },
                "required": ["ids"],
            },
            handler=longevity_itp_fetch_summary,
            source="itp",
        ),
    ]
