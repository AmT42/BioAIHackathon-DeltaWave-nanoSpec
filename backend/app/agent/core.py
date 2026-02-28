from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from app.agent.adapters import build_gemini_openai_messages
from app.agent.providers import GeminiProvider
from app.agent.prompt import DEFAULT_SYSTEM_PROMPT
from app.agent.repl import ReplRuntime, ReplSessionManager
from app.agent.tools.registry import ToolRegistry
from app.agent.types import ToolCall
from app.config import Settings
from app.runtime_reload import schedule_process_reload
from app.persistence.models import (
    ConversationEventKind,
    ConversationEventRole,
    Message,
    MessageProviderFormat,
    MessageRole,
)
from app.persistence.service import ChatStore
from app.run_logging import (
    build_llm_request_record,
    write_llm_io_files,
    write_tool_io_file,
)
from app.trace_normalizer import build_trace_v1

ProviderName = Literal["gemini"]
Emitter = Callable[[dict[str, Any]], Awaitable[None]]

_REPL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "repl_exec",
        "description": (
            "Run Python code in the persistent coding REPL for this thread. "
            "Use this for Python logic, tool wrappers, and structured post-processing. "
            "Do not run shell commands here; use 'bash_exec' for shell commands. "
            "Only printed output is visible back to the model."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute in the thread-scoped REPL session.",
                }
            },
            "required": ["code"],
        },
    },
}

_BASH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash_exec",
        "description": (
            "Run a workspace-confined bash command. "
            "Use this for shell workflows: codebase navigation, file operations, and custom API calls "
            "with curl/wget when wrappers are not enough."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "default": 30,
                    "description": "Command timeout in seconds.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory relative to workspace root.",
                },
            },
            "required": ["command"],
        },
    },
}

_REPL_SESSION_MANAGER: ReplSessionManager | None = None
_REPL_SESSION_MANAGER_LOCK = threading.Lock()
_UI_VISIBLE_TOP_LEVEL_TOOLS = {"repl_exec", "bash_exec"}
_CODE_UPDATE_REPROMPT_NOTICE = (
    "Runtime code was updated during this turn. "
    "Please send another prompt so the next turn runs with the updated code."
)


def _get_repl_session_manager(settings: Settings) -> ReplSessionManager:
    global _REPL_SESSION_MANAGER
    with _REPL_SESSION_MANAGER_LOCK:
        if _REPL_SESSION_MANAGER is None:
            _REPL_SESSION_MANAGER = ReplSessionManager(
                max_sessions=settings.repl_max_sessions,
                session_ttl_seconds=settings.repl_session_ttl_seconds,
            )
        return _REPL_SESSION_MANAGER


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _thinking_title(text: str, max_words: int = 10) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    words = stripped.replace("\n", " ").split()
    if not words:
        return None
    title = " ".join(words[:max_words]).strip()
    if len(words) > max_words:
        title += "..."
    return title


def _chunk_text(value: str, chunk_size: int = 64) -> list[str]:
    text = str(value or "")
    if not text:
        return []
    size = max(1, int(chunk_size))
    return [text[i : i + size] for i in range(0, len(text), size)]


def _normalize_rel_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    return raw.replace("\\", "/").lstrip("./")


def _git_status_files(repo_root: Path) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return set()
    if completed.returncode != 0:
        return set()

    files: set[str] = set()
    for raw_line in (completed.stdout or "").splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        payload = line[3:]
        if "->" in payload:
            payload = payload.split("->", 1)[1]
        rel = _normalize_rel_path(payload)
        if rel:
            files.add(rel)
    return files


def _is_runtime_sensitive_path(path: str, prefixes: tuple[str, ...]) -> bool:
    normalized = _normalize_rel_path(path).lower()
    if not normalized:
        return False
    for prefix in prefixes:
        normalized_prefix = _normalize_rel_path(prefix).lower().rstrip("/")
        if normalized_prefix and (normalized == normalized_prefix or normalized.startswith(f"{normalized_prefix}/")):
            return True
    return False


class AgentCore:
    def __init__(self, *, settings: Settings, store: ChatStore, tools: ToolRegistry) -> None:
        self.settings = settings
        self.store = store
        self.tools = tools
        if settings.agent_execution_mode != "repl_only":
            raise ValueError("Only 'repl_only' execution mode is supported in this build")
        self.repl_runtime = ReplRuntime(
            tools=tools,
            workspace_root=settings.repl_workspace_root,
            artifact_root=settings.artifacts_root,
            allowed_command_prefixes=settings.repl_allowed_command_prefixes,
            blocked_command_prefixes=settings.repl_blocked_command_prefixes,
            blocked_command_patterns=settings.repl_blocked_command_patterns,
            shell_policy_mode=settings.repl_shell_policy_mode,
            max_stdout_bytes=settings.repl_max_stdout_bytes,
            stdout_soft_line_limit=settings.repl_stdout_line_soft_limit,
            stdout_max_line_artifacts=settings.repl_stdout_max_line_artifacts,
            max_wall_time_seconds=settings.repl_max_wall_time_seconds,
            max_tool_calls_per_exec=settings.repl_max_tool_calls_per_exec,
            session_manager=_get_repl_session_manager(settings),
            env_snapshot_mode=settings.repl_env_snapshot_mode,
            env_snapshot_max_items=settings.repl_env_snapshot_max_items,
            env_snapshot_max_preview_chars=settings.repl_env_snapshot_max_preview_chars,
            env_snapshot_redact_keys=settings.repl_env_snapshot_redact_keys,
            import_policy=settings.repl_import_policy,
            import_allow_modules=settings.repl_import_allow_modules,
            import_deny_modules=settings.repl_import_deny_modules,
            lazy_install_enabled=settings.repl_lazy_install_enabled,
            lazy_install_allowlist=settings.repl_lazy_install_allowlist,
            lazy_install_timeout_seconds=settings.repl_lazy_install_timeout_seconds,
            lazy_install_index_url=settings.repl_lazy_install_index_url,
        )
        self._providers = {
            "gemini": GeminiProvider(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                reasoning_effort=settings.gemini_reasoning_effort,
                include_thoughts=settings.gemini_include_thoughts,
                thinking_budget=settings.gemini_thinking_budget,
                replay_signature_mode=settings.gemini_replay_signature_mode,
                mock_mode=settings.mock_llm,
            ),
        }

    def _runtime_system_prompt(self) -> str:
        tool_names = sorted(self.tools.names())
        tool_list = ", ".join(tool_names)
        workspace_root = str(self.settings.repl_workspace_root)
        shell_mode = self.settings.repl_shell_policy_mode
        allowed_prefixes = ", ".join(sorted(self.settings.repl_allowed_command_prefixes))
        blocked_prefixes = ", ".join(sorted(self.settings.repl_blocked_command_prefixes))
        blocked_patterns = ", ".join(sorted(self.settings.repl_blocked_command_patterns))
        helpers = (
            "`help_repl()`, `help_tools()`, `help_tool('name')`, "
            "`help_examples('longevity')`, `help_examples('shell_vs_repl')`, `installed_packages()`, "
            "`runtime_info()`, `env_vars()`"
        )
        runtime_addendum = (
            "\n\n## Runtime Environment Brief (authoritative)\n"
            "- Execution model: `repl_exec` for Python wrappers, `bash_exec` for shell.\n"
            "- `bash_exec` is a top-level tool call and is not callable from inside Python REPL blocks.\n"
            "- Use wrappers first for supported biomedical retrieval; use custom shell/API calls when wrappers are missing.\n"
            "- Shell routing guide:\n"
            "  - codebase navigation/inspection/editing and CLI workflows -> `bash_exec`\n"
            "  - wrapper pipelines and structured Python transforms -> `repl_exec`\n"
            "  - do not run shell from Python REPL blocks\n"
            "- Available wrapper tools right now:\n"
            f"  {tool_list}\n"
            "- REPL helper functions available at runtime:\n"
            f"  {helpers}\n"
            "- First-turn package discovery:\n"
            "  - `print(installed_packages(limit=200))` to inspect Python packages available in this runtime.\n"
            "- Result handle ergonomics:\n"
            "  `res.ids.head(n)`, `res.shape()`, `res.records`, `for rec in res: ...`\n"
            "- REPL stdout capping behavior:\n"
            "  - very long printed lines are capped in visible stdout and full content is written to `repl_stdout` artifacts.\n"
            "  - capped-line notes include artifact path plus `bash_exec` inspect hints (`sed`/`rg`).\n"
            "- Tool-specific validation reminder:\n"
            "  - `longevity_itp_fetch_summary` requires `ids` as a non-empty list of ITP summary URLs.\n"
            "  - if you do not have ITP URLs, skip this tool instead of calling it empty.\n"
            "- Shell policy for `bash_exec` (workspace confined):\n"
            f"  workspace root: {workspace_root}\n"
            f"  mode: {shell_mode}\n"
            f"  allowed prefixes (guarded mode): {allowed_prefixes}\n"
            f"  blocked prefixes: {blocked_prefixes}\n"
            f"  blocked patterns: {blocked_patterns}\n"
            f"- REPL import policy: `{self.settings.repl_import_policy}`; denylist: `{', '.join(self.settings.repl_import_deny_modules)}`\n"
            f"- REPL preload mode: enabled={self.settings.repl_preload_enabled}, profile=`{self.settings.repl_preload_profile}`\n"
            f"- Execution limits: max_wall={self.settings.repl_max_wall_time_seconds}s, "
            f"max_stdout={self.settings.repl_max_stdout_bytes} bytes, "
            f"stdout_line_soft_limit={self.settings.repl_stdout_line_soft_limit} chars, "
            f"stdout_max_line_artifacts={self.settings.repl_stdout_max_line_artifacts}, "
            f"max_tool_calls_per_exec={self.settings.repl_max_tool_calls_per_exec}\n"
            "- Bash examples:\n"
            "  - `bash_exec(command=\"rg -n 'normalize_merge_candidates' backend/app\")`\n"
            "  - `bash_exec(command=\"curl -sS 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&retmax=3&term=metformin+aging' | jq .esearchresult.idlist\")`\n"
            "  - `bash_exec(command=\"curl -sS 'https://clinicaltrials.gov/api/v2/studies?query.term=metformin&query.intr=metformin&pageSize=3' | jq '.studies | length'\")`\n"
            "  - `bash_exec(command=\"wget -qO- 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=32333835' | jq '.result.uids'\")`\n"
            "- REPL examples:\n"
            "  - `res = normalize_ontology(query='Hyperbaric oxygen therapy', limit=5); merged = normalize_merge_candidates([res], user_text='Hyperbaric oxygen therapy')`\n"
            "  - `terms = retrieval_build_query_terms(concept=merged.data.get('concept')); kg = kg_cypher_execute(cypher='MATCH (i)-[r]-(n) RETURN i,r,n LIMIT 25')`\n"
            "  - `templates = retrieval_build_pubmed_templates(terms=terms.data.get('terms')); queries = templates.data.get('queries', {}); pm = pubmed_search(query=queries.get('systematic_reviews', ''), limit=5); print(pm.preview())`\n"
            "  - `kg_followup = kg_cypher_execute(cypher='MATCH (n)-[r]-(m) RETURN n,r,m LIMIT 25'); print(kg_followup.preview())`\n"
            "  - `itp = longevity_itp_fetch_summary(ids=['<itp_summary_url>']); print(itp.preview())`\n"
            "- Runtime code-change handoff:\n"
            "  - If you modify runtime code (for example under `backend/app`), end with an explicit reprompt handoff message so the user can send the next prompt on updated code.\n"
            f"  - controlled reload enabled: {self.settings.repl_controlled_reload_enabled}\n"
            "- If uncertain about args/signatures, call `help_tool('tool_name')` first, then print previews.\n"
        )
        return DEFAULT_SYSTEM_PROMPT.rstrip() + runtime_addendum

    async def run_turn_stream(
        self,
        *,
        thread_id: str,
        provider: ProviderName,
        user_message: str,
        emit: Emitter,
        run_id: str | None = None,
        max_iterations: int = 50,
    ) -> dict[str, Any]:
        if provider not in self._providers:
            raise ValueError(f"Unsupported provider '{provider}'")

        run_id = run_id or uuid.uuid4().hex

        async def emit_chunked_tokens(
            *,
            event_type: str,
            segment_index: int,
            text: str,
            tool_use_id: str | None = None,
            chunk_size: int = 48,
            pace_s: float = 0.006,
            pace_max_chunks: int = 80,
        ) -> None:
            chunks = _chunk_text(text, chunk_size=chunk_size)
            for idx, chunk in enumerate(chunks):
                payload: dict[str, Any] = {
                    "type": event_type,
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "segment_index": segment_index,
                    "token": chunk,
                }
                if tool_use_id:
                    payload["tool_use_id"] = tool_use_id
                await emit(payload)
                if idx < pace_max_chunks:
                    await asyncio.sleep(pace_s)

        await emit(
            {
                "type": "main_agent_start",
                "thread_id": thread_id,
                "run_id": run_id,
                "message": "Processing your message...",
            }
        )

        await self.store.create_message(
            thread_id=thread_id,
            role=MessageRole.USER,
            content=user_message,
            record_text_event=True,
        )

        user_msg_index = await self.store.count_user_messages(thread_id)

        provider_client = self._providers[provider]
        provider_format = MessageProviderFormat.GEMINI_INTERLEAVED

        segment_counter = 0
        request_index = 0
        tool_call_index = 0
        final_text = ""

        collected_blocks: list[dict[str, Any]] = []
        last_assistant_message_id: str | None = None
        iteration_limit_exhausted = True
        git_tracking_enabled = bool(self.settings.repl_git_tracking_enabled)
        status_baseline = (
            await asyncio.to_thread(_git_status_files, self.settings.repl_workspace_root)
            if git_tracking_enabled
            else set()
        )
        changed_files: set[str] = set()
        runtime_dirty_files: set[str] = set()

        async def _capture_runtime_changes() -> None:
            if not git_tracking_enabled:
                return
            current = await asyncio.to_thread(_git_status_files, self.settings.repl_workspace_root)
            delta = {item for item in current if item not in status_baseline}
            if not delta:
                return
            changed_files.update(delta)
            for rel in delta:
                if _is_runtime_sensitive_path(rel, self.settings.repl_runtime_sensitive_paths):
                    runtime_dirty_files.add(rel)

        for _ in range(max_iterations):
            request_index += 1

            canonical_events = await self.store.get_canonical_events(thread_id)
            runtime_prompt = self._runtime_system_prompt()
            provider_messages = build_gemini_openai_messages(canonical_events, system_prompt=runtime_prompt)
            tool_schemas = [_REPL_TOOL_SCHEMA, _BASH_TOOL_SCHEMA]

            request_payload: dict[str, Any] = {
                "model": self.settings.gemini_model,
                "messages": provider_messages,
                "stream": True,
                "reasoning_effort": self.settings.gemini_reasoning_effort,
                "thinking_config": {
                    "include_thoughts": self.settings.gemini_include_thoughts,
                    "thinking_budget": self.settings.gemini_thinking_budget,
                },
                "tools": tool_schemas,
                "tool_choice": "auto" if tool_schemas else None,
            }
            if request_payload.get("tool_choice") is None:
                request_payload.pop("tool_choice", None)

            request_record = build_llm_request_record(
                function_name="GeminiProvider.stream_turn",
                provider="gemini",
                model=self.settings.gemini_model,
                raw_payload=request_payload,
            )

            thinking_tokens: list[str] = []
            assistant_tokens: list[str] = []
            thinking_segment: int | None = None
            assistant_segment: int | None = None

            streaming_assistant = await self.store.create_message(
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                content=None,
                provider_format=provider_format,
                content_blocks=None,
                message_metadata={
                    "provider": provider,
                    "model": self.settings.gemini_model,
                    "run_id": run_id,
                    "request_index": request_index,
                    "stream_mode": "interleaved",
                },
                record_text_event=False,
            )
            last_assistant_message_id = streaming_assistant.id

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            sentinel = {"type": "__done__"}

            async def _pump() -> None:
                while True:
                    item = await queue.get()
                    if item.get("type") == "__done__":
                        break
                    await emit(item)

            pump_task = asyncio.create_task(_pump())

            def _next_segment() -> int:
                nonlocal segment_counter
                idx = segment_counter
                segment_counter += 1
                return idx

            def on_thinking_token(token: str) -> None:
                nonlocal thinking_segment
                if not token:
                    return
                if thinking_segment is None:
                    thinking_segment = _next_segment()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "main_agent_thinking_start",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": thinking_segment,
                        },
                    )
                thinking_tokens.append(token)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "main_agent_thinking_token",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": thinking_segment,
                        "token": token,
                    },
                )

            def on_text_token(token: str) -> None:
                nonlocal assistant_segment
                if not token:
                    return
                if assistant_segment is None:
                    assistant_segment = _next_segment()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "main_agent_segment_start",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": assistant_segment,
                            "role": "assistant",
                            "message_id": streaming_assistant.id,
                        },
                    )
                assistant_tokens.append(token)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "main_agent_segment_token",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": assistant_segment,
                        "token": token,
                    },
                )

            stream_error: Exception | None = None
            stream_result = None
            try:
                stream_result = await asyncio.to_thread(
                    provider_client.stream_turn,
                    messages=provider_messages,
                    tools=tool_schemas,
                    system_prompt=runtime_prompt,
                    on_thinking_token=on_thinking_token,
                    on_text_token=on_text_token,
                )
            except Exception as exc:
                stream_error = exc
            finally:
                queue.put_nowait(sentinel)
                await pump_task

            if stream_error is not None:
                write_llm_io_files(
                    thread_id=thread_id,
                    user_index=user_msg_index,
                    request_index=request_index,
                    request_record=request_record,
                    answer_json={
                        "error": str(stream_error),
                        "error_type": type(stream_error).__name__,
                    },
                    answer_text=None,
                )
                raise stream_error

            assert stream_result is not None

            normalized_tool_calls: list[ToolCall] = []
            for tc in stream_result.tool_calls:
                tool_use_id = tc.id or f"tool_{uuid.uuid4().hex[:10]}"
                normalized_tool_calls.append(
                    ToolCall(
                        id=str(tool_use_id),
                        name=tc.name,
                        input=tc.input,
                        provider_specific_fields=tc.provider_specific_fields,
                        extra_content=tc.extra_content,
                    )
                )

            thinking_text = stream_result.thinking.strip() or "".join(thinking_tokens).strip()
            assistant_text = stream_result.text.strip() or "".join(assistant_tokens).strip()
            if assistant_text:
                final_text = assistant_text

            if thinking_text and thinking_segment is None:
                thinking_segment = _next_segment()
                await emit(
                    {
                        "type": "main_agent_thinking_start",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": thinking_segment,
                    }
                )
                if not thinking_tokens:
                    await emit_chunked_tokens(
                        event_type="main_agent_thinking_token",
                        segment_index=thinking_segment,
                        text=thinking_text,
                        chunk_size=42,
                    )

            if thinking_segment is not None:
                await emit(
                    {
                        "type": "main_agent_thinking_end",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": thinking_segment,
                        "summary": thinking_text,
                    }
                )
                title = _thinking_title(thinking_text)
                if title:
                    await emit(
                        {
                            "type": "main_agent_thinking_title",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": thinking_segment,
                            "summary": title,
                        }
                    )

            if assistant_text and assistant_segment is None:
                assistant_segment = _next_segment()
                await emit(
                    {
                        "type": "main_agent_segment_start",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": assistant_segment,
                        "role": "assistant",
                        "message_id": streaming_assistant.id,
                    }
                )
                if not assistant_tokens:
                    await emit_chunked_tokens(
                        event_type="main_agent_segment_token",
                        segment_index=assistant_segment,
                        text=assistant_text,
                        chunk_size=42,
                    )

            if assistant_segment is not None:
                await emit(
                    {
                        "type": "main_agent_segment_end",
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "segment_index": assistant_segment,
                        "message_id": streaming_assistant.id,
                        "content": assistant_text,
                        "tool_calls": [
                            {
                                "id": tool.id,
                                "type": "function",
                                "function": {
                                    "name": tool.name,
                                    "arguments": json.dumps(tool.input),
                                },
                            }
                            for tool in normalized_tool_calls
                        ]
                        or None,
                    }
                )

            tool_segment_by_call_id: dict[str, int] = {}
            for tc in normalized_tool_calls:
                tool_segment_by_call_id[tc.id] = _next_segment()

            iteration_blocks: list[dict[str, Any]] = []
            if thinking_text:
                thinking_block: dict[str, Any] = {"type": "thinking", "thinking": thinking_text}
                if thinking_segment is not None:
                    thinking_block["segment_index"] = thinking_segment
                iteration_blocks.append(thinking_block)

            if assistant_text:
                text_block: dict[str, Any] = {"type": "text", "text": assistant_text}
                if assistant_segment is not None:
                    text_block["segment_index"] = assistant_segment
                iteration_blocks.append(text_block)

            for tc in normalized_tool_calls:
                tool_name = str(tc.name or "").strip()
                block: dict[str, Any] = {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                    "segment_index": tool_segment_by_call_id[tc.id],
                    "ui_visible": tool_name in _UI_VISIBLE_TOP_LEVEL_TOOLS,
                }
                if tc.provider_specific_fields:
                    block["provider_specific_fields"] = tc.provider_specific_fields
                if tc.extra_content:
                    block["extra_content"] = tc.extra_content
                iteration_blocks.append(block)

            collected_blocks.extend(iteration_blocks)

            updated_message = await self.store.update_message(
                message_id=streaming_assistant.id,
                content=assistant_text if assistant_text else None,
                content_blocks=iteration_blocks or None,
                provider_format=provider_format,
                message_metadata={
                    "provider": provider,
                    "model": self.settings.gemini_model,
                    "provider_state": stream_result.provider_state,
                    "run_id": run_id,
                    "request_index": request_index,
                    "stream_mode": "interleaved",
                },
            )
            if updated_message is not None:
                last_assistant_message_id = updated_message.id

            if not assistant_text and iteration_blocks:
                await self.store.append_event(
                    thread_id=thread_id,
                    role=ConversationEventRole.ASSISTANT,
                    kind=ConversationEventKind.CONTROL,
                    content={
                        "type": "assistant_interleaved_blocks",
                        "provider_format": provider_format.value,
                        "content_blocks": iteration_blocks,
                    },
                    message_id=streaming_assistant.id,
                    visible_to_model=True,
                )
                await self.store.session.commit()
                await self.store._log_thread_snapshot(thread_id)

            answer_json = {
                "thinking": thinking_text,
                "text": assistant_text,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                        "provider_specific_fields": tc.provider_specific_fields,
                        "extra_content": tc.extra_content,
                    }
                    for tc in normalized_tool_calls
                ],
                "provider_state": stream_result.provider_state,
            }
            write_llm_io_files(
                thread_id=thread_id,
                user_index=user_msg_index,
                request_index=request_index,
                request_record=request_record,
                answer_json=answer_json,
                answer_text=assistant_text,
            )

            if not normalized_tool_calls:
                iteration_limit_exhausted = False
                break

            for tc in normalized_tool_calls:
                tool_segment = tool_segment_by_call_id[tc.id]
                tool_name = str(tc.name or "").strip()
                tool_ui_visible = tool_name in _UI_VISIBLE_TOP_LEVEL_TOOLS
                show_generic_tool_card = tool_name == "bash_exec"
                if show_generic_tool_card:
                    await emit(
                        {
                            "type": "main_agent_tool_start",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": tool_segment,
                            "tool_use_id": tc.id,
                            "tool_name": tc.name,
                            "arguments": tc.input,
                            "ui_visible": True,
                        }
                    )

                await self.store.record_tool_call(
                    thread_id=thread_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    input_payload=tc.input,
                    provider_specific_fields=tc.provider_specific_fields,
                    extra_content=tc.extra_content,
                    visible_to_model=True,
                )

                started_at = _utc_iso()
                nested_events: list[dict[str, Any]] = []
                if tc.name == "repl_exec":
                    code = str(tc.input.get("code") or "").strip()
                    if not code:
                        tool_result = {
                            "status": "error",
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "repl_exec requires non-empty 'code'",
                                "retryable": False,
                                "details": {},
                            },
                        }
                    else:
                        code_chunks = _chunk_text(code, chunk_size=42)
                        start_code = code_chunks[0] if code_chunks else ""
                        await emit(
                            {
                                "type": "main_agent_repl_start",
                                "thread_id": thread_id,
                                "run_id": run_id,
                                "segment_index": tool_segment,
                                "tool_use_id": tc.id,
                                "tool_name": tc.name,
                                "code": start_code,
                            }
                        )
                        if len(code_chunks) > 1:
                            await emit_chunked_tokens(
                                event_type="main_agent_repl_code_token",
                                segment_index=tool_segment,
                                text="".join(code_chunks[1:]),
                                tool_use_id=tc.id,
                                chunk_size=42,
                            )

                        def _on_nested_start(call_id: str, tool_name: str, payload: dict[str, Any]) -> None:
                            nested_events.append(
                                {
                                    "kind": "start",
                                    "call_id": call_id,
                                    "tool_name": tool_name,
                                    "payload": payload,
                                    "started_at": _utc_iso(),
                                }
                            )

                        def _on_nested_result(call_id: str, tool_name: str, result_payload: dict[str, Any]) -> None:
                            nested_events.append(
                                {
                                    "kind": "result",
                                    "call_id": call_id,
                                    "tool_name": tool_name,
                                    "result": result_payload,
                                    "finished_at": _utc_iso(),
                                }
                            )

                        loop = asyncio.get_running_loop()
                        repl_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                        repl_sentinel = {"type": "__done__"}

                        async def _pump_repl_stream() -> None:
                            while True:
                                item = await repl_queue.get()
                                if item.get("type") == "__done__":
                                    break
                                await emit(item)

                        repl_pump_task = asyncio.create_task(_pump_repl_stream())

                        def _on_stdout_chunk(chunk: str) -> None:
                            if not chunk:
                                return
                            loop.call_soon_threadsafe(
                                repl_queue.put_nowait,
                                {
                                    "type": "main_agent_repl_stdout",
                                    "thread_id": thread_id,
                                    "run_id": run_id,
                                    "segment_index": tool_segment,
                                    "tool_use_id": tc.id,
                                    "content": chunk,
                                },
                            )

                        def _on_stderr_chunk(chunk: str) -> None:
                            if not chunk:
                                return
                            loop.call_soon_threadsafe(
                                repl_queue.put_nowait,
                                {
                                    "type": "main_agent_repl_stderr",
                                    "thread_id": thread_id,
                                    "run_id": run_id,
                                    "segment_index": tool_segment,
                                    "tool_use_id": tc.id,
                                    "content": chunk,
                                },
                            )

                        repl_result = None
                        try:
                            repl_result = await asyncio.to_thread(
                                self.repl_runtime.execute,
                                thread_id=thread_id,
                                run_id=run_id,
                                request_index=request_index,
                                user_msg_index=user_msg_index,
                                execution_id=tc.id,
                                code=code,
                                on_tool_start=_on_nested_start,
                                on_tool_result=_on_nested_result,
                                on_stdout_chunk=_on_stdout_chunk,
                                on_stderr_chunk=_on_stderr_chunk,
                            )
                            tool_result = repl_result.to_tool_output()
                        except Exception as exc:  # pragma: no cover - defensive guard
                            tool_result = {
                                "status": "error",
                                "error": {
                                    "code": "REPL_RUNTIME_ERROR",
                                    "message": f"{type(exc).__name__}: {exc}",
                                    "retryable": True,
                                    "details": {},
                                },
                            }
                        finally:
                            repl_queue.put_nowait(repl_sentinel)
                            await repl_pump_task

                        if repl_result is not None and repl_result.stdout:
                            await emit(
                                {
                                    "type": "main_agent_repl_stdout",
                                    "thread_id": thread_id,
                                    "run_id": run_id,
                                    "segment_index": tool_segment,
                                    "tool_use_id": tc.id,
                                    "content": repl_result.stdout,
                                }
                            )
                        if repl_result is not None and repl_result.stderr:
                            await emit(
                                {
                                    "type": "main_agent_repl_stderr",
                                    "thread_id": thread_id,
                                    "run_id": run_id,
                                    "segment_index": tool_segment,
                                    "tool_use_id": tc.id,
                                    "content": repl_result.stderr,
                                }
                            )
                        if repl_result is not None and isinstance(repl_result.env_snapshot, dict):
                            await emit(
                                {
                                    "type": "main_agent_repl_env",
                                    "thread_id": thread_id,
                                    "run_id": run_id,
                                    "segment_index": tool_segment,
                                    "tool_use_id": tc.id,
                                    "env": repl_result.env_snapshot,
                                }
                            )
                            collected_blocks.append(
                                {
                                    "type": "repl_env",
                                    "tool_use_id": tc.id,
                                    "env": repl_result.env_snapshot,
                                    "segment_index": tool_segment,
                                    "ui_visible": True,
                                }
                            )
                        await emit(
                            {
                                "type": "main_agent_repl_end",
                                "thread_id": thread_id,
                                "run_id": run_id,
                                "segment_index": tool_segment,
                                "tool_use_id": tc.id,
                                "result": tool_result,
                            }
                        )
                elif tc.name == "bash_exec":
                    command = str(tc.input.get("command") or "").strip()
                    if not command:
                        tool_result = {
                            "status": "error",
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "bash_exec requires non-empty 'command'",
                                "retryable": False,
                                "details": {},
                            },
                        }
                    else:
                        timeout_raw = tc.input.get("timeout_s", 30)
                        try:
                            timeout_s = int(timeout_raw)
                        except Exception:
                            timeout_s = 30
                        cwd_raw = tc.input.get("cwd")
                        cwd = str(cwd_raw).strip() if isinstance(cwd_raw, str) and str(cwd_raw).strip() else None

                        await emit_chunked_tokens(
                            event_type="main_agent_bash_command_token",
                            segment_index=tool_segment,
                            text=command,
                            tool_use_id=tc.id,
                            chunk_size=30,
                        )

                        loop = asyncio.get_running_loop()
                        bash_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                        bash_sentinel = {"type": "__done__"}

                        async def _pump_bash_stream() -> None:
                            while True:
                                item = await bash_queue.get()
                                if item.get("type") == "__done__":
                                    break
                                await emit(item)

                        bash_pump_task = asyncio.create_task(_pump_bash_stream())

                        def _queue_bash_stream(field: str, chunk: str) -> None:
                            if not chunk:
                                return
                            loop.call_soon_threadsafe(
                                bash_queue.put_nowait,
                                {
                                    "type": "main_agent_tool_result",
                                    "thread_id": thread_id,
                                    "run_id": run_id,
                                    "segment_index": tool_segment,
                                    "tool_use_id": tc.id,
                                    "tool_name": tc.name,
                                    "ui_visible": True,
                                    "result": {
                                        "status": "streaming",
                                        "output": {
                                            "command": command,
                                            field: chunk,
                                        },
                                    },
                                },
                            )

                        def _on_bash_stdout(chunk: str) -> None:
                            _queue_bash_stream("stdout", chunk)

                        def _on_bash_stderr(chunk: str) -> None:
                            _queue_bash_stream("stderr", chunk)

                        try:
                            shell_result = await asyncio.to_thread(
                                self.repl_runtime.execute_bash,
                                command=command,
                                timeout_s=timeout_s,
                                cwd=cwd,
                                on_stdout_chunk=_on_bash_stdout,
                                on_stderr_chunk=_on_bash_stderr,
                            )
                            summary = "Bash command completed successfully."
                            if shell_result.returncode != 0:
                                summary = f"Bash command exited with code {shell_result.returncode}."
                            tool_result = {
                                "status": "success",
                                "output": {
                                    "summary": summary,
                                    "command": shell_result.command,
                                    "returncode": shell_result.returncode,
                                    "stdout": shell_result.stdout,
                                    "stderr": shell_result.stderr,
                                    "truncated": shell_result.truncated,
                                },
                            }
                        except Exception as exc:
                            message = f"{type(exc).__name__}: {exc}"
                            code = "SHELL_RUNTIME_ERROR"
                            retryable = True
                            if isinstance(exc, ValueError) and "Blocked command prefix" in str(exc):
                                code = "SHELL_POLICY_ERROR"
                                retryable = False
                                message = (
                                    f"{type(exc).__name__}: {exc}. "
                                    "Use science wrappers (e.g., pubmed_search/pubmed_fetch) "
                                    "instead of curl/wget for retrieval."
                                )
                            tool_result = {
                                "status": "error",
                                "error": {
                                    "code": code,
                                    "message": message,
                                    "retryable": retryable,
                                    "details": {
                                        "command": command,
                                        "cwd": cwd,
                                    },
                                },
                            }
                        finally:
                            bash_queue.put_nowait(bash_sentinel)
                            await bash_pump_task
                else:
                    tool_result = {
                        "status": "error",
                        "error": {
                            "code": "UNSUPPORTED_TOOL",
                            "message": f"Unsupported tool '{tc.name}'. Supported tools: repl_exec, bash_exec.",
                            "retryable": False,
                            "details": {"tool_name": tc.name},
                        },
                    }
                finished_at = _utc_iso()

                nested_segment_by_call_id: dict[str, int] = {}
                nested_started_at: dict[str, str] = {}
                nested_payload_by_call_id: dict[str, dict[str, Any]] = {}
                for nested in nested_events:
                    kind = str(nested.get("kind") or "")
                    nested_call_id = str(nested.get("call_id") or "")
                    nested_tool_name = str(nested.get("tool_name") or "tool")
                    if not nested_call_id:
                        continue
                    if kind == "start":
                        nested_segment = _next_segment()
                        nested_segment_by_call_id[nested_call_id] = nested_segment
                        nested_payload = nested.get("payload") if isinstance(nested.get("payload"), dict) else {}
                        await self.store.record_tool_call(
                            thread_id=thread_id,
                            tool_call_id=nested_call_id,
                            tool_name=nested_tool_name,
                            input_payload=nested_payload,
                            visible_to_model=False,
                        )
                        nested_started_at[nested_call_id] = str(nested.get("started_at") or _utc_iso())
                        nested_payload_by_call_id[nested_call_id] = nested_payload
                        collected_blocks.append(
                            {
                                "type": "tool_use",
                                "id": nested_call_id,
                                "name": nested_tool_name,
                                "input": nested_payload,
                                "segment_index": nested_segment,
                                "parent_tool_use_id": tc.id,
                                "ui_visible": False,
                            }
                        )
                        continue

                    if kind == "result":
                        nested_segment = nested_segment_by_call_id.get(nested_call_id, _next_segment())
                        nested_segment_by_call_id[nested_call_id] = nested_segment
                        nested_result = nested.get("result") if isinstance(nested.get("result"), dict) else {}
                        await self.store.record_tool_result(
                            thread_id=thread_id,
                            tool_call_id=nested_call_id,
                            tool_name=nested_tool_name,
                            status=str(nested_result.get("status") or "unknown"),
                            output=nested_result.get("output") if isinstance(nested_result.get("output"), dict) else None,
                            error=nested_result.get("error") if isinstance(nested_result.get("error"), dict) else None,
                            visible_to_model=False,
                        )
                        tool_call_index += 1
                        write_tool_io_file(
                            thread_id=thread_id,
                            tool_name=nested_tool_name,
                            tool_call_index=tool_call_index,
                            tool_use_id=nested_call_id,
                            user_index=user_msg_index,
                            request_index=request_index,
                            run_id=run_id,
                            arguments=nested_payload_by_call_id.get(nested_call_id, {}),
                            result=nested_result,
                            status=str(nested_result.get("status") or "unknown"),
                            error=(nested_result.get("error") or {}).get("message")
                            if isinstance(nested_result.get("error"), dict)
                            else None,
                            started_at=nested_started_at.get(nested_call_id),
                            finished_at=str(nested.get("finished_at") or _utc_iso()),
                        )
                        collected_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": nested_call_id,
                                "name": nested_tool_name,
                                "content": nested_result,
                                "segment_index": nested_segment,
                                "parent_tool_use_id": tc.id,
                                "ui_visible": False,
                            }
                        )
                        await emit(
                            {
                                "type": "main_agent_tool_result",
                                "thread_id": thread_id,
                                "run_id": run_id,
                                "segment_index": nested_segment,
                                "tool_use_id": nested_call_id,
                                "parent_tool_use_id": tc.id,
                                "tool_name": nested_tool_name,
                                "result": nested_result,
                                "ui_visible": False,
                            }
                        )

                if show_generic_tool_card:
                    await emit(
                        {
                            "type": "main_agent_tool_result",
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "segment_index": tool_segment,
                            "tool_use_id": tc.id,
                            "tool_name": tc.name,
                            "result": tool_result,
                            "ui_visible": True,
                        }
                    )

                await self.store.record_tool_result(
                    thread_id=thread_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    status=tool_result["status"],
                    output=tool_result.get("output"),
                    error=tool_result.get("error"),
                    visible_to_model=True,
                )

                tool_call_index += 1
                write_tool_io_file(
                    thread_id=thread_id,
                    tool_name=tc.name,
                    tool_call_index=tool_call_index,
                    tool_use_id=tc.id,
                    user_index=user_msg_index,
                    request_index=request_index,
                    run_id=run_id,
                    arguments=tc.input,
                    result=tool_result,
                    status=tool_result.get("status", "unknown"),
                    error=(tool_result.get("error") or {}).get("message")
                    if isinstance(tool_result.get("error"), dict)
                    else None,
                    started_at=started_at,
                    finished_at=finished_at,
                )

                collected_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "name": tc.name,
                        "content": tool_result,
                        "segment_index": tool_segment,
                        "ui_visible": tool_ui_visible,
                    }
                )
                await _capture_runtime_changes()

        limit_completion_text = ""
        if iteration_limit_exhausted and not final_text.strip():
            limit_completion_text = (
                f"I stopped after reaching the tool-iteration limit ({max_iterations}) "
                "before producing a final narrative answer. "
                "Please continue with a narrower scope or a higher iteration budget."
            )
        code_updated = bool(changed_files)
        runtime_code_updated = bool(runtime_dirty_files)
        reprompt_required = runtime_code_updated and bool(self.settings.repl_reprompt_required_on_code_change)
        completion_override: str | None = None
        if limit_completion_text:
            completion_override = limit_completion_text
        elif reprompt_required:
            completion_override = (
                f"{final_text.rstrip()}\n\n{_CODE_UPDATE_REPROMPT_NOTICE}"
                if final_text.strip()
                else _CODE_UPDATE_REPROMPT_NOTICE
            )

        completion_message: dict[str, Any] | None = None
        if last_assistant_message_id:
            last_message = await self.store.session.get(Message, last_assistant_message_id)
            if last_message:
                metadata = dict(last_message.message_metadata or {})
                trace_v1 = build_trace_v1(
                    provider=provider,
                    content_blocks=collected_blocks,
                    assistant_message_id=last_message.id,
                    include_thinking=True,
                )
                if trace_v1:
                    metadata["trace_v1"] = trace_v1
                metadata["run_id"] = run_id
                metadata["stream_mode"] = "interleaved"
                metadata["max_iterations"] = max_iterations
                metadata["iteration_limit_exhausted"] = iteration_limit_exhausted
                metadata["code_updated"] = code_updated
                metadata["runtime_code_updated"] = runtime_code_updated
                metadata["reprompt_required"] = reprompt_required
                metadata["changed_files"] = sorted(changed_files)[:200]
                metadata["runtime_dirty_files"] = sorted(runtime_dirty_files)[:200]
                metadata["controlled_reload_enabled"] = bool(self.settings.repl_controlled_reload_enabled)

                updated_last = await self.store.update_message(
                    message_id=last_message.id,
                    content=completion_override,
                    message_metadata=metadata,
                )
                final_message = updated_last or last_message
                completion_message = {
                    "id": final_message.id,
                    "thread_id": final_message.thread_id,
                    "role": final_message.role.value if hasattr(final_message.role, "value") else str(final_message.role),
                    "content": final_message.content or "",
                    "created_at": final_message.created_at.isoformat() if final_message.created_at else None,
                    "metadata": final_message.message_metadata or {},
                }
                if final_message.content:
                    final_text = final_message.content

        await emit(
            {
                "type": "main_agent_complete",
                "thread_id": thread_id,
                "run_id": run_id,
                "message": completion_message,
            }
        )

        if reprompt_required:
            await emit(
                {
                    "type": "main_agent_reprompt_required",
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "content": _CODE_UPDATE_REPROMPT_NOTICE,
                    "summary": "Runtime updated; reprompt required",
                    "message": {
                        "runtime_dirty_files": sorted(runtime_dirty_files)[:20],
                        "changed_files": sorted(changed_files)[:20],
                    },
                }
            )

        if reprompt_required and self.settings.repl_controlled_reload_enabled:
            schedule_process_reload(
                exit_code=self.settings.repl_controlled_reload_exit_code,
                delay_ms=self.settings.repl_controlled_reload_delay_ms,
            )

        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "content": final_text,
            "provider": provider,
            "message": completion_message,
        }


def normalize_provider(value: str | None) -> ProviderName:
    lowered = (value or "gemini").strip().lower()
    if lowered != "gemini":
        raise ValueError("provider must be 'gemini'")
    return lowered  # type: ignore[return-value]


def parse_tool_result_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"value": payload}
