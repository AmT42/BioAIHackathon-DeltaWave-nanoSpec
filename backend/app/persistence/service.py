from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import (
    ConversationEvent,
    ConversationEventKind,
    ConversationEventRole,
    Message,
    MessageProviderFormat,
    MessageRole,
    Thread,
    ToolInvocation,
)
from app.run_logging import write_db_thread_snapshot


@dataclass(frozen=True)
class CanonicalEventView:
    event_id: str
    thread_id: str
    role: ConversationEventRole
    kind: ConversationEventKind
    position: int
    created_at: datetime
    content: dict[str, Any]
    tool_call_id: str | None
    visible_to_model: bool
    message_id: str | None
    message_provider_format: MessageProviderFormat | None
    message_content_blocks: list[dict[str, Any]] | None


class ChatStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_thread(self) -> Thread:
        thread = Thread()
        self.session.add(thread)
        await self.session.commit()
        await self.session.refresh(thread)
        await self._log_thread_snapshot(thread.id)
        return thread

    async def get_thread(self, thread_id: str) -> Thread | None:
        result = await self.session.execute(select(Thread).where(Thread.id == thread_id))
        return result.scalar_one_or_none()

    async def ensure_thread(self, thread_id: str | None) -> Thread:
        if thread_id:
            found = await self.get_thread(thread_id)
            if found:
                return found
        return await self.create_thread()

    async def _next_position(self, thread_id: str) -> int:
        result = await self.session.execute(
            select(func.max(ConversationEvent.position)).where(ConversationEvent.thread_id == thread_id)
        )
        max_position = result.scalar()
        return int(max_position or 0) + 1

    async def create_message(
        self,
        *,
        thread_id: str,
        role: MessageRole,
        content: str | None,
        provider_format: MessageProviderFormat | None = None,
        content_blocks: list[dict[str, Any]] | None = None,
        message_metadata: dict[str, Any] | None = None,
        record_text_event: bool = True,
    ) -> Message:
        message = Message(
            thread_id=thread_id,
            role=role,
            content=content,
            provider_format=provider_format,
            content_blocks=content_blocks,
            message_metadata=message_metadata or {},
        )
        self.session.add(message)
        await self.session.flush()

        if record_text_event and content is not None:
            role_map = {
                MessageRole.USER: ConversationEventRole.USER,
                MessageRole.ASSISTANT: ConversationEventRole.ASSISTANT,
                MessageRole.SYSTEM: ConversationEventRole.SYSTEM,
            }
            await self.append_event(
                thread_id=thread_id,
                role=role_map[role],
                kind=ConversationEventKind.TEXT,
                content={"type": "text", "text": content},
                message_id=message.id,
                visible_to_model=True,
            )

        await self.session.commit()
        await self.session.refresh(message)
        await self._log_thread_snapshot(thread_id)
        return message

    async def update_message(
        self,
        *,
        message_id: str,
        content: str | None = None,
        content_blocks: list[dict[str, Any]] | None = None,
        provider_format: MessageProviderFormat | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> Message | None:
        message = await self.session.get(Message, message_id)
        if not message:
            return None

        if content is not None:
            message.content = content
        if content_blocks is not None:
            message.content_blocks = content_blocks
        if provider_format is not None:
            message.provider_format = provider_format
        if message_metadata is not None:
            merged = dict(message.message_metadata or {})
            merged.update(message_metadata)
            message.message_metadata = merged

        await self.session.flush()

        if content is not None:
            result = await self.session.execute(
                select(ConversationEvent)
                .where(
                    ConversationEvent.message_id == message.id,
                    ConversationEvent.kind == ConversationEventKind.TEXT,
                )
                .order_by(desc(ConversationEvent.position))
                .limit(1)
            )
            text_event = result.scalar_one_or_none()
            if text_event:
                text_event.content = {"type": "text", "text": content}
            else:
                role_map = {
                    MessageRole.USER: ConversationEventRole.USER,
                    MessageRole.ASSISTANT: ConversationEventRole.ASSISTANT,
                    MessageRole.SYSTEM: ConversationEventRole.SYSTEM,
                }
                await self.append_event(
                    thread_id=message.thread_id,
                    role=role_map[message.role],
                    kind=ConversationEventKind.TEXT,
                    content={"type": "text", "text": content},
                    message_id=message.id,
                    visible_to_model=True,
                )

        await self.session.commit()
        await self.session.refresh(message)
        await self._log_thread_snapshot(message.thread_id)
        return message

    async def append_event(
        self,
        *,
        thread_id: str,
        role: ConversationEventRole,
        kind: ConversationEventKind,
        content: dict[str, Any],
        message_id: str | None = None,
        tool_call_id: str | None = None,
        visible_to_model: bool = True,
    ) -> ConversationEvent:
        event = ConversationEvent(
            thread_id=thread_id,
            message_id=message_id,
            role=role,
            kind=kind,
            position=await self._next_position(thread_id),
            content=content,
            tool_call_id=tool_call_id,
            visible_to_model=visible_to_model,
            created_at=datetime.utcnow(),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def record_tool_call(
        self,
        *,
        thread_id: str,
        tool_call_id: str,
        tool_name: str,
        input_payload: dict[str, Any],
        provider_specific_fields: dict[str, Any] | None = None,
        extra_content: dict[str, Any] | None = None,
        visible_to_model: bool = True,
    ) -> None:
        content: dict[str, Any] = {
            "type": "tool_call",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "input": input_payload,
        }
        if provider_specific_fields:
            content["provider_specific_fields"] = provider_specific_fields
        if extra_content:
            content["extra_content"] = extra_content

        call_event = await self.append_event(
            thread_id=thread_id,
            role=ConversationEventRole.ASSISTANT,
            kind=ConversationEventKind.TOOL_CALL,
            content=content,
            tool_call_id=tool_call_id,
            visible_to_model=visible_to_model,
        )
        invocation = ToolInvocation(
            id=tool_call_id,
            thread_id=thread_id,
            assistant_event_id=call_event.id,
            tool_name=tool_name,
            input=input_payload,
            status="pending",
        )
        self.session.add(invocation)
        await self.session.commit()
        await self._log_thread_snapshot(thread_id)

    async def record_tool_result(
        self,
        *,
        thread_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        output: dict[str, Any] | None,
        error: dict[str, Any] | None,
        visible_to_model: bool = True,
    ) -> None:
        result_event = await self.append_event(
            thread_id=thread_id,
            role=ConversationEventRole.TOOL,
            kind=ConversationEventKind.TOOL_RESULT,
            content={
                "type": "tool_result",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": status,
                "output": output,
                "error": error,
            },
            tool_call_id=tool_call_id,
            visible_to_model=visible_to_model,
        )

        invocation = await self.session.get(ToolInvocation, tool_call_id)
        if invocation:
            invocation.result_event_id = result_event.id
            invocation.status = status
            invocation.output = output
            invocation.error = error

        await self.session.commit()
        await self._log_thread_snapshot(thread_id)

    async def get_events(self, thread_id: str) -> list[ConversationEvent]:
        result = await self.session.execute(
            select(ConversationEvent)
            .where(ConversationEvent.thread_id == thread_id)
            .order_by(ConversationEvent.position)
        )
        return list(result.scalars())

    async def get_thread_messages(
        self,
        thread_id: str,
        *,
        skip: int = 0,
        limit: int = 200,
    ) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars())

    async def count_user_messages(self, thread_id: str) -> int:
        result = await self.session.execute(
            select(func.count(Message.id)).where(
                Message.thread_id == thread_id,
                Message.role == MessageRole.USER,
            )
        )
        return int(result.scalar() or 0)

    async def get_canonical_events(self, thread_id: str) -> list[CanonicalEventView]:
        events = await self.get_events(thread_id)
        if not events:
            return []

        message_ids = [event.message_id for event in events if event.message_id]
        messages_by_id: dict[str, Message] = {}
        if message_ids:
            msg_result = await self.session.execute(select(Message).where(Message.id.in_(message_ids)))
            for msg in msg_result.scalars():
                messages_by_id[msg.id] = msg

        out: list[CanonicalEventView] = []
        for event in events:
            msg = messages_by_id.get(event.message_id or "")
            out.append(
                CanonicalEventView(
                    event_id=event.id,
                    thread_id=event.thread_id,
                    role=event.role,
                    kind=event.kind,
                    position=event.position,
                    created_at=event.created_at,
                    content=event.content or {},
                    tool_call_id=event.tool_call_id,
                    visible_to_model=event.visible_to_model,
                    message_id=event.message_id,
                    message_provider_format=msg.provider_format if msg else None,
                    message_content_blocks=msg.content_blocks if msg else None,
                )
            )
        return out

    def _serialize_thread(self, thread: Thread) -> dict[str, Any]:
        return {
            "id": thread.id,
            "created_at": thread.created_at.isoformat() if thread.created_at else None,
            "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        }

    def _serialize_message(self, msg: Message) -> dict[str, Any]:
        return {
            "id": msg.id,
            "thread_id": msg.thread_id,
            "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
            "content": msg.content,
            "content_blocks": msg.content_blocks,
            "provider_format": msg.provider_format.value if msg.provider_format else None,
            "message_metadata": msg.message_metadata or {},
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }

    async def _log_thread_snapshot(self, thread_id: str) -> None:
        thread = await self.get_thread(thread_id)
        if thread is None:
            return

        messages = await self.get_thread_messages(thread_id, skip=0, limit=5000)
        snapshot = {
            "thread": self._serialize_thread(thread),
            "messages": [self._serialize_message(msg) for msg in messages],
        }
        write_db_thread_snapshot(thread_id=thread_id, snapshot=snapshot)
