from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.agent.tools.builtin import builtin_tool_specs
from app.agent.tools.http_client import SimpleHttpClient
from app.agent.tools.registry import ToolRegistry, ToolSpec
from app.agent.tools.sources.literature import build_literature_tools
from app.agent.tools.sources.longevity import build_longevity_tools
from app.agent.tools.sources.normalization import build_normalization_tools
from app.agent.tools.sources.optional_sources import build_optional_source_tools
from app.agent.tools.sources.safety import build_safety_tools
from app.agent.tools.sources.trials import build_trial_tools


def _apply_source_gating(settings: Settings, tools: list[ToolSpec]) -> list[ToolSpec]:
    gated: list[ToolSpec] = []
    for tool in tools:
        if tool.source == "openalex" and not settings.openalex_api_key:
            continue
        if tool.source == "epistemonikos" and not settings.epistemonikos_api_key:
            continue
        gated.append(tool)
    return gated


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
    if settings.enable_builtin_demo_tools:
        tools.extend(builtin_tool_specs())
    if settings.enable_normalization_tools:
        tools.extend(build_normalization_tools(http))
    if settings.enable_literature_tools:
        tools.extend(build_literature_tools(settings, http))
    if settings.enable_trial_tools:
        tools.extend(build_trial_tools(settings, http))
    if settings.enable_safety_tools:
        tools.extend(build_safety_tools(http))
    if settings.enable_longevity_tools:
        tools.extend(build_longevity_tools(http))
    if settings.enable_optional_source_tools:
        tools.extend(build_optional_source_tools(settings, http))

    tools = _apply_source_gating(settings, tools)

    return ToolRegistry(
        tools,
        artifact_root=artifacts_root,
        source_cache_root=source_cache_root,
    )
