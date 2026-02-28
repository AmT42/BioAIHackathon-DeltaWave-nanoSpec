from __future__ import annotations

import builtins
import contextlib
import io
import json
import re
import subprocess
import sys
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
TextStreamCallback = Callable[[str], None]
ImportCallback = Callable[[str, dict[str, Any] | None, dict[str, Any] | None, Any, int], Any]

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
        max_tool_calls_per_exec: int,
    ) -> None:
        self.thread_id = thread_id
        self.tools = tools
        self.shell = shell
        self.import_hook = import_hook
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
        max_tool_calls_per_exec: int,
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
                max_tool_calls_per_exec=max_tool_calls_per_exec,
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
    safe_builtins["__import__"] = bindings.import_hook

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

    globals_map: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "bash": _bash_disabled,
        "grep": _grep_disabled,
        "parallel_map": bindings.parallel_map,
        "json": json,
        "help_tools": lambda: sorted(bindings.tools.names()),
        "help_tool": _help_tool,
        "help_bash": lambda: "Use bash_exec for shell commands, e.g. bash_exec(command='rg -n \"query\" .').",
        "env_vars": _env_vars,
        "help_repl": lambda: (
            "Use repl_exec for Python + tool wrappers and bash_exec for shell.\n"
            "Call help_tools() / help_tool('name') when unsure about wrapper signatures.\n"
            "Use env_vars() to inspect current user-defined REPL variables (name/type/preview).\n"
            "Search tools usually take query + limit (or term + retmax aliases).\n"
            "Fetch tools usually take ids (aliases pmids/nct_ids are accepted).\n"
            "Example:\n"
            "  res = pubmed_search(query='exercise AND alzheimer', limit=3)\n"
            "  print(res.preview())\n"
            "  rows = pubmed_fetch(ids=res.ids[:3], include_abstract=True)\n"
            "  print(rows.preview())"
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
        env_snapshot_mode: str = "debug",
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
        import_policy: str = "broad",
        import_allow_modules: tuple[str, ...] = (),
        lazy_install_enabled: bool = False,
        lazy_install_allowlist: tuple[str, ...] = (),
        lazy_install_timeout_seconds: int = 60,
        lazy_install_index_url: str | None = None,
    ) -> None:
        self.tools = tools
        self.max_wall_time_seconds = max(1, int(max_wall_time_seconds))
        self.max_stdout_bytes = max(1024, int(max_stdout_bytes))
        self.max_tool_calls_per_exec = max(1, int(max_tool_calls_per_exec))
        self.env_snapshot_mode = (
            env_snapshot_mode if env_snapshot_mode in {"off", "debug", "always"} else "debug"
        )
        self.env_snapshot_max_items = max(10, int(env_snapshot_max_items))
        self.env_snapshot_max_preview_chars = max(32, int(env_snapshot_max_preview_chars))
        self.env_snapshot_redact_keys = tuple(
            key.strip().lower() for key in env_snapshot_redact_keys if str(key).strip()
        ) or ("api_key", "token", "secret", "password", "auth", "cookie")

        self.import_policy = import_policy if import_policy in {"minimal", "broad"} else "broad"
        self.allowed_import_roots = set(_MINIMAL_ALLOWED_IMPORT_ROOTS)
        self.allowed_import_modules = set(_MINIMAL_ALLOWED_IMPORT_MODULES)
        if self.import_policy == "broad":
            self.allowed_import_roots.update(_BROAD_EXTRA_IMPORT_ROOTS)
            self.allowed_import_modules.update(_BROAD_EXTRA_IMPORT_MODULES)
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
        self._lazy_install_lock = threading.Lock()
        self._lazy_install_success: set[str] = set()
        self._lazy_install_failed: set[str] = set()

        self.session_manager = session_manager
        self.shell = ShellExecutor(
            ShellPolicy(
                workspace_root=workspace_root,
                allowed_prefixes=allowed_command_prefixes,
                blocked_prefixes=blocked_command_prefixes,
                max_output_bytes=self.max_stdout_bytes,
            )
        )

    def _is_import_allowed(self, module_name: str) -> bool:
        normalized = str(module_name or "").strip()
        if not normalized:
            return False
        if normalized in self.allowed_import_modules:
            return True
        return normalized.split(".", 1)[0] in self.allowed_import_roots

    def _format_blocked_import_message(self, module_name: str) -> str:
        allowed_roots = ", ".join(sorted(self.allowed_import_roots))
        allowed_modules = ", ".join(sorted(self.allowed_import_modules))
        return (
            f"Import '{module_name}' is blocked in REPL. Allowed roots: {allowed_roots}. "
            f"Allowed modules: {allowed_modules}. "
            "Use tool wrappers for biomedical retrieval and bash_exec for shell commands."
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
                "Use bash_exec to install dependencies or rely on available wrappers."
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
        on_stdout_chunk: TextStreamCallback | None = None,
        on_stderr_chunk: TextStreamCallback | None = None,
    ) -> ReplExecutionResult:
        started = time.monotonic()
        session = self.session_manager.get_or_create(
            thread_id=thread_id,
            tools=self.tools,
            shell=self.shell,
            import_hook=self._import_hook,
            max_tool_calls_per_exec=self.max_tool_calls_per_exec,
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
            raw_stdout = "REPL executed successfully but produced no visible output. Use print(...) to expose results."

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
        )
