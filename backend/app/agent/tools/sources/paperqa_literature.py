from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import Settings
from app.agent.tools.artifacts import source_cache_dir, write_raw_json_artifact, write_text_file_artifact
from app.agent.tools.context import ToolContext
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.registry import ToolSpec

try:  # pragma: no cover - POSIX-only primitive
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None


_PAPERQA_ENV_LOCK = threading.Lock()
_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", flags=re.IGNORECASE)
_PMID_PATTERN = re.compile(r"\bPMID[:\s]*([0-9]{4,9})\b", flags=re.IGNORECASE)
_NCT_PATTERN = re.compile(r"\bNCT\d{8}\b", flags=re.IGNORECASE)
_MIN_PAPERQA_TIMEOUT_SECONDS = 1500


def _safe_slug(value: str | None, *, default: str = "adhoc", max_len: int = 96) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    cleaned = cleaned.strip("._")
    if not cleaned:
        return default
    return cleaned[:max_len]


def _request_scope(ctx: ToolContext | None) -> str:
    if ctx is None:
        return "adhoc"
    return _safe_slug(ctx.thread_id or ctx.run_id or ctx.tool_use_id, default="adhoc")


def _vendor_src_path() -> Path:
    # backend/app/agent/tools/sources/paperqa_literature.py -> backend/
    backend_root = Path(__file__).resolve().parents[4]
    return backend_root / "vendor" / "paper-qa" / "src"


def _ensure_vendor_paperqa_on_path() -> Path:
    vendor_src = _vendor_src_path()
    if vendor_src.exists():
        vendor_entry = str(vendor_src)
        if vendor_entry not in sys.path:
            sys.path.insert(0, vendor_entry)
    return vendor_src


@contextmanager
def _quiet_paperqa_loggers() -> Any:
    """Temporarily silence PaperQA progress logging in REPL stdout/stderr."""
    target_names: set[str] = {
        "paperqa",
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
        "litellm",
    }
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name.startswith("paperqa"):
            target_names.add(name)

    previous: dict[str, tuple[int, bool]] = {}
    try:
        for name in target_names:
            logger = logging.getLogger(name)
            previous[name] = (logger.level, logger.propagate)
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
        yield
    finally:
        for name, (level, propagate) in previous.items():
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.propagate = propagate


def _import_paperqa() -> tuple[type[Any], Any, Any | None]:
    vendor_src = _ensure_vendor_paperqa_on_path()
    if not vendor_src.exists():
        raise ToolExecutionError(
            code="DEPENDENCY_MISSING",
            message="Vendored paper-qa source is missing at backend/vendor/paper-qa/src.",
            details={"expected_path": str(vendor_src)},
        )
    try:
        from paperqa import Settings as PaperQASettings
        from paperqa.agents.main import agent_query
        from paperqa.utils import run_or_ensure

        def paperqa_ask(*, query: str, settings: Any) -> Any:
            # NOTE: deliberately bypasses paperqa.ask(), which configures Rich console
            # logging intended for CLI usage and pollutes REPL tool stdout.
            with _quiet_paperqa_loggers():
                return run_or_ensure(
                    coro=agent_query(
                        query,
                        settings,
                        agent_type=settings.agent.agent_type,
                    )
                )
    except Exception as exc:
        python_bin = sys.executable or "python"
        raise ToolExecutionError(
            code="DEPENDENCY_MISSING",
            message=(
                "PaperQA import failed. Run `./scripts/eve-up.sh` to bootstrap vendored PaperQA "
                "and transitive dependencies in the startup venv. "
                "Manual fallback (local vendor, not PyPI paper-qa): "
                f"`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PAPER_QA=0.0.0 {python_bin} -m pip install --no-build-isolation -e ./vendor/paper-qa` "
                "and "
                f"`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PAPER_QA_PYPDF=0.0.0 {python_bin} -m pip install --no-build-isolation -e ./vendor/paper-qa/packages/paper-qa-pypdf`."
            ),
            details={
                "expected_path": str(vendor_src),
                "python_executable": python_bin,
                "python_version": sys.version.split(" ", 1)[0],
                "error": str(exc),
            },
        ) from exc

    multimodal = None
    try:
        from paperqa.settings import MultimodalOptions as _MultimodalOptions

        multimodal = _MultimodalOptions
    except Exception:
        multimodal = None
    return PaperQASettings, paperqa_ask, multimodal


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _optional_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except Exception:
        return None


def _format_year_directive(min_year: int | None, max_year: int | None) -> str | None:
    if min_year is None and max_year is None:
        return None
    if min_year is not None and max_year is not None:
        return f"between {min_year} and {max_year}"
    if min_year is not None:
        return f"from {min_year} onward"
    return f"up to {max_year}"


def _query_with_year_bounds(query: str, min_year: int | None, max_year: int | None) -> str:
    directive = _format_year_directive(min_year, max_year)
    if not directive:
        return query
    return f"{query}\n\nFocus the literature search on works published {directive}."


def _as_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            dumped = dict(value.__dict__)
            return dumped
        except Exception:
            pass
    return {}


def _extract_ids_from_text(text: str) -> tuple[list[str], list[str], list[str]]:
    dois = [match.group(0).lower() for match in _DOI_PATTERN.finditer(text or "")]
    pmids = [match.group(1) for match in _PMID_PATTERN.finditer(text or "")]
    ncts = [match.group(0).upper() for match in _NCT_PATTERN.finditer(text or "")]
    return dois, pmids, ncts


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _collect_citations_and_evidence(session: Any, *, max_contexts: int = 30) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    session_dict = _as_json_dict(session)
    contexts_raw = session_dict.get("contexts") if isinstance(session_dict.get("contexts"), list) else []

    citations: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    ids: list[str] = []
    warnings: list[str] = []
    seen_citation_keys: set[str] = set()

    for idx, raw in enumerate(contexts_raw):
        if not isinstance(raw, dict):
            continue
        text = raw.get("text") if isinstance(raw.get("text"), dict) else {}
        doc = text.get("doc") if isinstance(text.get("doc"), dict) else {}

        docname = str(doc.get("docname") or text.get("name") or f"context_{idx + 1}").strip() or f"context_{idx + 1}"
        title = str(doc.get("title") or docname).strip() or docname
        year = doc.get("year")
        doi = str(doc.get("doi") or "").strip().lower() or None
        citation_text = str(doc.get("citation") or "").strip() or None

        pmid: str | None = None
        nct_ids: list[str] = []
        if citation_text:
            _, pmids_from_citation, ncts_from_citation = _extract_ids_from_text(citation_text)
            pmid = pmids_from_citation[0] if pmids_from_citation else None
            nct_ids = ncts_from_citation

        citation_item = {
            "docname": docname,
            "title": title,
            "year": year if isinstance(year, int) else None,
            "doi": doi,
            "pmid": pmid,
            "nct_ids": nct_ids,
            "citation": citation_text,
        }
        citation_key = (doi or (pmid and f"pmid:{pmid}") or docname).strip().lower()
        if citation_key and citation_key not in seen_citation_keys:
            seen_citation_keys.add(citation_key)
            citations.append(citation_item)

        if doi:
            ids.append(doi)
        if pmid:
            ids.append(f"PMID:{pmid}")
        for nct_id in nct_ids:
            ids.append(nct_id)
        ids.append(docname)

        snippet = str(raw.get("context") or "").strip()
        if snippet:
            evidence.append(
                {
                    "context_id": raw.get("id"),
                    "docname": docname,
                    "title": title,
                    "year": year if isinstance(year, int) else None,
                    "score": raw.get("score"),
                    "snippet": snippet[:900],
                }
            )

    citations = citations[:max_contexts]
    if len(evidence) > max_contexts:
        warnings.append(f"Evidence snippets truncated to top {max_contexts} contexts.")
    evidence = evidence[:max_contexts]

    return citations, evidence, _dedupe_keep_order(ids), warnings


def _run_with_timeout(fn: Any, *, timeout_seconds: int) -> Any:
    done = threading.Event()
    state: dict[str, Any] = {}

    def _target() -> None:
        try:
            state["result"] = fn()
        except BaseException as exc:  # pragma: no cover - propagated after join
            state["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_target, daemon=True, name="paperqa-runner")
    thread.start()

    if not done.wait(max(1, timeout_seconds)):
        raise ToolExecutionError(
            code="TIMEOUT",
            message=f"PaperQA query timed out after {timeout_seconds}s.",
            retryable=True,
        )

    if "error" in state:
        error = state["error"]
        if isinstance(error, ToolExecutionError):
            raise error
        raise ToolExecutionError(code="UPSTREAM_ERROR", message=f"PaperQA execution failed: {error}") from error

    return state.get("result")


@contextmanager
def _scoped_env(overrides: dict[str, str]) -> Any:
    previous = {name: os.environ.get(name) for name in overrides}
    with _PAPERQA_ENV_LOCK:
        try:
            for name, value in overrides.items():
                os.environ[name] = value
            yield
        finally:
            for name, old in previous.items():
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old


@contextmanager
def _scoped_file_lock(lock_path: Path, *, timeout_seconds: float = 180.0) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover
        yield
        return

    lock_file = lock_path.open("a+", encoding="utf-8")
    started = time.time()
    locked = False
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if (time.time() - started) >= timeout_seconds:
                    raise ToolExecutionError(
                        code="TIMEOUT",
                        message=f"Timed out waiting for PaperQA cache lock: {lock_path.name}",
                        retryable=True,
                    )
                time.sleep(0.2)
        yield
    finally:
        if locked:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        lock_file.close()


def _runtime_paths(settings: Settings, ctx: ToolContext | None) -> dict[str, Path | str]:
    cache_root = source_cache_dir(ctx, "paperqa") if ctx is not None else None
    if cache_root is None:
        cache_root = Path(settings.source_cache_root) / "paperqa"
        cache_root.mkdir(parents=True, exist_ok=True)

    scope = _request_scope(ctx)
    thread_root = cache_root / "thread" / scope
    index_dir = thread_root / "indexes"
    papers_dir = thread_root / "papers"
    stats_dir = thread_root / "run_stats"
    lock_dir = thread_root / "locks"
    pqa_home = thread_root / "pqa_home"
    logs_dir = thread_root / "logs"
    openalex_log = logs_dir / "paperqa_openalex.log"
    for path in [thread_root, index_dir, papers_dir, stats_dir, lock_dir, pqa_home, logs_dir]:
        path.mkdir(parents=True, exist_ok=True)

    return {
        "scope": scope,
        "cache_root": cache_root,
        "thread_root": thread_root,
        "index_dir": index_dir,
        "papers_dir": papers_dir,
        "stats_dir": stats_dir,
        "lock_dir": lock_dir,
        "pqa_home": pqa_home,
        "openalex_log": openalex_log,
    }


def _apply_model_config_if_present(settings_obj: Any, field_name: str, model_name: str, api_key: str | None) -> None:
    if not hasattr(settings_obj, field_name):
        return
    resolved_model = _normalize_litellm_model_name(model_name)
    payload: dict[str, Any] = {"model": resolved_model}
    if api_key:
        payload["api_key"] = api_key
    setattr(
        settings_obj,
        field_name,
        {
            "model_list": [
                {
                    "model_name": resolved_model,
                    "litellm_params": payload,
                }
            ]
        },
    )


def _normalize_litellm_model_name(model_name: str) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        return raw
    if "/" in raw:
        return raw
    # LiteLLM expects provider-qualified names when no router/provider context is set.
    if raw.startswith("gemini-"):
        return f"gemini/{raw}"
    return raw


def _build_paperqa_settings(
    *,
    app_settings: Settings,
    payload: dict[str, Any],
    runtime: dict[str, Path | str],
    paperqa_settings_cls: type[Any],
    multimodal_options: Any | None,
    mode: str,
) -> Any:
    settings_obj = paperqa_settings_cls()

    llm_model = _normalize_litellm_model_name(app_settings.paperqa_model)
    workflow_model = _normalize_litellm_model_name(app_settings.paperqa_workflow_model)
    sub_agent_model = _normalize_litellm_model_name(app_settings.paperqa_sub_agent_model)
    pipeline_model = _normalize_litellm_model_name(app_settings.paperqa_pipeline_model)

    settings_obj.llm = llm_model
    settings_obj.summary_llm = workflow_model
    if hasattr(settings_obj, "verbosity"):
        settings_obj.verbosity = 0
    if hasattr(settings_obj, "embedding"):
        settings_obj.embedding = "sparse"

    # PaperQA nested model controls.
    if hasattr(settings_obj, "agent"):
        settings_obj.agent.agent_llm = sub_agent_model
        settings_obj.agent.external_search_provider = "openalex"
        settings_obj.agent.return_paper_metadata = True
        settings_obj.agent.search_count = _safe_int(
            payload.get("max_papers", payload.get("limit", 6)),
            default=6 if mode == "balanced" else (4 if mode == "precision" else 10),
            minimum=1,
            maximum=25,
        )
        settings_obj.agent.external_search_max_results = _safe_int(
            payload.get("external_search_max_results", settings_obj.agent.search_count * 2),
            default=max(12, settings_obj.agent.search_count * 2),
            minimum=settings_obj.agent.search_count,
            maximum=120,
        )
        settings_obj.agent.external_pdf_max_downloads = settings_obj.agent.search_count
        settings_obj.agent.html_ingest_enabled = False
        settings_obj.agent.jats_ingest_enabled = False
        settings_obj.agent.respect_robots_txt = False
        settings_obj.agent.allow_bronze = True
        settings_obj.agent.ignore_license_filter = True
        settings_obj.agent.headless_pdf_enabled = False
        settings_obj.agent.headless = True
        settings_obj.agent.per_work_resolution_budget_s = 50
        settings_obj.agent.per_link_timeout_s = 12
        settings_obj.agent.timeout = _safe_int(
            payload.get("timeout_seconds", app_settings.paperqa_timeout_seconds),
            default=app_settings.paperqa_timeout_seconds,
            minimum=_MIN_PAPERQA_TIMEOUT_SECONDS,
            maximum=1800,
        )
        settings_obj.agent.run_id = _safe_slug(
            payload.get("run_id") or str(runtime.get("scope") or "run"),
            default="run",
            max_len=80,
        )
        settings_obj.agent.run_stats_dir = str(runtime["stats_dir"])
        settings_obj.agent.fulltext_archive_dir = str(runtime["papers_dir"])
        if hasattr(settings_obj.agent, "index"):
            settings_obj.agent.index.paper_directory = str(runtime["papers_dir"])
            settings_obj.agent.index.index_directory = str(runtime["index_dir"])

    if hasattr(settings_obj, "parsing"):
        settings_obj.parsing.use_doc_details = False
        settings_obj.parsing.defer_embedding = True
        settings_obj.parsing.disable_doc_valid_check = True
        settings_obj.parsing.enrichment_llm = pipeline_model
        # Keep media parsing OFF for stability with the vendored pypdf backend.
        # Some PDFs trigger parser-level NotImplementedError when media parsing is enabled.
        if multimodal_options is not None and hasattr(multimodal_options, "OFF"):
            settings_obj.parsing.multimodal = multimodal_options.OFF
        else:
            settings_obj.parsing.multimodal = False

    # LiteLLM router configs where supported.
    default_key = app_settings.gemini_api_key or app_settings.anthropic_api_key
    _apply_model_config_if_present(settings_obj, "llm_config", llm_model, default_key)
    _apply_model_config_if_present(
        settings_obj,
        "summary_llm_config",
        workflow_model,
        default_key,
    )
    if hasattr(settings_obj, "agent"):
        _apply_model_config_if_present(
            settings_obj.agent,
            "agent_llm_config",
            sub_agent_model,
            default_key,
        )
    if hasattr(settings_obj, "parsing"):
        _apply_model_config_if_present(
            settings_obj.parsing,
            "enrichment_llm_config",
            pipeline_model,
            default_key,
        )

    return settings_obj


def _normalize_paperqa_result(raw: Any) -> Any:
    if isinstance(raw, asyncio.Task):
        if raw.done():
            return raw.result()
        raise ToolExecutionError(
            code="UPSTREAM_ERROR",
            message="PaperQA returned an unresolved async task; retry the request.",
            retryable=True,
        )
    return raw


def build_paperqa_literature_tools(settings: Settings) -> list[ToolSpec]:
    def search_pubmed_agent(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        query = str(payload.get("query") or payload.get("search_term") or "").strip()
        if not query:
            raise ToolExecutionError(code="VALIDATION_ERROR", message="'query' is required")

        mode = str(payload.get("mode") or "balanced").strip().lower() or "balanced"
        if mode not in {"precision", "balanced", "recall"}:
            mode = "balanced"

        min_year = _optional_int(payload.get("min_year", payload.get("from_year")))
        max_year = _optional_int(payload.get("max_year", payload.get("to_year")))
        if min_year is not None and max_year is not None and min_year > max_year:
            raise ToolExecutionError(
                code="VALIDATION_ERROR",
                message="'min_year' must be <= 'max_year'",
            )

        runtime = _runtime_paths(settings, ctx)
        final_query = _query_with_year_bounds(query, min_year, max_year)
        timeout_seconds = _safe_int(
            payload.get("timeout_seconds", settings.paperqa_timeout_seconds),
            default=settings.paperqa_timeout_seconds,
            minimum=_MIN_PAPERQA_TIMEOUT_SECONDS,
            maximum=1800,
        )

        paperqa_settings_cls, ask_fn, multimodal_options = _import_paperqa()
        paperqa_settings = _build_paperqa_settings(
            app_settings=settings,
            payload=payload,
            runtime=runtime,
            paperqa_settings_cls=paperqa_settings_cls,
            multimodal_options=multimodal_options,
            mode=mode,
        )

        env_overrides = {
            "PQA_HOME": str(runtime["pqa_home"]),
            "PAPERQA_OPENALEX_LOG": str(runtime["openalex_log"]),
        }
        if settings.openalex_api_key:
            env_overrides["OPENALEX_API_KEY"] = str(settings.openalex_api_key)
        if settings.openalex_mailto:
            env_overrides["OPENALEX_MAILTO"] = str(settings.openalex_mailto)

        lock_path = Path(runtime["lock_dir"]) / "paperqa.lock"
        with _scoped_file_lock(lock_path, timeout_seconds=min(180.0, float(timeout_seconds))):
            with _scoped_env(env_overrides):
                raw_answer = _run_with_timeout(
                    lambda: ask_fn(query=final_query, settings=paperqa_settings),
                    timeout_seconds=timeout_seconds,
                )
        answer = _normalize_paperqa_result(raw_answer)

        answer_dict = _as_json_dict(answer)
        session = answer_dict.get("session") if isinstance(answer_dict.get("session"), dict) else _as_json_dict(getattr(answer, "session", None))
        answer_text = str(session.get("answer") or session.get("formatted_answer") or session.get("raw_answer") or "").strip()
        if not answer_text:
            answer_text = "PaperQA did not produce a grounded answer for this query."

        citations, evidence, ids, collect_warnings = _collect_citations_and_evidence(session)
        warnings: list[str] = list(collect_warnings)

        dois_from_text, pmids_from_text, ncts_from_text = _extract_ids_from_text(answer_text)
        for doi in dois_from_text:
            ids.append(doi)
        for pmid in pmids_from_text:
            ids.append(f"PMID:{pmid}")
        for nct_id in ncts_from_text:
            ids.append(nct_id)
        ids = _dedupe_keep_order(ids)[:200]

        answer_limit = 16_000
        answer_truncated = len(answer_text) > answer_limit
        answer_for_output = answer_text[:answer_limit]
        if answer_truncated:
            warnings.append(f"Answer text truncated to {answer_limit} characters.")

        artifacts: list[dict[str, Any]] = []
        if ctx is not None:
            text_ref = write_text_file_artifact(ctx, "paperqa_answer.md", answer_text)
            if text_ref:
                artifacts.append(text_ref)

            session_ref = write_raw_json_artifact(
                ctx,
                "paperqa_session",
                {
                    "query": query,
                    "final_query": final_query,
                    "answer_response": answer_dict,
                    "runtime": {
                        "scope": runtime["scope"],
                        "cache_root": str(runtime["cache_root"]),
                        "index_dir": str(runtime["index_dir"]),
                        "papers_dir": str(runtime["papers_dir"]),
                        "stats_dir": str(runtime["stats_dir"]),
                        "lock_file": str(lock_path),
                        "openalex_log": str(runtime["openalex_log"]),
                    },
                },
            )
            if session_ref:
                artifacts.append(session_ref)

        summary = (
            f"PaperQA synthesized an answer with {len(citations)} cited paper context(s) "
            f"and {len(evidence)} evidence snippet(s)."
        )
        return make_tool_output(
            source="paperqa",
            summary=summary,
            result_kind="document",
            data={
                "query": query,
                "final_query": final_query,
                "mode": mode,
                "answer": answer_for_output,
                "answer_truncated": answer_truncated,
                "papers": citations,
                "evidence": evidence if bool(payload.get("include_contexts", True)) else [],
                "paper_count": len(citations),
                "paperqa": {
                    "status": answer_dict.get("status"),
                    "duration_seconds": answer_dict.get("duration"),
                    "stats": answer_dict.get("stats"),
                    "scope": runtime["scope"],
                    "cache_root": str(runtime["cache_root"]),
                    "index_dir": str(runtime["index_dir"]),
                    "papers_dir": str(runtime["papers_dir"]),
                    "stats_dir": str(runtime["stats_dir"]),
                },
            },
            ids=ids,
            citations=citations,
            warnings=warnings,
            artifacts=artifacts,
            data_schema_version="v2.1",
            auth_required=False,
            auth_configured=bool(settings.gemini_api_key or settings.anthropic_api_key),
            ctx=ctx,
        )

    synthesis_input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["precision", "balanced", "recall"],
                "default": "balanced",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 6},
            "max_papers": {"type": "integer", "minimum": 1, "maximum": 25},
            "external_search_max_results": {"type": "integer", "minimum": 10, "maximum": 120},
            "min_year": {"type": "integer", "minimum": 1800, "maximum": 2100},
            "max_year": {"type": "integer", "minimum": 1800, "maximum": 2100},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": _MIN_PAPERQA_TIMEOUT_SECONDS,
                        "maximum": 1800,
                        "default": _MIN_PAPERQA_TIMEOUT_SECONDS,
                    },
            "include_contexts": {"type": "boolean", "default": True},
        },
        "required": ["query"],
    }

    return [
        ToolSpec(
            name="literature_review_agent",
            description=(
                "WHEN: Use for final literature synthesis questions where you need an evidence answer, not only IDs."
                " This is the PRIMARY review tool and should be preferred over raw metadata tools.\n"
                "AVOID: Do not use this for simple PMID lookup or deterministic query debugging. For that use"
                " `pubmed_search` then `pubmed_fetch`/`fetch_pubmed`.\n"
                "CRITICAL_ARGS: query required; mode/limit/min_year/max_year/timeout_seconds are optional.\n"
                "RETURNS: Final contract v2.1 synthesized answer with citations + optional context snippets."
                " Example: `res = literature_review_agent(query='rapamycin human longevity evidence', mode='precision', limit=5)`.\n"
                "FAILS_IF: query is empty, PaperQA dependencies are missing, or execution times out."
            ),
            input_schema=synthesis_input_schema,
            handler=search_pubmed_agent,
            source="paperqa",
        ),
        ToolSpec(
            name="search_pubmed_agent",
            description=(
                "WHEN: Compatibility alias for `literature_review_agent`; use when legacy prompts/code still call this name.\n"
                "AVOID: Prefer `literature_review_agent` in new prompts to reduce confusion with `pubmed_search`/`pubmed_fetch`.\n"
                "CRITICAL_ARGS: same as `literature_review_agent`.\n"
                "RETURNS: Same final synthesis contract as `literature_review_agent`.\n"
                "FAILS_IF: same failure conditions as `literature_review_agent`."
            ),
            input_schema=synthesis_input_schema,
            handler=search_pubmed_agent,
            source="paperqa",
        ),
    ]
