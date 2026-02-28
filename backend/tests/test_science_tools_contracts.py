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
        epistemonikos_api_key="epi-key",
        artifacts_root=tmp_path / "artifacts",
        source_cache_root=tmp_path / "artifacts" / "cache" / "sources",
        enable_literature_tools=True,
        enable_pubmed_tools=True,
        enable_openalex_tools=True,
        enable_optional_source_tools=True,
    )


def test_science_registry_contains_core_and_pipeline_tools(tmp_path: Path) -> None:
    registry = create_science_registry(_settings(tmp_path))
    names = {schema["function"]["name"] for schema in registry.openai_schemas()}

    assert "pubmed_esearch" in names
    assert "pubmed_efetch" in names
    assert "openalex_search_works" in names
    assert "clinicaltrials_search_studies" in names
    assert "clinicaltrials_search" in names
    assert "clinicaltrials_fetch" in names
    assert "rxnorm_resolve" in names
    assert "concept_merge_candidates" in names
    assert "normalize_mesh_expand" in names
    assert "normalize_expand_terms_llm" in names
    assert "pubmed_search" in names
    assert "pubmed_fetch" in names
    assert "europmc_search" in names
    assert "europmc_fetch" in names
    assert "evidence_classify_pubmed_records" in names
    assert "evidence_classify_trial_records" in names
    assert "evidence_build_ledger" in names
    assert "evidence_grade" in names
    assert "evidence_gap_map" in names
    assert "evidence_render_report" in names
    assert "evidence_retrieve_bundle" in names
    assert "evidence_grade_bundle" in names
    assert "evidence_generate_report" in names


def test_science_registry_omits_key_gated_tools_without_keys(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        openalex_api_key=None,
        epistemonikos_api_key=None,
    )
    registry = create_science_registry(settings)
    names = {schema["function"]["name"] for schema in registry.openai_schemas()}

    assert "openalex_search_works" not in names
    assert "openalex_get_works" not in names
    assert "epistemonikos_search_reviews" not in names
    assert "epistemonikos_get_review" not in names
    assert "pubmed_esearch" in names


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
    assert output["source_meta"]["data_schema_version"] == "v1"

    err = registry.execute("missing_tool", {}, ctx=ctx)
    assert err["status"] == "error"
    assert err["error"]["code"] == "NOT_FOUND"


def test_new_tools_have_structured_description_blocks(tmp_path: Path) -> None:
    registry = create_science_registry(_settings(tmp_path))
    schemas = registry.openai_schemas()
    descriptions = {item["function"]["name"]: item["function"]["description"] for item in schemas}

    required_blocks = ["WHEN:", "AVOID:", "CRITICAL_ARGS:", "RETURNS:", "FAILS_IF:"]
    new_tools = {
        "pubmed_search",
        "pubmed_fetch",
        "europmc_search",
        "europmc_fetch",
        "clinicaltrials_search",
        "clinicaltrials_fetch",
        "normalize_mesh_expand",
        "normalize_expand_terms_llm",
        "evidence_classify_pubmed_records",
        "evidence_classify_trial_records",
        "evidence_build_ledger",
        "evidence_grade",
        "evidence_gap_map",
        "evidence_render_report",
    }

    for name in new_tools:
        assert name in descriptions
        desc = descriptions[name]
        for block in required_blocks:
            assert block in desc
