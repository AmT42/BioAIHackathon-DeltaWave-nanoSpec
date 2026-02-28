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
    ]
    for frag in required_fragments:
        assert frag in text


def test_prompt_does_not_make_optional_sources_mandatory() -> None:
    text = DEFAULT_SYSTEM_PROMPT.lower()
    assert "do not stop the workflow because optional tools are unavailable" in text
    assert "openalex" in text
    assert "epistemonikos" in text
