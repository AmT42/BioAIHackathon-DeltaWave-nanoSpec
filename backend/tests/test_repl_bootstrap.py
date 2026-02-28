from __future__ import annotations

from dataclasses import replace

import pytest

from app.agent.repl.bootstrap import ensure_repl_preload, resolve_preload_package_set
from app.config import get_settings


def test_resolve_preload_package_set_profile_default() -> None:
    settings = replace(get_settings(), repl_preload_profile="bio_data_full", repl_preload_packages=())
    packages = resolve_preload_package_set(settings)
    assert "pandas" in packages
    assert "biopython" in packages


def test_resolve_preload_package_set_explicit_override() -> None:
    settings = replace(get_settings(), repl_preload_packages=("foo", "bar", "foo"), repl_preload_profile="minimal_core")
    assert resolve_preload_package_set(settings) == ["foo", "bar"]


def test_ensure_repl_preload_disabled() -> None:
    settings = replace(get_settings(), repl_preload_enabled=False)
    report = ensure_repl_preload(settings)
    assert report["status"] == "disabled"


def test_ensure_repl_preload_ready_when_all_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(
        get_settings(),
        repl_preload_enabled=True,
        repl_preload_packages=("demo-pkg",),
        repl_preload_fail_mode="warn_continue",
    )

    monkeypatch.setattr("app.agent.repl.bootstrap._is_import_available", lambda _name: True)
    report = ensure_repl_preload(settings)
    assert report["status"] == "ready"
    assert report["missing_before"] == []
    assert report["missing_after"] == []


def test_ensure_repl_preload_fail_fast_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(
        get_settings(),
        repl_preload_enabled=True,
        repl_preload_packages=("demo-pkg",),
        repl_preload_fail_mode="fail_fast",
    )

    monkeypatch.setattr("app.agent.repl.bootstrap._is_import_available", lambda _name: False)
    monkeypatch.setattr("app.agent.repl.bootstrap._install_packages", lambda *args, **kwargs: (False, "boom"))

    with pytest.raises(RuntimeError):
        ensure_repl_preload(settings)

