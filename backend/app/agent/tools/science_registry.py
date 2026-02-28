from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.agent.tools.builtin import builtin_tool_specs
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolRegistry, ToolSpec
from app.agent.tools.sources.literature import build_literature_tools
from app.agent.tools.sources.longevity import build_longevity_tools
from app.agent.tools.sources.normalization import build_normalization_tools
from app.agent.tools.sources.optional_sources import build_optional_source_tools
from app.agent.tools.sources.evidence_tools import build_evidence_tools
from app.agent.tools.sources.paperqa_literature import build_paperqa_literature_tools
from app.agent.tools.sources.safety import build_safety_tools
from app.agent.tools.sources.trials import build_trial_tools


logger = logging.getLogger(__name__)


def create_science_registry(settings: Settings) -> ToolRegistry:
    artifacts_root = Path(settings.artifacts_root)
    source_cache_root = Path(settings.source_cache_root)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    source_cache_root.mkdir(parents=True, exist_ok=True)

    http = SimpleHttpClient(
        timeout_seconds=settings.tool_http_timeout_seconds,
        max_retries=settings.tool_http_max_retries,
        user_agent=settings.tool_http_user_agent,
    )

    tools: list[ToolSpec] = []
    tools.extend(builtin_tool_specs())
    if settings.enable_normalization_tools:
        tools.extend(build_normalization_tools(http, settings))
    if settings.enable_literature_tools and (settings.enable_pubmed_tools or settings.enable_openalex_tools):
        if settings.enable_openalex_tools and not (settings.openalex_api_key or settings.openalex_mailto):
            logger.warning(
                "OpenAlex tools enabled but neither OPENALEX_API_KEY nor OPENALEX_MAILTO is set; OpenAlex tools will be omitted."
            )
        tools.extend(build_literature_tools(settings, http))
    if settings.enable_literature_tools and settings.enable_paperqa_tools:
        tools.extend(build_paperqa_literature_tools(settings))
    if settings.enable_trial_tools:
        tools.extend(build_trial_tools(settings, http))
    if settings.enable_safety_tools:
        tools.extend(build_safety_tools(http))
    if settings.enable_longevity_tools:
        tools.extend(build_longevity_tools(http))
    if settings.enable_optional_source_tools:
        tools.extend(build_optional_source_tools(settings, http))
    tools.extend(build_evidence_tools())

    return ToolRegistry(
        tools,
        artifact_root=artifacts_root,
        source_cache_root=source_cache_root,
    )
