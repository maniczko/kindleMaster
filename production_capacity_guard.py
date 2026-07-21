from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class MemoryAdmissionPolicy:
    enabled: bool = True
    min_available_bytes: int = 256 * 1024 * 1024
    min_available_ratio: float = 0.10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MemoryAdmissionPolicy":
        source = os.environ if env is None else env
        enabled = str(source.get("KINDLEMASTER_MEMORY_ADMISSION", "1") or "1").strip().lower()
        return cls(
            enabled=enabled not in {"0", "false", "no", "off"},
            min_available_bytes=max(
                1,
                int(
                    source.get(
                        "KINDLEMASTER_MIN_MEMORY_AVAILABLE_BYTES",
                        str(256 * 1024 * 1024),
                    )
                    or 256 * 1024 * 1024
                ),
            ),
            min_available_ratio=max(
                0.0,
                float(source.get("KINDLEMASTER_MIN_MEMORY_AVAILABLE_RATIO", "0.10") or 0.10),
            ),
        )


@dataclass(frozen=True)
class MemoryHeadroom:
    measurable: bool
    allowed: bool
    total_bytes: int
    available_bytes: int
    available_ratio: float
    source: str


def _read_integer(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _cgroup_v2_memory(root: Path) -> tuple[int, int] | None:
    limit = _read_integer(root / "memory.max")
    current = _read_integer(root / "memory.current")
    if limit is None or current is None or current > limit:
        return None
    return limit, max(0, limit - current)


def _cgroup_v1_memory(root: Path) -> tuple[int, int] | None:
    limit = _read_integer(root / "memory.limit_in_bytes")
    current = _read_integer(root / "memory.usage_in_bytes")
    if limit is None or current is None or current > limit:
        return None
    # Very large v1 limits usually mean no effective cgroup limit.
    if limit >= 1 << 60:
        return None
    return limit, max(0, limit - current)


def _proc_meminfo(path: Path) -> tuple[int, int] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    values: dict[str, int] = {}
    for line in lines:
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        amount = remainder.strip().split()[0] if remainder.strip() else ""
        try:
            values[key] = int(amount) * 1024
        except ValueError:
            continue
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    if total <= 0 or available < 0:
        return None
    return total, min(total, available)


def memory_headroom(
    policy: MemoryAdmissionPolicy,
    *,
    cgroup_v2_root: str | os.PathLike[str] = "/sys/fs/cgroup",
    cgroup_v1_root: str | os.PathLike[str] = "/sys/fs/cgroup/memory",
    proc_meminfo: str | os.PathLike[str] = "/proc/meminfo",
) -> MemoryHeadroom:
    if not policy.enabled:
        return MemoryHeadroom(False, True, 0, 0, 0.0, "disabled")

    measurement = _cgroup_v2_memory(Path(cgroup_v2_root))
    source = "cgroup-v2"
    if measurement is None:
        measurement = _cgroup_v1_memory(Path(cgroup_v1_root))
        source = "cgroup-v1"
    if measurement is None:
        measurement = _proc_meminfo(Path(proc_meminfo))
        source = "proc-meminfo"
    if measurement is None:
        # Missing telemetry must be visible to observability, but should not make
        # a supported local environment unusable.
        return MemoryHeadroom(False, True, 0, 0, 0.0, "unavailable")

    total, available = measurement
    ratio = available / max(1, total)
    allowed = available >= policy.min_available_bytes and ratio >= policy.min_available_ratio
    return MemoryHeadroom(True, allowed, total, available, ratio, source)


def install_memory_admission_guard(
    app_module: Any,
    *,
    policy: MemoryAdmissionPolicy | None = None,
) -> None:
    from flask import request

    policy = policy or MemoryAdmissionPolicy.from_env()

    @app_module.app.before_request
    def enforce_memory_headroom():
        if request.method != "POST" or request.path not in {"/convert/start", "/convert"}:
            return None
        decision = memory_headroom(policy)
        if decision.allowed:
            return None
        response = app_module._json_error(
            "Brak bezpiecznego zapasu pamięci na rozpoczęcie konwersji.",
            error_code="memory_capacity_exceeded",
            status_code=503,
            phase="admission",
            retryable=True,
            extra={"retry_after_seconds": 30},
        )
        response.headers["Retry-After"] = "30"
        return response

    app_module._PRODUCTION_MEMORY_POLICY = policy
