from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class ToolContext:
    thread_id: str | None = None
    run_id: str | None = None
    request_index: int | None = None
    user_msg_index: int | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    artifact_root: Path | None = None
    source_cache_root: Path | None = None

    def with_tool(self, *, tool_name: str, artifact_root: Path | None = None, source_cache_root: Path | None = None) -> "ToolContext":
        return replace(
            self,
            tool_name=tool_name,
            artifact_root=artifact_root or self.artifact_root,
            source_cache_root=source_cache_root or self.source_cache_root,
        )

    def lineage(self) -> dict[str, str | int | None]:
        return {
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "tool_use_id": self.tool_use_id,
            "request_index": self.request_index,
            "user_msg_index": self.user_msg_index,
            "tool_name": self.tool_name,
        }
