#!/usr/bin/env python3
"""Export backend files into a single text file plus a JSON manifest.

Recreated script with compatible CLI:
  python3 backend/scripts/export_backend_to_txt.py --root backend --output ~/Downloads/backend_context.txt
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
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
    ".idea",
    ".vscode",
    # Runtime/data directories that can explode export size.
    "logs",
    "log",
    "artifacts",
    "tmp",
    "temp",
}

DEFAULT_EXCLUDED_GLOBS = {
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.class",
    "*.jar",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.bz2",
    "*.xz",
    "*.7z",
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
    "*.bin",
    "*.log",
    "*.json",
    "*.jsonl",
    "*.ndjson",
    "*.sqlite",
    "*.db",
    ".DS_Store",
}

# JSON exports are excluded by default to avoid including runtime traces.
DEFAULT_JSON_GLOBS = {"*.json", "*.jsonl", "*.ndjson"}

# "Without package" defaults: package/lock/dependency descriptor files.
DEFAULT_EXCLUDED_PACKAGE_FILES = {
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
}


@dataclass
class ExportedFile:
    path: str
    size_bytes: int
    line_count: int
    sha256: str
    start_line: int
    end_line: int


@dataclass
class SkippedFile:
    path: str
    reason: str


def parse_args() -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Export backend source to one text file and a JSON manifest."
    )
    parser.add_argument(
        "--root",
        default=str(default_root),
        help="Project root to export (default: backend root).",
    )
    parser.add_argument(
        "--output",
        default="backend_context.txt",
        help="Path to output text file.",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Path to JSON manifest (default: output path with .json suffix).",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=500_000,
        help="Skip files larger than this many bytes (default: 500000).",
    )
    parser.add_argument(
        "--include-package-files",
        action="store_true",
        help="Include package/lock files (excluded by default).",
    )
    parser.add_argument(
        "--include-json-files",
        action="store_true",
        help="Include JSON/JSONL/NDJSON files (excluded by default).",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Additional directory name to exclude (repeatable).",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Additional glob pattern to exclude files (repeatable).",
    )
    return parser.parse_args()


def should_exclude_file(
    rel_path: str,
    name: str,
    *,
    excluded_globs: set[str],
    excluded_package_files: set[str],
) -> bool:
    if name in excluded_package_files:
        return True
    return any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat) for pat in excluded_globs)


def is_probably_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
    non_text = raw.translate(None, text_chars)
    return (len(non_text) / len(raw)) > 0.30


def iter_files(root: Path, excluded_dirs: set[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded_dirs)
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def main() -> int:
    args = parse_args()

    root = Path(args.root).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    log_path = (
        Path(args.log).expanduser().resolve()
        if args.log
        else output_path.with_suffix(".json")
    )

    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(args.exclude_dir)

    excluded_globs = set(DEFAULT_EXCLUDED_GLOBS)
    excluded_globs.update(args.exclude_glob)
    if args.include_json_files:
        excluded_globs.difference_update(DEFAULT_JSON_GLOBS)

    excluded_package_files = (
        set()
        if args.include_package_files
        else set(DEFAULT_EXCLUDED_PACKAGE_FILES)
    )

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Root does not exist or is not a directory: {root}")

    exported: list[ExportedFile] = []
    skipped: list[SkippedFile] = []

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()

    line_cursor = 1
    total_bytes = 0

    protected_paths = {output_path}
    if log_path:
        protected_paths.add(log_path)

    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        header = [
            "# Backend Context Export",
            f"# Generated: {now}",
            f"# Root: {root}",
            "",
        ]
        out.write("\n".join(header))
        line_cursor += len(header)

        for path in iter_files(root, excluded_dirs):
            if not path.is_file():
                continue

            resolved = path.resolve()
            if resolved in protected_paths:
                continue

            rel_path = path.relative_to(root).as_posix()
            name = path.name

            if should_exclude_file(
                rel_path,
                name,
                excluded_globs=excluded_globs,
                excluded_package_files=excluded_package_files,
            ):
                skipped.append(SkippedFile(path=rel_path, reason="excluded"))
                continue

            size = path.stat().st_size
            if size > args.max_bytes:
                skipped.append(
                    SkippedFile(path=rel_path, reason=f"too_large>{args.max_bytes}")
                )
                continue

            raw = path.read_bytes()
            if is_probably_binary(raw):
                skipped.append(SkippedFile(path=rel_path, reason="binary"))
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(SkippedFile(path=rel_path, reason="not_utf8"))
                continue

            if not text.endswith("\n"):
                text += "\n"

            file_header = f"===== BEGIN FILE: {rel_path} =====\n"
            file_footer = f"===== END FILE: {rel_path} =====\n\n"

            start_line = line_cursor
            body_line_count = text.count("\n")
            end_line = start_line + 1 + body_line_count  # +1 for footer line

            out.write(file_header)
            out.write(text)
            out.write(file_footer)

            line_cursor += 1 + body_line_count + 2  # header + content + footer + blank line
            total_bytes += len(raw)

            exported.append(
                ExportedFile(
                    path=rel_path,
                    size_bytes=size,
                    line_count=body_line_count,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    start_line=start_line,
                    end_line=end_line,
                )
            )

    manifest = {
        "generated_at": now,
        "root": str(root),
        "output_file": str(output_path),
        "total_files": len(exported),
        "total_bytes": total_bytes,
        "max_bytes": args.max_bytes,
        "package_files_excluded": not args.include_package_files,
        "excluded_dirs": sorted(excluded_dirs),
        "excluded_globs": sorted(excluded_globs),
        "files": [vars(item) for item in exported],
        "skipped": [vars(item) for item in skipped],
    }

    log_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Exported {len(exported)} files to {output_path}")
    print(f"Wrote manifest JSON to {log_path}")
    print(f"Skipped {len(skipped)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
