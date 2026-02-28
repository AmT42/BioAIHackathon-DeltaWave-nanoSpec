from __future__ import annotations

from reports.tool_real_validation.run_tool_validation import summarize_strict_results


def _row(tool: str, status: str, *, code: str | None = None, enabled: bool = True) -> dict:
    result = {"status": status}
    if status == "error":
        result["error"] = {
            "code": code or "UPSTREAM_ERROR",
            "message": "boom",
            "retryable": True,
            "details": {},
        }
    return {
        "tool": tool,
        "enabled": enabled,
        "result": result,
    }


def test_optional_rate_limit_does_not_fail_strict() -> None:
    summary = summarize_strict_results(
        [
            _row("pubmed_fetch", "success"),
            _row("semanticscholar_search_papers", "error", code="RATE_LIMIT"),
        ]
    )

    assert summary["core_passed"] is True
    assert summary["strict_passed"] is True
    assert len(summary["optional_failures"]) == 1


def test_core_failure_fails_strict() -> None:
    summary = summarize_strict_results(
        [
            _row("pubmed_fetch", "error", code="UPSTREAM_ERROR"),
            _row("semanticscholar_search_papers", "error", code="UNCONFIGURED"),
        ]
    )

    assert summary["core_passed"] is False
    assert summary["strict_passed"] is False
    assert "pubmed_fetch" in summary["strict_failure_tools"]
