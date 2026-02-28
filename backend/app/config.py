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
    gemini_replay_signature_mode: str
    mock_llm: bool
    artifacts_root: Path
    source_cache_root: Path
    tool_http_timeout_seconds: int
    tool_http_max_retries: int
    tool_http_user_agent: str
    openalex_api_key: str | None
    openalex_mailto: str | None
    pubmed_api_key: str | None
    semanticscholar_api_key: str | None
    epistemonikos_api_key: str | None
    paperqa_model: str
    paperqa_sub_agent_model: str
    paperqa_workflow_model: str
    paperqa_pipeline_model: str
    paperqa_timeout_seconds: int
    enable_normalization_tools: bool
    enable_literature_tools: bool
    enable_pubmed_tools: bool
    enable_openalex_tools: bool
    enable_paperqa_tools: bool
    enable_trial_tools: bool
    enable_safety_tools: bool
    enable_longevity_tools: bool
    enable_optional_source_tools: bool
    enable_builtin_demo_tools: bool
    agent_execution_mode: str
    repl_workspace_root: Path
    repl_max_wall_time_seconds: int
    repl_max_stdout_bytes: int
    repl_max_tool_calls_per_exec: int
    repl_session_ttl_seconds: int
    repl_max_sessions: int
    repl_allowed_command_prefixes: tuple[str, ...]
    repl_blocked_command_prefixes: tuple[str, ...]
    repl_env_snapshot_mode: str
    repl_env_snapshot_max_items: int
    repl_env_snapshot_max_preview_chars: int
    repl_env_snapshot_redact_keys: tuple[str, ...]
    repl_import_policy: str
    repl_import_allow_modules: tuple[str, ...]
    repl_import_deny_modules: tuple[str, ...]
    repl_lazy_install_enabled: bool
    repl_lazy_install_allowlist: tuple[str, ...]
    repl_lazy_install_timeout_seconds: int
    repl_lazy_install_index_url: str | None
    repl_preload_enabled: bool
    repl_preload_profile: str
    repl_preload_packages: tuple[str, ...]
    repl_preload_timeout_seconds: int
    repl_preload_fail_mode: str


def _normalize_reasoning_effort(value: str | None) -> str:
    allowed = {"minimal", "low", "medium", "high", "disable", "none"}
    normalized = (value or "low").strip().lower()
    if normalized in allowed:
        return normalized
    return "low"


def _normalize_replay_signature_mode(value: str | None) -> str:
    normalized = (value or "strict").strip().lower()
    if normalized in {"strict", "placeholder"}:
        return normalized
    return "strict"


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


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    values = [item.strip() for item in str(raw).split(",")]
    return tuple(item for item in values if item)


def _normalize_agent_execution_mode(value: str | None) -> str:
    normalized = (value or "repl_only").strip().lower()
    if normalized in {"repl_only"}:
        return normalized
    return "repl_only"


def _normalize_repl_env_snapshot_mode(value: str | None) -> str:
    normalized = (value or "debug").strip().lower()
    if normalized in {"off", "debug", "always"}:
        return normalized
    return "debug"


def _normalize_repl_import_policy(value: str | None) -> str:
    normalized = (value or "permissive").strip().lower()
    if normalized in {"minimal", "broad", "permissive"}:
        return normalized
    return "permissive"


def _normalize_repl_preload_fail_mode(value: str | None) -> str:
    normalized = (value or "warn_continue").strip().lower()
    if normalized in {"warn_continue", "fail_fast"}:
        return normalized
    return "warn_continue"


def _normalize_repl_preload_profile(value: str | None) -> str:
    normalized = (value or "bio_data_full").strip().lower()
    if normalized in {"bio_data_full", "data_first", "minimal_core"}:
        return normalized
    return "bio_data_full"


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
    enable_paperqa_tools = _env_bool("ENABLE_PAPERQA_TOOLS", default=True)
    workspace_root = Path(os.getenv("REPL_WORKSPACE_ROOT", str(backend_root.parent))).expanduser().resolve()
    allowed_prefixes = _env_csv(
        "REPL_ALLOWED_COMMAND_PREFIXES",
        "pwd,ls,cat,head,tail,rg,grep,find,git,python,python3,pip,uv,pytest,npm,node,make,bash,curl,wget,jq,awk,sed,cut,sort,uniq,wc,xargs,tar,gzip,gunzip,unzip",
    )
    blocked_prefixes = _env_csv(
        "REPL_BLOCKED_COMMAND_PREFIXES",
        "rm,shutdown,reboot,mkfs,dd,sudo,ssh,scp,nc,nmap,chmod,chown",
    )
    repl_env_snapshot_mode = _normalize_repl_env_snapshot_mode(os.getenv("REPL_ENV_SNAPSHOT_MODE", "always"))
    repl_import_policy = _normalize_repl_import_policy(os.getenv("REPL_IMPORT_POLICY", "permissive"))
    repl_import_allow_modules = _env_csv("REPL_IMPORT_ALLOW_MODULES", "")
    repl_import_deny_modules = _env_csv(
        "REPL_IMPORT_DENY_MODULES",
        "subprocess,pty,resource,ctypes,multiprocessing,signal,socket",
    )
    repl_lazy_install_allowlist = _env_csv(
        "REPL_LAZY_INSTALL_ALLOWLIST",
        "requests,httpx,aiohttp,pandas,numpy,scipy",
    )
    repl_preload_packages = _env_csv("REPL_PRELOAD_PACKAGES", "")

    paperqa_model = os.getenv("PAPERQA_MODEL", os.getenv("GEMINI_MODEL", "gemini/gemini-3-flash"))
    paperqa_sub_agent_model = os.getenv("PAPERQA_SUB_AGENT_MODEL", paperqa_model)
    paperqa_workflow_model = os.getenv("PAPERQA_WORKFLOW_MODEL", paperqa_model)
    paperqa_pipeline_model = os.getenv("PAPERQA_PIPELINE_MODEL", paperqa_workflow_model)
    paperqa_timeout_seconds = int(_env_int("PAPERQA_TIMEOUT_SECONDS", default=1500) or 1500)

    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./chat.db"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini/gemini-3-flash"),
        gemini_reasoning_effort=_normalize_reasoning_effort(os.getenv("GEMINI_REASONING_EFFORT", "medium")),
        gemini_include_thoughts=_env_bool("GEMINI_INCLUDE_THOUGHTS", default=True),
        gemini_thinking_budget=_env_int("GEMINI_THINKING_BUDGET"),
        gemini_replay_signature_mode=_normalize_replay_signature_mode(
            os.getenv("GEMINI_REPLAY_SIGNATURE_MODE", "strict")
        ),
        mock_llm=_env_bool("MOCK_LLM", default=False),
        artifacts_root=artifacts_root,
        source_cache_root=source_cache_root,
        tool_http_timeout_seconds=_env_int("TOOL_HTTP_TIMEOUT_SECONDS", default=20),
        tool_http_max_retries=_env_int("TOOL_HTTP_MAX_RETRIES", default=2),
        tool_http_user_agent=os.getenv("TOOL_HTTP_USER_AGENT", "hackathon-agent-core/0.1"),
        openalex_api_key=os.getenv("OPENALEX_API_KEY"),
        openalex_mailto=os.getenv("OPENALEX_MAILTO", "ahmet@deltawave.fr"),
        pubmed_api_key=os.getenv("PUBMED_API_KEY"),
        semanticscholar_api_key=os.getenv("SEMANTICSCHOLAR_API_KEY"),
        epistemonikos_api_key=os.getenv("EPISTEMONIKOS_API_KEY"),
        paperqa_model=paperqa_model,
        paperqa_sub_agent_model=paperqa_sub_agent_model,
        paperqa_workflow_model=paperqa_workflow_model,
        paperqa_pipeline_model=paperqa_pipeline_model,
        paperqa_timeout_seconds=paperqa_timeout_seconds,
        enable_normalization_tools=_env_bool("ENABLE_NORMALIZATION_TOOLS", default=True),
        enable_literature_tools=enable_literature_tools,
        enable_pubmed_tools=enable_pubmed_tools,
        enable_openalex_tools=enable_openalex_tools,
        enable_paperqa_tools=enable_paperqa_tools,
        enable_trial_tools=_env_bool("ENABLE_TRIAL_TOOLS", default=True),
        enable_safety_tools=_env_bool("ENABLE_SAFETY_TOOLS", default=True),
        enable_longevity_tools=_env_bool("ENABLE_LONGEVITY_TOOLS", default=True),
        enable_optional_source_tools=_env_bool("ENABLE_OPTIONAL_SOURCE_TOOLS", default=True),
        enable_builtin_demo_tools=_env_bool("ENABLE_BUILTIN_DEMO_TOOLS", default=False),
        agent_execution_mode=_normalize_agent_execution_mode(os.getenv("AGENT_EXECUTION_MODE", "repl_only")),
        repl_workspace_root=workspace_root,
        repl_max_wall_time_seconds=int(_env_int("REPL_MAX_WALL_TIME_SECONDS", default=1500) or 1500),
        repl_max_stdout_bytes=int(_env_int("REPL_MAX_STDOUT_BYTES", default=65536) or 65536),
        repl_max_tool_calls_per_exec=int(_env_int("REPL_MAX_TOOL_CALLS_PER_EXEC", default=200) or 200),
        repl_session_ttl_seconds=int(_env_int("REPL_SESSION_TTL_SECONDS", default=86_400) or 86_400),
        repl_max_sessions=int(_env_int("REPL_MAX_SESSIONS", default=500) or 500),
        repl_allowed_command_prefixes=allowed_prefixes,
        repl_blocked_command_prefixes=blocked_prefixes,
        repl_env_snapshot_mode=repl_env_snapshot_mode,
        repl_env_snapshot_max_items=int(_env_int("REPL_ENV_SNAPSHOT_MAX_ITEMS", default=80) or 80),
        repl_env_snapshot_max_preview_chars=int(_env_int("REPL_ENV_SNAPSHOT_MAX_PREVIEW_CHARS", default=160) or 160),
        repl_env_snapshot_redact_keys=_env_csv(
            "REPL_ENV_SNAPSHOT_REDACT_KEYS",
            "api_key,token,secret,password,auth,cookie",
        ),
        repl_import_policy=repl_import_policy,
        repl_import_allow_modules=repl_import_allow_modules,
        repl_import_deny_modules=repl_import_deny_modules,
        repl_lazy_install_enabled=_env_bool("REPL_LAZY_INSTALL_ENABLED", default=False),
        repl_lazy_install_allowlist=repl_lazy_install_allowlist,
        repl_lazy_install_timeout_seconds=int(_env_int("REPL_LAZY_INSTALL_TIMEOUT_SECONDS", default=60) or 60),
        repl_lazy_install_index_url=os.getenv("REPL_LAZY_INSTALL_INDEX_URL"),
        repl_preload_enabled=_env_bool("REPL_PRELOAD_ENABLED", default=True),
        repl_preload_profile=_normalize_repl_preload_profile(os.getenv("REPL_PRELOAD_PROFILE", "bio_data_full")),
        repl_preload_packages=repl_preload_packages,
        repl_preload_timeout_seconds=int(_env_int("REPL_PRELOAD_TIMEOUT_SECONDS", default=180) or 180),
        repl_preload_fail_mode=_normalize_repl_preload_fail_mode(os.getenv("REPL_PRELOAD_FAIL_MODE", "warn_continue")),
    )
