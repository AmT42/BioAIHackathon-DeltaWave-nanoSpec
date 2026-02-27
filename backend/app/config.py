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
    mock_llm: bool


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    _load_env_file()
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./chat.db"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini/gemini-3-flash"),
        mock_llm=_env_bool("MOCK_LLM", default=False),
    )
