from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.repl.bootstrap import ensure_repl_preload
from app.api.chat import router as chat_router
from app.config import get_settings
from app.persistence.db import init_db

app = FastAPI(title="Hackathon Agent Core API", version="0.1.0")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    settings = get_settings()
    preload_report = await asyncio.to_thread(ensure_repl_preload, settings)
    app.state.repl_preload_report = preload_report
    status = str(preload_report.get("status") or "unknown")
    if status == "ready":
        logger.info(
            "REPL preload ready profile=%s installed=%s missing_after=%s duration_s=%s",
            preload_report.get("profile"),
            len(preload_report.get("installed") or []),
            len(preload_report.get("missing_after") or []),
            preload_report.get("duration_s"),
        )
    else:
        logger.warning(
            "REPL preload status=%s profile=%s missing_after=%s error=%s",
            status,
            preload_report.get("profile"),
            preload_report.get("missing_after"),
            preload_report.get("install_error"),
        )
    await init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(chat_router)
