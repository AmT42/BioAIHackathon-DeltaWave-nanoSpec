#!/usr/bin/env python3
"""
Generate a single Markdown file that concatenates the contents of specific files
you provide, in the order provided.

Examples
--------
- Basic:        ./files_to_markdown.py src/a.py src/b.py -o ~/Downloads/snippets.md
- From a list:  ./files_to_markdown.py -f filelist.txt -o selected.md

Notes
-----
- Keeps the provided order (unless --sort is set).
- Skips missing paths with a warning (use --fail-missing to exit non‑zero).
- Detects code block language from file extension (best effort).
- If --root is provided, headings show paths relative to it; otherwise the
  common parent directory of all inputs is used.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple


def get_language_from_extension(path: Path) -> str:
    """Map file extension or known filename to a Markdown code fence language."""
    name = path.name
    ext = path.suffix.lower()

    # Special-case common extensionless filenames
    special_names = {
        'makefile': 'makefile',
        'dockerfile': 'dockerfile',
        'cmakelists.txt': 'cmake',
        'procfile': 'bash',
        'justfile': 'makefile',
    }
    if name.lower() in special_names:
        return special_names[name.lower()]

    language_map = {
        '.py': 'python',
        '.js': 'javascript',
        '.mjs': 'javascript',
        '.cjs': 'javascript',
        '.ts': 'typescript',
        '.jsx': 'jsx',
        '.tsx': 'tsx',
        '.java': 'java',
        '.c': 'c',
        '.cpp': 'cpp',
        '.cc': 'cpp',
        '.cxx': 'cpp',
        '.h': 'c',
        '.hpp': 'cpp',
        '.cs': 'csharp',
        '.php': 'php',
        '.rb': 'ruby',
        '.go': 'go',
        '.rs': 'rust',
        '.sh': 'bash',
        '.bash': 'bash',
        '.zsh': 'zsh',
        '.fish': 'fish',
        '.ps1': 'powershell',
        '.sql': 'sql',
        '.html': 'html',
        '.htm': 'html',
        '.css': 'css',
        '.scss': 'scss',
        '.sass': 'sass',
        '.less': 'less',
        '.xml': 'xml',
        '.json': 'json',
        '.yaml': 'yaml',
        '.yml': 'yaml',
        '.toml': 'toml',
        '.ini': 'ini',
        '.cfg': 'ini',
        '.conf': 'ini',
        '.md': 'markdown',
        '.txt': 'text',
        '.r': 'r',
        '.m': 'matlab',
        '.scala': 'scala',
        '.kt': 'kotlin',
        '.swift': 'swift',
        '.dart': 'dart',
        '.lua': 'lua',
        '.pl': 'perl',
        '.vim': 'vim',
    }
    return language_map.get(ext, 'text')


def anchor_from_path(rel_path: Path) -> str:
    """Create a simple markdown anchor from a relative path."""
    anchor = str(rel_path)
    # Strip common punctuation to keep anchors simple and stable
    for ch in ('/', ' ', '.', '_', '-', ':'):
        anchor = anchor.replace(ch, '')
    return anchor.lower()


def read_list_file(list_file: Path) -> List[str]:
    """Read file paths from a text file (one per line). Ignores blanks and # comments."""
    items: List[str] = []
    with open(list_file, 'r', encoding='utf-8') as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith('#'):
                continue
            items.append(raw)
    return items


def normalize_inputs(
    cli_files: Iterable[str],
    list_file: Optional[str],
    sort: bool,
) -> Tuple[List[Path], List[str]]:
    """Turn inputs into a unique, ordered list of files. Returns (files, missing).

    - Keeps original order unless sort=True.
    - Deduplicates by resolved absolute path.
    """
    items: List[str] = list(cli_files)
    if list_file:
        lf = Path(list_file)
        if str(list_file).strip() == '-':
            items.extend([ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()])
        else:
            items.extend(read_list_file(lf))

    # Preserve order while deduping
    seen: Set[Path] = set()
    files: List[Path] = []
    missing: List[str] = []
    for item in items:
        p = Path(item).expanduser()
        if not p.exists() or not p.is_file():
            missing.append(item)
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        files.append(rp)

    if sort:
        files.sort(key=lambda p: str(p))

    return files, missing


def determine_base_path(files: List[Path], explicit_root: Optional[str]) -> Path:
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()
    # Fallback to common parent directory of all files
    if not files:
        return Path.cwd()
    parents = [str(p.parent) for p in files]
    common = os.path.commonpath(parents) if parents else os.getcwd()
    return Path(common).resolve()


def safe_relative(path: Path, base: Path) -> Path:
    try:
        return path.relative_to(base)
    except Exception:
        return Path(path.name)


def generate_markdown(
    files: List[Path],
    output_file: Path,
    base_path: Path,
    title: Optional[str] = None,
    include_toc: bool = True,
):
    output_file.parent.mkdir(parents=True, exist_ok=True)

    doc_title = title or 'Selected Files - Combined Code'
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# {doc_title}\n\n")
        f.write(f"Relative to: `{base_path}`\n\n")
        f.write(f"Total files: {len(files)}\n\n")

        if include_toc:
            f.write("## Table of Contents\n\n")
            for p in files:
                rel = safe_relative(p, base_path)
                anchor = anchor_from_path(rel)
                f.write(f"- [{rel}](#{anchor})\n")
            f.write("\n")

        f.write("## Files\n\n")
        for p in files:
            if p.resolve() == output_file.resolve():
                # Avoid self-inclusion if user listed the output path as an input
                continue

            rel = safe_relative(p, base_path)
            f.write(f"### {rel}\n\n")
            f.write(f"**Path:** `{rel}`\n\n")

            try:
                with open(p, 'r', encoding='utf-8') as src:
                    content = src.read()
                lang = get_language_from_extension(p)
                f.write(f"```{lang}\n{content}\n```\n\n")
            except UnicodeDecodeError:
                f.write("*Binary or non-UTF8 file — content not displayed*\n\n")
            except Exception as e:
                f.write(f"*Error reading file: {e}*\n\n")

            f.write("---\n\n")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Generate a Markdown doc from specific files.')
    p.add_argument('files', nargs='*', help='File paths to include, in order.')
    p.add_argument('-f', '--from-file', dest='from_file', default=None,
                   help='Read additional file paths from a text file (one per line). Use - to read from stdin.')
    p.add_argument('-o', '--output', default='~/Downloads/selected_files.md',
                   help='Output markdown file path (default: ~/Downloads/selected_files.md)')
    p.add_argument('-r', '--root', default=None,
                   help='Base path used to show relative paths (default: common parent of inputs).')
    p.add_argument('-t', '--title', default=None, help='Title for the generated markdown.')
    p.add_argument('--no-toc', action='store_true', help='Do not include a table of contents.')
    p.add_argument('--sort', action='store_true', help='Sort files alphabetically (default: keep order).')
    p.add_argument('--fail-missing', action='store_true', help='Exit non-zero if any input files are missing.')
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    files, missing = normalize_inputs(args.files, args.from_file, args.sort)
    if missing:
        for m in missing:
            print(f"Warning: missing or not a file: {m}", file=sys.stderr)
        if args.fail_missing:
            return 2

    if not files:
        print("No files to process. Provide file paths or use --from-file.", file=sys.stderr)
        return 1

    base_path = determine_base_path(files, args.root)
    output_path = Path(args.output).expanduser()

    print(f"Writing {len(files)} files to: {output_path}")
    print(f"Relative base: {base_path}")

    generate_markdown(
        files=files,
        output_file=output_path,
        base_path=base_path,
        title=args.title,
        include_toc=not args.no_toc,
    )

    print("Done!")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
