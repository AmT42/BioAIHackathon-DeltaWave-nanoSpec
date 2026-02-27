from app.agent.tools.builtin import create_builtin_registry


def test_calc_tool_success() -> None:
    registry = create_builtin_registry()
    result = registry.execute("calc", {"expression": "(2+3)*4"})
    assert result["status"] == "success"
    assert result["output"]["value"] == 20.0


def test_unknown_tool_returns_error() -> None:
    registry = create_builtin_registry()
    result = registry.execute("missing_tool", {})
    assert result["status"] == "error"
    assert "Unknown tool" in result["error"]["message"]
