from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.repl import ReplRuntime, ReplSessionManager
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.context import ToolContext
from app.agent.tools.builtin import create_builtin_registry
from app.agent.tools.registry import ToolRegistry, ToolSpec


def _runtime() -> ReplRuntime:
    return ReplRuntime(
        tools=create_builtin_registry(),
        workspace_root=Path("."),
        allowed_command_prefixes=("pwd", "ls", "rg", "grep", "cat", "bash"),
        blocked_command_prefixes=("rm", "curl", "wget"),
        max_stdout_bytes=8192,
        max_wall_time_seconds=30,
        max_tool_calls_per_exec=50,
        session_manager=ReplSessionManager(max_sessions=10, session_ttl_seconds=3600),
    )


def _runtime_with_tools(registry: ToolRegistry) -> ReplRuntime:
    return ReplRuntime(
        tools=registry,
        workspace_root=Path("."),
        allowed_command_prefixes=("pwd", "ls", "rg", "grep", "cat", "bash"),
        blocked_command_prefixes=("rm", "curl", "wget"),
        max_stdout_bytes=8192,
        max_wall_time_seconds=30,
        max_tool_calls_per_exec=50,
        session_manager=ReplSessionManager(max_sessions=10, session_ttl_seconds=3600),
    )


def _echo_registry() -> ToolRegistry:
    def demo_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="search",
            data={"payload": payload},
            ctx=ctx,
        )

    def demo_fetch(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="fetch",
            data={"payload": payload},
            ctx=ctx,
        )

    return ToolRegistry(
        [
            ToolSpec(
                name="demo_search",
                description="Demo search tool.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
                handler=demo_search,
                source="test",
            ),
            ToolSpec(
                name="demo_fetch",
                description="Demo fetch tool.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["ids"],
                },
                handler=demo_fetch,
                source="test",
            ),
        ]
    )


def test_repl_persists_variables_per_thread() -> None:
    runtime = _runtime()

    first = runtime.execute(
        thread_id="thread-a",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="x = 41",
    )
    assert first.error is None

    second = runtime.execute(
        thread_id="thread-a",
        run_id="run-2",
        request_index=2,
        user_msg_index=2,
        execution_id="repl-2",
        code="print(x + 1)",
    )
    assert second.error is None
    assert "42" in second.stdout


def test_repl_tool_wrapper_returns_handle() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-b",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="papers = fetch_paper_stub(topic='aging')\nprint(len(papers.ids))",
    )

    assert out.error is None
    assert "2" in out.stdout


def test_repl_warns_when_no_print_output() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-c",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="value = calc(expression='2+2')",
    )

    assert out.error is None
    assert out.had_visible_output is False
    assert "no visible output" in out.stdout.lower()


def test_repl_supports_dir_and_safe_json_import() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-d",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="import json\nprint('json' in dir())\nprint(json.dumps({'ok': True}))",
    )

    assert out.error is None
    assert "True" in out.stdout
    assert '{"ok": true}' in out.stdout


def test_repl_supports_globals_and_locals_builtins() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-d2",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="print(callable(globals))\nprint(callable(locals))\nprint('__builtins__' in globals())",
    )

    assert out.error is None
    assert "True\nTrue\nTrue" in out.stdout


def test_repl_blocks_unsafe_imports() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-e",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="import subprocess",
    )

    assert out.error is not None
    assert "blocked in REPL" in out.stderr


def test_repl_bash_helper_redirects_to_bash_exec() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-f",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="bash('pwd')",
    )

    assert out.error is not None
    assert "bash_exec" in out.stderr


def test_execute_bash_runs_command_outside_repl() -> None:
    runtime = _runtime()

    out = runtime.execute_bash(command="pwd", timeout_s=10)

    assert out.returncode == 0
    assert out.stdout.strip()


def test_repl_wrapper_maps_mixed_positional_and_max_results_alias() -> None:
    runtime = _runtime_with_tools(_echo_registry())

    out = runtime.execute(
        thread_id="thread-g",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="res = demo_search('alzheimers exercise', max_results=3)\nprint(res.data['payload'])",
    )

    assert out.error is None
    assert "'query': 'alzheimers exercise'" in out.stdout
    assert "'limit': 3" in out.stdout


def test_repl_wrapper_maps_pmids_alias_to_ids() -> None:
    runtime = _runtime_with_tools(_echo_registry())

    out = runtime.execute(
        thread_id="thread-h",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="res = demo_fetch(pmids=['1','2'])\nprint(res.data['payload'])",
    )

    assert out.error is None
    assert "'ids': ['1', '2']" in out.stdout


def test_repl_help_tool_exposes_schema_hints() -> None:
    runtime = _runtime_with_tools(_echo_registry())

    out = runtime.execute(
        thread_id="thread-i",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="print(help_tool('demo_search'))",
    )

    assert out.error is None
    assert "'name': 'demo_search'" in out.stdout
    assert "'required_args': ['query']" in out.stdout
