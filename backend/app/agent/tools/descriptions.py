from __future__ import annotations


def _join_items(values: list[str]) -> str:
    clean = [str(item).strip() for item in values if str(item).strip()]
    return "; ".join(clean) if clean else "none"


def render_tool_description(
    *,
    purpose: str,
    when: list[str],
    avoid: list[str],
    critical_args: list[str],
    returns: str,
    fails_if: list[str],
) -> str:
    return "\n".join(
        [
            purpose.strip(),
            f"WHEN: {_join_items(when)}",
            f"AVOID: {_join_items(avoid)}",
            f"CRITICAL_ARGS: {_join_items(critical_args)}",
            f"RETURNS: {returns.strip()}",
            f"FAILS_IF: {_join_items(fails_if)}",
        ]
    )
