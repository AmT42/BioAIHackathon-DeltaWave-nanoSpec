from __future__ import annotations

from app.agent.prompt import DEFAULT_SYSTEM_PROMPT


def test_master_prompt_contains_required_policy_sections() -> None:
    text = DEFAULT_SYSTEM_PROMPT
    required_fragments = [
        "Mission:",
        "Adaptive retrieval strategy:",
        "Tool routing heuristics by concept type:",
        "Argument calibration rules:",
        "Source trust hierarchy:",
        "Fallback behavior:",
        "Output discipline:",
        "REPL vs Bash decision guide:",
        "Sub-agent routing guide:",
        "Use `bash_exec` for codebase navigation/inspection",
        "Use `repl_exec` for tool-wrapper orchestration",
        "`bash_exec` is a top-level tool call",
        "`llm_query`",
        "`llm_query_batch`",
    ]
    for frag in required_fragments:
        assert frag in text


def test_prompt_does_not_make_optional_sources_mandatory() -> None:
    text = DEFAULT_SYSTEM_PROMPT.lower()
    assert "do not stop the workflow because optional tools are unavailable" in text
    assert "openalex" in text
    assert "epistemonikos" in text


def test_prompt_includes_concrete_terminal_examples_and_itp_ids_guard() -> None:
    text = DEFAULT_SYSTEM_PROMPT
    assert "installed_packages(limit=200)" in text
    assert "help_examples(\"subagents\")" in text
    assert "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in text
    assert "https://clinicaltrials.gov/api/v2/studies" in text
    assert "longevity_itp_fetch_summary(ids=[...])" in text
    assert "non-empty list of ITP summary URLs" in text
