from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_RUN_DIR_ENV = "EVE_RUN_DIR"
_LLM_IO_ENV = "EVE_LOG_LLM_IO"
_DB_THREAD_ENV = "EVE_LOG_DB_THREADS"
_TOOL_IO_ENV = "EVE_LOG_TOOL_IO"
_write_lock = threading.Lock()
_cached_run_dir: Path | None = None


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _next_run_dir(logs_dir: Path) -> tuple[int, Path]:
    idx = 0
    while (logs_dir / str(idx)).exists():
        idx += 1
    return idx, logs_dir / str(idx)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    with _write_lock:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)


def is_llm_io_logging_enabled() -> bool:
    return _env_bool(_LLM_IO_ENV, default=True)


def is_db_thread_logging_enabled() -> bool:
    return _env_bool(_DB_THREAD_ENV, default=True)


def is_tool_io_logging_enabled() -> bool:
    return _env_bool(_TOOL_IO_ENV, default=True)


def get_run_dir() -> Path:
    global _cached_run_dir
    if _cached_run_dir is not None:
        return _cached_run_dir

    env_dir = os.getenv(_RUN_DIR_ENV)
    if env_dir:
        run_dir = Path(env_dir).expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        _cached_run_dir = run_dir
        return run_dir

    logs_dir = _backend_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_index, run_dir = _next_run_dir(logs_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ[_RUN_DIR_ENV] = str(run_dir)
    _cached_run_dir = run_dir

    metadata_path = run_dir / "run_metadata.json"
    if not metadata_path.exists():
        atomic_write_json(
            metadata_path,
            {
                "run_index": run_index,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "invoker": "app.run_logging:auto",
            },
        )
    return run_dir


def get_thread_dir(thread_id: str) -> Path:
    thread_dir = get_run_dir() / "threads" / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    return thread_dir


def build_llm_request_record(
    *,
    function_name: str,
    provider: str,
    model: str,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "function": function_name,
        "provider": provider,
        "model": model,
        "raw_request_payload": raw_payload,
    }


def write_llm_io_files(
    *,
    thread_id: str,
    user_index: int,
    request_index: int,
    request_record: dict[str, Any],
    answer_json: Any | None = None,
    answer_text: str | None = None,
) -> None:
    if not is_llm_io_logging_enabled():
        return

    thread_dir = get_thread_dir(thread_id)
    request_dir = thread_dir / f"user_msg_{user_index:03d}" / f"request_{request_index:03d}"
    request_dir.mkdir(parents=True, exist_ok=True)

    atomic_write_json(request_dir / "request_payload.json", request_record)

    if answer_json is not None:
        atomic_write_json(request_dir / "answer.json", answer_json)
    if answer_text is not None:
        (request_dir / "answer.txt").write_text(answer_text, encoding="utf-8")


def _add_duration_ms(context: dict[str, Any], started_at: str | None, finished_at: str | None) -> None:
    if not started_at or not finished_at:
        return
    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        context["duration_ms"] = int((end_dt - start_dt).total_seconds() * 1000)
    except Exception:
        return


def write_tool_io_file(
    *,
    thread_id: str,
    tool_name: str,
    tool_call_index: int,
    tool_use_id: str | None,
    user_index: int | None,
    request_index: int | None,
    run_id: str | None,
    run_iteration: int | None = None,
    agent_title: str | None = None,
    agent_name: str | None = None,
    arguments: Any,
    result: Any = None,
    status: str = "ok",
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    if not is_tool_io_logging_enabled():
        return
    if user_index is None or request_index is None:
        return

    thread_dir = get_thread_dir(thread_id)
    tools_dir = (
        thread_dir
        / f"user_msg_{user_index:03d}"
        / f"request_{request_index:03d}"
        / "tools"
        / "normal_tool"
    )
    tools_dir.mkdir(parents=True, exist_ok=True)
    path = tools_dir / f"{tool_name}_{tool_call_index:04d}.json"

    context: dict[str, Any] = {
        "thread_id": thread_id,
        "user_msg_index": user_index,
        "llm_request_index": request_index,
        "run_id": run_id,
        "run_iteration": run_iteration,
        "agent_title": agent_title,
        "agent_name": agent_name,
        "timestamp_started": started_at,
        "timestamp_finished": finished_at,
    }
    _add_duration_ms(context, started_at, finished_at)

    record = {
        "tool_name": tool_name,
        "tool_call_index": tool_call_index,
        "tool_use_id": tool_use_id,
        "status": status,
        "error": error,
        "context": context,
        "arguments": arguments,
        "result": result,
    }
    atomic_write_json(path, record)


def write_db_thread_snapshot(*, thread_id: str, snapshot: dict[str, Any]) -> None:
    if not is_db_thread_logging_enabled():
        return
    thread_dir = get_thread_dir(thread_id)
    atomic_write_json(thread_dir / "db_thread.json", snapshot)
