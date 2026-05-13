from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
import os
from pathlib import Path
import subprocess
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


@dataclass(frozen=True)
class TriggerDevConfig:
    enabled: bool = False
    task_id: str = "kindlemaster-conversion"
    script_path: str = "scripts/trigger_conversion_job.mjs"
    project_ref: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TriggerDevConfig":
        source = os.environ if env is None else env
        enabled = str(source.get("KINDLEMASTER_TRIGGER_ENABLED", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        secret_key = str(source.get("TRIGGER_SECRET_KEY", "") or "").strip()
        return cls(
            enabled=enabled and bool(secret_key),
            task_id=str(source.get("KINDLEMASTER_TRIGGER_TASK_ID", "") or "").strip() or "kindlemaster-conversion",
            script_path=str(source.get("KINDLEMASTER_TRIGGER_SCRIPT", "") or "").strip() or "scripts/trigger_conversion_job.mjs",
            project_ref=str(source.get("TRIGGER_PROJECT_REF", "") or "").strip(),
        )


class TriggerDevRuntimeJobAdapter(LocalRuntimeJobAdapter):
    """Submit conversion metadata to Trigger.dev while keeping local metadata semantics."""

    def __init__(
        self,
        *,
        config: TriggerDevConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: int = 30 * 60,
        command_runner: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__(provider="trigger.dev", retry_policy=retry_policy, timeout_seconds=timeout_seconds)
        self.config = config or TriggerDevConfig.from_env()
        self._command_runner = command_runner or _run_trigger_command

    def submit(self, command: ReplayableCommand, *, job_id: str | None = None) -> RuntimeJobHandle:
        handle = super().submit(command, job_id=job_id)
        if not self.config.enabled:
            return self.update_status(handle.job_id, RuntimeJobStatus.QUEUED, message="Trigger.dev is not configured.")

        payload = {
            "job_id": handle.job_id,
            "task_id": self.config.task_id,
            "retry_policy": self.retry_policy.to_metadata(),
            "timeout_seconds": self.timeout_seconds,
            "replay": command.to_metadata(),
        }
        output = self._command_runner(self.config.script_path, json.dumps(payload, ensure_ascii=False))
        if output.strip().startswith("{"):
            response = json.loads(output)
        else:
            response = {}
        external_id = str(response.get("external_id") or response.get("id") or "")
        if not external_id:
            raise RuntimeJobExecutionError("Trigger.dev did not return a run id.")
        return self.update_status(
            handle.job_id,
            RuntimeJobStatus.QUEUED,
            external_id=external_id,
            message="Submitted to Trigger.dev.",
        )


def build_runtime_job_adapter(
    *,
    env: Mapping[str, str] | None = None,
    retry_policy: RetryPolicy | None = None,
    timeout_seconds: int = 30 * 60,
) -> LocalRuntimeJobAdapter:
    config = TriggerDevConfig.from_env(env)
    if config.enabled:
        return TriggerDevRuntimeJobAdapter(config=config, retry_policy=retry_policy, timeout_seconds=timeout_seconds)
    return LocalRuntimeJobAdapter(retry_policy=retry_policy, timeout_seconds=timeout_seconds)


def _run_trigger_command(script_path: str, stdin_payload: str) -> str:
    script = Path(script_path)
    if not script.exists():
        raise RuntimeJobExecutionError(f"Trigger.dev script is missing: {script}")
    completed = subprocess.run(
        ["node", str(script)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeJobExecutionError((completed.stderr or completed.stdout or "Trigger.dev submission failed.").strip())
    return completed.stdout


def _utc_now_label() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RuntimeJobExecutionError(RuntimeError):
    pass
