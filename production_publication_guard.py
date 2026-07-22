from __future__ import annotations

import builtins
import os
import tempfile
import threading
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO

_TERMINAL_NON_READY = frozenset({"failed", "timed_out", "cancelled"})
_REQUIRED_QUALITY_ARTIFACTS = frozenset({"report_json", "report_markdown", "log"})


class QualityArtifactPublicationError(RuntimeError):
    error_code = "quality_artifact_storage_failed"


class AtomicBinaryPublisher:
    """Write a binary artifact beside its target and publish it with os.replace."""

    def __init__(self, target: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        self.target = Path(target)
        self.args = args
        self.kwargs = kwargs
        self.temp_path: Path | None = None
        self.handle: BinaryIO | None = None

    def __enter__(self) -> BinaryIO:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp_path = tempfile.mkstemp(
            prefix=f".{self.target.name}.",
            suffix=".tmp",
            dir=self.target.parent,
        )
        os.close(descriptor)
        self.temp_path = Path(raw_temp_path)
        try:
            self.handle = builtins.open(self.temp_path, "wb", *self.args, **self.kwargs)
        except Exception:
            self.temp_path.unlink(missing_ok=True)
            raise
        return self.handle

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        handle = self.handle
        temp_path = self.temp_path
        try:
            if handle is not None and not handle.closed:
                if exc_type is None:
                    handle.flush()
                    os.fsync(handle.fileno())
                handle.close()
            if temp_path is None:
                return False
            if exc_type is not None:
                temp_path.unlink(missing_ok=True)
                return False
            os.replace(temp_path, self.target)
            self._fsync_directory(self.target.parent)
            return False
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            # Some filesystems do not support directory fsync.
            pass
        finally:
            os.close(descriptor)


def _production_epub_target(app_module: ModuleType, file: Any, mode: str) -> bool:
    if str(mode or "") != "wb":
        return False
    try:
        target = Path(os.fspath(file)).resolve()
        upload_root = Path(app_module.UPLOAD_DIR).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return target.parent == upload_root and target.suffix.lower() == ".epub"


def install_atomic_epub_writer(app_module: ModuleType) -> None:
    """Intercept only hosted worker EPUB output writes; delegate every other open."""

    current = getattr(app_module, "open", builtins.open)
    if getattr(current, "_kindlemaster_atomic_epub_writer", False):
        return

    original_open = current

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        if _production_epub_target(app_module, file, mode):
            return AtomicBinaryPublisher(file, *args, **kwargs)
        return original_open(file, mode, *args, **kwargs)

    guarded_open._kindlemaster_atomic_epub_writer = True
    guarded_open._kindlemaster_original_open = original_open
    app_module.open = guarded_open


def install_ready_after_quality_gate(app_module: ModuleType) -> None:
    """Keep a job active until every required quality artifact is durable."""

    current_setter = app_module._set_conversion_job
    if getattr(current_setter, "_kindlemaster_ready_after_quality", False):
        return

    original_setter = current_setter
    original_getter = app_module._get_conversion_job
    original_quality_writer = app_module._store_quality_report_artifacts
    pending_ready: dict[str, dict[str, Any]] = {}
    lock = threading.RLock()

    def gated_setter(job_id: str, **fields: Any):
        normalized_job_id = str(job_id or "").strip()
        status = str(fields.get("status") or "").strip().lower()
        if status == "ready":
            final_fields = dict(fields)
            progress = dict(final_fields.get("progress") or {})
            progress.update(
                {
                    "stage_id": "finalizing_quality",
                    "stage_label": "Utrwalanie raportów jakości",
                    "status": "running",
                    "message": "Utrwalanie raportów jakości przed publikacją...",
                    "percent_estimate": 99,
                }
            )
            with lock:
                pending_ready[normalized_job_id] = final_fields
            interim_fields = dict(final_fields)
            interim_fields.update(
                {
                    "status": "running",
                    "message": "Utrwalanie raportów jakości przed publikacją...",
                    "progress": progress,
                }
            )
            return original_setter(job_id, **interim_fields)

        if status in _TERMINAL_NON_READY:
            with lock:
                pending_ready.pop(normalized_job_id, None)
        return original_setter(job_id, **fields)

    def gated_quality_writer(job_id: str, *args: Any, **kwargs: Any):
        normalized_job_id = str(job_id or "").strip()
        with lock:
            final_fields = pending_ready.get(normalized_job_id)
        if final_fields is None:
            return original_quality_writer(job_id, *args, **kwargs)

        def quality_view_getter(requested_job_id: str):
            current = original_getter(requested_job_id)
            if str(requested_job_id or "").strip() != normalized_job_id or not current:
                return current
            synthetic = dict(current)
            synthetic.update(final_fields)
            synthetic["status"] = "ready"
            return synthetic

        with lock:
            previous_getter = app_module._get_conversion_job
            app_module._get_conversion_job = quality_view_getter
            try:
                result = original_quality_writer(job_id, *args, **kwargs)
            finally:
                app_module._get_conversion_job = previous_getter

            persisted = original_getter(job_id) or {}
            artifacts = dict(persisted.get("artifacts") or {})
            missing = sorted(_REQUIRED_QUALITY_ARTIFACTS - set(artifacts))
            report_error = artifacts.get("report_error")
            if missing or report_error:
                details = ", ".join(missing) if missing else "report_error"
                raise QualityArtifactPublicationError(
                    f"Required quality artifacts are not durable: {details}."
                )

            final_fields = pending_ready.pop(normalized_job_id, None)
            if final_fields is not None:
                publish_fields = dict(final_fields)
                publish_fields["artifacts"] = artifacts
                publish_fields["artifact_storage"] = dict(
                    persisted.get("artifact_storage")
                    or publish_fields.get("artifact_storage")
                    or {}
                )
                original_setter(job_id, **publish_fields)
            return result

    gated_setter._kindlemaster_ready_after_quality = True
    gated_setter._kindlemaster_original_setter = original_setter
    gated_quality_writer._kindlemaster_ready_after_quality = True
    gated_quality_writer._kindlemaster_original_quality_writer = original_quality_writer
    app_module._set_conversion_job = gated_setter
    app_module._store_quality_report_artifacts = gated_quality_writer
    app_module._PRODUCTION_PENDING_READY = pending_ready


def install_production_publication_guard(app_module: ModuleType) -> None:
    install_atomic_epub_writer(app_module)
    install_ready_after_quality_gate(app_module)
