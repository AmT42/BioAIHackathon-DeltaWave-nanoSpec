from __future__ import annotations

from pathlib import Path

from app.agent.tools.builtin import builtin_tool_specs
from app.agent.tools.context import ToolContext
from app.agent.tools.registry import ToolRegistry


def test_registry_writes_artifacts_by_thread_and_lineage(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    source_cache_root = artifact_root / "cache" / "sources"
    registry = ToolRegistry(
        builtin_tool_specs(),
        artifact_root=artifact_root,
        source_cache_root=source_cache_root,
    )

    ctx = ToolContext(
        thread_id="thread-abc",
        run_id="run-xyz",
        request_index=1,
        user_msg_index=1,
        tool_use_id="call-123",
    )

    result = registry.execute("calc", {"expression": "10/2"}, ctx=ctx)
    assert result["status"] == "success"

    base = artifact_root / "threads" / "thread-abc" / "lineages" / "run-xyz" / "tools" / "calc" / "call-123"
    assert (base / "request.json").exists()
    assert (base / "response.json").exists()
    assert (base / "manifest.json").exists()
