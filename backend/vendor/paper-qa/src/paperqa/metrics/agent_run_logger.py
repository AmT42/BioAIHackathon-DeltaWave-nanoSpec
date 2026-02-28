from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from paperqa.docs import Docs
from paperqa.types import Context, PQASession
from paperqa.utils import get_citation_ids

if TYPE_CHECKING:
    from paperqa.settings import Settings


SessionSnapshot = dict[str, Any]
_LOGGER_CACHE: dict[tuple[str, str], "AgentRunLogger"] = {}
_CACHE_LOCK = threading.Lock()


def _ensure_serializable_token_counts(
    token_counts: Mapping[str, list[int]],
) -> dict[str, list[int]]:
    return {model: list(counts) for model, counts in token_counts.items()}


def capture_session_snapshot(session: PQASession) -> SessionSnapshot:
    return {
        "cost": session.cost,
        "token_counts": _ensure_serializable_token_counts(session.token_counts),
    }


def compute_session_delta(
    before: SessionSnapshot | None, session: PQASession
) -> SessionSnapshot | None:
    if not before:
        return None
    delta_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    current = _ensure_serializable_token_counts(session.token_counts)
    for model, counts in current.items():
        prev = before["token_counts"].get(model, [0, 0])
        delta_counts[model] = [
            counts[0] - prev[0],
            counts[1] - prev[1],
        ]
    for model, counts in before["token_counts"].items():
        if model not in delta_counts:
            delta_counts[model] = [-counts[0], -counts[1]]
    return {
        "cost": session.cost - before["cost"],
        "token_counts": delta_counts,
    }


def summarize_doc_chunks(docs: Docs, limit: int = 25) -> dict[str, Any]:
    summary: dict[str, dict[str, Any]] = {}
    for text in docs.texts:
        dockey = getattr(text.doc, "dockey", None)
        if dockey is None:
            continue
        record = summary.setdefault(
            str(dockey),
            {
                "dockey": str(dockey),
                "docname": getattr(text.doc, "docname", None),
                "title": getattr(text.doc, "title", None),
                "chunks": 0,
            },
        )
        record["chunks"] += 1
    top_docs = sorted(summary.values(), key=lambda r: r["chunks"], reverse=True)
    return {
        "total_docs": len(summary),
        "total_chunks": len(docs.texts),
        "per_doc": top_docs[:limit],
    }


def summarize_contexts(
    contexts: list[Context], *, question: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for context in contexts[:limit]:
        doc = getattr(context.text, "doc", None)
        chunk_name = getattr(context.text, "name", None)
        items.append(
            {
                "id": context.id,
                "question": context.question,
                "invocation_question": question,
                "score": context.score,
                "docname": getattr(doc, "docname", None),
                "dockey": getattr(doc, "dockey", None),
                "chunk_name": chunk_name,
            }
        )
    return items


def list_citation_ids(answer: str | None) -> list[str]:
    if not answer:
        return []
    return list(dict.fromkeys(get_citation_ids(answer)))


def _generate_run_id() -> str:
    return time.strftime("run_%Y%m%d_%H%M%S")


class AgentRunLogger:
    def __init__(
        self,
        base_dir: str | Path | None,
        run_id: str | None = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.run_id = run_id or _generate_run_id()
        self.base_dir = Path(base_dir or "data")
        self.run_dir = self.base_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_path = self.run_dir / "trajectory.jsonl"
        self.summary_path = self.run_dir / "agent_summary.json"
        self._step_count = 0
        self._finalized = False
        self._lock = threading.Lock()

    def log_event(
        self,
        *,
        step: str,
        inputs: Mapping[str, Any] | None = None,
        outputs: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        session_delta: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "inputs": inputs or {},
            "outputs": outputs or {},
            "session_delta": session_delta or {},
        }
        if metadata:
            record["metadata"] = dict(metadata)
        with self._lock:
            with self.trajectory_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            self._step_count += 1

    def finalize(
        self,
        session: PQASession,
        *,
        agent_status: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enabled or self._finalized:
            return
        files = {
            "trajectory": str(self.trajectory_path),
        }
        openalex_summary = self.run_dir / "summary.json"
        if openalex_summary.exists():
            files["openalex_summary"] = str(openalex_summary)
        openalex_outcomes = self.run_dir / "outcomes.jsonl"
        if openalex_outcomes.exists():
            files["openalex_outcomes"] = str(openalex_outcomes)
        data = {
            "run_id": self.run_id,
            "session_id": str(session.id),
            "question": session.question,
            "has_successful_answer": session.has_successful_answer,
            "agent_status": agent_status,
            "final_cost": session.cost,
            "total_contexts": len(session.contexts),
            "total_tokens": session.token_counts,
            "files": files,
            "steps_logged": self._step_count,
        }
        if extra:
            data["extra"] = dict(extra)
        with self.summary_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        self._finalized = True


def get_agent_run_logger(settings: Settings | None) -> AgentRunLogger | None:
    if settings is None:
        return None
    if not settings.agent.collect_run_stats:
        return None
    if settings.agent.run_id is None:
        settings.agent.run_id = _generate_run_id()
    base_dir = settings.agent.run_stats_dir or "data"
    run_id = settings.agent.run_id
    cache_key = (str(Path(base_dir).resolve()), run_id)
    with _CACHE_LOCK:
        logger = _LOGGER_CACHE.get(cache_key)
        if logger is None:
            logger = AgentRunLogger(base_dir, run_id, enabled=True)
            _LOGGER_CACHE[cache_key] = logger
        return logger
