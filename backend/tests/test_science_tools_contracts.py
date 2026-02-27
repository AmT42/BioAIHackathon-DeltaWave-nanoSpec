from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.agent.tools.context import ToolContext
from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings


def _settings(tmp_path: Path):
    return replace(
        get_settings(),
        mock_llm=True,
        openalex_api_key="test-key",
        artifacts_root=tmp_path / "artifacts",
        source_cache_root=tmp_path / "artifacts" / "cache" / "sources",
    )


def test_science_registry_contains_core_tools(tmp_path: Path) -> None:
    registry = create_science_registry(_settings(tmp_path))
    names = {schema["function"]["name"] for schema in registry.openai_schemas()}

    assert "openalex_search_works" in names
    assert "clinicaltrials_search_studies" in names
    assert "rxnorm_resolve" in names
    assert "concept_merge_candidates" in names


def test_tool_output_contract_and_error_shape(tmp_path: Path) -> None:
    registry = create_science_registry(_settings(tmp_path))
    ctx = ToolContext(
        thread_id="thread-1",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        tool_use_id="call-1",
    )

    ok = registry.execute("calc", {"expression": "3*7"}, ctx=ctx)
    assert ok["status"] == "success"

    output = ok["output"]
    for key in [
        "summary",
        "data",
        "ids",
        "citations",
        "warnings",
        "artifacts",
        "pagination",
        "source_meta",
    ]:
        assert key in output

    err = registry.execute("missing_tool", {}, ctx=ctx)
    assert err["status"] == "error"
    assert err["error"]["code"] == "NOT_FOUND"
