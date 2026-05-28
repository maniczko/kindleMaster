from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_REPAIRING_HEADINGS = "repairing_headings"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_TIMED_OUT = "timed_out"

ACTIVE_CONVERSION_JOB_STATUSES = frozenset(
    {STATUS_QUEUED, STATUS_RUNNING, STATUS_REPAIRING_HEADINGS}
)
TERMINAL_CONVERSION_JOB_STATUSES = frozenset({STATUS_READY, STATUS_FAILED, STATUS_TIMED_OUT})


@dataclass(frozen=True)
class ConversionQueuePolicy:
    max_active_jobs: int = 2
    max_runtime_seconds: int = 60 * 60
    max_stale_seconds: int = 12 * 60
    default_poll_interval_ms: int = 1500
    max_poll_interval_ms: int = 5000


DEFAULT_CONVERSION_QUEUE_POLICY = ConversionQueuePolicy()


def normalize_job_status(value: Any, *, default: str = STATUS_QUEUED) -> str:
    normalized = str(value or default).strip().lower()
    return normalized or default


def is_active_conversion_status(value: Any) -> bool:
    return normalize_job_status(value) in ACTIVE_CONVERSION_JOB_STATUSES


def is_terminal_conversion_status(value: Any) -> bool:
    return normalize_job_status(value) in TERMINAL_CONVERSION_JOB_STATUSES


def parse_job_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def utc_now_label(*, now: datetime | None = None) -> str:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    return current_time.isoformat().replace("+00:00", "Z")


def compute_job_elapsed_seconds(job: Mapping[str, Any], *, now: datetime | None = None) -> int | None:
    created_at = parse_job_timestamp(str(job.get("created_at", "") or ""))
    if not created_at:
        return None
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    return max(0, int((current_time - created_at).total_seconds()))


def compute_job_history_elapsed_seconds(job: Mapping[str, Any], *, now: datetime | None = None) -> int | None:
    created_at = parse_job_timestamp(str(job.get("created_at", "") or ""))
    if not created_at:
        return None

    status = normalize_job_status(job.get("status"))
    finished_at = None
    if status in TERMINAL_CONVERSION_JOB_STATUSES:
        finished_at = parse_job_timestamp(str(job.get("updated_at", "") or ""))
    end_at = finished_at or now or datetime.now(UTC)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=UTC)
    return max(0, int((end_at - created_at).total_seconds()))


def recommended_poll_interval_ms(
    job: Mapping[str, Any],
    *,
    policy: ConversionQueuePolicy = DEFAULT_CONVERSION_QUEUE_POLICY,
    now: datetime | None = None,
) -> int:
    status = normalize_job_status(job.get("status"))
    if status in TERMINAL_CONVERSION_JOB_STATUSES:
        return 0

    elapsed_seconds = compute_job_elapsed_seconds(job, now=now) or 0
    if status == STATUS_QUEUED:
        return 1200
    if status == STATUS_RUNNING:
        return policy.max_poll_interval_ms
    if elapsed_seconds >= 240:
        return policy.max_poll_interval_ms
    if elapsed_seconds >= 120:
        return 3500
    if elapsed_seconds >= 45 or status == STATUS_REPAIRING_HEADINGS:
        return 2500
    return policy.default_poll_interval_ms


def count_active_conversion_jobs(jobs: Mapping[str, Mapping[str, Any]]) -> int:
    return sum(1 for job in jobs.values() if is_active_conversion_status(job.get("status")))


def should_timeout_job(
    job: Mapping[str, Any],
    *,
    now: datetime,
    policy: ConversionQueuePolicy = DEFAULT_CONVERSION_QUEUE_POLICY,
) -> tuple[bool, int | None, int | None]:
    if not is_active_conversion_status(job.get("status")):
        return (False, None, None)
    current_time = now if now.tzinfo else now.replace(tzinfo=UTC)
    created_at = parse_job_timestamp(str(job.get("created_at", "") or ""))
    updated_at = parse_job_timestamp(str(job.get("updated_at", "") or "")) or created_at
    runtime_seconds = int((current_time - created_at).total_seconds()) if created_at else 0
    stale_seconds = int((current_time - updated_at).total_seconds()) if updated_at else 0
    timed_out = (
        runtime_seconds > policy.max_runtime_seconds
        or stale_seconds > policy.max_stale_seconds
    )
    return (timed_out, runtime_seconds if created_at else None, stale_seconds if updated_at else None)


def build_timed_out_job_fields(*, now: datetime, message: str, error: str) -> dict[str, Any]:
    return {
        "status": STATUS_TIMED_OUT,
        "message": message,
        "error": error,
        "error_code": "conversion_timeout",
        "output_size_bytes": 0,
        "source_path": "",
        "updated_at": utc_now_label(now=now),
    }
