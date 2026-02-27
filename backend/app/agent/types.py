from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    provider_specific_fields: dict[str, Any] | None = None
    extra_content: dict[str, Any] | None = None


@dataclass
class ProviderStreamResult:
    text: str
    thinking: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_state: dict[str, Any] = field(default_factory=dict)
