#!/usr/bin/env python3
"""Export app source code into one text file for LLM context.

Usage:
  python3 scripts/export_app_code_to_txt.py
  python3 scripts/export_app_code_to_txt.py --root . --output ~/Downloads/app_code.txt
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".worktrees",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".cache",
    ".parcel-cache",
    ".venv",
    ".venv_local",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "out",
    "logs",
    "log",
    "tmp",
    "temp",
}

DEFAULT_EXCLUDED_FILE_NAMES = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "bun.lockb",
    "pyproject.toml",
    "poetry.lock",
    "uv.lock",
    "Pipfile",
    "Pipfile.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    ".DS_Store",
}

DEFAULT_EXCLUDED_GLOBS = {
    "*.json",
    "*.jsonl",
    "*.ndjson",
    "*.txt",
    "*.md",
    "*.rst",
    "*.csv",
    "*.tsv",
    "*.lock",
    "*.log",
    "*.pdf",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.mp4",
    "*.mov",
    "*.avi",
    "*.wav",
    "*.mp3",
    "*.ogg",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.bz2",
    "*.xz",
    "*.7z",
    "*.sqlite",
    "*.db",
    "*.bin",
    "*.min.js",
    "*.min.css",
}

DEFAULT_INCLUDE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".kts",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".sql",
    ".graphql",
    ".gql",
    ".proto",
    ".vue",
    ".svelte",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".less",
}

DEFAULT_INCLUDE_FILENAMES = {
    "Dockerfile",
    "Makefile",
    "Procfile",
    "CMakeLists.txt",
}


@dataclass
class ExportStats:
    exported_files: int = 0
    exported_bytes: int = 0
    skipped_files: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export app code into one LLM-friendly .txt file."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root to export (default: current directory).",
    )
    parser.add_argument(
        "--output",
        default="app_code_export.txt",
        help="Path to output text file (default: ./app_code_export.txt).",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=500_000,
        help="Skip files larger than this many bytes (default: 500000).",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Additional directory names to skip (repeatable).",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Additional file globs to skip (repeatable).",
    )
    parser.add_argument(
        "--include-ext",
        action="append",
        default=[],
        help="Additional extension to include, for example --include-ext .toml",
    )
    parser.add_argument(
        "--include-file",
        action="append",
        default=[],
        help="Additional filename to include, for example --include-file Justfile",
    )
    return parser.parse_args()


def iter_files(root: Path, excluded_dirs: set[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded_dirs)
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def is_probably_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
    non_text = raw.translate(None, text_chars)
    return (len(non_text) / len(raw)) > 0.30


def should_exclude(
    rel_path: str,
    name: str,
    extension: str,
    *,
    excluded_file_names: set[str],
    excluded_globs: set[str],
    include_extensions: set[str],
    include_file_names: set[str],
) -> str | None:
    if name in excluded_file_names:
        return "excluded_file_name"

    if any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat) for pat in excluded_globs):
        return "excluded_glob"

    if name in include_file_names:
        return None

    if extension not in include_extensions:
        return "non_code_extension"

    return None


def main() -> int:
    args = parse_args()

    root = Path(args.root).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Root does not exist or is not a directory: {root}")

    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)

    excluded_file_names = set(DEFAULT_EXCLUDED_FILE_NAMES)
    excluded_globs = set(DEFAULT_EXCLUDED_GLOBS)
    excluded_globs.update(args.exclude_glob)

    include_extensions = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in DEFAULT_INCLUDE_EXTENSIONS}
    include_extensions.update(
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in args.include_ext
    )

    include_file_names = set(DEFAULT_INCLUDE_FILENAMES)
    include_file_names.update(args.include_file)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    protected_paths = {output_path}
    now = datetime.now(timezone.utc).isoformat()

    stats = ExportStats()
    skip_reasons: Counter[str] = Counter()

    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        out.write("# App Code Export\n")
        out.write(f"# Generated: {now}\n")
        out.write(f"# Root: {root}\n")
        out.write(
            "# Includes: code-like files only (package files and json/jsonl/txt noise excluded)\n\n"
        )

        for path in iter_files(root, excluded_dirs):
            if not path.is_file():
                continue
            if path.resolve() in protected_paths:
                continue

            rel_path = path.relative_to(root).as_posix()
            name = path.name
            extension = path.suffix.lower()

            reason = should_exclude(
                rel_path,
                name,
                extension,
                excluded_file_names=excluded_file_names,
                excluded_globs=excluded_globs,
                include_extensions=include_extensions,
                include_file_names=include_file_names,
            )
            if reason:
                stats.skipped_files += 1
                skip_reasons[reason] += 1
                continue

            size = path.stat().st_size
            if size > args.max_bytes:
                stats.skipped_files += 1
                skip_reasons["too_large"] += 1
                continue

            raw = path.read_bytes()
            if is_probably_binary(raw):
                stats.skipped_files += 1
                skip_reasons["binary"] += 1
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                stats.skipped_files += 1
                skip_reasons["not_utf8"] += 1
                continue

            if not text.endswith("\n"):
                text += "\n"

            out.write(f"===== BEGIN FILE: {rel_path} =====\n")
            out.write(text)
            out.write(f"===== END FILE: {rel_path} =====\n\n")

            stats.exported_files += 1
            stats.exported_bytes += len(raw)

    reason_summary = ", ".join(
        f"{reason}={count}" for reason, count in sorted(skip_reasons.items())
    )
    if not reason_summary:
        reason_summary = "none"

    print(f"Exported {stats.exported_files} files to {output_path}")
    print(f"Exported bytes: {stats.exported_bytes}")
    print(f"Skipped files: {stats.skipped_files} ({reason_summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
