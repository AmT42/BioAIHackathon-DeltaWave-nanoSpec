from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence


class RecordRow(dict):
    """Dict row with attribute-style read access for REPL ergonomics."""

    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        raise AttributeError(name)


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

    def head(self, n: int = 5) -> list[str]:
        count = max(0, int(n))
        return self._values[:count]

    def unique(self) -> "IdListHandle":
        seen: set[str] = set()
        deduped: list[str] = []
        for value in self._values:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return IdListHandle(deduped)

    def union(self, other: Sequence[str]) -> "IdListHandle":
        merged = list(self._values) + [str(item) for item in other]
        return IdListHandle(merged).unique()

    def __add__(self, other: Sequence[str]) -> "IdListHandle":
        return self.union(other)

    def __or__(self, other: Sequence[str]) -> "IdListHandle":
        return self.union(other)

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

    def _as_records(self, key: str) -> list[dict[str, Any]]:
        data = self._payload.get("data")
        if not isinstance(data, dict):
            return []
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [RecordRow(item) for item in value if isinstance(item, dict)]

    @property
    def records(self) -> list[dict[str, Any]]:
        direct = self._as_records("records")
        if direct:
            return direct
        candidates = self._as_records("candidates")
        if candidates:
            return candidates
        return self._as_records("hits")

    @property
    def candidates(self) -> list[dict[str, Any]]:
        direct = self._as_records("candidates")
        if direct:
            return direct
        return self.records

    @property
    def items(self) -> list[dict[str, Any]]:
        records = self.records
        if records:
            return records
        return self._as_records("items")

    @property
    def studies(self) -> list[dict[str, Any]]:
        records = self.records
        if records:
            return records
        return self._as_records("studies")

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

    def keys(self) -> list[str]:
        data = self._payload.get("data")
        if isinstance(data, dict):
            return sorted(str(key) for key in data.keys())
        return []

    def get(self, key: str, default: Any = None) -> Any:
        data = self._payload.get("data")
        if isinstance(data, dict):
            return data.get(key, default)
        return default

    def __getitem__(self, key: Any) -> Any:
        data = self._payload.get("data")
        if isinstance(data, dict):
            return data[key]
        raise TypeError("ToolResultHandle data is not a mapping")

    def __getattr__(self, name: str) -> Any:
        data = self._payload.get("data")
        if isinstance(data, dict) and name in data:
            return data[name]
        raise AttributeError(name)

    def shape(self) -> dict[str, Any]:
        data = self._payload.get("data")
        if isinstance(data, dict):
            return {
                "data_type": "object",
                "keys": self.keys(),
                "records_count": len(self.records),
                "items_count": len(self.items),
                "studies_count": len(self.studies),
                "ids_count": len(self.ids),
            }
        if isinstance(data, list):
            return {"data_type": "list", "length": len(data), "ids_count": len(self.ids)}
        return {"data_type": type(data).__name__, "ids_count": len(self.ids)}

    def __iter__(self) -> Iterator[dict[str, Any]]:
        rows = self.records or self.items or self.studies
        return iter(rows)

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
    env_snapshot: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    stdout_capping: dict[str, Any] | None = None

    def to_tool_output(self) -> dict[str, Any]:
        summary = "REPL execution completed."
        if self.error:
            summary = f"REPL execution failed: {self.error}"
        elif not self.had_visible_output:
            summary = "REPL execution completed with no visible output; use print(...) to expose results."
        if self.artifacts:
            summary = f"{summary} Capped long stdout lines saved as {len(self.artifacts)} artifact(s)."

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
                "env": self.env_snapshot,
                "artifacts": self.artifacts,
                "stdout_capping": self.stdout_capping,
            },
            "error": {"message": self.error} if self.error else None,
        }
