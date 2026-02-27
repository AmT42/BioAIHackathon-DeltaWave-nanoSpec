from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageProviderFormat(str, enum.Enum):
    CLAUDE_INTERLEAVED = "claude_interleaved"
    GEMINI_INTERLEAVED = "gemini_interleaved"


class ConversationEventRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class ConversationEventKind(str, enum.Enum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTROL = "control"


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="thread", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(String(50), ForeignKey("threads.id"), nullable=False, index=True)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    provider_format: Mapped[MessageProviderFormat | None] = mapped_column(Enum(MessageProviderFormat), nullable=True)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    thread: Mapped[Thread] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_message_thread_created", "thread_id", "created_at"),
    )


class ConversationEvent(Base):
    __tablename__ = "conversation_events"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id: Mapped[str] = mapped_column(String(50), ForeignKey("threads.id"), nullable=False, index=True)
    message_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("messages.id"), nullable=True, index=True)
    role: Mapped[ConversationEventRole] = mapped_column(Enum(ConversationEventRole), nullable=False)
    kind: Mapped[ConversationEventKind] = mapped_column(Enum(ConversationEventKind), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    visible_to_model: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    message: Mapped[Message | None] = relationship()

    __table_args__ = (
        UniqueConstraint("thread_id", "position", name="uq_conversation_events_position"),
        Index("idx_conversation_events_thread_order", "thread_id", "position"),
    )


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(50), ForeignKey("threads.id"), nullable=False, index=True)
    assistant_event_id: Mapped[str] = mapped_column(String(50), ForeignKey("conversation_events.id"), nullable=False)
    result_event_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("conversation_events.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
