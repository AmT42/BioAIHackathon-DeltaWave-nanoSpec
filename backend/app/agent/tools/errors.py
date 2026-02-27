from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolExecutionError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_error_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def unknown_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "code": "UPSTREAM_ERROR",
        "message": str(exc),
        "retryable": False,
        "details": {},
    }
