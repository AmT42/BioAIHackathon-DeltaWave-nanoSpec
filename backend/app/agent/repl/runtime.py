from __future__ import annotations

import builtins
import contextlib
import io
import importlib.metadata
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.agent.tools.context import ToolContext
from app.agent.tools.registry import ToolRegistry
from app.agent.repl.shell import ShellExecutor, ShellPolicy
from app.agent.repl.types import IdListHandle, ReplExecutionResult, ToolResultHandle


ToolStartCallback = Callable[[str, str, dict[str, Any]], None]
ToolResultCallback = Callable[[str, str, dict[str, Any]], None]
TextStreamCallback = Callable[[str], None]
ImportCallback = Callable[[str, dict[str, Any] | None, dict[str, Any] | None, Any, int], Any]
LlmQueryHandler = Callable[..., str]
LlmQueryBatchHandler = Callable[..., list[dict[str, Any]]]

_MINIMAL_ALLOWED_IMPORT_ROOTS = {
    "collections",
    "datetime",
    "functools",
    "itertools",
    "json",
    "math",
    "pathlib",
    "random",
    "re",
    "statistics",
    "string",
    "textwrap",
    "typing",
}
_MINIMAL_ALLOWED_IMPORT_MODULES = {
    "urllib.parse",
}
_BROAD_EXTRA_IMPORT_ROOTS = {
    "aiohttp",
    "httpx",
    "requests",
    "urllib",
}
_BROAD_EXTRA_IMPORT_MODULES = {
    "urllib.error",
    "urllib.request",
}
_LAZY_INSTALL_PACKAGE_ALIASES = {
    "yaml": "pyyaml",
}
_SAFE_PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_DEFAULT_DENIED_IMPORT_MODULES = {
    "subprocess",
    "pty",
    "resource",
    "ctypes",
    "multiprocessing",
    "signal",
    "socket",
}
_KG_TOOL_NAMES = {"kg_cypher_execute", "kg_query"}
_KG_GATED_PUBMED_TOOLS = {"pubmed_search", "pubmed_esearch"}


def _safe_path_segment(value: str | None, fallback: str = "item") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)
    normalized = normalized.strip("._")
    return normalized or fallback


def _slug_from_text(value: str, *, fallback: str = "line", max_len: int = 56) -> str:
    compact = " ".join(str(value or "").split())
    if not compact:
        return fallback
    slug = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in compact).strip("_")
    if not slug:
        return fallback
    return slug[:max_len]


def _coerce_for_payload(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, IdListHandle):
        return value.to_list()
    if isinstance(value, ToolResultHandle):
        if key in {"ids", "pmids", "nct_ids"}:
            return value.ids.to_list()
        return value.data
    if isinstance(value, list):
        return [_coerce_for_payload(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_coerce_for_payload(item, key=key) for item in value]
    if isinstance(value, dict):
        return {str(k): _coerce_for_payload(v, key=str(k)) for k, v in value.items()}
    return value


class _ToolNamespace:
    pass


def _looks_sensitive_name(name: str, redact_keys: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(key and key in lowered for key in redact_keys)


def _preview_value(value: Any, *, max_chars: int) -> str:
    try:
        text = repr(value)
    except Exception:
        text = f"<unrepresentable {type(value).__name__}>"
    text = text.replace("\n", "\\n")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _snapshot_user_scope(
    globals_map: dict[str, Any],
    *,
    baseline_names: set[str],
    max_items: int,
    max_preview_chars: int,
    redact_keys: tuple[str, ...],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for name in sorted(globals_map.keys()):
        if name.startswith("_"):
            continue
        if name in baseline_names:
            continue
        value = globals_map.get(name)
        redacted = _looks_sensitive_name(name, redact_keys)
        entry = {
            "name": name,
            "type": type(value).__name__,
            "preview": "[REDACTED]" if redacted else _preview_value(value, max_chars=max_preview_chars),
        }
        if redacted:
            entry["redacted"] = True
        entries.append(entry)

    limited = entries[:max_items]
    return {
        "count": len(entries),
        "truncated": len(entries) > max_items,
        "items": limited,
    }


def _index_env_items(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return {}
    items = snapshot.get("items")
    if not isinstance(items, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        indexed[name] = item
    return indexed


def _build_env_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    max_items: int,
) -> dict[str, Any]:
    before_map = _index_env_items(before)
    after_map = _index_env_items(after)
    before_names = set(before_map.keys())
    after_names = set(after_map.keys())

    added = [after_map[name] for name in sorted(after_names - before_names)]
    removed = [before_map[name] for name in sorted(before_names - after_names)]
    updated: list[dict[str, Any]] = []
    for name in sorted(before_names & after_names):
        prev = before_map[name]
        nxt = after_map[name]
        if prev.get("type") != nxt.get("type") or prev.get("preview") != nxt.get("preview"):
            updated.append(nxt)

    return {
        "added_count": len(added),
        "updated_count": len(updated),
        "removed_count": len(removed),
        "added": added[:max_items],
        "updated": updated[:max_items],
        "removed": removed[:max_items],
        "truncated": any(len(items) > max_items for items in (added, updated, removed)),
    }


class _StreamingTextBuffer(io.TextIOBase):
    def __init__(self, on_chunk: TextStreamCallback | None = None) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._on_chunk = on_chunk

    def write(self, text: str) -> int:
        chunk = str(text)
        if not chunk:
            return 0
        self._chunks.append(chunk)
        if self._on_chunk is not None:
            try:
                self._on_chunk(chunk)
            except Exception:
                # Streaming callbacks are best-effort; execution must continue.
                pass
        return len(chunk)

    def flush(self) -> None:  # pragma: no cover - interface parity
        return None

    def tell(self) -> int:
        return len(self.getvalue())

    def getvalue(self) -> str:
        return "".join(self._chunks)


@dataclass
class _ExecutionHooks:
    on_tool_start: ToolStartCallback | None = None
    on_tool_result: ToolResultCallback | None = None
    run_id: str | None = None
    request_index: int | None = None
    user_msg_index: int | None = None
    parent_tool_use_id: str | None = None


class ReplBindings:
    def __init__(
        self,
        *,
        thread_id: str,
        tools: ToolRegistry,
        shell: ShellExecutor,
        import_hook: ImportCallback,
        max_wall_time_seconds: int,
        max_tool_calls_per_exec: int,
        llm_query_handler: LlmQueryHandler | None = None,
        llm_query_batch_handler: LlmQueryBatchHandler | None = None,
        enable_subagent_helpers: bool = False,
        subagent_stdout_line_soft_limit: int | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.tools = tools
        self.shell = shell
        self.import_hook = import_hook
        self.max_wall_time_seconds = max_wall_time_seconds
        self.max_tool_calls_per_exec = max_tool_calls_per_exec
        self.llm_query_handler = llm_query_handler
        self.llm_query_batch_handler = llm_query_batch_handler
        self.enable_subagent_helpers = bool(enable_subagent_helpers)
        self.subagent_stdout_line_soft_limit = (
            int(subagent_stdout_line_soft_limit) if isinstance(subagent_stdout_line_soft_limit, int) else None
        )
        self._hooks = _ExecutionHooks()
        self._nested_calls = 0
        registered = {str(name).strip().lower() for name in self.tools.names()}
        self._kg_gate_enabled = bool(registered & _KG_TOOL_NAMES)
        self._kg_pass_executed = False
        self._kg_unavailable = not self._kg_gate_enabled

    def set_execution_context(
        self,
        *,
        run_id: str | None,
        request_index: int | None,
        user_msg_index: int | None,
        parent_tool_use_id: str | None,
        on_tool_start: ToolStartCallback | None,
        on_tool_result: ToolResultCallback | None,
    ) -> None:
        self._hooks = _ExecutionHooks(
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
            run_id=run_id,
            request_index=request_index,
            user_msg_index=user_msg_index,
            parent_tool_use_id=parent_tool_use_id,
        )
        self._nested_calls = 0

    def nested_call_count(self) -> int:
        return self._nested_calls

    def llm_query(
        self,
        task: str,
        *,
        env: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        custom_instruction: str | None = None,
        allow_repl: bool = True,
        allow_bash: bool = True,
        max_iterations: int | None = None,
    ) -> str:
        if not self.enable_subagent_helpers or self.llm_query_handler is None:
            raise RuntimeError("llm_query(...) is unavailable in this REPL runtime.")
        task_text = str(task or "").strip()
        if not task_text:
            raise ValueError("llm_query requires non-empty task")
        env_payload = env if isinstance(env, dict) else None
        tool_payload = allowed_tools if isinstance(allowed_tools, list) else None
        return self.llm_query_handler(
            thread_id=self.thread_id,
            run_id=self._hooks.run_id,
            request_index=self._hooks.request_index,
            user_msg_index=self._hooks.user_msg_index,
            parent_tool_use_id=self._hooks.parent_tool_use_id,
            task=task_text,
            env=env_payload,
            allowed_tools=tool_payload,
            custom_instruction=(str(custom_instruction) if isinstance(custom_instruction, str) else None),
            allow_repl=bool(allow_repl),
            allow_bash=bool(allow_bash),
            max_iterations=int(max_iterations) if isinstance(max_iterations, int) else None,
        )

    def llm_query_batch(
        self,
        tasks: list[str | dict[str, Any]],
        *,
        shared_env: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        custom_instruction: str | None = None,
        allow_repl: bool = True,
        allow_bash: bool = True,
        max_iterations: int | None = None,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enable_subagent_helpers or self.llm_query_batch_handler is None:
            raise RuntimeError("llm_query_batch(...) is unavailable in this REPL runtime.")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("llm_query_batch requires a non-empty tasks list")
        env_payload = shared_env if isinstance(shared_env, dict) else None
        tool_payload = allowed_tools if isinstance(allowed_tools, list) else None
        return self.llm_query_batch_handler(
            thread_id=self.thread_id,
            run_id=self._hooks.run_id,
            request_index=self._hooks.request_index,
            user_msg_index=self._hooks.user_msg_index,
            parent_tool_use_id=self._hooks.parent_tool_use_id,
            tasks=tasks,
            shared_env=env_payload,
            allowed_tools=tool_payload,
            custom_instruction=(str(custom_instruction) if isinstance(custom_instruction, str) else None),
            allow_repl=bool(allow_repl),
            allow_bash=bool(allow_bash),
            max_iterations=int(max_iterations) if isinstance(max_iterations, int) else None,
            max_workers=int(max_workers) if isinstance(max_workers, int) else None,
        )

    def _tool_properties(self, tool_name: str) -> dict[str, Any]:
        spec = self.tools.get_spec(tool_name)
        if spec is None:
            return {}
        schema = spec.input_schema if isinstance(spec.input_schema, dict) else {}
        properties = schema.get("properties")
        return properties if isinstance(properties, dict) else {}

    def _tool_required(self, tool_name: str) -> set[str]:
        spec = self.tools.get_spec(tool_name)
        if spec is None:
            return set()
        schema = spec.input_schema if isinstance(spec.input_schema, dict) else {}
        required = schema.get("required")
        if not isinstance(required, list):
            return set()
        return {str(item) for item in required}

    def _merge_field_for_tool_result(self, tool_name: str) -> str | None:
        normalized = str(tool_name or "").strip().lower()
        if normalized in {"normalize_drug", "rxnorm_resolve"}:
            return "drug_candidates"
        if normalized in {"normalize_compound", "pubchem_resolve"}:
            return "compound_candidates"
        if normalized in {"normalize_ontology", "ols_search_terms"}:
            return "ontology_candidates"
        return None

    def _infer_user_text_from_candidate(self, value: Any) -> str | None:
        if isinstance(value, ToolResultHandle):
            data = value.data
            if isinstance(data, dict):
                query = data.get("query")
                if isinstance(query, str) and query.strip():
                    return query.strip()
        if isinstance(value, dict):
            for key in ("query", "term", "user_text"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return None

    def _coerce_merge_candidates_positional(self, arg: Any) -> dict[str, Any]:
        if isinstance(arg, ToolResultHandle):
            field = self._merge_field_for_tool_result(arg.tool_name)
            if field is None:
                raise ValueError(
                    "Unsupported positional ToolResultHandle for 'normalize_merge_candidates'. "
                    "Pass normalization handles from normalize_drug/normalize_compound/normalize_ontology."
                )
            user_text = self._infer_user_text_from_candidate(arg)
            payload: dict[str, Any] = {field: arg.data}
            if user_text:
                payload["user_text"] = user_text
            return payload

        if isinstance(arg, list):
            payload: dict[str, Any] = {}
            for item in arg:
                if isinstance(item, ToolResultHandle):
                    field = self._merge_field_for_tool_result(item.tool_name)
                    if field and field not in payload:
                        payload[field] = item.data
                    if "user_text" not in payload:
                        inferred = self._infer_user_text_from_candidate(item)
                        if inferred:
                            payload["user_text"] = inferred
                    continue
                if isinstance(item, dict):
                    for key in ("drug_candidates", "compound_candidates", "ontology_candidates", "user_text"):
                        if key in item and key not in payload:
                            payload[key] = _coerce_for_payload(item.get(key), key=key)
                    if "user_text" not in payload:
                        inferred = self._infer_user_text_from_candidate(item)
                        if inferred:
                            payload["user_text"] = inferred
                    continue
                raise ValueError(
                    "Unsupported list element for 'normalize_merge_candidates'. "
                    "Expected ToolResultHandle or dict items."
                )
            if "user_text" not in payload:
                raise ValueError(
                    "normalize_merge_candidates requires 'user_text'. "
                    "Pass user_text explicitly or include a normalization result with a query."
                )
            return payload

        raise ValueError(
            "Unsupported positional argument for 'normalize_merge_candidates'. "
            "Use a normalization ToolResultHandle, list of handles, or keyword args."
        )

    def _coerce_single_positional(self, tool_name: str, arg: Any) -> dict[str, Any]:
        if tool_name == "normalize_merge_candidates":
            return self._coerce_merge_candidates_positional(arg)
        if tool_name == "retrieval_build_query_terms" and isinstance(arg, ToolResultHandle):
            concept_payload = arg.data
            if isinstance(concept_payload, dict) and isinstance(concept_payload.get("concept"), dict):
                concept_payload = concept_payload.get("concept")
            return {"concept": concept_payload}
        if tool_name == "retrieval_build_pubmed_templates":
            if isinstance(arg, ToolResultHandle):
                data = arg.data
                if isinstance(data, dict):
                    terms_obj = data.get("terms") if isinstance(data.get("terms"), dict) else data
                    if isinstance(terms_obj.get("pubmed"), list):
                        return {"intervention_terms": terms_obj.get("pubmed")}
                return {"terms": data}
            if isinstance(arg, list):
                return {"intervention_terms": _coerce_for_payload(arg, key="intervention_terms")}
        if isinstance(arg, dict):
            return _coerce_for_payload(arg)  # type: ignore[assignment]
        if isinstance(arg, IdListHandle):
            return {"ids": arg.to_list()}
        if isinstance(arg, ToolResultHandle):
            return {"ids": arg.ids.to_list()}

        props = self._tool_properties(tool_name)
        required = self._tool_required(tool_name)

        if isinstance(arg, list):
            if "ids" in props:
                return {"ids": _coerce_for_payload(arg, key="ids")}
            raise ValueError(
                f"Unsupported list positional argument for '{tool_name}'. "
                "This tool does not declare an 'ids' field."
            )

        if isinstance(arg, str):
            for candidate in ("query", "term", "command", "expression"):
                if candidate in props:
                    return {candidate: arg}

            if len(required) == 1:
                only = next(iter(required))
                return {only: arg}
            if len(props) == 1:
                only = next(iter(props.keys()))
                return {only: arg}

            raise ValueError(
                f"Unsupported string positional argument for '{tool_name}'. "
                "Use keyword args matching tool schema."
            )

        raise ValueError(
            f"Unsupported positional argument for '{tool_name}'. "
            "Use keyword args matching tool schema."
        )

    def _normalize_payload(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = {str(k): _coerce_for_payload(v, key=str(k)) for k, v in payload.items()}

        def _first_nonempty_text(value: Any) -> str | None:
            if isinstance(value, str):
                text = value.strip()
                return text or None
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        text = item.strip()
                        if text:
                            return text
            return None

        # Common ergonomic aliases
        if "max_results" in normalized and "limit" not in normalized:
            normalized["limit"] = normalized.pop("max_results")
        if "pmids" in normalized and "ids" not in normalized:
            normalized["ids"] = normalized.pop("pmids")
        if "nct_ids" in normalized and "ids" not in normalized:
            normalized["ids"] = normalized.pop("nct_ids")
        if "setid" in normalized and "ids" not in normalized:
            setid_value = normalized.pop("setid")
            if isinstance(setid_value, list):
                normalized["ids"] = setid_value
            elif isinstance(setid_value, str):
                stripped = setid_value.strip()
                if stripped:
                    normalized["ids"] = [stripped]
        if tool_name == "pubmed_search" and "term" in normalized and "query" not in normalized:
            normalized["query"] = normalized.pop("term")
        if tool_name == "kg_cypher_execute":
            if "cypher" not in normalized:
                for alias in ("query", "statement", "cypher_query", "command", "expression"):
                    alias_value = _first_nonempty_text(normalized.get(alias))
                    if alias_value:
                        normalized["cypher"] = alias_value
                        break
            cypher_payload = normalized.get("cypher")
            if isinstance(cypher_payload, dict):
                nested = _first_nonempty_text(cypher_payload.get("cypher")) or _first_nonempty_text(
                    cypher_payload.get("query")
                )
                if nested:
                    normalized["cypher"] = nested
            elif isinstance(cypher_payload, list):
                nested = _first_nonempty_text(cypher_payload)
                if nested:
                    normalized["cypher"] = nested
        if tool_name == "clinicaltrials_search":
            for source_key, target_key in (
                ("query.term", "query"),
                ("query.intr", "intervention"),
                ("query.cond", "condition"),
            ):
                if source_key in normalized and target_key not in normalized:
                    normalized[target_key] = normalized.pop(source_key)
            query_payload = normalized.get("query")
            if isinstance(query_payload, dict):
                term_value = query_payload.get("term")
                if isinstance(term_value, str) and term_value.strip():
                    normalized["query"] = term_value.strip()
                if "intervention" not in normalized:
                    intr_value = query_payload.get("intr") or query_payload.get("intervention")
                    if isinstance(intr_value, str) and intr_value.strip():
                        normalized["intervention"] = intr_value.strip()
                if "condition" not in normalized:
                    cond_value = query_payload.get("cond") or query_payload.get("condition")
                    if isinstance(cond_value, str) and cond_value.strip():
                        normalized["condition"] = cond_value.strip()
            elif isinstance(query_payload, list):
                list_query = _first_nonempty_text(query_payload)
                if list_query:
                    normalized["query"] = list_query

            if "query" not in normalized:
                clinical_terms = _first_nonempty_text(normalized.get("clinicaltrials"))
                if clinical_terms:
                    normalized["query"] = clinical_terms
                else:
                    terms_payload = normalized.get("terms")
                    if isinstance(terms_payload, dict):
                        inferred = _first_nonempty_text(terms_payload.get("clinicaltrials")) or _first_nonempty_text(
                            terms_payload.get("pubmed")
                        )
                        if inferred:
                            normalized["query"] = inferred

            for field in ("query", "intervention", "condition"):
                field_value = normalized.get(field)
                if isinstance(field_value, list):
                    inferred = _first_nonempty_text(field_value)
                    if inferred:
                        normalized[field] = inferred

        if tool_name == "retrieval_build_query_terms":
            concept_payload = normalized.get("concept")
            if isinstance(concept_payload, dict) and isinstance(concept_payload.get("concept"), dict):
                normalized["concept"] = concept_payload["concept"]
            if "label" not in normalized and isinstance(normalized.get("concept"), dict):
                label = str((normalized.get("concept") or {}).get("label") or "").strip()
                if label:
                    normalized["label"] = label

        if tool_name == "retrieval_build_pubmed_templates":
            terms_payload = normalized.get("terms")
            if isinstance(terms_payload, dict) and isinstance(terms_payload.get("terms"), dict):
                normalized["terms"] = terms_payload.get("terms")

            terms_payload = normalized.get("terms")
            if "intervention_terms" not in normalized:
                if isinstance(terms_payload, list):
                    normalized["intervention_terms"] = terms_payload
                elif isinstance(terms_payload, str):
                    stripped = terms_payload.strip()
                    if stripped:
                        normalized["intervention_terms"] = [stripped]

            intervention_terms = normalized.get("intervention_terms")
            if isinstance(intervention_terms, dict):
                source_terms = intervention_terms.get("terms") if isinstance(intervention_terms.get("terms"), dict) else intervention_terms
                if isinstance(source_terms, dict):
                    if isinstance(source_terms.get("pubmed"), list):
                        normalized["intervention_terms"] = source_terms.get("pubmed")
                    elif isinstance(source_terms.get("intervention"), list):
                        normalized["intervention_terms"] = source_terms.get("intervention")
            elif isinstance(intervention_terms, str):
                stripped = intervention_terms.strip()
                if stripped:
                    normalized["intervention_terms"] = [stripped]
            if "intervention_terms" not in normalized:
                terms_obj = normalized.get("terms")
                if isinstance(terms_obj, dict):
                    if isinstance(terms_obj.get("pubmed"), list):
                        normalized["intervention_terms"] = terms_obj.get("pubmed")
                    elif isinstance(terms_obj.get("intervention"), list):
                        normalized["intervention_terms"] = terms_obj.get("intervention")
            if "intervention_terms" not in normalized:
                top_level_terms = normalized.get("pubmed") or normalized.get("intervention") or normalized.get("query_terms")
                if isinstance(top_level_terms, list):
                    normalized["intervention_terms"] = top_level_terms
                elif isinstance(top_level_terms, str):
                    stripped = top_level_terms.strip()
                    if stripped:
                        normalized["intervention_terms"] = [stripped]

        if tool_name == "longevity_drugage_query":
            if "query" not in normalized:
                for alias in ("compound_name", "compound", "name", "intervention", "term"):
                    value = normalized.get(alias)
                    if isinstance(value, str) and value.strip():
                        normalized["query"] = value.strip()
                        break

        return normalized

    def _tool_error_hint(self, tool_name: str, error_message: str) -> str | None:
        lowered = str(error_message or "").lower()
        if tool_name == "kg_cypher_execute" and "cypher" in lowered and "required" in lowered:
            return "Hint: call `kg_cypher_execute(cypher='MATCH ... RETURN ...')` (or pass `query='...'`)."
        if tool_name == "retrieval_build_query_terms" and "concept.label" in lowered:
            return (
                "Hint: pass concept explicitly, e.g. "
                "`terms = retrieval_build_query_terms(concept=merged.data.get('concept'))`."
            )
        if tool_name == "retrieval_build_pubmed_templates" and "intervention_terms" in lowered:
            return (
                "Hint: pass terms/intervention terms explicitly, e.g. "
                "`tpl = retrieval_build_pubmed_templates(terms=terms.data.get('terms'))` "
                "then read `tpl.data['queries']`."
            )
        if tool_name == "clinicaltrials_search" and "provide at least one of" in lowered:
            return (
                "Hint: use `clinicaltrials_search(query='...', intervention='...')` "
                "instead of nested query objects."
            )
        if tool_name == "normalize_merge_candidates" and "user_text" in lowered:
            return (
                "Hint: include `user_text='...'` or pass normalization handles where query can be inferred."
            )
        if tool_name == "longevity_itp_fetch_summary" and "ids" in lowered and "non-empty list" in lowered:
            return (
                "Hint: pass ITP summary URLs explicitly, e.g. "
                "`longevity_itp_fetch_summary(ids=['<itp_summary_url>'])`. "
                "Do not call this tool without ids."
            )
        if tool_name == "longevity_drugage_query" and "'query' is required" in lowered:
            return (
                "Hint: use `longevity_drugage_query(query='metformin')`. "
                "Aliases like `compound_name` are accepted, but `query` is canonical."
            )
        return None

    def _run_tool(self, tool_name: str, payload: dict[str, Any]) -> ToolResultHandle:
        normalized_tool_name = str(tool_name or "").strip().lower()
        if (
            normalized_tool_name in _KG_GATED_PUBMED_TOOLS
            and self._kg_gate_enabled
            and not self._kg_pass_executed
            and not self._kg_unavailable
        ):
            raise RuntimeError(
                "KG-first gate: run `kg_cypher_execute(...)` before PubMed search when KG tools are available. "
                "If KG is unconfigured, call KG once, report that, then continue with PubMed."
            )

        self._nested_calls += 1
        if self._nested_calls > self.max_tool_calls_per_exec:
            raise RuntimeError(
                f"Exceeded nested tool call limit ({self.max_tool_calls_per_exec}) in one REPL execution"
            )

        nested_call_id = f"{self._hooks.parent_tool_use_id or 'repl'}:nested:{self._nested_calls:04d}"
        if self._hooks.on_tool_start is not None:
            self._hooks.on_tool_start(nested_call_id, tool_name, payload)

        result = self.tools.execute(
            tool_name,
            payload,
            ctx=ToolContext(
                thread_id=self.thread_id,
                run_id=self._hooks.run_id,
                request_index=self._hooks.request_index,
                user_msg_index=self._hooks.user_msg_index,
                tool_use_id=nested_call_id,
                tool_name=tool_name,
            ),
        )

        if normalized_tool_name in _KG_TOOL_NAMES:
            if result.get("status") == "success":
                self._kg_pass_executed = True
            else:
                error = result.get("error")
                error_payload = error if isinstance(error, dict) else {}
                code = str(error_payload.get("code") or "").strip().upper()
                if code == "UNCONFIGURED":
                    self._kg_unavailable = True

        if self._hooks.on_tool_result is not None:
            self._hooks.on_tool_result(nested_call_id, tool_name, result)

        if result.get("status") != "success":
            error = result.get("error") or {}
            hint = self._tool_error_hint(tool_name, str(error.get("message") or ""))
            suffix = f" {hint}" if hint else ""
            raise RuntimeError(f"Tool '{tool_name}' failed: {error}.{suffix}".rstrip())

        output = result.get("output")
        if not isinstance(output, dict):
            raise RuntimeError(f"Tool '{tool_name}' returned malformed output")
        return ToolResultHandle(tool_name=tool_name, payload=output, raw_result=result)

    def tool_wrapper(self, tool_name: str) -> Callable[..., ToolResultHandle]:
        def _wrapped(*args: Any, **kwargs: Any) -> ToolResultHandle:
            payload: dict[str, Any]
            if kwargs and args:
                if len(args) != 1:
                    raise ValueError(
                        f"Unsupported positional arguments for '{tool_name}'. "
                        "Use at most one positional arg + keyword args."
                    )
                inferred = self._coerce_single_positional(tool_name, args[0])
                payload = {**inferred, **{str(k): _coerce_for_payload(v, key=str(k)) for k, v in kwargs.items()}}
            elif kwargs:
                payload = {str(k): _coerce_for_payload(v, key=str(k)) for k, v in kwargs.items()}
            elif len(args) == 1:
                payload = self._coerce_single_positional(tool_name, args[0])
            elif len(args) == 0:
                payload = {}
            else:
                raise ValueError(f"Unsupported positional arguments for '{tool_name}'. Use keyword args.")
            payload = self._normalize_payload(tool_name, payload)
            return self._run_tool(tool_name, payload)

        _wrapped.__name__ = tool_name
        _wrapped.__doc__ = f"Programmatic wrapper for tool '{tool_name}'. Returns ToolResultHandle."
        return _wrapped

    def run_bash(self, command: str, *, timeout_s: int = 30, cwd: str | None = None):
        return self.shell.run(command, timeout_s=timeout_s, cwd=cwd)

    def run_grep(
        self,
        pattern: str,
        *,
        path: str = ".",
        glob: str = "**/*",
        ignore_case: bool = False,
        timeout_s: int = 30,
    ):
        return self.shell.grep(
            pattern,
            path=path,
            glob=glob,
            ignore_case=ignore_case,
            timeout_s=timeout_s,
        )

    def parallel_map(self, fn: Callable[[Any], Any], items: list[Any], *, max_workers: int = 8) -> list[Any]:
        if max_workers < 1:
            max_workers = 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(fn, items))


@dataclass
class ReplSessionState:
    thread_id: str
    globals: dict[str, Any] = field(default_factory=dict)
    bindings: ReplBindings | None = None
    baseline_names: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ReplSessionManager:
    def __init__(self, *, max_sessions: int = 200, session_ttl_seconds: int = 3600) -> None:
        self.max_sessions = max_sessions
        self.session_ttl_seconds = session_ttl_seconds
        self._lock = threading.RLock()
        self._sessions: dict[str, ReplSessionState] = {}

    def _cleanup(self) -> None:
        now = time.time()
        expired = [
            key
            for key, session in self._sessions.items()
            if now - session.updated_at > self.session_ttl_seconds
        ]
        for key in expired:
            self._sessions.pop(key, None)

        if len(self._sessions) <= self.max_sessions:
            return
        ordered = sorted(self._sessions.values(), key=lambda item: item.updated_at)
        to_remove = len(self._sessions) - self.max_sessions
        for session in ordered[:to_remove]:
            self._sessions.pop(session.thread_id, None)

    def get_or_create(
        self,
        *,
        thread_id: str,
        tools: ToolRegistry,
        shell: ShellExecutor,
        import_hook: ImportCallback,
        max_wall_time_seconds: int,
        max_tool_calls_per_exec: int,
        llm_query_handler: LlmQueryHandler | None = None,
        llm_query_batch_handler: LlmQueryBatchHandler | None = None,
        enable_subagent_helpers: bool = False,
        subagent_stdout_line_soft_limit: int | None = None,
    ) -> ReplSessionState:
        with self._lock:
            self._cleanup()
            session = self._sessions.get(thread_id)
            if session is not None and session.bindings is not None:
                session.bindings.import_hook = import_hook
                session.updated_at = time.time()
                return session

            bindings = ReplBindings(
                thread_id=thread_id,
                tools=tools,
                shell=shell,
                import_hook=import_hook,
                max_wall_time_seconds=max_wall_time_seconds,
                max_tool_calls_per_exec=max_tool_calls_per_exec,
                llm_query_handler=llm_query_handler,
                llm_query_batch_handler=llm_query_batch_handler,
                enable_subagent_helpers=enable_subagent_helpers,
                subagent_stdout_line_soft_limit=subagent_stdout_line_soft_limit,
            )
            globals_map = _build_base_globals(bindings)
            baseline_names = set(globals_map.keys())
            globals_map["__repl_baseline_names__"] = baseline_names
            session = ReplSessionState(
                thread_id=thread_id,
                globals=globals_map,
                bindings=bindings,
                baseline_names=baseline_names,
            )
            self._sessions[thread_id] = session
            return session


def _bash_disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(
        "bash(...) is disabled inside repl_exec. "
        "Do not run shell in Python blocks; use the top-level bash_exec tool instead."
    )


def _bash_exec_disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(
        "bash_exec(...) cannot be called from inside repl_exec Python code. "
        "Run bash_exec as a separate top-level tool call: bash_exec(command='...')."
    )


def _grep_disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("grep(...) is disabled inside repl_exec. Use bash_exec with an rg command.")


def _example_value_for_schema(schema: dict[str, Any]) -> Any:
    value_type = str(schema.get("type") or "")
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]
    if "default" in schema:
        return schema.get("default")
    if value_type == "string":
        return "..."
    if value_type == "integer":
        return 1
    if value_type == "number":
        return 1.0
    if value_type == "boolean":
        return False
    if value_type == "array":
        return []
    if value_type == "object":
        return {}
    return "..."


def _build_tool_example(
    *,
    tool_name: str,
    properties: dict[str, Any],
    required: list[str],
) -> str:
    ordered_keys = [str(name) for name in required]
    for name in sorted(properties.keys()):
        text = str(name)
        if text not in ordered_keys:
            ordered_keys.append(text)
    args: list[str] = []
    for name in ordered_keys[:4]:
        schema = properties.get(name) if isinstance(properties.get(name), dict) else {}
        args.append(f"{name}={repr(_example_value_for_schema(schema))}")
    return f"{tool_name}({', '.join(args)})"


def _build_base_globals(bindings: ReplBindings) -> dict[str, Any]:
    safe_builtin_names = {
        "abs",
        "all",
        "any",
        "bool",
        "callable",
        "dict",
        "dir",
        "enumerate",
        "Exception",
        "float",
        "format",
        "getattr",
        "globals",
        "hasattr",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "min",
        "next",
        "object",
        "open",
        "print",
        "repr",
        "range",
        "reversed",
        "round",
        "setattr",
        "slice",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "ValueError",
        "RuntimeError",
        "KeyError",
        "IndexError",
        "zip",
    }
    safe_builtins = {name: getattr(builtins, name) for name in safe_builtin_names}
    safe_builtins["__import__"] = bindings.import_hook

    def _help_tool(tool_name: str) -> dict[str, Any]:
        spec = bindings.tools.get_spec(str(tool_name))
        if spec is None:
            return {"error": f"Unknown tool '{tool_name}'", "available_tools": sorted(bindings.tools.names())}
        schema = spec.input_schema if isinstance(spec.input_schema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        property_hints: dict[str, Any] = {}
        for raw_name, raw_schema in properties.items():
            name = str(raw_name)
            schema_obj = raw_schema if isinstance(raw_schema, dict) else {}
            hint: dict[str, Any] = {
                "type": schema_obj.get("type"),
                "required": name in {str(item) for item in required},
            }
            if "default" in schema_obj:
                hint["default"] = schema_obj.get("default")
            if isinstance(schema_obj.get("enum"), list):
                hint["enum"] = schema_obj.get("enum")
            property_hints[name] = hint
        return {
            "name": spec.name,
            "required_args": [str(item) for item in required],
            "properties": property_hints,
            "source": spec.source,
            "example": _build_tool_example(
                tool_name=spec.name,
                properties={str(k): v for k, v in properties.items() if isinstance(v, dict)},
                required=[str(item) for item in required],
            ),
        }

    def _help_examples(topic: str = "longevity") -> dict[str, Any]:
        normalized_topic = str(topic or "longevity").strip().lower()
        examples: dict[str, list[str]] = {
            "longevity": [
                "res = normalize_ontology(query='Hyperbaric oxygen therapy', limit=5)",
                "print(res.preview())",
                "merged = normalize_merge_candidates([res], user_text='Hyperbaric oxygen therapy')",
                "terms = retrieval_build_query_terms(concept=merged.data.get('concept'))",
                "print(terms.preview())",
                "kg0 = kg_cypher_execute(cypher='MATCH (i)-[r]-(n) RETURN i,r,n LIMIT 25')",
                "print(kg0.preview())",
                "templates = retrieval_build_pubmed_templates(terms=terms.data.get('terms'), outcome_terms=['aging', 'healthspan'])",
                "queries = templates.data.get('queries', {})",
                "pm = pubmed_search(query=queries.get('systematic_reviews', ''), limit=8)",
                "docs = pubmed_fetch(ids=pm.ids.head(5), include_abstract=True)",
                "print(docs.shape())",
                "for row in docs: print(row.get('pmid'), row.get('title'))",
                "kg1 = kg_cypher_execute(cypher='MATCH (n)-[r]-(m) RETURN n,r,m LIMIT 25')",
                "print(kg1.preview())",
            ],
            "pubmed": [
                "kg = kg_cypher_execute(cypher='MATCH (i)-[r]-(n) RETURN i,r,n LIMIT 25')",
                "print(kg.preview())",
                "pm = pubmed_search(query='exercise AND alzheimer', limit=5)",
                "docs = pubmed_fetch(ids=pm.ids.head(3), include_abstract=True)",
                "print(docs.preview())",
            ],
            "trials": [
                "hits = clinicaltrials_search(query='hyperbaric oxygen therapy aging', limit=5)",
                "trials = clinicaltrials_fetch(ids=hits.ids.head(3))",
                "print(trials.shape())",
            ],
            "shell_vs_repl": [
                "SHELL TOOL: bash_exec(command=\"rg -n 'normalize_merge_candidates' backend/app\")",
                "SHELL TOOL: bash_exec(command=\"sed -n '1,140p' backend/app/agent/core.py\")",
                "SHELL TOOL: bash_exec(command=\"curl -sS 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&retmax=3&term=metformin+aging' | jq .esearchresult.idlist\")",
                "SHELL TOOL: bash_exec(command=\"curl -sS 'https://clinicaltrials.gov/api/v2/studies?query.term=metformin&query.intr=metformin&pageSize=3' | jq '.studies | length'\")",
                "SHELL TOOL: bash_exec(command=\"wget -qO- 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=32333835' | jq '.result.uids'\")",
                "REPL CODE: kg = kg_cypher_execute(cypher='MATCH (i)-[r]-(n) RETURN i,r,n LIMIT 25'); res = pubmed_search(query='exercise AND alzheimer', limit=5); print(kg.preview()); print(res.preview())",
            ],
            "subagents": [
                "ids = pubmed_search(query='metformin aging', limit=20).ids",
                "summary = llm_query('Use env ids to inspect strongest RCT signal with citations.', env={'ids': ids}, allowed_tools=['pubmed_fetch', 'evidence_classify_studies'])",
                "print(summary)",
                "tasks = [",
                "  {'task': 'Find strongest human evidence for metformin in aging', 'allowed_tools': ['pubmed_search', 'pubmed_fetch']},",
                "  {'task': 'Find ongoing CT.gov aging trials for metformin', 'allowed_tools': ['clinicaltrials_search', 'clinicaltrials_fetch']},",
                "]",
                "batch = llm_query_batch(tasks, max_workers=2)",
                "for row in batch: print(row['ok'], row['trace_path'])",
            ],
        }
        if normalized_topic not in examples:
            normalized_topic = "longevity"
        return {"topic": normalized_topic, "examples": examples[normalized_topic], "available_topics": sorted(examples.keys())}

    def _env_vars() -> dict[str, Any]:
        baseline = globals_map.get("__repl_baseline_names__")
        baseline_names = baseline if isinstance(baseline, set) else set()
        return _snapshot_user_scope(
            globals_map,
            baseline_names=baseline_names,
            max_items=40,
            max_preview_chars=120,
            redact_keys=("api_key", "token", "secret", "password", "auth", "cookie"),
        )

    def _runtime_info() -> dict[str, Any]:
        mode = str(bindings.shell.policy.mode or "open").strip().lower()
        blocked = {str(item).strip().lower() for item in bindings.shell.policy.blocked_prefixes}
        allowed = {str(item).strip().lower() for item in bindings.shell.policy.allowed_prefixes}
        can_edit_workspace_files = mode == "open" or any(
            item in allowed for item in {"python", "python3", "bash", "cat", "cp", "mv", "sed", "awk", "tee"}
        )
        can_use_curl_wget = (
            (mode == "open" or "curl" in allowed) and "curl" not in blocked
        ) or ((mode == "open" or "wget" in allowed) and "wget" not in blocked)
        info = {
            "python_version": sys.version.split(" ", 1)[0],
            "shell_policy_mode": mode,
            "workspace_root": str(bindings.shell.policy.workspace_root),
            "bash_allowed_prefixes": sorted(str(item) for item in bindings.shell.policy.allowed_prefixes),
            "bash_blocked_prefixes": sorted(str(item) for item in bindings.shell.policy.blocked_prefixes),
            "bash_blocked_patterns": sorted(str(item) for item in bindings.shell.policy.blocked_patterns),
            "can_edit_workspace_files": can_edit_workspace_files,
            "can_use_curl_wget": can_use_curl_wget,
            "execution_limits": {
                "max_wall_time_s": bindings.max_wall_time_seconds,
                "max_stdout_bytes": bindings.shell.policy.max_output_bytes,
                "max_tool_calls_per_exec": bindings.max_tool_calls_per_exec,
            },
            "available_tools": sorted(bindings.tools.names()),
            "helpers": [
                "help_repl",
                "help_tools",
                "help_tool",
                "help_examples",
                "installed_packages",
                "env_vars",
                "runtime_info",
            ],
        }
        if bindings.enable_subagent_helpers:
            info["helpers"].extend(["llm_query", "llm_query_batch"])
            info["subagent_limits"] = {
                "stdout_line_soft_limit": bindings.subagent_stdout_line_soft_limit,
            }
        return info

    def _help_repl_text() -> str:
        text = (
            "Use repl_exec for Python wrappers/data transforms and bash_exec for shell commands.\n"
            "Use bash_exec for navigation (`rg`, `ls`, `cat`), file workflow, and vendor API calls (`curl`/`wget`).\n"
            "Important: bash_exec is a top-level tool call, not a Python function inside repl_exec blocks.\n"
            "Do not import internal project modules in REPL; wrappers are already available as global callables.\n"
            "Call help_tools() / help_tool('name') when unsure about wrapper signatures.\n"
            "Use help_examples('longevity') and help_examples('shell_vs_repl') for safe workflow snippets.\n"
            "Call runtime_info() for Python/workspace/tool/shell policy details.\n"
            "Call installed_packages(limit=200) on first turn to inspect Python packages in this runtime.\n"
            "Use env_vars() to inspect current user-defined REPL variables (name/type/preview).\n"
            "Search tools usually take query + limit (or term + retmax aliases).\n"
            "Fetch tools usually take ids (aliases pmids/nct_ids are accepted).\n"
            "longevity_itp_fetch_summary is strict: ids must be a non-empty list of ITP summary URLs.\n"
            "Handles expose ids.head(n), shape(), records/items/studies convenience accessors.\n"
        )
        if bindings.enable_subagent_helpers:
            text += (
                "Use llm_query(...) / llm_query_batch(...) for sub-agent fan-out exploration. "
                f"Sub-agent REPL line cap is {bindings.subagent_stdout_line_soft_limit} chars.\n"
            )
        text += (
            "If you changed runtime code and need it active, end with a reprompt handoff to the user.\n"
            "Example:\n"
            "  res = pubmed_search(query='exercise AND alzheimer', limit=3)\n"
            "  print(res.preview())\n"
            "  rows = pubmed_fetch(ids=res.ids[:3], include_abstract=True)\n"
            "  print(rows.shape())\n"
            "  for rec in rows: print(rec.get('pmid'))"
        )
        return text

    def _installed_packages(limit: int = 200, prefix: str | None = None) -> dict[str, Any]:
        max_items = max(1, min(int(limit), 1000))
        normalized_prefix = str(prefix or "").strip().lower()
        rows: list[dict[str, str]] = []
        try:
            for dist in importlib.metadata.distributions():
                name = str(dist.metadata.get("Name") or "").strip()
                if not name:
                    continue
                if normalized_prefix and not name.lower().startswith(normalized_prefix):
                    continue
                rows.append({"name": name, "version": str(dist.version or "")})
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "items": [], "count": 0, "truncated": False}

        rows.sort(key=lambda item: item["name"].lower())
        return {
            "count": len(rows),
            "truncated": len(rows) > max_items,
            "items": rows[:max_items],
        }

    globals_map: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "bash": _bash_disabled,
        "bash_exec": _bash_exec_disabled,
        "grep": _grep_disabled,
        "parallel_map": bindings.parallel_map,
        "json": json,
        "help_tools": lambda: sorted(bindings.tools.names()),
        "help_tool": _help_tool,
        "help_examples": _help_examples,
        "installed_packages": _installed_packages,
        "help_bash": lambda: (
            "Use bash_exec for shell tasks: codebase navigation (rg/ls/cat), "
            "workspace edits, and custom API calls (curl/wget)."
        ),
        "help_terminal": lambda: (
            "Use bash_exec(command='...') for shell workflows. "
            "Use runtime_info() to inspect shell mode, blocked prefixes, and workspace root."
        ),
        "env_vars": _env_vars,
        "runtime_info": _runtime_info,
        "help_repl": _help_repl_text,
    }

    if bindings.enable_subagent_helpers and bindings.llm_query_handler is not None:
        globals_map["llm_query"] = bindings.llm_query
    if bindings.enable_subagent_helpers and bindings.llm_query_batch_handler is not None:
        globals_map["llm_query_batch"] = bindings.llm_query_batch

    tool_ns = _ToolNamespace()
    for tool_name in sorted(bindings.tools.names()):
        if not tool_name.isidentifier():
            continue
        wrapper = bindings.tool_wrapper(tool_name)
        globals_map[tool_name] = wrapper
        setattr(tool_ns, tool_name, wrapper)

    globals_map["tools"] = tool_ns
    return globals_map


class ReplRuntime:
    def __init__(
        self,
        *,
        tools: ToolRegistry,
        workspace_root: Path,
        artifact_root: Path | None = None,
        allowed_command_prefixes: tuple[str, ...],
        blocked_command_prefixes: tuple[str, ...],
        blocked_command_patterns: tuple[str, ...] = (),
        shell_policy_mode: str = "open",
        max_stdout_bytes: int,
        stdout_soft_line_limit: int = 500,
        stdout_max_line_artifacts: int = 12,
        max_wall_time_seconds: int,
        max_tool_calls_per_exec: int,
        session_manager: ReplSessionManager,
        env_snapshot_mode: str = "always",
        env_snapshot_max_items: int = 80,
        env_snapshot_max_preview_chars: int = 160,
        env_snapshot_redact_keys: tuple[str, ...] = (
            "api_key",
            "token",
            "secret",
            "password",
            "auth",
            "cookie",
        ),
        import_policy: str = "permissive",
        import_allow_modules: tuple[str, ...] = (),
        import_deny_modules: tuple[str, ...] = (),
        lazy_install_enabled: bool = False,
        lazy_install_allowlist: tuple[str, ...] = (),
        lazy_install_timeout_seconds: int = 60,
        lazy_install_index_url: str | None = None,
        enable_subagent_helpers: bool = False,
        llm_query_handler: LlmQueryHandler | None = None,
        llm_query_batch_handler: LlmQueryBatchHandler | None = None,
        subagent_stdout_line_soft_limit: int | None = None,
    ) -> None:
        self.tools = tools
        self.workspace_root = Path(workspace_root).resolve()
        self.max_wall_time_seconds = max(1, int(max_wall_time_seconds))
        self.max_stdout_bytes = max(1024, int(max_stdout_bytes))
        self.stdout_soft_line_limit = max(120, int(stdout_soft_line_limit))
        self.stdout_max_line_artifacts = max(1, int(stdout_max_line_artifacts))
        self.max_tool_calls_per_exec = max(1, int(max_tool_calls_per_exec))
        self.artifact_root = Path(artifact_root).resolve() if artifact_root is not None else None
        self._stdout_artifact_seq = 0
        self._stdout_artifact_seq_lock = threading.Lock()
        self.shell_policy_mode = shell_policy_mode if shell_policy_mode in {"guarded", "open"} else "open"
        self.env_snapshot_mode = (
            env_snapshot_mode if env_snapshot_mode in {"off", "debug", "always"} else "always"
        )
        self.env_snapshot_max_items = max(10, int(env_snapshot_max_items))
        self.env_snapshot_max_preview_chars = max(32, int(env_snapshot_max_preview_chars))
        self.env_snapshot_redact_keys = tuple(
            key.strip().lower() for key in env_snapshot_redact_keys if str(key).strip()
        ) or ("api_key", "token", "secret", "password", "auth", "cookie")

        self.import_policy = import_policy if import_policy in {"minimal", "broad", "permissive"} else "permissive"
        self.allowed_import_roots = set(_MINIMAL_ALLOWED_IMPORT_ROOTS)
        self.allowed_import_modules = set(_MINIMAL_ALLOWED_IMPORT_MODULES)
        if self.import_policy == "broad":
            self.allowed_import_roots.update(_BROAD_EXTRA_IMPORT_ROOTS)
            self.allowed_import_modules.update(_BROAD_EXTRA_IMPORT_MODULES)
        self.denied_import_roots: set[str] = set()
        self.denied_import_modules: set[str] = set()
        denied_candidates = set(_DEFAULT_DENIED_IMPORT_MODULES)
        denied_candidates.update(str(item).strip() for item in import_deny_modules if str(item).strip())
        for candidate in denied_candidates:
            if "." in candidate:
                self.denied_import_modules.add(candidate)
                self.denied_import_roots.add(candidate.split(".", 1)[0])
            else:
                self.denied_import_roots.add(candidate)
        for module in import_allow_modules:
            candidate = str(module).strip()
            if not candidate:
                continue
            if "." in candidate:
                self.allowed_import_modules.add(candidate)
                self.allowed_import_roots.add(candidate.split(".", 1)[0])
            else:
                self.allowed_import_roots.add(candidate)

        self.lazy_install_enabled = bool(lazy_install_enabled)
        self.lazy_install_allowlist = {
            str(item).strip().lower()
            for item in lazy_install_allowlist
            if str(item).strip()
        }
        self.lazy_install_timeout_seconds = max(5, int(lazy_install_timeout_seconds))
        self.lazy_install_index_url = (
            str(lazy_install_index_url).strip() if isinstance(lazy_install_index_url, str) and lazy_install_index_url.strip() else None
        )
        self.enable_subagent_helpers = bool(enable_subagent_helpers)
        self.llm_query_handler = llm_query_handler
        self.llm_query_batch_handler = llm_query_batch_handler
        self.subagent_stdout_line_soft_limit = (
            int(subagent_stdout_line_soft_limit)
            if isinstance(subagent_stdout_line_soft_limit, int)
            else None
        )
        self._lazy_install_lock = threading.Lock()
        self._lazy_install_success: set[str] = set()
        self._lazy_install_failed: set[str] = set()

        self.session_manager = session_manager
        self.shell = ShellExecutor(
            ShellPolicy(
                workspace_root=workspace_root,
                mode=self.shell_policy_mode,
                allowed_prefixes=allowed_command_prefixes,
                blocked_prefixes=blocked_command_prefixes,
                blocked_patterns=blocked_command_patterns,
                max_output_bytes=self.max_stdout_bytes,
            )
        )

    def _next_stdout_artifact_seq(self) -> int:
        with self._stdout_artifact_seq_lock:
            self._stdout_artifact_seq += 1
            return self._stdout_artifact_seq

    def _is_import_denied(self, module_name: str) -> bool:
        normalized = str(module_name or "").strip()
        if not normalized:
            return True
        root = normalized.split(".", 1)[0]
        if root in self.denied_import_roots:
            return True
        for denied in self.denied_import_modules:
            if normalized == denied or normalized.startswith(f"{denied}."):
                return True
        return False

    def _is_import_allowed(self, module_name: str) -> bool:
        normalized = str(module_name or "").strip()
        if not normalized:
            return False
        if self._is_import_denied(normalized):
            return False
        if self.import_policy == "permissive":
            return True
        if normalized in self.allowed_import_modules:
            return True
        return normalized.split(".", 1)[0] in self.allowed_import_roots

    def _format_blocked_import_message(self, module_name: str) -> str:
        if self._is_import_denied(module_name):
            denied_roots = ", ".join(sorted(self.denied_import_roots))
            denied_modules = ", ".join(sorted(self.denied_import_modules))
            return (
                f"Import '{module_name}' is blocked by REPL denylist. "
                f"Denied roots: {denied_roots or '(none)'}. "
                f"Denied modules: {denied_modules or '(none)'}. "
                "Use registered wrappers or bash_exec for controlled shell tasks."
            )

        allowed_roots = ", ".join(sorted(self.allowed_import_roots))
        allowed_modules = ", ".join(sorted(self.allowed_import_modules))
        return (
            f"Import '{module_name}' is blocked in REPL. Allowed roots: {allowed_roots}. "
            f"Allowed modules: {allowed_modules}. "
            "Try help_tools()/help_tool('name') for wrappers, or run shell operations via bash_exec."
        )

    def _install_package(self, package_name: str) -> bool:
        if not _SAFE_PACKAGE_PATTERN.match(package_name):
            return False
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            package_name,
            "--disable-pip-version-check",
            "--quiet",
        ]
        if self.lazy_install_index_url:
            command.extend(["--index-url", self.lazy_install_index_url])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.lazy_install_timeout_seconds,
                check=False,
            )
        except Exception:
            return False
        return completed.returncode == 0

    def _maybe_lazy_install(self, module_name: str) -> bool:
        if not self.lazy_install_enabled:
            return False
        root = str(module_name or "").split(".", 1)[0].lower()
        if not root:
            return False
        if root not in self.lazy_install_allowlist:
            return False
        package_name = _LAZY_INSTALL_PACKAGE_ALIASES.get(root, root)
        with self._lazy_install_lock:
            if package_name in self._lazy_install_success:
                return True
            if package_name in self._lazy_install_failed:
                return False
            installed = self._install_package(package_name)
            if installed:
                self._lazy_install_success.add(package_name)
            else:
                self._lazy_install_failed.add(package_name)
            return installed

    def _import_hook(
        self,
        name: str,
        globals_map: dict[str, Any] | None = None,
        locals_map: dict[str, Any] | None = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        module_name = str(name or "")
        if not self._is_import_allowed(module_name):
            raise ImportError(self._format_blocked_import_message(module_name))
        try:
            return builtins.__import__(module_name, globals_map, locals_map, fromlist, level)
        except ModuleNotFoundError as exc:
            missing = str(getattr(exc, "name", "") or module_name).split(".", 1)[0]
            if self._maybe_lazy_install(missing):
                return builtins.__import__(module_name, globals_map, locals_map, fromlist, level)
            raise ImportError(
                f"Import '{module_name}' is allowed but '{missing}' is not installed. "
                "Use bash_exec to install dependencies, or call preloaded wrappers directly "
                "(for example: kg_cypher_execute(...), normalize_merge_candidates(...))."
            ) from exc

    def _snapshot_scope(self, session: ReplSessionState) -> dict[str, Any]:
        return _snapshot_user_scope(
            session.globals,
            baseline_names=session.baseline_names,
            max_items=self.env_snapshot_max_items,
            max_preview_chars=self.env_snapshot_max_preview_chars,
            redact_keys=self.env_snapshot_redact_keys,
        )

    def _should_capture_env(self, *, error: str | None) -> bool:
        if self.env_snapshot_mode == "off":
            return False
        if self.env_snapshot_mode == "always":
            return True
        return bool(error)

    def execute_bash(
        self,
        *,
        command: str,
        timeout_s: int = 30,
        cwd: str | None = None,
        on_stdout_chunk: TextStreamCallback | None = None,
        on_stderr_chunk: TextStreamCallback | None = None,
    ):
        return self.shell.run(
            command,
            timeout_s=timeout_s,
            cwd=cwd,
            on_stdout_chunk=on_stdout_chunk,
            on_stderr_chunk=on_stderr_chunk,
        )

    def seed_session_variables(self, *, thread_id: str, values: dict[str, Any]) -> list[str]:
        if not values:
            return []
        session = self.session_manager.get_or_create(
            thread_id=thread_id,
            tools=self.tools,
            shell=self.shell,
            import_hook=self._import_hook,
            max_wall_time_seconds=self.max_wall_time_seconds,
            max_tool_calls_per_exec=self.max_tool_calls_per_exec,
            llm_query_handler=self.llm_query_handler,
            llm_query_batch_handler=self.llm_query_batch_handler,
            enable_subagent_helpers=self.enable_subagent_helpers,
            subagent_stdout_line_soft_limit=self.subagent_stdout_line_soft_limit,
        )
        seeded: list[str] = []
        for raw_name, raw_value in values.items():
            name = str(raw_name or "").strip()
            if not name or name.startswith("_") or not name.isidentifier():
                continue
            session.globals[name] = raw_value
            seeded.append(name)
        session.updated_at = time.time()
        return seeded

    def _truncate(self, text: str) -> tuple[str, bool]:
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_stdout_bytes:
            return text, False
        return encoded[: self.max_stdout_bytes].decode("utf-8", errors="replace"), True

    def _stdout_artifact_dir(
        self,
        *,
        user_msg_index: int,
        request_index: int,
        exec_seq: int,
    ) -> Path | None:
        if self.artifact_root is None:
            return None
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        turn_dir = f"turn-m{user_msg_index:04d}-r{request_index:04d}-e{exec_seq:04d}"
        return self.artifact_root / "repl_stdout" / day / turn_dir

    def _display_artifact_path(self, path: Path) -> str:
        # Prefer workspace-relative paths because bash_exec runs from workspace root.
        try:
            rel = path.resolve().relative_to(self.workspace_root)
            return str(rel)
        except Exception:
            pass
        if self.artifact_root is not None:
            try:
                rel_to_artifacts = path.resolve().relative_to(self.artifact_root.resolve())
                return str(rel_to_artifacts)
            except Exception:
                pass
        return str(path)

    def _write_long_stdout_artifact(
        self,
        *,
        user_msg_index: int,
        request_index: int,
        exec_seq: int,
        line_number: int,
        line_text: str,
    ) -> dict[str, Any] | None:
        base = self._stdout_artifact_dir(
            user_msg_index=user_msg_index,
            request_index=request_index,
            exec_seq=exec_seq,
        )
        if base is None:
            return None
        file_name = f"line-{line_number:04d}-chars-{len(line_text)}.md"
        path = base / file_name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                (
                    "# REPL Stdout Full Line\n\n"
                    f"- line_number: `{line_number}`\n"
                    f"- chars: `{len(line_text)}`\n\n"
                    "```text\n"
                    f"{line_text}\n"
                    "```\n"
                ),
                encoding="utf-8",
            )
        except Exception:
            return None
        display_path = self._display_artifact_path(path)
        quoted_path = shlex.quote(display_path)
        preview = " ".join(line_text.split())[:160]
        return {
            "kind": "repl_stdout_full_line",
            "name": file_name,
            "path": str(path),
            "display_path": display_path,
            "line_number": line_number,
            "chars": len(line_text),
            "preview": preview,
            "inspect_sed": f"bash_exec(command=\"sed -n '1,120p' {quoted_path}\")",
            "inspect_rg": f"bash_exec(command=\"rg -n 'keyword_here' {quoted_path}\")",
        }

    def _cap_long_stdout_lines(
        self,
        *,
        text: str,
        user_msg_index: int,
        request_index: int,
        exec_seq: int,
    ) -> tuple[str, list[dict[str, Any]], int]:
        if not text:
            return text, [], 0
        lines = text.splitlines(keepends=True)
        out_lines: list[str] = []
        artifacts: list[dict[str, Any]] = []
        capped_lines = 0
        for line_idx, raw_line in enumerate(lines, start=1):
            if raw_line.endswith("\n"):
                body = raw_line[:-1]
                newline = "\n"
            else:
                body = raw_line
                newline = ""
            if len(body) <= self.stdout_soft_line_limit:
                out_lines.append(raw_line)
                continue

            capped_lines += 1
            preview = body[: max(1, self.stdout_soft_line_limit - 3)] + "..."
            artifact_entry: dict[str, Any] | None = None
            if len(artifacts) < self.stdout_max_line_artifacts:
                artifact_entry = self._write_long_stdout_artifact(
                    user_msg_index=user_msg_index,
                    request_index=request_index,
                    exec_seq=exec_seq,
                    line_number=line_idx,
                    line_text=body,
                )
                if artifact_entry is not None:
                    artifacts.append(artifact_entry)

            if artifact_entry is not None:
                display_path = str(artifact_entry.get("display_path") or artifact_entry.get("path"))
                note = (
                    f"[stdout capped at {self.stdout_soft_line_limit} chars; full line ({len(body)} chars) "
                    f"saved to {display_path}; inspect with {artifact_entry['inspect_sed']} "
                    f"or {artifact_entry['inspect_rg']}]"
                )
            else:
                note = (
                    f"[stdout line capped at {self.stdout_soft_line_limit} chars; full line artifact unavailable "
                    "(artifact root missing or cap {self.stdout_max_line_artifacts} reached)]"
                )
            out_lines.append(f"{preview}\n{note}{newline}")
        return "".join(out_lines), artifacts, capped_lines

    def execute(
        self,
        *,
        thread_id: str,
        run_id: str,
        request_index: int,
        user_msg_index: int,
        execution_id: str,
        code: str,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
        on_stdout_chunk: TextStreamCallback | None = None,
        on_stderr_chunk: TextStreamCallback | None = None,
    ) -> ReplExecutionResult:
        started = time.monotonic()
        session = self.session_manager.get_or_create(
            thread_id=thread_id,
            tools=self.tools,
            shell=self.shell,
            import_hook=self._import_hook,
            max_wall_time_seconds=self.max_wall_time_seconds,
            max_tool_calls_per_exec=self.max_tool_calls_per_exec,
            llm_query_handler=self.llm_query_handler,
            llm_query_batch_handler=self.llm_query_batch_handler,
            enable_subagent_helpers=self.enable_subagent_helpers,
            subagent_stdout_line_soft_limit=self.subagent_stdout_line_soft_limit,
        )
        assert session.bindings is not None
        session.globals["env_vars"] = lambda: self._snapshot_scope(session)
        session.bindings.set_execution_context(
            run_id=run_id,
            request_index=request_index,
            user_msg_index=user_msg_index,
            parent_tool_use_id=execution_id,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
        )

        stdout_buffer = _StreamingTextBuffer(on_stdout_chunk)
        stderr_buffer = _StreamingTextBuffer(on_stderr_chunk)
        error: str | None = None
        before_scope = self._snapshot_scope(session) if self.env_snapshot_mode != "off" else None

        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                exec(compile(code, "<agent_repl>", "exec"), session.globals, session.globals)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            stderr_buffer.write(error)

        elapsed = time.monotonic() - started
        if elapsed > self.max_wall_time_seconds:
            timeout_message = (
                f"Execution time {elapsed:.2f}s exceeded max wall clock {self.max_wall_time_seconds}s"
            )
            if error:
                error = f"{error}; {timeout_message}"
            else:
                error = timeout_message
            stderr_buffer.write(("\n" if stderr_buffer.tell() else "") + timeout_message)

        raw_stdout = stdout_buffer.getvalue()
        raw_stderr = stderr_buffer.getvalue()
        had_visible_output = bool(raw_stdout.strip())

        if not raw_stdout and not error:
            if "print(" in code:
                raw_stdout = (
                    "REPL executed successfully but produced no visible output. "
                    "Your code includes print(...), but those print statements may not have run "
                    "(for example, empty loops or filters). "
                    "Try printing counts first, e.g. print(result.shape()) or print(len(result.records))."
                )
            else:
                raw_stdout = (
                    "REPL executed successfully but produced no visible output. "
                    "Use print(...) to expose results."
                )

        stdout_artifacts: list[dict[str, Any]] = []
        stdout_capping: dict[str, Any] | None = None
        if raw_stdout:
            exec_seq = self._next_stdout_artifact_seq()
            raw_stdout, stdout_artifacts, capped_lines = self._cap_long_stdout_lines(
                text=raw_stdout,
                user_msg_index=user_msg_index,
                request_index=request_index,
                exec_seq=exec_seq,
            )
            if capped_lines:
                stdout_capping = {
                    "line_soft_limit": self.stdout_soft_line_limit,
                    "lines_capped": capped_lines,
                    "artifacts_written": len(stdout_artifacts),
                    "artifact_cap": self.stdout_max_line_artifacts,
                }

        stdout, out_truncated = self._truncate(raw_stdout)
        stderr, err_truncated = self._truncate(raw_stderr)
        after_scope = self._snapshot_scope(session) if self.env_snapshot_mode != "off" else None

        env_snapshot: dict[str, Any] | None = None
        if self._should_capture_env(error=error) and before_scope is not None and after_scope is not None:
            env_snapshot = {
                "before": before_scope,
                "after": after_scope,
                "delta": _build_env_delta(
                    before_scope,
                    after_scope,
                    max_items=self.env_snapshot_max_items,
                ),
            }

        session.updated_at = time.time()

        return ReplExecutionResult(
            execution_id=execution_id,
            stdout=stdout,
            stderr=stderr,
            nested_tool_calls=session.bindings.nested_call_count(),
            truncated=out_truncated or err_truncated,
            had_visible_output=had_visible_output,
            error=error,
            env_snapshot=env_snapshot,
            artifacts=stdout_artifacts,
            stdout_capping=stdout_capping,
        )
