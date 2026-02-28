from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.tools.context import ToolContext
from app.agent.tools.errors import ToolExecutionError
from app.agent.tools.sources import paperqa_literature as paperqa_mod
from app.agent.tools.sources.paperqa_literature import build_paperqa_literature_tools
from app.config import get_settings


class _FakePaperQASettings:
    def __init__(self) -> None:
        self.llm = ""
        self.summary_llm = ""
        self.llm_config = None
        self.summary_llm_config = None
        self.verbosity = 0
        self.embedding = ""
        self.agent = SimpleNamespace(
            agent_llm="",
            agent_llm_config=None,
            external_search_provider=None,
            return_paper_metadata=False,
            search_count=0,
            external_search_max_results=0,
            external_pdf_max_downloads=None,
            html_ingest_enabled=True,
            jats_ingest_enabled=True,
            respect_robots_txt=True,
            allow_bronze=False,
            ignore_license_filter=False,
            headless_pdf_enabled=False,
            headless=False,
            per_work_resolution_budget_s=0,
            per_link_timeout_s=0,
            timeout=0,
            run_id=None,
            run_stats_dir="",
            fulltext_archive_dir="",
            index=SimpleNamespace(paper_directory="", index_directory=""),
        )
        self.parsing = SimpleNamespace(
            use_doc_details=True,
            defer_embedding=False,
            disable_doc_valid_check=False,
            enrichment_llm="",
            enrichment_llm_config=None,
            multimodal=None,
        )


class _FakeAnswer:
    def __init__(self) -> None:
        self.session = SimpleNamespace(
            answer="Rapamycin shows preclinical lifespan extension (PMID:12345678).",
            contexts=[
                {
                    "id": "pqac-abc12345",
                    "score": 9,
                    "context": "Mouse lifespan improved with rapamycin treatment.",
                    "text": {
                        "name": "Harrison2009",
                        "doc": {
                            "docname": "Harrison2009",
                            "title": "Rapamycin fed late in life extends lifespan in genetically heterogeneous mice",
                            "year": 2009,
                            "doi": "10.1038/nature08221",
                            "citation": "Harrison et al. (2009) PMID: 12345678",
                        },
                    },
                }
            ],
        )
        self.status = "success"
        self.duration = 1.23
        self.stats = {"sources": "1"}

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "session": {
                "answer": self.session.answer,
                "contexts": self.session.contexts,
            },
            "status": self.status,
            "duration": self.duration,
            "stats": self.stats,
        }


def _settings(tmp_path: Path):
    return replace(
        get_settings(),
        mock_llm=True,
        openalex_api_key="oa-test-key",
        artifacts_root=tmp_path / "artifacts",
        source_cache_root=tmp_path / "cache" / "sources",
        paperqa_timeout_seconds=90,
    )


def _tool(specs, name: str):
    return next(spec for spec in specs if spec.name == name)


def test_search_pubmed_agent_returns_contract_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    tools = build_paperqa_literature_tools(settings)
    handler = _tool(tools, "search_pubmed_agent").handler

    call_state: dict[str, str] = {}

    def _fake_ask(*, query: str, settings: object):
        call_state["query"] = query
        return _FakeAnswer()

    monkeypatch.setattr(
        "app.agent.tools.sources.paperqa_literature._import_paperqa",
        lambda: (_FakePaperQASettings, _fake_ask, None),
    )
    monkeypatch.setattr(
        "app.agent.tools.sources.paperqa_literature._run_with_timeout",
        lambda fn, timeout_seconds: fn(),
    )

    ctx = ToolContext(
        thread_id="thread-1",
        run_id="run-1",
        tool_use_id="call-1",
        artifact_root=tmp_path / "artifacts",
        source_cache_root=tmp_path / "cache" / "sources",
    )
    out = handler({"query": "rapamycin aging", "min_year": 2010, "max_year": 2024}, ctx)

    assert "2010" in call_state["query"]
    assert "2024" in call_state["query"]
    assert out["source_meta"]["source"] == "paperqa"
    assert out["result_kind"] == "document"
    assert out["data"]["paper_count"] == 1
    assert out["data"]["papers"][0]["doi"] == "10.1038/nature08221"
    assert any(item == "10.1038/nature08221" for item in out["ids"])
    assert any(item == "PMID:12345678" for item in out["ids"])


def test_search_pubmed_agent_surfaces_dependency_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    tools = build_paperqa_literature_tools(settings)
    handler = _tool(tools, "search_pubmed_agent").handler

    def _raise_import() -> tuple[type[object], object, None]:
        raise ToolExecutionError(code="DEPENDENCY_MISSING", message="paperqa missing")

    monkeypatch.setattr("app.agent.tools.sources.paperqa_literature._import_paperqa", _raise_import)

    with pytest.raises(ToolExecutionError) as exc:
        handler({"query": "rapamycin aging"}, None)

    assert exc.value.code == "DEPENDENCY_MISSING"


def test_build_settings_disables_multimodal_media_parsing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = paperqa_mod._runtime_paths(settings, None)

    class _FakeMultimodalEnum:
        OFF = "off"
        ON_WITHOUT_ENRICHMENT = "on_without_enrichment"

    built = paperqa_mod._build_paperqa_settings(
        app_settings=settings,
        payload={},
        runtime=runtime,
        paperqa_settings_cls=_FakePaperQASettings,
        multimodal_options=_FakeMultimodalEnum,
        mode="balanced",
    )

    assert built.parsing.multimodal == _FakeMultimodalEnum.OFF


def test_quiet_paperqa_loggers_temporarily_suppresses_progress_logs() -> None:
    logger = logging.getLogger("paperqa.agents.tools")
    logger.setLevel(logging.INFO)
    logger.propagate = True

    with paperqa_mod._quiet_paperqa_loggers():
        muted = logging.getLogger("paperqa.agents.tools")
        assert muted.level > logging.CRITICAL
        assert muted.propagate is False

    restored = logging.getLogger("paperqa.agents.tools")
    assert restored.level == logging.INFO
    assert restored.propagate is True
