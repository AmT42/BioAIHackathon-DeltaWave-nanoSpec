from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent.subagent_runner import SubagentRunner
from app.agent.tools.builtin import create_builtin_registry
from app.agent.types import ProviderStreamResult, ToolCall
from app.config import get_settings


class _NoToolProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def stream_turn(self, **kwargs: dict) -> ProviderStreamResult:
        self.calls.append(kwargs)
        return ProviderStreamResult(
            text="sub-answer",
            thinking="plan",
            tool_calls=[],
            provider_state={"provider": "gemini", "mock": True},
        )


class _ReplThenTextProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream_turn(self, **_kwargs: dict) -> ProviderStreamResult:
        self.calls += 1
        if self.calls == 1:
            return ProviderStreamResult(
                text="",
                thinking="inspect env",
                tool_calls=[
                    ToolCall(
                        id="sub_repl_1",
                        name="repl_exec",
                        input={"code": "print('llm_query' in globals())\nprint(ids[0])"},
                    )
                ],
                provider_state={"provider": "gemini", "mock": True},
            )
        return ProviderStreamResult(
            text="done",
            thinking="complete",
            tool_calls=[],
            provider_state={"provider": "gemini", "mock": True},
        )


class _EchoTaskProvider:
    def stream_turn(self, **kwargs: dict) -> ProviderStreamResult:
        messages = kwargs.get("messages")
        task = ""
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    task = str(msg.get("content") or "")
                    break
        return ProviderStreamResult(
            text=f"echo:{task}",
            thinking="ok",
            tool_calls=[],
            provider_state={"provider": "gemini", "mock": True},
        )


def _settings(tmp_path: Path):
    return replace(
        get_settings(),
        mock_llm=True,
        artifacts_root=tmp_path / "artifacts",
        source_cache_root=tmp_path / "cache",
        repl_subagent_enabled=True,
        repl_subagent_stdout_line_soft_limit=20_000,
        repl_subagent_max_iterations=4,
        repl_subagent_max_batch_workers=2,
    )


def test_subagent_run_query_writes_trace(tmp_path: Path) -> None:
    provider = _NoToolProvider()
    runner = SubagentRunner(
        settings=_settings(tmp_path),
        tools=create_builtin_registry(),
        provider=provider,
    )

    result = runner.run_query(
        thread_id="thread-a",
        run_id="run-a",
        request_index=1,
        user_msg_index=1,
        parent_tool_use_id="repl-main-1",
        task="summarize the strongest evidence",
        custom_instruction="Be strict about confidence.",
    )

    assert result["ok"] is True
    assert result["text"] == "sub-answer"
    assert result["tool_calls"] == 0
    assert result["iterations"] == 1
    assert isinstance(result["trace_path"], str) and result["trace_path"]
    assert provider.calls
    assert len(provider.calls[0].get("tools") or []) == 2

    trace_path = Path(str(result["trace_path"]))
    assert trace_path.exists()
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["query"]["task"] == "summarize the strongest evidence"
    assert payload["query"]["custom_instruction"] == "Be strict about confidence."
    assert payload["query"]["attached_tools"]


def test_subagent_repl_env_seed_and_no_recursive_llm_query(tmp_path: Path) -> None:
    runner = SubagentRunner(
        settings=_settings(tmp_path),
        tools=create_builtin_registry(),
        provider=_ReplThenTextProvider(),
    )

    result = runner.run_query(
        thread_id="thread-b",
        run_id="run-b",
        request_index=1,
        user_msg_index=1,
        parent_tool_use_id="repl-main-2",
        task="inspect env",
        env={"ids": [123, 456]},
        allow_repl=True,
        allow_bash=False,
    )

    assert result["ok"] is True
    assert result["text"] == "done"
    assert result["tool_calls"] == 1

    trace_payload = json.loads(Path(str(result["trace_path"])).read_text(encoding="utf-8"))
    tool_results = trace_payload["steps"][0]["tool_results"]
    assert len(tool_results) == 1
    stdout = str(tool_results[0]["result"]["output"]["stdout"])
    assert "False" in stdout
    assert "123" in stdout


def test_subagent_batch_returns_per_item_errors(tmp_path: Path) -> None:
    runner = SubagentRunner(
        settings=_settings(tmp_path),
        tools=create_builtin_registry(),
        provider=_EchoTaskProvider(),
    )

    results = runner.llm_query_batch(
        thread_id="thread-c",
        run_id="run-c",
        request_index=2,
        user_msg_index=2,
        parent_tool_use_id="repl-main-3",
        tasks=[
            "task-one",
            {"task": ""},
            {"task": "task-two"},
        ],
        max_workers=2,
    )

    assert len(results) == 3
    assert results[0]["ok"] is True
    assert results[0]["text"] == "echo:task-one"
    assert results[1]["ok"] is False
    assert "non-empty task" in str(results[1]["error"])
    assert results[2]["ok"] is True
    assert results[2]["text"] == "echo:task-two"
    assert isinstance(results[0]["trace_path"], str)


def test_subagent_rejects_unknown_allowed_tool(tmp_path: Path) -> None:
    runner = SubagentRunner(
        settings=_settings(tmp_path),
        tools=create_builtin_registry(),
        provider=_NoToolProvider(),
    )

    with pytest.raises(ValueError):
        runner.run_query(
            thread_id="thread-d",
            run_id="run-d",
            request_index=1,
            user_msg_index=1,
            parent_tool_use_id="repl-main-4",
            task="test",
            allowed_tools=["missing_tool"],
        )
