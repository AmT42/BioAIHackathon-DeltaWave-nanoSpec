#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OPENALEX_MAILTO = "ahmet@deltawave.fr"
DEFAULT_LONG_TIMEOUT_SECONDS = 1500
SUPPORTED_PAPERQA_PYTHON_MINORS = {11, 12, 13}

CORE_MODULE_TO_DIST: dict[str, str] = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "greenlet": "greenlet",
    "aiosqlite": "aiosqlite",
    "pydantic": "pydantic",
    "dotenv": "python-dotenv",
    "anthropic": "anthropic",
    "google.genai": "google-genai",
}

PAPERQA_MODULE_TO_DIST: dict[str, str] = {
    "paperqa": "",
    "paperqa_pypdf": "",
    "pydantic_settings": "pydantic-settings",
    "lmi": "fhlmi",
    "aviary": "fhaviary[llm]",
    "pybtex": "pybtex",
    "tantivy": "tantivy",
    "tiktoken": "tiktoken",
    "html2text": "html2text",
    "httpx_aiohttp": "httpx-aiohttp",
    "pypdf": "pypdf",
}

BUILD_MODULE_TO_DIST: dict[str, str] = {
    "wheel": "wheel",
    "setuptools": "setuptools",
    "setuptools_scm": "setuptools-scm",
}


@dataclass(frozen=True)
class BootstrapConfig:
    backend_dir: Path
    run_dir: Path
    emit_env_file: Path | None
    venv_dir: Path
    venv_override_ignored: str | None
    verbose: bool
    skip_install: bool


class BootstrapError(RuntimeError):
    pass


def _probe_python_version(python_bin: Path, *, cwd: Path, env: dict[str, str]) -> tuple[int, int] | None:
    completed = subprocess.run(
        [str(python_bin), "-c", "import sys, json; print(json.dumps({'major': sys.version_info.major, 'minor': sys.version_info.minor}))"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        parsed = json.loads(completed.stdout.strip() or "{}")
        return (int(parsed.get("major", 0)), int(parsed.get("minor", 0)))
    except Exception:
        return None


def _is_supported_paperqa_python(version: tuple[int, int] | None) -> bool:
    if version is None:
        return False
    return version[0] == 3 and version[1] in SUPPORTED_PAPERQA_PYTHON_MINORS


def _bool_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    report: list[dict[str, Any]],
    label: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    report.append(
        {
            "label": label,
            "command": cmd,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    )
    if check and completed.returncode != 0:
        raise BootstrapError(
            f"{label} failed (code={completed.returncode}). "
            f"Command: {' '.join(cmd)}. stderr={completed.stderr.strip()[:400]}"
        )
    return completed


def _ensure_local_env_file(backend_dir: Path, report: dict[str, Any]) -> Path:
    env_path = backend_dir / ".env"
    example_path = backend_dir / ".env.example"
    if not example_path.exists():
        raise BootstrapError("backend/.env.example is missing")

    if env_path.is_symlink():
        target = env_path.resolve(strict=False)
        text = ""
        if target.exists():
            text = target.read_text(encoding="utf-8")
        else:
            text = example_path.read_text(encoding="utf-8")
        env_path.unlink()
        env_path.write_text(text, encoding="utf-8")
        report["env_localized_from_symlink"] = str(target)
        return env_path

    if not env_path.exists():
        shutil.copyfile(example_path, env_path)
        report["env_created_from_example"] = True
    return env_path


def _ensure_venv(config: BootstrapConfig, report: list[dict[str, Any]], env: dict[str, str]) -> Path:
    venv_python = config.venv_dir / "bin" / "python"
    if venv_python.exists():
        version_tuple = _probe_python_version(venv_python, cwd=config.backend_dir, env=env)
        if _is_supported_paperqa_python(version_tuple):
            return venv_python
        report.append(
            {
                "label": "recreate_venv_due_unsupported_python",
                "command": [str(venv_python), "-c", "import sys; print(sys.version)"],
                "returncode": 0 if version_tuple is not None else 1,
                "stdout": str(version_tuple),
                "stderr": "",
            }
        )
        shutil.rmtree(config.venv_dir, ignore_errors=True)

    creator_version = _probe_python_version(Path(sys.executable), cwd=config.backend_dir, env=env)
    if not _is_supported_paperqa_python(creator_version):
        raise BootstrapError(
            "Bootstrap Python is unsupported for PaperQA. "
            f"Detected {sys.executable} version={creator_version}. "
            "Use backend/.venv (Python 3.11-3.13) or set BOOTSTRAP_PYTHON to a supported interpreter."
        )

    config.venv_dir.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [sys.executable, "-m", "venv", str(config.venv_dir)],
        cwd=config.backend_dir,
        env=env,
        report=report,
        label="create_venv",
    )
    if not venv_python.exists():
        raise BootstrapError(f"Virtualenv creation succeeded but {venv_python} not found")
    created_version = _probe_python_version(venv_python, cwd=config.backend_dir, env=env)
    if not _is_supported_paperqa_python(created_version):
        raise BootstrapError(
            "Created virtualenv uses unsupported Python for PaperQA. "
            f"venv_python={venv_python} version={created_version}. "
            "Use Python 3.11-3.13 for bootstrap."
        )
    return venv_python


def _check_imports(venv_python: Path, modules: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, bool]:
    code = (
        "import importlib.util, json\n"
        f"mods={json.dumps(modules)}\n"
        "out={}\n"
        "for m in mods:\n"
        "  try:\n"
        "    out[m] = bool(importlib.util.find_spec(m))\n"
        "  except Exception:\n"
        "    out[m] = False\n"
        "print(json.dumps(out))\n"
    )
    completed = subprocess.run(
        [str(venv_python), "-c", code],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {m: False for m in modules}
    try:
        parsed = json.loads(completed.stdout.strip() or "{}")
    except Exception:
        return {m: False for m in modules}
    return {m: bool(parsed.get(m)) for m in modules}


def _ensure_core_deps(
    config: BootstrapConfig,
    *,
    venv_python: Path,
    env: dict[str, str],
    cmd_report: list[dict[str, Any]],
    install_report: dict[str, Any],
) -> None:
    checks = _check_imports(venv_python, list(CORE_MODULE_TO_DIST.keys()), cwd=config.backend_dir, env=env)
    missing = [CORE_MODULE_TO_DIST[m] for m, ok in checks.items() if not ok]
    missing_unique = sorted({item for item in missing if item})
    install_report["core_missing_before"] = missing
    if not missing_unique or config.skip_install:
        return
    _run(
        [str(venv_python), "-m", "pip", "install", *missing_unique],
        cwd=config.backend_dir,
        env=env,
        report=cmd_report,
        label="install_backend_runtime_deps",
    )


def _ensure_paperqa_deps(
    config: BootstrapConfig,
    *,
    venv_python: Path,
    env: dict[str, str],
    cmd_report: list[dict[str, Any]],
    install_report: dict[str, Any],
) -> None:
    vendor_pqa = config.backend_dir / "vendor" / "paper-qa"
    vendor_pypdf = vendor_pqa / "packages" / "paper-qa-pypdf"
    if not vendor_pqa.exists():
        raise BootstrapError(f"Missing vendored PaperQA at {vendor_pqa}")
    if not vendor_pypdf.exists():
        raise BootstrapError(f"Missing vendored PaperQA pypdf package at {vendor_pypdf}")

    checks = _check_imports(venv_python, list(PAPERQA_MODULE_TO_DIST.keys()), cwd=config.backend_dir, env=env)
    missing = [PAPERQA_MODULE_TO_DIST[m] for m, ok in checks.items() if not ok and PAPERQA_MODULE_TO_DIST[m]]
    missing_unique = sorted({item for item in missing if item})
    install_report["paperqa_missing_before"] = missing
    if config.skip_install:
        return

    build_checks = _check_imports(venv_python, list(BUILD_MODULE_TO_DIST.keys()), cwd=config.backend_dir, env=env)
    build_missing = sorted({BUILD_MODULE_TO_DIST[m] for m, ok in build_checks.items() if not ok and BUILD_MODULE_TO_DIST[m]})
    install_report["paperqa_build_runtime_missing_before"] = build_missing
    if build_missing:
        _run(
            [str(venv_python), "-m", "pip", "install", *build_missing],
            cwd=config.backend_dir,
            env=env,
            report=cmd_report,
            label="install_paperqa_build_runtime",
        )

    _run(
        [str(venv_python), "-m", "pip", "uninstall", "-y", "paper-qa", "paper-qa-pypdf"],
        cwd=config.backend_dir,
        env=env,
        report=cmd_report,
        label="uninstall_existing_paperqa",
        check=False,
    )

    # Always reinstall editable vendored packages when PaperQA path may drift across worktrees.
    env_pqa = dict(env)
    env_pqa["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PAPER_QA"] = "0.0.0"
    _run(
        [str(venv_python), "-m", "pip", "install", "--no-build-isolation", "-e", str(vendor_pqa)],
        cwd=config.backend_dir,
        env=env_pqa,
        report=cmd_report,
        label="install_paperqa_editable",
    )

    env_pypdf = dict(env)
    env_pypdf["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PAPER_QA_PYPDF"] = "0.0.0"
    _run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "-e",
            str(vendor_pypdf),
        ],
        cwd=config.backend_dir,
        env=env_pypdf,
        report=cmd_report,
        label="install_paperqa_pypdf_editable",
    )

    remaining_checks = _check_imports(venv_python, list(PAPERQA_MODULE_TO_DIST.keys()), cwd=config.backend_dir, env=env)
    remaining_missing = sorted(
        {
            PAPERQA_MODULE_TO_DIST[m]
            for m, ok in remaining_checks.items()
            if not ok and PAPERQA_MODULE_TO_DIST[m]
        }
    )
    install_report["paperqa_missing_after_local_editable"] = remaining_missing
    if remaining_missing:
        _run(
            [str(venv_python), "-m", "pip", "install", *remaining_missing],
            cwd=config.backend_dir,
            env=env,
            report=cmd_report,
            label="install_paperqa_transitive_runtime",
        )


def _verify_local_vendored_paperqa(
    venv_python: Path,
    *,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    probe = (
        "import json\n"
        "from pathlib import Path\n"
        "payload = {'ok': False}\n"
        "try:\n"
        "  import paperqa, paperqa_pypdf\n"
        "  backend = Path.cwd()\n"
        "  expected_pqa = (backend / 'vendor' / 'paper-qa' / 'src').resolve()\n"
        "  expected_pypdf = (backend / 'vendor' / 'paper-qa' / 'packages' / 'paper-qa-pypdf' / 'src').resolve()\n"
        "  actual_pqa = Path(getattr(paperqa, '__file__', '')).resolve()\n"
        "  actual_pypdf = Path(getattr(paperqa_pypdf, '__file__', '')).resolve()\n"
        "  payload.update({\n"
        "    'actual_pqa': str(actual_pqa),\n"
        "    'actual_pypdf': str(actual_pypdf),\n"
        "    'expected_pqa_prefix': str(expected_pqa),\n"
        "    'expected_pypdf_prefix': str(expected_pypdf),\n"
        "  })\n"
        "  payload['ok'] = str(actual_pqa).startswith(str(expected_pqa)) and str(actual_pypdf).startswith(str(expected_pypdf))\n"
        "except Exception as exc:\n"
        "  payload['error'] = str(exc)\n"
        "print(json.dumps(payload))\n"
    )
    completed = subprocess.run(
        [str(venv_python), "-c", probe],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr.strip()[:1000]}
    try:
        return json.loads(completed.stdout.strip() or "{}")
    except Exception:
        return {"ok": False, "error": f"Invalid vendored-paperqa probe output: {completed.stdout.strip()[:400]}"}


def _verify_runtime_ready(venv_python: Path, *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    probe = (
        "import json, app.config as c\n"
        "from app.agent.tools.science_registry import create_science_registry\n"
        "s = c.get_settings()\n"
        "reg = create_science_registry(s)\n"
        "payload = {\n"
        "  'python_executable': __import__('sys').executable,\n"
        "  'config_module': c.__file__,\n"
        "  'enable_paperqa_tools': getattr(s, 'enable_paperqa_tools', None),\n"
        "  'has_search_pubmed_agent': 'search_pubmed_agent' in reg.names(),\n"
        "}\n"
        "print(json.dumps(payload))\n"
    )
    completed = subprocess.run(
        [str(venv_python), "-c", probe],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr.strip()[:1000]}
    try:
        payload = json.loads(completed.stdout.strip())
    except Exception:
        return {"ok": False, "error": f"Invalid probe output: {completed.stdout.strip()[:400]}"}
    payload["ok"] = bool(payload.get("has_search_pubmed_agent"))
    return payload


def _emit_shell_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"export {key}={json.dumps(value)}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_min_timeout(env_values: dict[str, str], *, key: str, minimum_seconds: int) -> None:
    raw = str(env_values.get(key, "")).strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = minimum_seconds
    if parsed < minimum_seconds:
        parsed = minimum_seconds
    env_values[key] = str(parsed)


def run_bootstrap(config: BootstrapConfig) -> dict[str, Any]:
    report: dict[str, Any] = {
        "backend_dir": str(config.backend_dir),
        "run_dir": str(config.run_dir),
        "venv_dir": str(config.venv_dir),
        "verbose": config.verbose,
        "skip_install": config.skip_install,
        "commands": [],
        "installs": {},
        "status": "starting",
    }
    if config.venv_override_ignored:
        report["venv_override_ignored"] = config.venv_override_ignored

    process_env = dict(os.environ)
    process_env.pop("PYTHONPATH", None)
    process_env.pop("PYTHONHOME", None)
    process_env["PYTHONNOUSERSITE"] = "1"

    env_path = _ensure_local_env_file(config.backend_dir, report)
    env_values = _parse_env_file(env_path)

    # Force requested runtime behavior.
    env_values["ENABLE_PAPERQA_TOOLS"] = "true"
    if not env_values.get("OPENALEX_MAILTO"):
        env_values["OPENALEX_MAILTO"] = DEFAULT_OPENALEX_MAILTO
    _ensure_min_timeout(
        env_values,
        key="PAPERQA_TIMEOUT_SECONDS",
        minimum_seconds=DEFAULT_LONG_TIMEOUT_SECONDS,
    )
    _ensure_min_timeout(
        env_values,
        key="REPL_MAX_WALL_TIME_SECONDS",
        minimum_seconds=DEFAULT_LONG_TIMEOUT_SECONDS,
    )

    mock_llm = env_values.get("MOCK_LLM", "").strip().lower() in {"1", "true", "yes", "on"}
    gemini_key = os.getenv("GEMINI_API_KEY") or env_values.get("GEMINI_API_KEY", "")
    if not gemini_key and not mock_llm:
        raise BootstrapError(
            "GEMINI_API_KEY is required when MOCK_LLM is false. "
            "Set it in backend/.env or export it before running ./scripts/eve-up.sh."
        )

    venv_python = _ensure_venv(config, report["commands"], process_env)
    report["venv_python"] = str(venv_python)

    _ensure_core_deps(
        config,
        venv_python=venv_python,
        env=process_env,
        cmd_report=report["commands"],
        install_report=report["installs"],
    )
    _ensure_paperqa_deps(
        config,
        venv_python=venv_python,
        env=process_env,
        cmd_report=report["commands"],
        install_report=report["installs"],
    )

    final_core = _check_imports(venv_python, list(CORE_MODULE_TO_DIST.keys()), cwd=config.backend_dir, env=process_env)
    final_pqa = _check_imports(venv_python, list(PAPERQA_MODULE_TO_DIST.keys()), cwd=config.backend_dir, env=process_env)
    report["core_imports"] = final_core
    report["paperqa_imports"] = final_pqa

    missing_final = [m for m, ok in {**final_core, **final_pqa}.items() if not ok]
    if missing_final:
        raise BootstrapError(f"Missing runtime modules after bootstrap: {missing_final}")

    vendored_probe = _verify_local_vendored_paperqa(venv_python, cwd=config.backend_dir, env=process_env)
    report["vendored_paperqa_probe"] = vendored_probe
    if not vendored_probe.get("ok"):
        raise BootstrapError(
            "PaperQA is not installed from local vendor sources. "
            f"Details: {vendored_probe}"
        )

    runtime_env = dict(process_env)
    runtime_env.update(env_values)
    runtime_env["PYTHONNOUSERSITE"] = "1"
    runtime_probe = _verify_runtime_ready(venv_python, cwd=config.backend_dir, env=runtime_env)
    report["runtime_probe"] = runtime_probe
    if not runtime_probe.get("ok"):
        raise BootstrapError(
            "Runtime probe failed. "
            f"Details: {runtime_probe.get('error') or runtime_probe}"
        )

    shell_env = {
        "EVE_PYTHON_BIN": str(venv_python),
        "ENABLE_PAPERQA_TOOLS": "true",
        "OPENALEX_MAILTO": env_values.get("OPENALEX_MAILTO", DEFAULT_OPENALEX_MAILTO),
        "PAPERQA_TIMEOUT_SECONDS": env_values["PAPERQA_TIMEOUT_SECONDS"],
        "REPL_MAX_WALL_TIME_SECONDS": env_values["REPL_MAX_WALL_TIME_SECONDS"],
        "PYTHONNOUSERSITE": "1",
    }
    if config.emit_env_file is not None:
        _emit_shell_env(config.emit_env_file, shell_env)
        report["emit_env_file"] = str(config.emit_env_file)

    report["status"] = "ready"
    return report


def _build_config(argv: list[str]) -> BootstrapConfig:
    parser = argparse.ArgumentParser(description="Bootstrap backend environment for eve-up startup.")
    parser.add_argument("--backend-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--emit-env-file", default="")
    args = parser.parse_args(argv)

    backend_dir = Path(args.backend_dir).expanduser().resolve()
    run_dir = Path(args.run_dir).expanduser().resolve()
    emit_env_file = Path(args.emit_env_file).expanduser().resolve() if args.emit_env_file else None
    default_venv_dir = backend_dir / ".venv"
    allow_external_venv = _bool_env("EVE_ALLOW_EXTERNAL_VENV", default=False)
    raw_override = os.getenv("EVE_VENV_DIR")
    venv_override_ignored: str | None = None
    if raw_override:
        candidate = Path(raw_override).expanduser()
        if not candidate.is_absolute():
            candidate = (backend_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if allow_external_venv or candidate == backend_dir or backend_dir in candidate.parents:
            venv_dir = candidate
        else:
            venv_dir = default_venv_dir
            venv_override_ignored = str(candidate)
    else:
        venv_dir = default_venv_dir
    verbose = _bool_env("EVE_BOOTSTRAP_VERBOSE", default=False)
    skip_install = _bool_env("EVE_BOOTSTRAP_SKIP_INSTALL", default=False)

    return BootstrapConfig(
        backend_dir=backend_dir,
        run_dir=run_dir,
        emit_env_file=emit_env_file,
        venv_dir=venv_dir,
        venv_override_ignored=venv_override_ignored,
        verbose=verbose,
        skip_install=skip_install,
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    config = _build_config(args)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    report_path = config.run_dir / "bootstrap_report.json"
    try:
        report = run_bootstrap(config)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Bootstrap ready. python={report.get('venv_python')}")
        return 0
    except Exception as exc:
        report = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "backend_dir": str(config.backend_dir),
            "run_dir": str(config.run_dir),
            "venv_dir": str(config.venv_dir),
            "python_executable": sys.executable,
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(
            "Bootstrap failed. "
            f"See {report_path} for details. "
            f"Error: {report['error']}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
