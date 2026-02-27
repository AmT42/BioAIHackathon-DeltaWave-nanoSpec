from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from app.agent.types import ProviderStreamResult

ThinkingCallback = Callable[[str], None]
TextCallback = Callable[[str], None]


class ProviderClient(ABC):
    @abstractmethod
    def stream_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        on_thinking_token: ThinkingCallback,
        on_text_token: TextCallback,
    ) -> ProviderStreamResult:
        raise NotImplementedError
