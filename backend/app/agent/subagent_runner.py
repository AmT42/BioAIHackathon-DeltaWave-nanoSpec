from __future__ import annotations

import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.prompt import SUBAGENT_SYSTEM_PROMPT_TEMPLATE
from app.agent.repl import ReplRuntime, ReplSessionManager
from app.agent.repl.types import IdListHandle, ToolResultHandle
from app.agent.tools.registry import ToolRegistry
from app.agent.types import ToolCall
from app.config import Settings
from app.run_logging import atomic_write_json


_REPL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "repl_exec",
        "description": (
            "Run Python code in a clean sub-agent REPL session for this query. "
            "Use this for Python logic, tool wrappers, and structured post-processing. "
            "Do not run shell commands here; use 'bash_exec'. "
            "Only printed output is visible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute in sub-agent REPL.",
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
            "Run a workspace-confined bash command for this sub-agent query. "
            "Use this for shell workflows: codebase navigation, file operations, and custom API calls."
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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_identifier(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    return text if text.isidentifier() and not text.startswith("_") else ""


def _clone_seed_value(value: Any) -> Any:
    if isinstance(value, (IdListHandle, ToolResultHandle)):
        return value
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _normalize_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": str(call.id or f"subtool_{uuid.uuid4().hex[:10]}"),
        "name": str(call.name or "").strip(),
        "input": dict(call.input or {}),
        "provider_specific_fields": dict(call.provider_specific_fields or {}),
        "extra_content": dict(call.extra_content or {}),
    }


@dataclass
class _SingleQueryResult:
    ok: bool
    task: str
    text: str | None
    error: str | None
    trace_path: str | None
    tool_calls: int
    iterations: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "task": self.task,
            "text": self.text,
            "error": self.error,
            "trace_path": self.trace_path,
            "tool_calls": self.tool_calls,
            "iterations": self.iterations,
        }


class SubagentRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        tools: ToolRegistry,
        provider: Any,
    ) -> None:
        self.settings = settings
        self.tools = tools
        self.provider = provider

    def _selected_tool_names(self, allowed_tools: list[str] | None) -> list[str]:
        available = self.tools.names()
        if allowed_tools is None:
            return available

        normalized: list[str] = []
        seen: set[str] = set()
        missing: list[str] = []
        for raw in allowed_tools:
            name = str(raw or "").strip()
            if not name or name in seen:
                continue
            if self.tools.get_spec(name) is None:
                missing.append(name)
                continue
            seen.add(name)
            normalized.append(name)
        if missing:
            raise ValueError(f"Unknown allowed_tools entries: {sorted(missing)}")
        return normalized

    def _filtered_registry(self, tool_names: list[str]) -> ToolRegistry:
        specs = []
        for name in tool_names:
            spec = self.tools.get_spec(name)
            if spec is not None:
                specs.append(spec)
        return ToolRegistry(
            specs,
            artifact_root=self.settings.artifacts_root,
            source_cache_root=self.settings.source_cache_root,
        )

    def _subagent_runtime(self, registry: ToolRegistry) -> ReplRuntime:
        return ReplRuntime(
            tools=registry,
            workspace_root=self.settings.repl_workspace_root,
            artifact_root=self.settings.artifacts_root,
            allowed_command_prefixes=self.settings.repl_allowed_command_prefixes,
            blocked_command_prefixes=self.settings.repl_blocked_command_prefixes,
            blocked_command_patterns=self.settings.repl_blocked_command_patterns,
            shell_policy_mode=self.settings.repl_shell_policy_mode,
            max_stdout_bytes=self.settings.repl_max_stdout_bytes,
            stdout_soft_line_limit=self.settings.repl_subagent_stdout_line_soft_limit,
            stdout_max_line_artifacts=self.settings.repl_stdout_max_line_artifacts,
            max_wall_time_seconds=self.settings.repl_max_wall_time_seconds,
            max_tool_calls_per_exec=self.settings.repl_max_tool_calls_per_exec,
            session_manager=ReplSessionManager(
                max_sessions=max(100, self.settings.repl_max_sessions),
                session_ttl_seconds=self.settings.repl_session_ttl_seconds,
            ),
            env_snapshot_mode=self.settings.repl_env_snapshot_mode,
            env_snapshot_max_items=self.settings.repl_env_snapshot_max_items,
            env_snapshot_max_preview_chars=self.settings.repl_env_snapshot_max_preview_chars,
            env_snapshot_redact_keys=self.settings.repl_env_snapshot_redact_keys,
            import_policy=self.settings.repl_import_policy,
            import_allow_modules=self.settings.repl_import_allow_modules,
            import_deny_modules=self.settings.repl_import_deny_modules,
            lazy_install_enabled=self.settings.repl_lazy_install_enabled,
            lazy_install_allowlist=self.settings.repl_lazy_install_allowlist,
            lazy_install_timeout_seconds=self.settings.repl_lazy_install_timeout_seconds,
            lazy_install_index_url=self.settings.repl_lazy_install_index_url,
            enable_subagent_helpers=False,
            llm_query_handler=None,
            llm_query_batch_handler=None,
            subagent_stdout_line_soft_limit=self.settings.repl_subagent_stdout_line_soft_limit,
        )

    def _tool_schemas(self, *, allow_repl: bool, allow_bash: bool) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        if allow_repl:
            schemas.append(_REPL_TOOL_SCHEMA)
        if allow_bash:
            schemas.append(_BASH_TOOL_SCHEMA)
        return schemas

    def _build_system_prompt(
        self,
        *,
        custom_instruction: str | None,
        attached_tools: list[str],
        allow_repl: bool,
        allow_bash: bool,
    ) -> str:
        custom = str(custom_instruction or "").strip() or "None."
        prompt = SUBAGENT_SYSTEM_PROMPT_TEMPLATE.replace("{custom_instruction}", custom)
        mode_lines = [
            "",
            "## Runtime Contract",
            f"- Attached wrapper tools: {', '.join(attached_tools) if attached_tools else '(none)'}",
            f"- Top-level tools enabled: repl_exec={allow_repl}, bash_exec={allow_bash}",
            f"- Sub-agent REPL stdout line cap: {self.settings.repl_subagent_stdout_line_soft_limit} chars.",
            f"- Main-agent REPL stdout line cap: {self.settings.repl_stdout_line_soft_limit} chars.",
        ]
        return f"{prompt}\n" + "\n".join(mode_lines)

    def _trace_dir(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str | None,
        query_id: str,
    ) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        run_label = str(parent_run_id or "norun").replace("/", "_")
        return (
            Path(self.settings.artifacts_root)
            / "subagent_traces"
            / day
            / f"thread-{parent_thread_id}"
            / f"run-{run_label}"
            / f"query-{query_id}"
        )

    def _write_trace(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str | None,
        query_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        base = self._trace_dir(
            parent_thread_id=parent_thread_id,
            parent_run_id=parent_run_id,
            query_id=query_id,
        )
        path = base / "transcript.json"
        try:
            atomic_write_json(path, payload)
        except Exception:
            return None
        return str(path)

    def _seed_env(
        self,
        *,
        runtime: ReplRuntime,
        thread_id: str,
        env: dict[str, Any] | None,
    ) -> list[str]:
        if not isinstance(env, dict) or not env:
            return []
        seeded: dict[str, Any] = {}
        for raw_key, raw_value in env.items():
            name = _sanitize_identifier(str(raw_key))
            if not name:
                continue
            seeded[name] = _clone_seed_value(raw_value)
        if not seeded:
            return []
        return runtime.seed_session_variables(thread_id=thread_id, values=seeded)

    def _execute_tool_call(
        self,
        *,
        runtime: ReplRuntime,
        tool_call: dict[str, Any],
        thread_id: str,
        run_id: str,
        request_index: int,
        user_msg_index: int,
        allow_repl: bool,
        allow_bash: bool,
    ) -> dict[str, Any]:
        tool_name = str(tool_call.get("name") or "").strip()
        payload = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
        tool_use_id = str(tool_call.get("id") or f"tool_{uuid.uuid4().hex[:10]}")

        if tool_name == "repl_exec":
            if not allow_repl:
                return {
                    "status": "error",
                    "error": {
                        "code": "TOOL_NOT_ENABLED",
                        "message": "repl_exec is disabled for this sub-agent query.",
                        "retryable": False,
                        "details": {},
                    },
                }
            code = str(payload.get("code") or "").strip()
            if not code:
                return {
                    "status": "error",
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "repl_exec requires non-empty 'code'",
                        "retryable": False,
                        "details": {},
                    },
                }
            repl_result = runtime.execute(
                thread_id=thread_id,
                run_id=run_id,
                request_index=request_index,
                user_msg_index=user_msg_index,
                execution_id=tool_use_id,
                code=code,
            )
            return repl_result.to_tool_output()

        if tool_name == "bash_exec":
            if not allow_bash:
                return {
                    "status": "error",
                    "error": {
                        "code": "TOOL_NOT_ENABLED",
                        "message": "bash_exec is disabled for this sub-agent query.",
                        "retryable": False,
                        "details": {},
                    },
                }
            command = str(payload.get("command") or "").strip()
            if not command:
                return {
                    "status": "error",
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "bash_exec requires non-empty 'command'",
                        "retryable": False,
                        "details": {},
                    },
                }
            timeout_raw = payload.get("timeout_s", 30)
            try:
                timeout_s = int(timeout_raw)
            except Exception:
                timeout_s = 30
            cwd_raw = payload.get("cwd")
            cwd = str(cwd_raw).strip() if isinstance(cwd_raw, str) and str(cwd_raw).strip() else None
            try:
                shell_result = runtime.execute_bash(command=command, timeout_s=timeout_s, cwd=cwd)
                summary = "Bash command completed successfully."
                if shell_result.returncode != 0:
                    summary = f"Bash command exited with code {shell_result.returncode}."
                return {
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
                return {
                    "status": "error",
                    "error": {
                        "code": "SHELL_RUNTIME_ERROR",
                        "message": f"{type(exc).__name__}: {exc}",
                        "retryable": False,
                        "details": {"command": command, "cwd": cwd},
                    },
                }

        return {
            "status": "error",
            "error": {
                "code": "UNSUPPORTED_TOOL",
                "message": f"Unsupported tool '{tool_name}'. Supported tools: repl_exec, bash_exec.",
                "retryable": False,
                "details": {"tool_name": tool_name},
            },
        }

    def run_query(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        request_index: int | None,
        user_msg_index: int | None,
        parent_tool_use_id: str | None,
        task: str,
        env: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        custom_instruction: str | None = None,
        allow_repl: bool = True,
        allow_bash: bool = True,
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        task_text = str(task or "").strip()
        if not task_text:
            raise ValueError("llm_query requires non-empty task")

        if not allow_repl and not allow_bash:
            raise ValueError("At least one of allow_repl or allow_bash must be true")

        tool_names = self._selected_tool_names(allowed_tools)
        query_id = uuid.uuid4().hex[:12]
        sub_thread_id = f"subagent-{query_id}"
        sub_run_id = f"{run_id or 'norun'}-sub-{query_id}"
        effective_request_index = int(request_index or 1)
        effective_user_index = int(user_msg_index or 1)
        iteration_budget = int(max_iterations or self.settings.repl_subagent_max_iterations)
        if iteration_budget < 1:
            iteration_budget = 1

        registry = self._filtered_registry(tool_names)
        runtime = self._subagent_runtime(registry)
        seeded_vars = self._seed_env(runtime=runtime, thread_id=sub_thread_id, env=env)
        tool_schemas = self._tool_schemas(allow_repl=allow_repl, allow_bash=allow_bash)
        system_prompt = self._build_system_prompt(
            custom_instruction=custom_instruction,
            attached_tools=tool_names,
            allow_repl=allow_repl,
            allow_bash=allow_bash,
        )

        transcript_steps: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = [{"role": "user", "content": task_text}]
        total_tool_calls = 0
        final_text = ""
        iteration_limit_hit = True
        error_message: str | None = None

        for iteration in range(1, iteration_budget + 1):
            thinking_tokens: list[str] = []
            text_tokens: list[str] = []
            step_payload: dict[str, Any] = {
                "iteration": iteration,
                "request_messages": copy.deepcopy(messages),
            }

            try:
                stream_result = self.provider.stream_turn(
                    messages=messages,
                    tools=tool_schemas,
                    system_prompt=system_prompt,
                    on_thinking_token=lambda token: thinking_tokens.append(str(token or "")),
                    on_text_token=lambda token: text_tokens.append(str(token or "")),
                )
            except Exception as exc:
                error_message = f"{type(exc).__name__}: {exc}"
                step_payload["error"] = error_message
                transcript_steps.append(step_payload)
                break

            assistant_text = str(stream_result.text or "").strip() or "".join(text_tokens).strip()
            if assistant_text:
                final_text = assistant_text

            normalized_calls = [_normalize_tool_call(call) for call in list(stream_result.tool_calls or [])]
            total_tool_calls += len(normalized_calls)

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_text,
            }
            if normalized_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["input"]),
                        },
                        "provider_specific_fields": call.get("provider_specific_fields") or {},
                        "extra_content": call.get("extra_content") or {},
                    }
                    for call in normalized_calls
                ]
            messages.append(assistant_message)

            step_payload["assistant"] = {
                "thinking": str(stream_result.thinking or "").strip() or "".join(thinking_tokens).strip(),
                "text": assistant_text,
                "tool_calls": copy.deepcopy(normalized_calls),
                "provider_state": copy.deepcopy(stream_result.provider_state),
            }

            if not normalized_calls:
                iteration_limit_hit = False
                transcript_steps.append(step_payload)
                break

            tool_results: list[dict[str, Any]] = []
            for raw_call in normalized_calls:
                result_payload = self._execute_tool_call(
                    runtime=runtime,
                    tool_call=raw_call,
                    thread_id=sub_thread_id,
                    run_id=sub_run_id,
                    request_index=effective_request_index,
                    user_msg_index=effective_user_index,
                    allow_repl=allow_repl,
                    allow_bash=allow_bash,
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": raw_call["id"],
                    "name": raw_call["name"],
                    "content": result_payload,
                }
                messages.append(tool_message)
                tool_results.append(
                    {
                        "tool_call_id": raw_call["id"],
                        "name": raw_call["name"],
                        "result": copy.deepcopy(result_payload),
                    }
                )
            step_payload["tool_results"] = tool_results
            transcript_steps.append(step_payload)
        else:
            iteration_limit_hit = True

        if iteration_limit_hit and not final_text.strip():
            final_text = (
                f"Sub-agent stopped after reaching iteration limit ({iteration_budget}) without a final text response."
            )

        trace_payload = {
            "created_at": _utc_iso(),
            "query_id": query_id,
            "parent": {
                "thread_id": thread_id,
                "run_id": run_id,
                "request_index": request_index,
                "user_msg_index": user_msg_index,
                "parent_tool_use_id": parent_tool_use_id,
            },
            "query": {
                "task": task_text,
                "custom_instruction": custom_instruction,
                "allow_repl": allow_repl,
                "allow_bash": allow_bash,
                "max_iterations": iteration_budget,
                "attached_tools": tool_names,
                "seeded_env_vars": seeded_vars,
            },
            "system_prompt": system_prompt,
            "iteration_limit_hit": iteration_limit_hit,
            "error": error_message,
            "final_text": final_text,
            "tool_calls_total": total_tool_calls,
            "iterations": len(transcript_steps),
            "steps": transcript_steps,
            "messages_final": messages,
        }
        trace_path = self._write_trace(
            parent_thread_id=thread_id,
            parent_run_id=run_id,
            query_id=query_id,
            payload=trace_payload,
        )

        result = _SingleQueryResult(
            ok=error_message is None,
            task=task_text,
            text=final_text,
            error=error_message,
            trace_path=trace_path,
            tool_calls=total_tool_calls,
            iterations=len(transcript_steps),
        )
        return result.as_dict()

    def llm_query(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        request_index: int | None,
        user_msg_index: int | None,
        parent_tool_use_id: str | None,
        task: str,
        env: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        custom_instruction: str | None = None,
        allow_repl: bool = True,
        allow_bash: bool = True,
        max_iterations: int | None = None,
    ) -> str:
        result = self.run_query(
            thread_id=thread_id,
            run_id=run_id,
            request_index=request_index,
            user_msg_index=user_msg_index,
            parent_tool_use_id=parent_tool_use_id,
            task=task,
            env=env,
            allowed_tools=allowed_tools,
            custom_instruction=custom_instruction,
            allow_repl=allow_repl,
            allow_bash=allow_bash,
            max_iterations=max_iterations,
        )
        return str(result.get("text") or "")

    def llm_query_batch(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        request_index: int | None,
        user_msg_index: int | None,
        parent_tool_use_id: str | None,
        tasks: list[str | dict[str, Any]],
        shared_env: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        custom_instruction: str | None = None,
        allow_repl: bool = True,
        allow_bash: bool = True,
        max_iterations: int | None = None,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("llm_query_batch requires a non-empty tasks list")

        worker_count = int(max_workers or self.settings.repl_subagent_max_batch_workers)
        if worker_count < 1:
            worker_count = 1
        worker_count = min(worker_count, 64)

        results: list[dict[str, Any]] = [
            {
                "ok": False,
                "task": "",
                "text": None,
                "error": "uninitialized",
                "trace_path": None,
                "tool_calls": 0,
                "iterations": 0,
            }
            for _ in tasks
        ]

        def _run_one(index: int) -> None:
            entry = tasks[index]
            if isinstance(entry, dict):
                task_text = str(entry.get("task") or "").strip()
                entry_env = entry.get("env")
                merged_env = {}
                if isinstance(shared_env, dict):
                    merged_env.update(shared_env)
                if isinstance(entry_env, dict):
                    merged_env.update(entry_env)
                entry_tools = entry.get("allowed_tools")
                entry_instruction = entry.get("custom_instruction")
                entry_allow_repl = bool(entry.get("allow_repl", allow_repl))
                entry_allow_bash = bool(entry.get("allow_bash", allow_bash))
                entry_max_iterations = entry.get("max_iterations", max_iterations)
            else:
                task_text = str(entry or "").strip()
                merged_env = dict(shared_env or {})
                entry_tools = allowed_tools
                entry_instruction = custom_instruction
                entry_allow_repl = allow_repl
                entry_allow_bash = allow_bash
                entry_max_iterations = max_iterations

            try:
                result = self.run_query(
                    thread_id=thread_id,
                    run_id=run_id,
                    request_index=request_index,
                    user_msg_index=user_msg_index,
                    parent_tool_use_id=parent_tool_use_id,
                    task=task_text,
                    env=merged_env,
                    allowed_tools=entry_tools if isinstance(entry_tools, list) else allowed_tools,
                    custom_instruction=(
                        str(entry_instruction)
                        if isinstance(entry_instruction, str)
                        else custom_instruction
                    ),
                    allow_repl=entry_allow_repl,
                    allow_bash=entry_allow_bash,
                    max_iterations=int(entry_max_iterations) if isinstance(entry_max_iterations, int) else max_iterations,
                )
            except Exception as exc:
                results[index] = {
                    "ok": False,
                    "task": task_text,
                    "text": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace_path": None,
                    "tool_calls": 0,
                    "iterations": 0,
                }
                return

            results[index] = result

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(_run_one, idx) for idx in range(len(tasks))]
            for future in futures:
                future.result()

        return results
