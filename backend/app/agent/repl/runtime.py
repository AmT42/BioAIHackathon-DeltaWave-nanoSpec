from __future__ import annotations

import builtins
import contextlib
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.agent.tools.context import ToolContext
from app.agent.tools.registry import ToolRegistry
from app.agent.repl.shell import ShellExecutor, ShellPolicy
from app.agent.repl.types import IdListHandle, ReplExecutionResult, ToolResultHandle


ToolStartCallback = Callable[[str, str, dict[str, Any]], None]
ToolResultCallback = Callable[[str, str, dict[str, Any]], None]

_ALLOWED_IMPORT_ROOTS = {
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
_ALLOWED_IMPORT_MODULES = {
    "urllib.parse",
}


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
        max_tool_calls_per_exec: int,
    ) -> None:
        self.thread_id = thread_id
        self.tools = tools
        self.shell = shell
        self.max_tool_calls_per_exec = max_tool_calls_per_exec
        self._hooks = _ExecutionHooks()
        self._nested_calls = 0

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

    def _coerce_single_positional(self, tool_name: str, arg: Any) -> dict[str, Any]:
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

        # Common ergonomic aliases
        if "max_results" in normalized and "limit" not in normalized:
            normalized["limit"] = normalized.pop("max_results")
        if "pmids" in normalized and "ids" not in normalized:
            normalized["ids"] = normalized.pop("pmids")
        if "nct_ids" in normalized and "ids" not in normalized:
            normalized["ids"] = normalized.pop("nct_ids")
        if tool_name == "pubmed_search" and "term" in normalized and "query" not in normalized:
            normalized["query"] = normalized.pop("term")

        return normalized

    def _run_tool(self, tool_name: str, payload: dict[str, Any]) -> ToolResultHandle:
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

        if self._hooks.on_tool_result is not None:
            self._hooks.on_tool_result(nested_call_id, tool_name, result)

        if result.get("status") != "success":
            error = result.get("error") or {}
            raise RuntimeError(f"Tool '{tool_name}' failed: {error}")

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
        max_tool_calls_per_exec: int,
    ) -> ReplSessionState:
        with self._lock:
            self._cleanup()
            session = self._sessions.get(thread_id)
            if session is not None and session.bindings is not None:
                session.updated_at = time.time()
                return session

            bindings = ReplBindings(
                thread_id=thread_id,
                tools=tools,
                shell=shell,
                max_tool_calls_per_exec=max_tool_calls_per_exec,
            )
            globals_map = _build_base_globals(bindings)
            session = ReplSessionState(thread_id=thread_id, globals=globals_map, bindings=bindings)
            self._sessions[thread_id] = session
            return session


def _safe_import(name: str, globals: dict[str, Any] | None = None, locals: dict[str, Any] | None = None, fromlist: Any = (), level: int = 0) -> Any:
    if str(name or "") in _ALLOWED_IMPORT_MODULES:
        return builtins.__import__(name, globals, locals, fromlist, level)
    root = str(name or "").split(".", 1)[0]
    if root not in _ALLOWED_IMPORT_ROOTS:
        allowed_roots = ", ".join(sorted(_ALLOWED_IMPORT_ROOTS))
        allowed_modules = ", ".join(sorted(_ALLOWED_IMPORT_MODULES))
        raise ImportError(
            f"Import '{name}' is blocked in REPL. Allowed roots: {allowed_roots}. "
            f"Allowed modules: {allowed_modules}. "
            "For biomedical retrieval, use wrappers like pubmed_search/pubmed_fetch (not urllib/curl)."
        )
    return builtins.__import__(name, globals, locals, fromlist, level)


def _bash_disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("bash(...) is disabled inside repl_exec. Use the top-level tool bash_exec.")


def _grep_disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("grep(...) is disabled inside repl_exec. Use bash_exec with an rg command.")


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
    safe_builtins["__import__"] = _safe_import

    def _help_tool(tool_name: str) -> dict[str, Any]:
        spec = bindings.tools.get_spec(str(tool_name))
        if spec is None:
            return {"error": f"Unknown tool '{tool_name}'", "available_tools": sorted(bindings.tools.names())}
        schema = spec.input_schema if isinstance(spec.input_schema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        return {
            "name": spec.name,
            "required_args": [str(item) for item in required],
            "properties": sorted(str(key) for key in properties.keys()),
            "source": spec.source,
        }

    globals_map: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "bash": _bash_disabled,
        "grep": _grep_disabled,
        "parallel_map": bindings.parallel_map,
        "json": json,
        "help_tools": lambda: sorted(bindings.tools.names()),
        "help_tool": _help_tool,
        "help_repl": lambda: (
            "Use repl_exec for Python + tool wrappers and bash_exec for shell.\n"
            "Search tools usually take query + limit (or term + retmax aliases).\n"
            "Fetch tools usually take ids (aliases pmids/nct_ids are accepted).\n"
            "Example: res = pubmed_search(query='exercise AND alzheimer', limit=3); print(res.preview())"
        ),
    }

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
        allowed_command_prefixes: tuple[str, ...],
        blocked_command_prefixes: tuple[str, ...],
        max_stdout_bytes: int,
        max_wall_time_seconds: int,
        max_tool_calls_per_exec: int,
        session_manager: ReplSessionManager,
    ) -> None:
        self.tools = tools
        self.max_wall_time_seconds = max(1, int(max_wall_time_seconds))
        self.max_stdout_bytes = max(1024, int(max_stdout_bytes))
        self.max_tool_calls_per_exec = max(1, int(max_tool_calls_per_exec))
        self.session_manager = session_manager
        self.shell = ShellExecutor(
            ShellPolicy(
                workspace_root=workspace_root,
                allowed_prefixes=allowed_command_prefixes,
                blocked_prefixes=blocked_command_prefixes,
                max_output_bytes=self.max_stdout_bytes,
            )
        )

    def execute_bash(self, *, command: str, timeout_s: int = 30, cwd: str | None = None):
        return self.shell.run(command, timeout_s=timeout_s, cwd=cwd)

    def _truncate(self, text: str) -> tuple[str, bool]:
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_stdout_bytes:
            return text, False
        return encoded[: self.max_stdout_bytes].decode("utf-8", errors="replace"), True

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
    ) -> ReplExecutionResult:
        started = time.monotonic()
        session = self.session_manager.get_or_create(
            thread_id=thread_id,
            tools=self.tools,
            shell=self.shell,
            max_tool_calls_per_exec=self.max_tool_calls_per_exec,
        )
        assert session.bindings is not None
        session.bindings.set_execution_context(
            run_id=run_id,
            request_index=request_index,
            user_msg_index=user_msg_index,
            parent_tool_use_id=execution_id,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
        )

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        error: str | None = None

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
            raw_stdout = "REPL executed successfully but produced no visible output. Use print(...) to expose results."

        stdout, out_truncated = self._truncate(raw_stdout)
        stderr, err_truncated = self._truncate(raw_stderr)

        session.updated_at = time.time()

        return ReplExecutionResult(
            execution_id=execution_id,
            stdout=stdout,
            stderr=stderr,
            nested_tool_calls=session.bindings.nested_call_count(),
            truncated=out_truncated or err_truncated,
            had_visible_output=had_visible_output,
            error=error,
        )
