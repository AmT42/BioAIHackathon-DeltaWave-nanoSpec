from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


_ENV_LOADED = False


def _load_env_file() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    # Resolve project backend root: backend/app/config.py -> backend/
    backend_root = Path(__file__).resolve().parents[1]
    load_dotenv(backend_root / ".env", override=False)
    _ENV_LOADED = True


@dataclass(frozen=True)
class Settings:
    database_url: str
    anthropic_api_key: str | None
    gemini_api_key: str | None
    claude_model: str
    gemini_model: str
    gemini_reasoning_effort: str
    gemini_include_thoughts: bool
    gemini_thinking_budget: int | None
    mock_llm: bool
    artifacts_root: Path
    source_cache_root: Path
    tool_http_timeout_seconds: int
    tool_http_max_retries: int
    tool_execution_timeout_seconds: int
    tool_http_user_agent: str
    openalex_api_key: str | None
    pubmed_api_key: str | None
    semanticscholar_api_key: str | None
    epistemonikos_api_key: str | None
    enable_normalization_tools: bool
    enable_literature_tools: bool
    enable_pubmed_tools: bool
    enable_openalex_tools: bool
    enable_trial_tools: bool
    enable_safety_tools: bool
    enable_longevity_tools: bool
    enable_optional_source_tools: bool


def _normalize_reasoning_effort(value: str | None) -> str:
    allowed = {"minimal", "low", "medium", "high", "disable", "none"}
    normalized = (value or "low").strip().lower()
    if normalized in allowed:
        return normalized
    return "low"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except Exception:
        return default
    return value if value >= 0 else default


def get_settings() -> Settings:
    _load_env_file()
    backend_root = Path(__file__).resolve().parents[1]
    artifacts_root = Path(os.getenv("ARTIFACTS_ROOT", str(backend_root / "artifacts"))).expanduser().resolve()
    source_cache_root = Path(
        os.getenv("SOURCE_CACHE_ROOT", str(artifacts_root / "cache" / "sources"))
    ).expanduser().resolve()

    enable_literature_tools = _env_bool("ENABLE_LITERATURE_TOOLS", default=True)
    enable_pubmed_tools = _env_bool("ENABLE_PUBMED_TOOLS", default=enable_literature_tools)
    enable_openalex_tools = _env_bool("ENABLE_OPENALEX_TOOLS", default=enable_literature_tools)

    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./chat.db"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini/gemini-3-flash"),
        gemini_reasoning_effort=_normalize_reasoning_effort(os.getenv("GEMINI_REASONING_EFFORT", "medium")),
        gemini_include_thoughts=_env_bool("GEMINI_INCLUDE_THOUGHTS", default=True),
        gemini_thinking_budget=_env_int("GEMINI_THINKING_BUDGET"),
        mock_llm=_env_bool("MOCK_LLM", default=False),
        artifacts_root=artifacts_root,
        source_cache_root=source_cache_root,
        tool_http_timeout_seconds=_env_int("TOOL_HTTP_TIMEOUT_SECONDS", default=20),
        tool_http_max_retries=_env_int("TOOL_HTTP_MAX_RETRIES", default=2),
        tool_execution_timeout_seconds=_env_int("TOOL_EXECUTION_TIMEOUT_SECONDS", default=45) or 45,
        tool_http_user_agent=os.getenv("TOOL_HTTP_USER_AGENT", "hackathon-agent-core/0.1"),
        openalex_api_key=os.getenv("OPENALEX_API_KEY"),
        pubmed_api_key=os.getenv("PUBMED_API_KEY"),
        semanticscholar_api_key=os.getenv("SEMANTICSCHOLAR_API_KEY"),
        epistemonikos_api_key=os.getenv("EPISTEMONIKOS_API_KEY"),
        enable_normalization_tools=_env_bool("ENABLE_NORMALIZATION_TOOLS", default=True),
        enable_literature_tools=enable_literature_tools,
        enable_pubmed_tools=enable_pubmed_tools,
        enable_openalex_tools=enable_openalex_tools,
        enable_trial_tools=_env_bool("ENABLE_TRIAL_TOOLS", default=True),
        enable_safety_tools=_env_bool("ENABLE_SAFETY_TOOLS", default=True),
        enable_longevity_tools=_env_bool("ENABLE_LONGEVITY_TOOLS", default=True),
        enable_optional_source_tools=_env_bool("ENABLE_OPTIONAL_SOURCE_TOOLS", default=True),
    )
