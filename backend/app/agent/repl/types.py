from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Sequence


class IdListHandle(Sequence[str]):
    """Lazy-ish ID collection wrapper with compact repr for REPL usage."""

    def __init__(self, values: list[str]) -> None:
        self._values = [str(item) for item in values if str(item).strip()]

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int) -> str:
        return self._values[index]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def to_list(self) -> list[str]:
        return list(self._values)

    def __repr__(self) -> str:
        preview = ", ".join(self._values[:5])
        suffix = "" if len(self._values) <= 5 else f", ... (+{len(self._values) - 5} more)"
        return f"IdListHandle(count={len(self._values)}, ids=[{preview}{suffix}])"


class ToolResultHandle:
    """Programmatic view over normalized tool output."""

    def __init__(self, *, tool_name: str, payload: dict[str, Any], raw_result: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self._payload = payload
        self._raw_result = raw_result
        self.ids = IdListHandle(list(payload.get("ids") or []))

    @property
    def summary(self) -> str:
        return str(self._payload.get("summary") or "")

    @property
    def data(self) -> Any:
        return self._payload.get("data")

    @property
    def citations(self) -> list[dict[str, Any]]:
        return list(self._payload.get("citations") or [])

    @property
    def warnings(self) -> list[str]:
        return [str(item) for item in (self._payload.get("warnings") or [])]

    @property
    def artifacts(self) -> list[dict[str, Any]]:
        return list(self._payload.get("artifacts") or [])

    @property
    def source_meta(self) -> dict[str, Any]:
        source_meta = self._payload.get("source_meta")
        return source_meta if isinstance(source_meta, dict) else {}

    @property
    def result_kind(self) -> str:
        return str(self._payload.get("result_kind") or "record_list")

    def preview(self, *, max_items: int = 5, max_chars: int = 800) -> str:
        data = self._payload.get("data")
        ids_preview = self.ids.to_list()[:max_items]
        text = (
            f"{self.tool_name}: {self.summary}\n"
            f"result_kind={self.result_kind}, ids_count={len(self.ids)}, ids_preview={ids_preview}\n"
            f"data_preview={str(data)[:max_chars]}"
        )
        if len(text) > max_chars:
            return text[: max_chars - 3] + "..."
        return text

    def raw(self) -> dict[str, Any]:
        return dict(self._payload)

    def result(self) -> dict[str, Any]:
        return dict(self._raw_result)

    def __repr__(self) -> str:
        return self.preview(max_items=3, max_chars=240)


@dataclass
class ShellResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    truncated: bool = False

    def preview(self, *, max_chars: int = 800) -> str:
        rendered = (
            f"command={self.command!r} returncode={self.returncode}\n"
            f"stdout:\n{self.stdout}\n"
            f"stderr:\n{self.stderr}"
        )
        if len(rendered) <= max_chars:
            return rendered
        return rendered[: max_chars - 3] + "..."

    def __repr__(self) -> str:
        return self.preview(max_chars=240)


@dataclass
class ReplExecutionResult:
    execution_id: str
    stdout: str
    stderr: str
    nested_tool_calls: int
    truncated: bool
    had_visible_output: bool
    error: str | None = None

    def to_tool_output(self) -> dict[str, Any]:
        summary = "REPL execution completed."
        if self.error:
            summary = f"REPL execution failed: {self.error}"
        elif not self.had_visible_output:
            summary = "REPL execution completed with no visible output; use print(...) to expose results."

        return {
            "status": "error" if self.error else "success",
            "output": {
                "summary": summary,
                "stdout": self.stdout,
                "stderr": self.stderr,
                "execution_id": self.execution_id,
                "nested_tool_calls": self.nested_tool_calls,
                "truncated": self.truncated,
                "had_visible_output": self.had_visible_output,
            },
            "error": {"message": self.error} if self.error else None,
        }
