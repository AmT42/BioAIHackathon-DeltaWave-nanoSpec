from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.agent.repl.types import ShellResult


_SPLIT_PATTERN = re.compile(r"[|;&]{1,2}")


@dataclass(frozen=True)
class ShellPolicy:
    workspace_root: Path
    allowed_prefixes: tuple[str, ...]
    blocked_prefixes: tuple[str, ...]
    max_output_bytes: int


class ShellExecutor:
    def __init__(self, policy: ShellPolicy) -> None:
        self.policy = policy

    def _first_command_token(self, command: str) -> str:
        first_segment = _SPLIT_PATTERN.split(command, maxsplit=1)[0].strip()
        if not first_segment:
            return ""
        try:
            parts = shlex.split(first_segment)
        except Exception:
            parts = first_segment.split()
        return parts[0].strip().lower() if parts else ""

    def _ensure_allowed(self, command: str) -> None:
        token = self._first_command_token(command)
        if not token:
            raise ValueError("Empty shell command")
        if token in {item.lower() for item in self.policy.blocked_prefixes}:
            raise ValueError(f"Blocked command prefix: {token}")
        allowed = {item.lower() for item in self.policy.allowed_prefixes}
        if token not in allowed:
            raise ValueError(
                f"Command prefix '{token}' is not allowed in guarded mode. "
                f"Allowed prefixes: {sorted(allowed)}"
            )

    def _resolve_cwd(self, cwd: str | None) -> Path:
        root = self.policy.workspace_root.resolve()
        candidate = (Path(cwd).expanduser() if cwd else root)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if not str(resolved).startswith(str(root)):
            raise ValueError(f"cwd '{resolved}' escapes workspace root '{root}'")
        return resolved

    def _truncate(self, value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.policy.max_output_bytes:
            return value, False
        truncated = encoded[: self.policy.max_output_bytes].decode("utf-8", errors="replace")
        return truncated, True

    def run(self, command: str, *, timeout_s: int = 30, cwd: str | None = None) -> ShellResult:
        self._ensure_allowed(command)
        resolved_cwd = self._resolve_cwd(cwd)
        proc = subprocess.run(
            ["/bin/zsh", "-lc", command],
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_s)),
        )
        stdout, out_truncated = self._truncate(proc.stdout or "")
        stderr, err_truncated = self._truncate(proc.stderr or "")
        return ShellResult(
            command=command,
            returncode=int(proc.returncode),
            stdout=stdout,
            stderr=stderr,
            truncated=out_truncated or err_truncated,
        )

    def grep(
        self,
        pattern: str,
        *,
        path: str = ".",
        glob: str = "**/*",
        ignore_case: bool = False,
        timeout_s: int = 30,
    ) -> ShellResult:
        if not pattern:
            raise ValueError("'pattern' is required")
        safe_path = shlex.quote(path)
        safe_glob = shlex.quote(glob)
        safe_pattern = shlex.quote(pattern)
        cmd = f"rg -n --glob {safe_glob} {'-i ' if ignore_case else ''}{safe_pattern} {safe_path}"
        return self.run(cmd, timeout_s=timeout_s, cwd=None)
