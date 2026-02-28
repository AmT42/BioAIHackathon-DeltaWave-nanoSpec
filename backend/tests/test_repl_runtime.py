from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.repl import ReplRuntime, ReplSessionManager
from app.agent.repl.types import IdListHandle, ToolResultHandle
from app.agent.tools.contracts import make_tool_output
from app.agent.tools.context import ToolContext
from app.agent.tools.builtin import create_builtin_registry
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.registry import ToolRegistry, ToolSpec


def _runtime(**overrides: Any) -> ReplRuntime:
    return ReplRuntime(
        tools=create_builtin_registry(),
        workspace_root=Path("."),
        allowed_command_prefixes=("pwd", "ls", "rg", "grep", "cat", "bash"),
        blocked_command_prefixes=("rm", "curl", "wget"),
        max_stdout_bytes=8192,
        max_wall_time_seconds=30,
        max_tool_calls_per_exec=50,
        session_manager=ReplSessionManager(max_sessions=10, session_ttl_seconds=3600),
        **overrides,
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


def _merge_registry() -> ToolRegistry:
    def normalize_ontology(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="ontology",
            data={"query": payload.get("query"), "hits": [{"id": "NCIT:C38065"}]},
            ctx=ctx,
        )

    def normalize_drug(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="drug",
            data={"query": payload.get("query"), "candidates": []},
            ctx=ctx,
        )

    def normalize_merge_candidates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="merged",
            data={"payload": payload},
            ctx=ctx,
        )

    return ToolRegistry(
        [
            ToolSpec(
                name="normalize_ontology",
                description="Ontology alias",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=normalize_ontology,
                source="test",
            ),
            ToolSpec(
                name="normalize_drug",
                description="Drug alias",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=normalize_drug,
                source="test",
            ),
            ToolSpec(
                name="normalize_merge_candidates",
                description="Merge alias",
                input_schema={
                    "type": "object",
                    "properties": {
                        "user_text": {"type": "string"},
                        "drug_candidates": {"type": "object"},
                        "ontology_candidates": {"type": "object"},
                    },
                    "required": ["user_text"],
                },
                handler=normalize_merge_candidates,
                source="test",
            ),
        ]
    )


def _coercion_registry() -> ToolRegistry:
    def normalize_ontology(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="ontology",
            data={"query": payload.get("query"), "hits": [{"obo_id": "NCIT:C38065", "label": "HBOT"}]},
            ctx=ctx,
        )

    def normalize_merge_candidates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="merged",
            data={"concept": {"label": "Hyperbaric Oxygen Therapy"}, "payload": payload},
            ctx=ctx,
        )

    def retrieval_build_query_terms(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="terms",
            data={"payload": payload, "terms": {"pubmed": ["Hyperbaric Oxygen Therapy"]}},
            ctx=ctx,
        )

    def retrieval_build_pubmed_templates(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="templates",
            data={"payload": payload},
            ctx=ctx,
        )

    def clinicaltrials_search(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        return make_tool_output(
            source="test",
            summary="trials",
            data={"payload": payload},
            ctx=ctx,
        )

    return ToolRegistry(
        [
            ToolSpec(
                name="normalize_ontology",
                description="Ontology alias",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=normalize_ontology,
                source="test",
            ),
            ToolSpec(
                name="normalize_merge_candidates",
                description="Merge alias",
                input_schema={
                    "type": "object",
                    "properties": {
                        "user_text": {"type": "string"},
                        "ontology_candidates": {"type": "object"},
                    },
                    "required": ["user_text"],
                },
                handler=normalize_merge_candidates,
                source="test",
            ),
            ToolSpec(
                name="retrieval_build_query_terms",
                description="Build terms",
                input_schema={
                    "type": "object",
                    "properties": {"concept": {"type": "object"}},
                    "required": ["concept"],
                },
                handler=retrieval_build_query_terms,
                source="test",
            ),
            ToolSpec(
                name="retrieval_build_pubmed_templates",
                description="Build templates",
                input_schema={
                    "type": "object",
                    "properties": {
                        "terms": {"type": "object"},
                        "intervention_terms": {"type": "array", "items": {"type": "string"}},
                    },
                },
                handler=retrieval_build_pubmed_templates,
                source="test",
            ),
            ToolSpec(
                name="clinicaltrials_search",
                description="ClinicalTrials search",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "intervention": {"type": "string"},
                        "condition": {"type": "string"},
                    },
                },
                handler=clinicaltrials_search,
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


def test_repl_warns_when_print_exists_but_never_executes() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-c2",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="for _ in []:\n    print('x')",
    )

    assert out.error is None
    assert out.had_visible_output is False
    lowered = out.stdout.lower()
    assert "no visible output" in lowered
    assert "includes print" in lowered
    assert "empty loops" in lowered


def test_repl_caps_long_printed_line_and_writes_artifact(tmp_path: Path) -> None:
    runtime = _runtime(
        artifact_root=tmp_path,
        stdout_soft_line_limit=120,
        stdout_max_line_artifacts=5,
    )

    out = runtime.execute(
        thread_id="thread-cap",
        run_id="run-cap",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-cap-1",
        code="print('X' * 400)",
    )

    assert out.error is None
    assert out.had_visible_output is True
    assert "stdout line capped at 120 chars" in out.stdout
    assert out.stdout_capping is not None
    assert out.stdout_capping.get("lines_capped") == 1
    assert out.artifacts
    artifact_path = Path(str(out.artifacts[0].get("path") or ""))
    display_path = str(out.artifacts[0].get("display_path") or "")
    assert artifact_path.exists()
    assert "repl_stdout" in str(artifact_path)
    assert "thread-" not in str(artifact_path)
    assert display_path and not display_path.startswith("/")
    assert "saved to " + display_path in out.stdout
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert "REPL Stdout Full Line" in artifact_text
    assert "X" * 200 in artifact_text
    tool_payload = out.to_tool_output()["output"]
    assert isinstance(tool_payload.get("artifacts"), list) and tool_payload["artifacts"]
    assert isinstance(tool_payload.get("stdout_capping"), dict)


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


def test_repl_exposes_open_builtin() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-d3",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="print(callable(open))",
    )

    assert out.error is None
    assert "True" in out.stdout


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
    assert "blocked" in out.stderr


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


def test_repl_bash_exec_symbol_is_bound_with_clear_guidance() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-f2",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="bash_exec('pwd')",
    )

    assert out.error is not None
    assert "cannot be called from inside repl_exec" in out.stderr.lower()
    assert "top-level tool call" in out.stderr.lower()


def test_execute_bash_runs_command_outside_repl() -> None:
    runtime = _runtime()

    out = runtime.execute_bash(command="pwd", timeout_s=10)

    assert out.returncode == 0
    assert out.stdout.strip()


def test_execute_bash_open_mode_allows_unlisted_prefix() -> None:
    runtime = _runtime(shell_policy_mode="open")

    out = runtime.execute_bash(command="echo hello-open", timeout_s=10)

    assert out.returncode == 0
    assert "hello-open" in out.stdout


def test_execute_bash_open_mode_still_blocks_hard_prefix() -> None:
    runtime = _runtime(shell_policy_mode="open")

    with pytest.raises(ValueError):
        runtime.execute_bash(command="rm -f /tmp/repl_runtime_block_test", timeout_s=10)


def test_execute_bash_blocks_sensitive_pattern_even_in_open_mode() -> None:
    runtime = _runtime(shell_policy_mode="open", blocked_command_patterns=("git reset --hard",))

    with pytest.raises(ValueError):
        runtime.execute_bash(command="git reset --hard HEAD", timeout_s=10)


def test_execute_bash_guarded_mode_rejects_unlisted_prefix() -> None:
    runtime = _runtime(shell_policy_mode="guarded")

    with pytest.raises(ValueError):
        runtime.execute_bash(command="echo should-fail-guarded", timeout_s=10)


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


def test_repl_broad_import_policy_allows_urllib_request() -> None:
    runtime = _runtime(import_policy="broad")

    out = runtime.execute(
        thread_id="thread-j",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="import urllib.request\nprint('ok')",
    )

    assert out.error is None
    assert "ok" in out.stdout


def test_repl_minimal_import_policy_blocks_urllib_request() -> None:
    runtime = _runtime(import_policy="minimal")

    out = runtime.execute(
        thread_id="thread-k",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="import urllib.request",
    )

    assert out.error is not None
    assert "blocked in REPL" in out.stderr


def test_repl_env_snapshot_debug_mode_on_error() -> None:
    runtime = _runtime(
        env_snapshot_mode="debug",
        env_snapshot_max_items=20,
        env_snapshot_max_preview_chars=80,
        env_snapshot_redact_keys=("token",),
    )

    out = runtime.execute(
        thread_id="thread-l",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="api_token = 'secret-123'\nres = 42\nraise ValueError('boom')",
    )

    assert out.error is not None
    assert out.env_snapshot is not None
    after = out.env_snapshot.get("after") if isinstance(out.env_snapshot, dict) else {}
    items = after.get("items") if isinstance(after, dict) else []
    assert isinstance(items, list)
    names = {str(item.get("name")): item for item in items if isinstance(item, dict)}
    assert "res" in names
    assert "api_token" in names
    assert names["api_token"].get("preview") == "[REDACTED]"


def test_id_list_handle_supports_union_and_addition() -> None:
    left = IdListHandle(["1", "2"])
    right = IdListHandle(["2", "3"])
    merged = left + right
    assert isinstance(merged, IdListHandle)
    assert merged.to_list() == ["1", "2", "3"]
    assert left.head(1) == ["1"]


def test_tool_result_handle_records_iteration_and_shape() -> None:
    handle = ToolResultHandle(
        tool_name="demo",
        payload={
            "summary": "ok",
            "ids": ["a"],
            "data": {"records": [{"pmid": "1"}, {"pmid": "2"}]},
        },
        raw_result={"status": "success"},
    )
    rows = list(handle)
    assert len(rows) == 2
    assert rows[0]["pmid"] == "1"
    assert handle.get("records")[1]["pmid"] == "2"
    assert handle["records"][0]["pmid"] == "1"
    shape = handle.shape()
    assert shape["records_count"] == 2


def test_repl_merge_candidates_accepts_positional_handle_list() -> None:
    runtime = _runtime_with_tools(_merge_registry())

    out = runtime.execute(
        thread_id="thread-m",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code=(
            "o = normalize_ontology(query='Hyperbaric oxygen therapy')\n"
            "d = normalize_drug(query='Hyperbaric oxygen therapy')\n"
            "m = normalize_merge_candidates([o, d])\n"
            "print(m.data['payload']['user_text'])\n"
            "print(sorted([k for k in m.data['payload'].keys() if k.endswith('_candidates')]))"
        ),
    )

    assert out.error is None
    assert "Hyperbaric oxygen therapy" in out.stdout
    assert "drug_candidates" in out.stdout
    assert "ontology_candidates" in out.stdout


def test_repl_runtime_info_and_help_examples_helpers_available() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-n",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code=(
            "info = runtime_info()\n"
            "print('workspace_root' in info)\n"
            "print('help_examples' in info['helpers'])\n"
            "print('installed_packages' in info['helpers'])\n"
            "print('shell_policy_mode' in info)\n"
            "print('execution_limits' in info)\n"
            "ex = help_examples('longevity')\n"
            "print(ex['topic'])\n"
            "print(len(ex['examples']) > 0)\n"
            "ex2 = help_examples('shell_vs_repl')\n"
            "print(ex2['topic'])\n"
            "print(len(ex2['examples']) > 0)\n"
            "print(any('clinicaltrials.gov/api/v2/studies' in line for line in ex2['examples']))\n"
            "print(any('eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi' in line for line in ex2['examples']))"
        ),
    )

    assert out.error is None
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    assert lines[:9] == [
        "True",
        "True",
        "True",
        "True",
        "True",
        "longevity",
        "True",
        "shell_vs_repl",
        "True",
    ]
    assert lines[9:] == ["True", "True"]


def test_repl_installed_packages_helper_returns_items() -> None:
    runtime = _runtime()

    out = runtime.execute(
        thread_id="thread-n2",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code=(
            "pkgs = installed_packages(limit=5)\n"
            "print('items' in pkgs)\n"
            "print(isinstance(pkgs.get('items'), list))\n"
            "print('count' in pkgs)\n"
            "print(len(pkgs.get('items', [])) <= 5)"
        ),
    )

    assert out.error is None
    assert "True\nTrue\nTrue\nTrue" in out.stdout


def test_repl_coerces_query_terms_from_merge_handle() -> None:
    runtime = _runtime_with_tools(_coercion_registry())

    out = runtime.execute(
        thread_id="thread-o",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code=(
            "o = normalize_ontology(query='Hyperbaric oxygen therapy')\n"
            "m = normalize_merge_candidates([o], user_text='Hyperbaric oxygen therapy')\n"
            "t = retrieval_build_query_terms(m)\n"
            "print(t.data['payload']['concept']['label'])"
        ),
    )

    assert out.error is None
    assert "Hyperbaric Oxygen Therapy" in out.stdout


def test_repl_coerces_pubmed_templates_from_terms_handle() -> None:
    runtime = _runtime_with_tools(_coercion_registry())

    out = runtime.execute(
        thread_id="thread-p",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code=(
            "o = normalize_ontology(query='Hyperbaric oxygen therapy')\n"
            "m = normalize_merge_candidates([o], user_text='Hyperbaric oxygen therapy')\n"
            "terms = retrieval_build_query_terms(concept=m.data.get('concept'))\n"
            "templates = retrieval_build_pubmed_templates(intervention_terms=terms)\n"
            "print(templates.data['payload']['intervention_terms'])"
        ),
    )

    assert out.error is None
    assert "Hyperbaric Oxygen Therapy" in out.stdout


def test_repl_clinicaltrials_search_flattens_nested_query_object() -> None:
    runtime = _runtime_with_tools(_coercion_registry())

    out = runtime.execute(
        thread_id="thread-q",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code=(
            "res = clinicaltrials_search(query={'term': 'hbot', 'intr': 'hbot'})\n"
            "print(res.data['payload'])"
        ),
    )

    assert out.error is None
    assert "'query': 'hbot'" in out.stdout
    assert "'intervention': 'hbot'" in out.stdout


def test_repl_itp_missing_ids_surfaces_targeted_hint() -> None:
    def longevity_itp_fetch_summary(payload: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, Any]:
        ids = payload.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ToolExecutionError(
                code="VALIDATION_ERROR",
                message="'ids' must be a non-empty list",
                retryable=False,
                details={},
            )
        return make_tool_output(source="test", summary="ok", data={"records": []}, ctx=ctx)

    runtime = _runtime_with_tools(
        ToolRegistry(
            [
                ToolSpec(
                    name="longevity_itp_fetch_summary",
                    description="ITP fetch",
                    input_schema={
                        "type": "object",
                        "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                        "required": ["ids"],
                    },
                    handler=longevity_itp_fetch_summary,
                    source="test",
                )
            ]
        )
    )

    out = runtime.execute(
        thread_id="thread-r",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        execution_id="repl-1",
        code="longevity_itp_fetch_summary()",
    )

    assert out.error is not None
    assert "'ids' must be a non-empty list" in out.error
    assert "Hint:" in out.error
    assert "longevity_itp_fetch_summary(ids=['<itp_summary_url>'])" in out.error
