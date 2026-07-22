from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import threading
import time
from typing import Any
from uuid import uuid4

from durable_job_queue import DurableJob, DurableJobStatus
from durable_runtime_integration import build_queue
from durable_runtime_store import RuntimeDurableJobQueue


_RETRYABLE_ERROR_CODES = {
    "conversion_failed",
    "queue_failed",
    "artifact_storage_unavailable",
    "conversion_job_cloud_sync_failed",
}


class DurableConversionWorker:
    def __init__(
        self,
        *,
        app_module: Any,
        queue: RuntimeDurableJobQueue,
        worker_id: str = "",
        lease_seconds: int = 180,
        heartbeat_seconds: int = 15,
        poll_seconds: float = 1.0,
    ) -> None:
        self.app_module = app_module
        self.queue = queue
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        )
        self.lease_seconds = max(30, int(lease_seconds))
        self.heartbeat_seconds = max(2, int(heartbeat_seconds))
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            processed = self.run_once()
            if not processed:
                self._stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        self.queue.requeue_expired()
        durable = self.queue.claim(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if durable is None:
            return False
        try:
            self.queue.start(durable.job_id, worker_id=self.worker_id)
            self._execute_conversion(durable)
        except Exception as error:
            current = self.queue.get(durable.job_id)
            if (
                current is not None
                and current.worker_id == self.worker_id
                and current.status in {DurableJobStatus.LEASED, DurableJobStatus.RUNNING}
            ):
                source_exists = Path(
                    str(current.payload.get("source_path") or "")
                ).exists()
                failed = self.queue.fail_with_payload(
                    durable.job_id,
                    worker_id=self.worker_id,
                    retryable=source_exists,
                    error_code=str(getattr(error, "error_code", "") or "worker_execution_failed"),
                    error_message=str(error),
                    backoff_seconds=_backoff_seconds(current.attempt),
                    payload_patch={"worker_exception_class": error.__class__.__name__},
                )
                if failed.status in {
                    DurableJobStatus.FAILED,
                    DurableJobStatus.DEAD_LETTER,
                }:
                    _cleanup_source(failed)
        return True

    def _execute_conversion(self, durable: DurableJob) -> None:
        payload = durable.payload
        if payload.get("kind") != "conversion":
            raise ValueError("Unsupported durable job kind.")
        source = Path(str(payload.get("source_path") or ""))
        if not source.is_file():
            raise FileNotFoundError("Durable conversion source is missing.")

        attempt_source = Path(self.app_module.UPLOAD_DIR) / (
            f"{durable.job_id}-attempt-{durable.attempt}{source.suffix}"
        )
        attempt_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, attempt_source)

        job_record = dict(payload.get("job_record") or {})
        job_record.update(
            {
                "job_id": durable.job_id,
                "source_path": str(attempt_source),
                "status": "queued",
                "message": "Worker przejął zadanie z trwałej kolejki.",
            }
        )
        self.app_module._CONVERSION_JOB_STORE.create(job_record)

        self.app_module._spawn_conversion_job(
            job_id=durable.job_id,
            source_path=str(attempt_source),
            source_type=str(payload["source_type"]),
            original_filename=str(payload["original_filename"]),
            profile=str(payload["profile"]),
            force_ocr=bool(payload["force_ocr"]),
            language=str(payload["language"]),
            heading_repair_enabled=bool(payload["heading_repair_enabled"]),
            route_model_mode=str(payload.get("route_model_mode") or "shadow"),
            quality_gate_mode=str(payload.get("quality_gate_mode") or "draft"),
            cloud_user_id=str(payload.get("cloud_user_id") or ""),
            cloud_token="",
        )

        last_heartbeat = 0.0
        while not self._stop.is_set():
            queue_state = self.queue.get(durable.job_id)
            if queue_state is None:
                return
            if queue_state.status == DurableJobStatus.CANCELLED:
                self.app_module._set_conversion_job(
                    durable.job_id,
                    status="cancelled",
                    message="Konwersja została anulowana.",
                )
                _cleanup_source(queue_state)
                return

            local_job = self.app_module._get_conversion_job(durable.job_id) or {}
            now = time.monotonic()
            if now - last_heartbeat >= self.heartbeat_seconds:
                self.queue.heartbeat_with_payload(
                    durable.job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                    payload_patch={"job_record": dict(local_job)},
                )
                last_heartbeat = now

            status = str(local_job.get("status") or "").lower()
            if status == "ready":
                completed = self.queue.complete_with_payload(
                    durable.job_id,
                    worker_id=self.worker_id,
                    payload_patch={"job_record": dict(local_job)},
                )
                _cleanup_source(completed)
                return
            if status in {"failed", "timed_out"}:
                error_code = str(local_job.get("error_code") or "conversion_failed")
                retryable = error_code in _RETRYABLE_ERROR_CODES and source.exists()
                failed = self.queue.fail_with_payload(
                    durable.job_id,
                    worker_id=self.worker_id,
                    retryable=retryable,
                    error_code=error_code,
                    error_message=str(local_job.get("error") or local_job.get("message") or ""),
                    backoff_seconds=_backoff_seconds(queue_state.attempt),
                    payload_patch={"job_record": dict(local_job)},
                )
                if failed.status in {
                    DurableJobStatus.FAILED,
                    DurableJobStatus.DEAD_LETTER,
                }:
                    _cleanup_source(failed)
                return
            self._stop.wait(self.poll_seconds)


def _backoff_seconds(attempt: int) -> int:
    return min(15 * (2 ** max(0, int(attempt) - 1)), 15 * 60)


def _cleanup_source(job: DurableJob) -> None:
    source = Path(str(job.payload.get("source_path") or ""))
    if source:
        source.unlink(missing_ok=True)


def main() -> None:
    import app as app_module

    worker = DurableConversionWorker(
        app_module=app_module,
        queue=build_queue(app_module),
        lease_seconds=int(os.environ.get("KINDLEMASTER_DURABLE_LEASE_SECONDS", "180")),
        heartbeat_seconds=int(os.environ.get("KINDLEMASTER_DURABLE_HEARTBEAT_SECONDS", "15")),
        poll_seconds=float(os.environ.get("KINDLEMASTER_DURABLE_POLL_SECONDS", "1")),
    )
    worker.run_forever()


if __name__ == "__main__":
    main()
