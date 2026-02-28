from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.agent.tools.context import ToolContext
from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings


def _settings(tmp_path: Path, *, openalex_key: str | None = None, epi_key: str | None = None):
    return replace(
        get_settings(),
        mock_llm=True,
        openalex_api_key=openalex_key,
        epistemonikos_api_key=epi_key,
        artifacts_root=tmp_path / "artifacts",
        source_cache_root=tmp_path / "artifacts" / "cache" / "sources",
        enable_builtin_demo_tools=False,
    )


def test_science_registry_contains_core_tools(tmp_path: Path) -> None:
    registry = create_science_registry(_settings(tmp_path))
    names = {schema["function"]["name"] for schema in registry.openai_schemas()}

    assert "pubmed_search" in names
    assert "clinicaltrials_search" in names
    assert "normalize_drug" in names
    assert "normalize_merge_candidates" in names
    assert "web_search_mock" not in names


def test_registry_gates_openalex_and_epistemonikos_by_key(tmp_path: Path) -> None:
    no_keys = create_science_registry(_settings(tmp_path, openalex_key=None, epi_key=None))
    no_key_names = {schema["function"]["name"] for schema in no_keys.openai_schemas()}
    assert "openalex_search" not in no_key_names
    assert "epistemonikos_search" not in no_key_names

    with_keys = create_science_registry(_settings(tmp_path, openalex_key="oa-key", epi_key="epi-key"))
    with_key_names = {schema["function"]["name"] for schema in with_keys.openai_schemas()}
    assert "openalex_search" in with_key_names
    assert "epistemonikos_search" in with_key_names


def test_tool_output_contract_v2_shape(tmp_path: Path) -> None:
    registry = create_science_registry(_settings(tmp_path))
    ctx = ToolContext(
        thread_id="thread-1",
        run_id="run-1",
        request_index=1,
        user_msg_index=1,
        tool_use_id="call-1",
    )

    ok = registry.execute("normalize_merge_candidates", {"user_text": "rapamycin"}, ctx=ctx)
    assert ok["status"] == "success"

    output = ok["output"]
    for key in [
        "contract_version",
        "result_kind",
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

    assert output["contract_version"] == "2.0"
    assert output["result_kind"] in {"id_list", "record_list", "document", "aggregate", "status"}

    assert "auth" in output["source_meta"]
    assert isinstance(output["source_meta"]["auth"].get("required"), bool)
    assert isinstance(output["source_meta"]["auth"].get("configured"), bool)
    assert "guidance" not in output

    err = registry.execute("missing_tool", {}, ctx=ctx)
    assert err["status"] == "error"
    assert err["error"]["code"] == "NOT_FOUND"
