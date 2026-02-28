from __future__ import annotations

import re
from dataclasses import replace

from app.agent.prompt import DEFAULT_SYSTEM_PROMPT
from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings


_TOOL_NAME_PREFIXES = (
    "normalize",
    "retrieval",
    "pubmed",
    "clinicaltrials",
    "trial",
    "openalex",
    "europmc",
    "dailymed",
    "openfda",
    "longevity",
    "evidence",
    "literature",
    "fetch",
    "concept",
    "build",
    "chebi",
    "chembl",
    "rxnorm",
    "ols",
)

_TOP_LEVEL_TOOLS = {"repl_exec", "bash_exec"}


def _prompt_tool_candidates(text: str) -> set[str]:
    tokens = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))
    candidates: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        if lowered in _TOP_LEVEL_TOOLS:
            candidates.add(lowered)
            continue
        if any(lowered.startswith(prefix) for prefix in _TOOL_NAME_PREFIXES):
            candidates.add(lowered)
    return candidates


def test_system_prompt_references_only_registered_tools() -> None:
    settings = replace(
        get_settings(),
        mock_llm=True,
        openalex_api_key="oa-key",
        enable_builtin_demo_tools=False,
    )
    registry = create_science_registry(settings)
    available = {name.lower() for name in registry.names()} | _TOP_LEVEL_TOOLS

    candidates = _prompt_tool_candidates(DEFAULT_SYSTEM_PROMPT)
    missing = sorted(name for name in candidates if name not in available)
    assert not missing, f"Prompt references unknown tools: {missing}"
