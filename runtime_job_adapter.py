from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable, Mapping
from uuid import uuid4


class RuntimeJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: int = 0
    retryable_statuses: tuple[str, ...] = ("failed", "timed_out")

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be at least 1.")
        if self.backoff_seconds < 0:
            raise ValueError("RetryPolicy.backoff_seconds cannot be negative.")

    def to_metadata(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_seconds": self.backoff_seconds,
            "retryable_statuses": list(self.retryable_statuses),
        }


@dataclass(frozen=True)
class ReplayableCommand:
    name: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("ReplayableCommand.name is required.")

    def to_metadata(self) -> dict[str, Any]:
        return {
            "command": {
                "name": self.name,
                "args": list(self.args),
                "kwargs": dict(self.kwargs),
            },
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class RuntimeJobHandle:
    job_id: str
    provider: str
    external_id: str
    status: RuntimeJobStatus
    retry_policy: RetryPolicy
    timeout_seconds: int
    replayable: ReplayableCommand
    created_at: str
    updated_at: str
    attempt: int = 1
    message: str = ""
    error: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "provider": self.provider,
            "external_id": self.external_id,
            "status": self.status.value,
            "attempt": self.attempt,
            "retry_policy": self.retry_policy.to_metadata(),
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message": self.message,
            "error": self.error,
            "replay": self.replayable.to_metadata(),
        }


class LocalRuntimeJobAdapter:
    """Metadata-first runtime adapter with local execution as the default.

    The adapter deliberately does not launch subprocesses or contact external
    providers. It gives callers a stable job contract they can persist today
    and later replay through a remote runner without changing job metadata.
    """

    def __init__(
        self,
        *,
        provider: str = "local",
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: int = 30 * 60,
    ) -> None:
        normalized_provider = str(provider or "").strip() or "local"
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive.")
        self.provider = normalized_provider
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_seconds = timeout_seconds
        self._jobs: dict[str, RuntimeJobHandle] = {}

    def submit(self, command: ReplayableCommand, *, job_id: str | None = None) -> RuntimeJobHandle:
        normalized_job_id = str(job_id or "").strip() or f"job-{uuid4().hex}"
        now = _utc_now_label()
        handle = RuntimeJobHandle(
            job_id=normalized_job_id,
            provider=self.provider,
            external_id="",
            status=RuntimeJobStatus.QUEUED,
            retry_policy=self.retry_policy,
            timeout_seconds=self.timeout_seconds,
            replayable=command,
            created_at=now,
            updated_at=now,
        )
        self._jobs[normalized_job_id] = handle
        return handle

    def run_local(
        self,
        command: ReplayableCommand,
        runner: Callable[[ReplayableCommand], Any],
        *,
        job_id: str | None = None,
    ) -> tuple[RuntimeJobHandle, Any]:
        handle = self.submit(command, job_id=job_id)
        self.update_status(handle.job_id, RuntimeJobStatus.RUNNING)
        try:
            result = runner(command)
        except Exception as error:
            self.update_status(handle.job_id, RuntimeJobStatus.FAILED, error=str(error))
            raise RuntimeJobExecutionError(f"Local runtime job failed: {handle.job_id}") from error
        succeeded = self.update_status(handle.job_id, RuntimeJobStatus.SUCCEEDED)
        return succeeded, result

    def get(self, job_id: str) -> RuntimeJobHandle | None:
        return self._jobs.get(str(job_id or "").strip())

    def update_status(
        self,
        job_id: str,
        status: RuntimeJobStatus | str,
        *,
        external_id: str | None = None,
        message: str | None = None,
        error: str | None = None,
        attempt: int | None = None,
    ) -> RuntimeJobHandle:
        normalized_job_id = str(job_id or "").strip()
        current = self._jobs.get(normalized_job_id)
        if current is None:
            raise KeyError(f"Unknown runtime job: {normalized_job_id}")

        normalized_status = status if isinstance(status, RuntimeJobStatus) else RuntimeJobStatus(str(status))
        updated = RuntimeJobHandle(
            job_id=current.job_id,
            provider=current.provider,
            external_id=current.external_id if external_id is None else str(external_id),
            status=normalized_status,
            retry_policy=current.retry_policy,
            timeout_seconds=current.timeout_seconds,
            replayable=current.replayable,
            created_at=current.created_at,
            updated_at=_utc_now_label(),
            attempt=current.attempt if attempt is None else max(1, attempt),
            message=current.message if message is None else str(message),
            error=current.error if error is None else str(error),
        )
        self._jobs[normalized_job_id] = updated
        return updated


def _utc_now_label() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RuntimeJobExecutionError(RuntimeError):
    pass
