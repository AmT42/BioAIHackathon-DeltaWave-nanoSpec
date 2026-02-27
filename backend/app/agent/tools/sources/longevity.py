from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.tools.artifacts import (
    source_cache_dir,
    write_binary_file_artifact,
    write_text_file_artifact,
)
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolSpec


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _candidate_drugage_urls() -> list[str]:
    return [
        "https://genomics.senescence.info/static/data/drugage.csv",
        "https://genomics.senescence.info/static/download/drugage.csv",
        "https://genomics.senescence.info/drugs/DrugAge.csv",
    ]


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
    return [dict(row) for row in reader]


def _find_value(row: dict[str, Any], candidates: list[str]) -> str | None:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for key in candidates:
        if key in lower and str(lower[key]).strip():
            return str(lower[key]).strip()
    return None


def build_longevity_tools(http: SimpleHttpClient) -> list[ToolSpec]:
    def hagr_drugage_refresh(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        dataset = str(payload.get("dataset", "drugage")).strip().lower()
        if dataset != "drugage":
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Only dataset='drugage' is supported")

        forced_url = str(payload.get("download_url", "")).strip() or None
        urls = [forced_url] if forced_url else _candidate_drugage_urls()

        if ctx is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Tool context is required for source cache routing")

        cache_root = source_cache_dir(ctx, "hagr_drugage")
        if cache_root is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Source cache root is not configured")

        last_error: str | None = None
        saved_path: Path | None = None
        for url in urls:
            try:
                data, headers = http.get_bytes(url=url)
                ext = _detect_extension(url, headers)
                file_name = f"drugage_{_utc_stamp()}.{ext}"
                saved_path = cache_root / file_name
                saved_path.write_bytes(data)
                break
            except Exception as exc:  # pragma: no cover - best effort retry across urls
                last_error = str(exc)

        if saved_path is None:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message="Failed to refresh DrugAge dataset from all candidate URLs",
                details={"last_error": last_error},
            )

        rows = _rows_from_file(saved_path)
        artifacts: list[dict[str, Any]] = []
        artifact = write_binary_file_artifact(ctx, saved_path.name, saved_path.read_bytes(), subdir="files")
        if artifact:
            artifacts.append(artifact)

        return make_tool_output(
            source="hagr_drugage",
            summary=f"Refreshed DrugAge cache with {len(rows)} row(s).",
            data={
                "dataset": "drugage",
                "local_path": str(saved_path),
                "rows": len(rows),
            },
            ids=[str(saved_path)],
            artifacts=artifacts,
            ctx=ctx,
        )

    def hagr_drugage_query(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        compound = str(payload.get("compound", "")).strip()
        if not compound:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'compound' is required")

        species_filter = str(payload.get("species", "")).strip().lower() or None
        limit = min(max(int(payload.get("limit", 25)), 1), 200)
        auto_refresh = bool(payload.get("auto_refresh", True))

        if ctx is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Tool context is required for source cache routing")
        cache_root = source_cache_dir(ctx, "hagr_drugage")
        if cache_root is None:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="Source cache root is not configured")

        path = _latest_file(cache_root, "drugage")
        if path is None and auto_refresh:
            refresh_result = hagr_drugage_refresh({"dataset": "drugage"}, ctx)
            local_path = str(refresh_result.get("data", {}).get("local_path", "")).strip()
            path = Path(local_path) if local_path else None

        if path is None or not path.exists():
            raise ToolExecutionError(
                code="NOT_FOUND",
                message="No DrugAge cache file available. Run hagr_drugage_refresh first.",
            )

        rows = _rows_from_file(path)
        q = compound.lower()
        matches: list[dict[str, Any]] = []
        for row in rows:
            compound_name = _find_value(row, ["compound", "drug", "name", "intervention"]) or ""
            species = _find_value(row, ["species", "organism", "model"]) or ""
            if q not in compound_name.lower():
                continue
            if species_filter and species_filter not in species.lower():
                continue
            matches.append(
                {
                    "compound": compound_name,
                    "species": species,
                    "strain": _find_value(row, ["strain", "background"]),
                    "sex": _find_value(row, ["sex"]),
                    "dose": _find_value(row, ["dose", "dosage"]),
                    "avg_median_lifespan_change_pct": _find_value(
                        row,
                        [
                            "average lifespan change (%)",
                            "avg lifespan change (%)",
                            "median lifespan change (%)",
                        ],
                    ),
                    "max_lifespan_change_pct": _find_value(row, ["max lifespan change (%)", "maximum lifespan change (%)"]),
                    "reference": _find_value(row, ["pmid", "reference", "citation"]),
                    "raw": row,
                }
            )
            if len(matches) >= limit:
                break

        return make_tool_output(
            source="hagr_drugage",
            summary=f"Found {len(matches)} DrugAge row(s) matching '{compound}'.",
            data={
                "compound": compound,
                "species_filter": species_filter,
                "entries": matches,
                "cache_path": str(path),
            },
            ids=[entry.get("reference") for entry in matches if entry.get("reference")],
            warnings=["No matching rows found."] if not matches else [],
            ctx=ctx,
        )

    def itp_fetch_survival_summary(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        url = str(payload.get("url", "")).strip()
        if not url:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'url' is required")

        html, _ = http.get_text(url=url)
        artifacts: list[dict[str, Any]] = []
        raw_html_artifact = write_text_file_artifact(ctx, "itp_survival_summary.html", html, subdir="raw") if ctx else None
        if raw_html_artifact:
            artifacts.append(raw_html_artifact)

        text = re.sub(r"<script[\\s\\S]*?</script>", " ", html, flags=re.IGNORECASE)
        text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\\s+", " ", text).strip()
        preview = text[:2500]

        p_values = re.findall(r"p\\s*[=<>]\\s*([0-9]*\\.?[0-9]+)", text, flags=re.IGNORECASE)

        return make_tool_output(
            source="itp",
            summary="Fetched ITP survival summary page.",
            data={
                "url": url,
                "text_preview": preview,
                "p_values": p_values[:20],
            },
            ids=[url],
            artifacts=artifacts,
            ctx=ctx,
        )

    return [
        ToolSpec(
            name="hagr_drugage_refresh",
            description="Download and cache DrugAge dataset snapshot.",
            input_schema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "default": "drugage"},
                    "download_url": {"type": "string"},
                },
            },
            handler=hagr_drugage_refresh,
            source="hagr_drugage",
        ),
        ToolSpec(
            name="hagr_drugage_query",
            description="Query cached DrugAge rows by compound/species.",
            input_schema={
                "type": "object",
                "properties": {
                    "compound": {"type": "string"},
                    "species": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
                    "auto_refresh": {"type": "boolean", "default": True},
                },
                "required": ["compound"],
            },
            handler=hagr_drugage_query,
            source="hagr_drugage",
        ),
        ToolSpec(
            name="itp_fetch_survival_summary",
            description="Fetch and summarize an ITP survival summary page.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
            handler=itp_fetch_survival_summary,
            source="itp",
        ),
    ]
