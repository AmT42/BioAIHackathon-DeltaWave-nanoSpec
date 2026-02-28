from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_bootstrap_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "eve_bootstrap.py"
    module_name = "eve_bootstrap_script"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_ensure_local_env_file_localizes_symlink(tmp_path: Path) -> None:
    mod = _load_bootstrap_module()
    backend = tmp_path / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    example = backend / ".env.example"
    example.write_text("GEMINI_API_KEY=\n", encoding="utf-8")

    source = tmp_path / "shared.env"
    source.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    env_path = backend / ".env"
    env_path.symlink_to(source)

    report: dict[str, object] = {}
    resolved = mod._ensure_local_env_file(backend, report)
    assert resolved == env_path
    assert env_path.exists()
    assert not env_path.is_symlink()
    assert "GEMINI_API_KEY=abc" in env_path.read_text(encoding="utf-8")
    assert "env_localized_from_symlink" in report


def test_run_bootstrap_requires_gemini_key_when_mock_off(tmp_path: Path) -> None:
    mod = _load_bootstrap_module()
    backend = tmp_path / "backend"
    run_dir = tmp_path / "run"
    backend.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (backend / ".env.example").write_text("MOCK_LLM=false\nGEMINI_API_KEY=\n", encoding="utf-8")

    cfg = mod.BootstrapConfig(
        backend_dir=backend,
        run_dir=run_dir,
        emit_env_file=None,
        venv_dir=tmp_path / "venv",
        venv_override_ignored=None,
        verbose=False,
        skip_install=True,
    )

    with pytest.raises(mod.BootstrapError):
        mod.run_bootstrap(cfg)


def test_run_bootstrap_emits_forced_runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bootstrap_module()
    backend = tmp_path / "backend"
    run_dir = tmp_path / "run"
    emit_env = run_dir / "bootstrap.env"
    backend.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (backend / ".env.example").write_text("MOCK_LLM=false\nGEMINI_API_KEY=\n", encoding="utf-8")
    (backend / ".env").write_text(
        "MOCK_LLM=false\n"
        "GEMINI_API_KEY=test-key\n"
        "PAPERQA_TIMEOUT_SECONDS=240\n"
        "REPL_MAX_WALL_TIME_SECONDS=120\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_ensure_venv", lambda *_args, **_kwargs: Path(sys.executable))
    monkeypatch.setattr(mod, "_ensure_core_deps", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_ensure_paperqa_deps", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_check_imports", lambda *_args, **_kwargs: {"x": True})
    monkeypatch.setattr(mod, "_verify_local_vendored_paperqa", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        mod,
        "_verify_runtime_ready",
        lambda *_args, **_kwargs: {
            "ok": True,
            "has_search_pubmed_agent": True,
            "config_module": str(backend / "app" / "config.py"),
        },
    )

    cfg = mod.BootstrapConfig(
        backend_dir=backend,
        run_dir=run_dir,
        emit_env_file=emit_env,
        venv_dir=tmp_path / "venv",
        venv_override_ignored=None,
        verbose=False,
        skip_install=True,
    )
    report = mod.run_bootstrap(cfg)
    assert report["status"] == "ready"
    emitted = emit_env.read_text(encoding="utf-8")
    assert "ENABLE_PAPERQA_TOOLS" in emitted
    assert "OPENALEX_MAILTO" in emitted
    assert "PAPERQA_TIMEOUT_SECONDS" in emitted
    assert "REPL_MAX_WALL_TIME_SECONDS" in emitted
    assert "1500" in emitted


def test_build_config_ignores_external_venv_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bootstrap_module()
    backend = tmp_path / "backend"
    run_dir = tmp_path / "run"
    backend.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    external = (tmp_path / "external-venv").resolve()
    monkeypatch.setenv("EVE_VENV_DIR", str(external))
    monkeypatch.delenv("EVE_ALLOW_EXTERNAL_VENV", raising=False)

    cfg = mod._build_config(["--backend-dir", str(backend), "--run-dir", str(run_dir)])
    assert cfg.venv_dir == (backend / ".venv").resolve()
    assert cfg.venv_override_ignored == str(external)
