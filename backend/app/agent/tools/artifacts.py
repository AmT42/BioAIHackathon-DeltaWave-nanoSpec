from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.tools.context import ToolContext


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_segment(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def tool_invocation_dir(ctx: ToolContext) -> Path | None:
    if ctx.artifact_root is None or not ctx.thread_id or not ctx.run_id:
        return None
    tool_name = _safe_segment(ctx.tool_name, "unknown_tool")
    tool_use_id = _safe_segment(ctx.tool_use_id, "manual")
    return (
        ctx.artifact_root
        / "threads"
        / _safe_segment(ctx.thread_id, "unknown_thread")
        / "lineages"
        / _safe_segment(ctx.run_id, "unknown_run")
        / "tools"
        / tool_name
        / tool_use_id
    )


def write_request_artifact(ctx: ToolContext, payload: dict[str, Any]) -> Path | None:
    base = tool_invocation_dir(ctx)
    if base is None:
        return None
    _json_write(base / "request.json", payload)
    return base / "request.json"


def write_response_artifact(ctx: ToolContext, payload: dict[str, Any]) -> Path | None:
    base = tool_invocation_dir(ctx)
    if base is None:
        return None
    _json_write(base / "response.json", payload)
    return base / "response.json"


def write_raw_json_artifact(ctx: ToolContext, name: str, payload: Any) -> dict[str, Any] | None:
    base = tool_invocation_dir(ctx)
    if base is None:
        return None
    file_name = _safe_segment(name, "raw") + ".json"
    path = base / "raw" / file_name
    _json_write(path, payload)
    return {
        "kind": "raw_json",
        "name": file_name,
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def write_text_file_artifact(ctx: ToolContext, name: str, content: str, *, subdir: str = "files") -> dict[str, Any] | None:
    base = tool_invocation_dir(ctx)
    if base is None:
        return None
    file_name = _safe_segment(name, "artifact")
    path = base / subdir / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "kind": "file",
        "name": file_name,
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def write_binary_file_artifact(ctx: ToolContext, name: str, data: bytes, *, subdir: str = "files") -> dict[str, Any] | None:
    base = tool_invocation_dir(ctx)
    if base is None:
        return None
    file_name = _safe_segment(name, "artifact")
    path = base / subdir / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "kind": "file",
        "name": file_name,
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def finalize_manifest(ctx: ToolContext, extra_artifacts: list[dict[str, Any]] | None = None) -> Path | None:
    base = tool_invocation_dir(ctx)
    if base is None:
        return None

    entries: list[dict[str, Any]] = []
    for folder in [base / "raw", base / "files"]:
        if not folder.exists():
            continue
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            entries.append(
                {
                    "path": str(file_path),
                    "sha256": _sha256(file_path),
                    "size_bytes": file_path.stat().st_size,
                }
            )

    if extra_artifacts:
        entries.extend(extra_artifacts)

    payload = {
        "produced_at": _utc_iso(),
        "lineage": ctx.lineage(),
        "entries": entries,
    }
    path = base / "manifest.json"
    _json_write(path, payload)
    return path


def source_cache_dir(ctx: ToolContext, source_name: str) -> Path | None:
    if ctx.source_cache_root is None:
        return None
    path = ctx.source_cache_root / _safe_segment(source_name, "source")
    path.mkdir(parents=True, exist_ok=True)
    return path
