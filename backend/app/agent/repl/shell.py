from __future__ import annotations

import re
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, IO

from app.agent.repl.types import ShellResult


_SPLIT_PATTERN = re.compile(r"[|;&]{1,2}")
_SECRET_PATH_PATTERN = re.compile(
    r"(^|[\s\"'`])(\.env($|[.\s/])|id_rsa\b|\.pem\b|credentials\.json\b|service-account)",
    flags=re.IGNORECASE,
)
_WRITE_INTENT_PATTERN = re.compile(
    r"(>\s*[^|]+|>>\s*[^|]+|\btee\b|\bcp\b|\bmv\b|\bsed\s+-i\b|\btouch\b|\btruncate\b)",
    flags=re.IGNORECASE,
)
ChunkCallback = Callable[[str], None]


@dataclass(frozen=True)
class ShellPolicy:
    workspace_root: Path
    mode: str
    allowed_prefixes: tuple[str, ...]
    blocked_prefixes: tuple[str, ...]
    blocked_patterns: tuple[str, ...]
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
        lowered = command.lower()
        if token in {item.lower() for item in self.policy.blocked_prefixes}:
            raise ValueError(f"Blocked command prefix: {token}")
        for pattern in self.policy.blocked_patterns:
            needle = str(pattern or "").strip().lower()
            if needle and needle in lowered:
                raise ValueError(f"Blocked command pattern: {needle}")
        if _WRITE_INTENT_PATTERN.search(command) and _SECRET_PATH_PATTERN.search(command):
            raise ValueError("Blocked write to sensitive secret/config path")
        mode = str(self.policy.mode or "open").strip().lower()
        if mode == "open":
            return
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

    def _emit_chunk(self, callback: ChunkCallback | None, chunk: str) -> None:
        if callback is None or not chunk:
            return
        try:
            callback(chunk)
        except Exception:
            # Streaming callbacks are best-effort.
            pass

    def _read_stream(
        self,
        stream: IO[str] | None,
        chunks: list[str],
        callback: ChunkCallback | None,
    ) -> None:
        if stream is None:
            return
        try:
            for chunk in iter(stream.readline, ""):
                if not chunk:
                    break
                chunks.append(chunk)
                self._emit_chunk(callback, chunk)
        finally:
            stream.close()

    def run(
        self,
        command: str,
        *,
        timeout_s: int = 30,
        cwd: str | None = None,
        on_stdout_chunk: ChunkCallback | None = None,
        on_stderr_chunk: ChunkCallback | None = None,
    ) -> ShellResult:
        self._ensure_allowed(command)
        resolved_cwd = self._resolve_cwd(cwd)

        timeout = max(1, int(timeout_s))
        if on_stdout_chunk is None and on_stderr_chunk is None:
            proc = subprocess.run(
                ["/bin/zsh", "-lc", command],
                cwd=str(resolved_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout_raw = proc.stdout or ""
            stderr_raw = proc.stderr or ""
            returncode = int(proc.returncode)
        else:
            proc = subprocess.Popen(
                ["/bin/zsh", "-lc", command],
                cwd=str(resolved_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []

            stdout_reader = threading.Thread(
                target=self._read_stream,
                args=(proc.stdout, stdout_chunks, on_stdout_chunk),
                daemon=True,
            )
            stderr_reader = threading.Thread(
                target=self._read_stream,
                args=(proc.stderr, stderr_chunks, on_stderr_chunk),
                daemon=True,
            )
            stdout_reader.start()
            stderr_reader.start()

            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()

            stdout_reader.join()
            stderr_reader.join()

            stdout_raw = "".join(stdout_chunks)
            stderr_raw = "".join(stderr_chunks)
            if timed_out:
                timeout_msg = f"Command timed out after {timeout}s."
                stderr_raw = f"{stderr_raw}\n{timeout_msg}" if stderr_raw else timeout_msg
                self._emit_chunk(on_stderr_chunk, f"\n{timeout_msg}")
            returncode = int(proc.returncode if proc.returncode is not None else (124 if timed_out else 1))

        stdout, out_truncated = self._truncate(stdout_raw)
        stderr, err_truncated = self._truncate(stderr_raw)
        return ShellResult(
            command=command,
            returncode=returncode,
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
