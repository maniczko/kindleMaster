from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


PGN_EXTRACT_PAYLOAD_VERSION = "kindlemaster.pgn_extract.v1"
DEFAULT_PGN_EXTRACT_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class PgnExtractResult:
    available: bool
    returncode: int | None = None
    stdout_pgn: str = ""
    stderr: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    runtime_ms: int = 0
    tool_version: str = ""
    command: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "payload_version": PGN_EXTRACT_PAYLOAD_VERSION,
            "available": self.available,
            "returncode": self.returncode,
            "stdout_pgn": self.stdout_pgn,
            "stderr": self.stderr,
            "warnings": list(self.warnings),
            "runtime_ms": self.runtime_ms,
            "tool_version": self.tool_version,
            "command": list(self.command),
        }


def pgn_extract_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ or os.environ
    return str(env.get("KINDLEMASTER_PGN_EXTRACT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def pgn_extract_mode(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    mode = str(env.get("KINDLEMASTER_PGN_EXTRACT_MODE") or "audit").strip().lower()
    return mode if mode in {"audit", "format_accepted"} else "audit"


def pgn_extract_tool_path(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    return str(env.get("KINDLEMASTER_PGN_EXTRACT_PATH") or "pgn-extract").strip() or "pgn-extract"


def pgn_extract_timeout_ms(environ: Mapping[str, str] | None = None) -> int:
    env = environ or os.environ
    raw = str(env.get("KINDLEMASTER_PGN_EXTRACT_TIMEOUT_MS") or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PGN_EXTRACT_TIMEOUT_MS
    return max(250, min(value, 120000))


def probe_pgn_extract_tool(
    tool_path: str | None = None,
    *,
    timeout_ms: int | None = None,
) -> PgnExtractResult:
    resolved = _resolve_tool_path(tool_path or pgn_extract_tool_path())
    if not resolved:
        return PgnExtractResult(available=False, warnings=("pgn_extract_unavailable",))
    timeout_seconds = (timeout_ms if timeout_ms is not None else pgn_extract_timeout_ms()) / 1000
    started = time.perf_counter()
    for flag in ("--version", "--help"):
        try:
            completed = subprocess.run(
                [resolved, flag],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return PgnExtractResult(
                available=True,
                warnings=("pgn_extract_timeout",),
                runtime_ms=_elapsed_ms(started),
                command=(resolved, flag),
            )
        except OSError as exc:
            return PgnExtractResult(
                available=False,
                stderr=str(exc),
                warnings=("pgn_extract_unavailable",),
                runtime_ms=_elapsed_ms(started),
                command=(resolved, flag),
            )
        output = (completed.stdout or completed.stderr or "").strip()
        if output:
            first_line = output.splitlines()[0].strip()
            return PgnExtractResult(
                available=True,
                returncode=completed.returncode,
                stderr=_truncate_stderr(completed.stderr),
                warnings=(),
                runtime_ms=_elapsed_ms(started),
                tool_version=first_line,
                command=(resolved, flag),
            )
    return PgnExtractResult(available=True, warnings=("pgn_extract_available",), runtime_ms=_elapsed_ms(started))


def run_pgn_extract(
    pgn_text: str,
    *,
    tool_path: str | None = None,
    timeout_ms: int | None = None,
) -> PgnExtractResult:
    resolved = _resolve_tool_path(tool_path or pgn_extract_tool_path())
    if not resolved:
        return PgnExtractResult(available=False, warnings=("pgn_extract_unavailable",))
    timeout_seconds = (timeout_ms if timeout_ms is not None else pgn_extract_timeout_ms()) / 1000
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="kindlemaster-pgn-extract-") as temp_dir:
        input_path = Path(temp_dir) / "input.pgn"
        input_path.write_text(str(pgn_text or ""), encoding="utf-8")
        command = (resolved, str(input_path))
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return PgnExtractResult(
                available=True,
                stdout_pgn=exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=_truncate_stderr(
                    exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                ),
                warnings=("pgn_extract_timeout",),
                runtime_ms=_elapsed_ms(started),
                command=command,
            )
        except OSError as exc:
            return PgnExtractResult(
                available=False,
                stderr=str(exc),
                warnings=("pgn_extract_unavailable",),
                runtime_ms=_elapsed_ms(started),
                command=command,
            )
    warnings: list[str] = ["pgn_extract_available"]
    if completed.returncode != 0:
        warnings.append("pgn_extract_nonzero_exit")
    return PgnExtractResult(
        available=True,
        returncode=completed.returncode,
        stdout_pgn=completed.stdout or "",
        stderr=_truncate_stderr(completed.stderr),
        warnings=tuple(warnings),
        runtime_ms=_elapsed_ms(started),
        command=command,
    )


def _resolve_tool_path(tool_path: str) -> str:
    configured = str(tool_path or "").strip()
    if not configured:
        return ""
    if any(sep in configured for sep in ("/", "\\")):
        path = Path(configured)
        return str(path) if path.exists() else ""
    return shutil.which(configured) or ""


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _truncate_stderr(stderr: str | None, limit: int = 4000) -> str:
    value = str(stderr or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"
