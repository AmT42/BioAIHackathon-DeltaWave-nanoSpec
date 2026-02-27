from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.core import AgentCore, normalize_provider
from app.agent.tools.science_registry import create_science_registry
from app.config import get_settings
from app.persistence.db import SessionLocal, get_session
from app.persistence.service import ChatStore

router = APIRouter(tags=["chat"])


class CreateThreadResponse(BaseModel):
    thread_id: str


class EventResponse(BaseModel):
    id: str
    thread_id: str
    message_id: str | None
    role: str
    kind: str
    position: int
    content: dict[str, Any]
    tool_call_id: str | None
    visible_to_model: bool
    message_provider_format: str | None = None
    message_content_blocks: list[dict[str, Any]] | None = None
    created_at: str


class ThreadMessageResponse(BaseModel):
    id: str
    thread_id: str
    role: str
    content: str | None
    content_blocks: list[dict[str, Any]] | None
    provider_format: str | None
    metadata: dict[str, Any]
    created_at: str


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = None
    provider: str = "gemini"


class ChatSendResponse(BaseModel):
    thread_id: str
    run_id: str
    content: str
    provider: str
    events: list[dict[str, Any]]


@router.post("/api/threads", response_model=CreateThreadResponse)
async def create_thread(session: AsyncSession = Depends(get_session)) -> CreateThreadResponse:
    store = ChatStore(session)
    thread = await store.create_thread()
    return CreateThreadResponse(thread_id=thread.id)


@router.get("/api/threads/{thread_id}/events", response_model=list[EventResponse])
async def get_thread_events(thread_id: str, session: AsyncSession = Depends(get_session)) -> list[EventResponse]:
    store = ChatStore(session)
    events = await store.get_canonical_events(thread_id)
    out: list[EventResponse] = []
    for event in events:
        out.append(
            EventResponse(
                id=event.event_id,
                thread_id=event.thread_id,
                message_id=event.message_id,
                role=event.role.value,
                kind=event.kind.value,
                position=event.position,
                content=event.content or {},
                tool_call_id=event.tool_call_id,
                visible_to_model=event.visible_to_model,
                message_provider_format=event.message_provider_format.value if event.message_provider_format else None,
                message_content_blocks=event.message_content_blocks,
                created_at=event.created_at.isoformat(),
            )
        )
    return out


@router.get("/api/threads/{thread_id}/messages", response_model=list[ThreadMessageResponse])
async def get_thread_messages(thread_id: str, session: AsyncSession = Depends(get_session)) -> list[ThreadMessageResponse]:
    store = ChatStore(session)
    thread = await store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    messages = await store.get_thread_messages(thread_id, skip=0, limit=1000)
    return [
        ThreadMessageResponse(
            id=msg.id,
            thread_id=msg.thread_id,
            role=msg.role.value if hasattr(msg.role, "value") else str(msg.role),
            content=msg.content,
            content_blocks=msg.content_blocks,
            provider_format=msg.provider_format.value if msg.provider_format else None,
            metadata=msg.message_metadata or {},
            created_at=msg.created_at.isoformat() if msg.created_at else "",
        )
        for msg in messages
    ]


@router.post("/api/chat/send", response_model=ChatSendResponse)
async def chat_send(payload: ChatSendRequest, session: AsyncSession = Depends(get_session)) -> ChatSendResponse:
    settings = get_settings()
    store = ChatStore(session)
    thread = await store.ensure_thread(payload.thread_id)

    core = AgentCore(settings=settings, store=store, tools=create_science_registry(settings))
    try:
        provider = normalize_provider(payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    collected_events: list[dict[str, Any]] = []

    async def collect(event: dict[str, Any]) -> None:
        collected_events.append(event)

    result = await core.run_turn_stream(
        thread_id=thread.id,
        provider=provider,
        user_message=payload.message,
        emit=collect,
        run_id=uuid.uuid4().hex,
    )

    return ChatSendResponse(
        thread_id=result["thread_id"],
        run_id=result["run_id"],
        content=result["content"],
        provider=result["provider"],
        events=collected_events,
    )


@router.websocket("/ws/chat")
async def ws_chat(
    websocket: WebSocket,
    thread_id: str | None = Query(default=None),
    provider: str = Query(default="gemini"),
) -> None:
    await websocket.accept()

    settings = get_settings()
    try:
        provider_name = normalize_provider(provider)
    except ValueError as exc:
        await websocket.send_json({"type": "main_agent_error", "error": str(exc)})
        await websocket.close(code=1008)
        return

    try:
        async with SessionLocal() as session:
            store = ChatStore(session)
            thread = await store.ensure_thread(thread_id)
            resolved_thread_id = thread.id

        while True:
            incoming = await websocket.receive_json()
            msg_type = str(incoming.get("type", "")).strip().lower()

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type not in {"main_agent_chat", "user_message"}:
                await websocket.send_json(
                    {
                        "type": "main_agent_error",
                        "thread_id": resolved_thread_id,
                        "error": "Unsupported message type",
                    }
                )
                continue

            content = str(
                incoming.get("content")
                or incoming.get("message")
                or incoming.get("prompt")
                or ""
            ).strip()
            if not content:
                await websocket.send_json(
                    {
                        "type": "main_agent_error",
                        "thread_id": resolved_thread_id,
                        "error": "Empty content",
                    }
                )
                continue

            async with SessionLocal() as run_session:
                run_store = ChatStore(run_session)
                core = AgentCore(settings=settings, store=run_store, tools=create_science_registry(settings))

                async def emit(event: dict[str, Any]) -> None:
                    await websocket.send_json(event)

                try:
                    await core.run_turn_stream(
                        thread_id=resolved_thread_id,
                        provider=provider_name,
                        user_message=content,
                        emit=emit,
                        run_id=uuid.uuid4().hex,
                    )
                except Exception as exc:
                    await websocket.send_json(
                        {
                            "type": "main_agent_error",
                            "thread_id": resolved_thread_id,
                            "run_id": "error",
                            "error": str(exc),
                        }
                    )
    except WebSocketDisconnect:
        return
