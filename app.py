"""
KindleMaster — PDF to EPUB Converter
=====================================
Production-grade PDF to EPUB conversion with maximum visual fidelity.
"""

import io
import json
import os
import threading
import uuid
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from app_runtime_services import (
    DEFAULT_DEBUG,
    DEFAULT_PORT,
    LOCALHOST,
    ConversionRequest,
    ConversionJobStore,
    build_conversion_job_record,
    build_conversion_quality_state,
    enrich_conversion_metadata_with_output_size,
    build_local_app_url,
    detect_supported_source_type,
    resolve_debug_mode as runtime_resolve_debug_mode,
    resolve_server_port as runtime_resolve_server_port,
    run_document_conversion,
    serve_http_app,
)
from artifact_storage import ArtifactKind, build_artifact_storage
from conversion_api_contracts import (
    ConversionDownloadState,
    ERROR_MISSING_OUTPUT,
    ERROR_QUEUE_FAILED,
    ERROR_UNSUPPORTED_REPORT_FORMAT,
    ERROR_UPLOAD_FAILED,
    apply_no_store_headers,
    build_json_error_payload,
    resolve_conversion_download_state,
)
from conversion_jobs import (
    ACTIVE_CONVERSION_JOB_STATUSES,
    DEFAULT_CONVERSION_QUEUE_POLICY,
    TERMINAL_CONVERSION_JOB_STATUSES,
    build_timed_out_job_fields,
    compute_job_elapsed_seconds as lifecycle_compute_job_elapsed_seconds,
    compute_job_history_elapsed_seconds as lifecycle_compute_job_history_elapsed_seconds,
    count_active_conversion_jobs,
    is_active_conversion_status,
    recommended_poll_interval_ms as lifecycle_recommended_poll_interval_ms,
    should_timeout_job,
)
from conversion_library import (
    LibraryFilters,
    build_library_index,
    build_quality_report_payload,
    render_quality_report_markdown,
)
from flask import Flask, request, jsonify, render_template, redirect, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from converter import convert_document_to_epub_with_report, detect_pdf_type
from docx_conversion import analyze_docx
from epub_heading_repair import repair_epub_headings_and_toc
from publication_analysis import analyze_publication
from sentry_observability import (
    build_conversion_context,
    capture_conversion_exception,
    configure_sentry_backend,
)
from runtime_job_adapter import ReplayableCommand, RetryPolicy, RuntimeJobStatus, build_runtime_job_adapter

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB
SENTRY_BACKEND_STATE = configure_sentry_backend()

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "kindlemaster")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ARTIFACT_STORAGE = build_artifact_storage(local_root=Path("output") / "artifacts")
RUNTIME_JOB_ADAPTER = build_runtime_job_adapter(
    retry_policy=RetryPolicy(max_attempts=1),
    timeout_seconds=DEFAULT_CONVERSION_QUEUE_POLICY.max_runtime_seconds,
)

DEFAULT_CONVERSION_POLL_INTERVAL_MS = 1500
MAX_CONVERSION_POLL_INTERVAL_MS = 5000
DEFAULT_CONVERSION_JOB_HISTORY_LIMIT = 25
MAX_CONVERSION_JOB_HISTORY_LIMIT = 100
OVERSIZED_EPUB_WARNING_BYTES = 25 * 1024 * 1024
CONVERSION_JOB_RETENTION_SECONDS = 6 * 60 * 60
CONVERSION_TEMP_FILE_RETENTION_SECONDS = 12 * 60 * 60
CONVERSION_CLEANUP_MIN_INTERVAL_SECONDS = 60
MAX_ACTIVE_CONVERSION_JOBS = DEFAULT_CONVERSION_QUEUE_POLICY.max_active_jobs
MAX_CONVERSION_JOB_RUNTIME_SECONDS = DEFAULT_CONVERSION_QUEUE_POLICY.max_runtime_seconds
MAX_CONVERSION_JOB_STALE_SECONDS = DEFAULT_CONVERSION_QUEUE_POLICY.max_stale_seconds
CONVERSION_PROGRESS_HEARTBEAT_SECONDS = 45
CONVERSION_PROGRESS_LONG_RUNNING_SECONDS = 5 * 60
CONVERSION_PROGRESS_STALLED_SECONDS = 2 * 60
CONVERSION_PROGRESS_STAGES = {
    "queued": ("Przygotowanie", 5),
    "extracting": ("Ekstrakcja tekstu", 20),
    "assembling": ("Składanie artykułów", 45),
    "repairing_toc": ("Naprawa TOC", 65),
    "premium_audit": ("Audyt premium", 82),
    "packaging": ("Pakowanie EPUB", 94),
    "ready": ("Gotowe", 100),
    "failed": ("Błąd", 100),
    "timed_out": ("Limit czasu", 100),
}
_CONVERSION_JOBS: dict[str, dict] = {}
_CONVERSION_JOBS_LOCK = threading.Lock()
_CONVERSION_JOB_STORE = ConversionJobStore(
    _CONVERSION_JOBS,
    _CONVERSION_JOBS_LOCK,
    persistence_path=Path(UPLOAD_DIR) / "conversion_jobs.json",
    active_statuses=ACTIVE_CONVERSION_JOB_STATUSES,
)
_CONVERSION_JOB_STORE.load()
_LAST_CONVERSION_CLEANUP_AT: datetime | None = None


def _json_error(
    message: str,
    *,
    error_code: str,
    status_code: int,
    phase: str,
    job_id: str | None = None,
    retryable: bool = False,
):
    payload = build_json_error_payload(
        message,
        error_code=error_code,
        phase=phase,
        job_id=job_id,
        retryable=retryable,
    )
    response = jsonify(payload)
    response.status_code = status_code
    apply_no_store_headers(response.headers)
    return response


def _log_conversion_event(
    event: str,
    *,
    level: str = "info",
    job_id: str = "",
    phase: str = "",
    status: str = "",
    error_code: str = "",
    safe_message: str = "",
    source_type: str = "",
    elapsed_seconds: int | None = None,
    output_size_bytes: int | None = None,
    exception_class: str = "",
) -> dict:
    payload = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": event,
        "job_id": job_id,
        "phase": phase,
        "status": status,
        "error_code": error_code,
        "safe_message": safe_message,
        "source_type": source_type,
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = elapsed_seconds
    if output_size_bytes is not None:
        payload["output_size_bytes"] = output_size_bytes
    if exception_class:
        payload["exception_class"] = exception_class
    logger = getattr(app.logger, level, app.logger.info)
    logger(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return payload


def _conversion_sentry_context(
    *,
    job_id: str = "",
    source_type: str = "",
    profile: str = "",
    metadata: dict | None = None,
) -> dict:
    conversion_metadata = metadata or {}
    premium_scoring = conversion_metadata.get("premium_scoring") or {}
    if not isinstance(premium_scoring, dict):
        premium_scoring = {}
    quality_score = premium_scoring.get("premium_score")
    if quality_score is None:
        quality_score = conversion_metadata.get("quality_score")
    premium_ready = premium_scoring.get("premium_ready")
    if not isinstance(premium_ready, bool):
        premium_ready = None
    return build_conversion_context(
        job_id=job_id,
        input_type=source_type,
        source_type=source_type,
        profile=profile or str(conversion_metadata.get("profile", "") or ""),
        quality_score=quality_score,
        premium_ready=premium_ready,
    )


def _artifact_storage_status() -> dict:
    try:
        return dict(ARTIFACT_STORAGE.availability())
    except Exception as error:
        return {
            "provider": getattr(ARTIFACT_STORAGE, "provider", "unknown"),
            "status": "unavailable",
            "reason": str(error),
        }


def _store_artifact_bytes(
    *,
    job_id: str,
    kind: ArtifactKind,
    filename: str,
    data: bytes,
) -> dict:
    try:
        record = ARTIFACT_STORAGE.put_bytes(
            job_id=job_id,
            kind=kind,
            filename=filename,
            data=data,
        )
        metadata = record.to_metadata()
        if kind == ArtifactKind.OUTPUT:
            metadata["signed_url"] = ARTIFACT_STORAGE.signed_url(record)
        return metadata
    except Exception as error:
        return {
            "provider": getattr(ARTIFACT_STORAGE, "provider", "unknown"),
            "status": "failed",
            "kind": kind.value,
            "job_id": job_id,
            "filename": filename,
            "location": "",
            "size_bytes": len(data),
            "content_type": "",
            "retention": {},
            "signed_url": {"available": False, "url": "", "expires_in_seconds": 0, "reason": "store_failed"},
            "error": str(error),
        }


def _merge_job_artifacts(job_id: str, updates: dict[str, dict]) -> dict | None:
    job = _get_conversion_job(job_id)
    if not job:
        return None
    artifacts = dict(job.get("artifacts", {}) or {})
    artifacts.update(updates)
    return _set_conversion_job(
        job_id,
        artifacts=artifacts,
        artifact_storage=_artifact_storage_status(),
    )


def _store_quality_report_artifacts(job_id: str) -> None:
    job = _get_conversion_job(job_id)
    if not job or job.get("status") != "ready":
        return
    try:
        report_payload = build_quality_report_payload(
            job_id,
            job,
            quality_state=_build_job_quality_state(job_id, job),
            output_size_bytes=_read_output_size_bytes(job),
            include_text=True,
        )
        report_json = _store_artifact_bytes(
            job_id=job_id,
            kind=ArtifactKind.REPORT,
            filename=f"{job_id}.quality.json",
            data=json.dumps(report_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        report_markdown = _store_artifact_bytes(
            job_id=job_id,
            kind=ArtifactKind.REPORT,
            filename=f"{job_id}.quality.md",
            data=render_quality_report_markdown(report_payload).encode("utf-8"),
        )
        log_artifact = _store_artifact_bytes(
            job_id=job_id,
            kind=ArtifactKind.LOG,
            filename=f"{job_id}.runtime.json",
            data=json.dumps(
                {
                    "job_id": job_id,
                    "status": job.get("status", ""),
                    "runtime": job.get("runtime", {}) or {},
                    "artifact_storage": job.get("artifact_storage", {}) or {},
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
    except Exception as error:
        _merge_job_artifacts(
            job_id,
            {
                "report_error": {
                    "provider": getattr(ARTIFACT_STORAGE, "provider", "unknown"),
                    "status": "failed",
                    "kind": "report",
                    "job_id": job_id,
                    "error": str(error),
                }
            },
        )
        return
    _merge_job_artifacts(
        job_id,
        {
            "report_json": report_json,
            "report_markdown": report_markdown,
        "log": log_artifact,
        },
    )


def _ensure_quality_report_artifacts(job_id: str, job: dict) -> dict:
    if job.get("status") != "ready":
        return job
    artifacts = dict(job.get("artifacts", {}) or {})
    required = {"report_json", "report_markdown", "log"}
    if required.issubset(set(artifacts)):
        return job
    _store_quality_report_artifacts(job_id)
    return _get_conversion_job(job_id) or job


def _signed_output_artifact_url(job: dict) -> str:
    output_artifact = (job.get("artifacts", {}) or {}).get("output")
    if not isinstance(output_artifact, dict):
        return ""
    signed_url = output_artifact.get("signed_url")
    if not isinstance(signed_url, dict) or not signed_url.get("available"):
        return ""
    return str(signed_url.get("url", "") or "").strip()


def _build_replayable_conversion_command(
    *,
    job_id: str,
    source_type: str,
    original_filename: str,
    profile: str,
    force_ocr: bool,
    language: str,
    heading_repair_enabled: bool,
) -> ReplayableCommand:
    return ReplayableCommand(
        name="convert",
        kwargs={
            "source_type": source_type,
            "original_filename": original_filename,
            "profile": profile,
            "force_ocr": force_ocr,
            "language": language,
            "heading_repair_enabled": heading_repair_enabled,
        },
        context={
            "job_id": job_id,
            "public_status_url": f"/convert/status/{job_id}",
            "quality_url": f"/convert/quality/{job_id}",
            "download_url": f"/convert/download/{job_id}",
        },
    )


def _submit_runtime_job(job_id: str, command: ReplayableCommand) -> dict:
    try:
        return RUNTIME_JOB_ADAPTER.submit(command, job_id=job_id).to_metadata()
    except Exception as error:
        return {
            "job_id": job_id,
            "provider": "local",
            "external_id": "",
            "status": "failed",
            "error": str(error),
            "replay": command.to_metadata(),
        }


def _update_runtime_job(job_id: str, status: RuntimeJobStatus, **fields) -> dict:
    try:
        return RUNTIME_JOB_ADAPTER.update_status(job_id, status, **fields).to_metadata()
    except Exception:
        job = _get_conversion_job(job_id) or {}
        runtime = dict(job.get("runtime", {}) or {})
        runtime["status"] = status.value
        runtime.update({key: value for key, value in fields.items() if value is not None})
        return runtime


def _progress_timestamp(now: datetime | None = None) -> str:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    return current_time.isoformat().replace("+00:00", "Z")


def _normalize_progress_stage(stage_id: str | None) -> tuple[str, str, int]:
    normalized = str(stage_id or "extracting").strip().lower() or "extracting"
    label, percent = CONVERSION_PROGRESS_STAGES.get(normalized, CONVERSION_PROGRESS_STAGES["extracting"])
    return normalized, label, percent


def _build_progress_payload(
    *,
    stage_id: str | None,
    stage_label: str | None = None,
    percent_estimate: int | float | None = None,
    status: str = "running",
    message: str = "",
    heartbeat_at: str | None = None,
    existing: dict | None = None,
) -> dict:
    normalized_stage_id, default_label, default_percent = _normalize_progress_stage(stage_id)
    now_label = heartbeat_at or _progress_timestamp()
    try:
        normalized_percent = int(percent_estimate if percent_estimate is not None else default_percent)
    except (TypeError, ValueError):
        normalized_percent = default_percent
    payload = dict(existing or {})
    payload.update(
        {
            "stage_id": normalized_stage_id,
            "stage_label": str(stage_label or default_label),
            "percent_estimate": max(0, min(100, normalized_percent)),
            "status": str(status or "running"),
            "message": str(message or ""),
            "heartbeat_at": now_label,
            "updated_at": now_label,
        }
    )
    return payload


def _compute_progress_health(job: dict, progress: dict, *, now: datetime | None = None) -> dict:
    status = str(job.get("status", "") or "").lower()
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    heartbeat_at = _parse_job_timestamp(str(progress.get("heartbeat_at", "") or "")) or _parse_job_timestamp(
        str(job.get("updated_at", "") or "")
    )
    heartbeat_age_seconds = int((current_time - heartbeat_at).total_seconds()) if heartbeat_at else None
    elapsed_seconds = _compute_job_elapsed_seconds(job)
    if status == "timed_out":
        health = "timed_out"
    elif status in TERMINAL_CONVERSION_JOB_STATUSES:
        health = "working"
    elif heartbeat_age_seconds is not None and heartbeat_age_seconds > CONVERSION_PROGRESS_STALLED_SECONDS:
        health = "stalled"
    elif elapsed_seconds is not None and elapsed_seconds > CONVERSION_PROGRESS_LONG_RUNNING_SECONDS:
        health = "long_running"
    else:
        health = "working"
    payload = dict(progress)
    payload.update(
        {
            "health": health,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "elapsed_seconds": elapsed_seconds,
            "long_running_after_seconds": CONVERSION_PROGRESS_LONG_RUNNING_SECONDS,
            "stalled_after_seconds": CONVERSION_PROGRESS_STALLED_SECONDS,
            "runtime_timeout_seconds": MAX_CONVERSION_JOB_RUNTIME_SECONDS,
        }
    )
    return payload


def _build_job_progress_state(job: dict, *, now: datetime | None = None) -> dict:
    status = str(job.get("status", "") or "").lower()
    existing = dict(job.get("progress", {}) or {})
    stage_id = existing.get("stage_id")
    if not stage_id:
        if status == "repairing_headings":
            stage_id = "repairing_toc"
        elif status in {"ready", "failed", "timed_out"}:
            stage_id = status
        elif status == "queued":
            stage_id = "queued"
        else:
            stage_id = "extracting"
    payload = _build_progress_payload(
        stage_id=str(stage_id),
        stage_label=str(existing.get("stage_label") or ""),
        percent_estimate=existing.get("percent_estimate"),
        status=status or str(existing.get("status") or "running"),
        message=str(job.get("message") or existing.get("message") or ""),
        heartbeat_at=str(existing.get("heartbeat_at") or job.get("updated_at") or ""),
        existing=existing,
    )
    return _compute_progress_health(job, payload, now=now)


class ConversionProgressReporter:
    def __init__(self, job_id: str, *, source_type: str, heartbeat_seconds: int = CONVERSION_PROGRESS_HEARTBEAT_SECONDS) -> None:
        self.job_id = job_id
        self.source_type = source_type
        self.heartbeat_seconds = heartbeat_seconds
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        stage_id, stage_label, percent = _normalize_progress_stage("queued")
        self._progress = _build_progress_payload(
            stage_id=stage_id,
            stage_label=stage_label,
            percent_estimate=percent,
            status="queued",
            message="Plik odebrany. Konwersja zaraz się rozpocznie.",
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"km-progress-{self.job_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def update(self, status: str, message: str, **progress_fields) -> None:
        stage_id = progress_fields.get("stage_id")
        stage_label = progress_fields.get("stage_label")
        percent_estimate = progress_fields.get("percent_estimate")
        if not stage_id and status == "repairing_headings":
            stage_id = "repairing_toc"
        with self._lock:
            self._progress = _build_progress_payload(
                stage_id=stage_id or self._progress.get("stage_id") or "extracting",
                stage_label=stage_label or self._progress.get("stage_label"),
                percent_estimate=percent_estimate if percent_estimate is not None else self._progress.get("percent_estimate"),
                status=status,
                message=message,
                existing=self._progress,
            )
            progress = dict(self._progress)
        runtime_metadata = _update_runtime_job(self.job_id, RuntimeJobStatus.RUNNING, message=message)
        _set_conversion_job(self.job_id, status=status, message=message, runtime=runtime_metadata, progress=progress)
        _log_conversion_event(
            "convert.job.phase",
            job_id=self.job_id,
            phase=str(progress.get("stage_id") or "conversion"),
            status=status,
            safe_message=message,
            source_type=self.source_type,
        )

    def heartbeat(self) -> None:
        job = _get_conversion_job(self.job_id)
        if not job or not is_active_conversion_status(job.get("status")):
            return
        with self._lock:
            progress = _build_progress_payload(
                stage_id=str(self._progress.get("stage_id") or "extracting"),
                stage_label=str(self._progress.get("stage_label") or ""),
                percent_estimate=self._progress.get("percent_estimate"),
                status=str(job.get("status") or self._progress.get("status") or "running"),
                message=str(job.get("message") or self._progress.get("message") or ""),
                existing=self._progress,
            )
            self._progress = progress
        runtime_metadata = _update_runtime_job(
            self.job_id,
            RuntimeJobStatus.RUNNING,
            message=str(progress.get("message") or ""),
        )
        _set_conversion_job(self.job_id, runtime=runtime_metadata, progress=progress)

    def terminal_progress(self, stage_id: str, message: str) -> dict:
        stage_id, stage_label, percent = _normalize_progress_stage(stage_id)
        with self._lock:
            self._progress = _build_progress_payload(
                stage_id=stage_id,
                stage_label=stage_label,
                percent_estimate=percent,
                status=stage_id,
                message=message,
                existing=self._progress,
            )
            return dict(self._progress)

    def _run(self) -> None:
        while not self._stop_event.wait(self.heartbeat_seconds):
            self.heartbeat()


@app.errorhandler(RequestEntityTooLarge)
def _handle_oversized_upload(error):
    return _json_error(
        "Plik jest zbyt duzy dla lokalnego limitu uploadu.",
        error_code=ERROR_UPLOAD_FAILED,
        status_code=413,
        phase="upload",
    )


def _encode_header_payload(payload, *, limit: int = 20) -> str:
    if isinstance(payload, list):
        payload = payload[:limit]
    return quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _resolve_server_port() -> int:
    return runtime_resolve_server_port()


def _resolve_debug_mode() -> bool:
    return runtime_resolve_debug_mode()


def _apply_conversion_headers(response, metadata: dict) -> None:
    response.headers["X-Source-Type"] = str(metadata.get("source_type", "pdf"))
    response.headers["X-Publication-Profile"] = str(metadata.get("profile", "unknown"))
    response.headers["X-Publication-Confidence"] = f"{float(metadata.get('confidence', 0.0)):.2f}"
    response.headers["X-EPUB-Validation"] = str(metadata.get("validation", "unavailable"))
    response.headers["X-EPUB-Validation-Tool"] = str(metadata.get("validation_tool", "unknown"))
    strategy = metadata.get("strategy")
    if strategy:
        response.headers["X-PDF-Type"] = str(strategy)
    response.headers["X-Publication-Sections"] = str(metadata.get("sections", 0))
    response.headers["X-Publication-Assets"] = str(metadata.get("assets", 0))
    response.headers["X-Publication-Layout"] = str(metadata.get("layout", "reflowable"))
    response.headers["X-Publication-Warnings"] = str(metadata.get("warnings", 0))
    response.headers["X-Publication-HighRiskPages"] = str(metadata.get("high_risk_pages", 0))
    response.headers["X-Publication-HighRiskSections"] = str(metadata.get("high_risk_sections", 0))
    response.headers["X-Publication-Warning-List"] = _encode_header_payload(metadata.get("warning_list", []) or [], limit=12)
    response.headers["X-Publication-HighRiskPageList"] = _encode_header_payload(
        metadata.get("high_risk_page_list", []) or [],
        limit=20,
    )
    response.headers["X-Publication-HighRiskSectionList"] = _encode_header_payload(
        metadata.get("high_risk_section_list", []) or [],
        limit=20,
    )
    if metadata.get("render_budget_class"):
        response.headers["X-Render-Budget-Class"] = str(metadata.get("render_budget_class", ""))
    if metadata.get("render_budget_attempt"):
        response.headers["X-Render-Budget-Attempt"] = str(metadata.get("render_budget_attempt", ""))
    if metadata.get("size_budget_status"):
        response.headers["X-Render-Budget-Status"] = str(metadata.get("size_budget_status", ""))
    if metadata.get("target_warn_bytes"):
        response.headers["X-Render-Budget-Warn"] = str(metadata.get("target_warn_bytes", 0))
    if metadata.get("target_hard_bytes"):
        response.headers["X-Render-Budget-Hard"] = str(metadata.get("target_hard_bytes", 0))
    heading_repair = metadata.get("heading_repair", {}) or {}
    response.headers["X-Heading-Repair-Status"] = str(heading_repair.get("status", "skipped"))
    if heading_repair.get("status") != "skipped":
        response.headers["X-Heading-Repair-Release"] = str(heading_repair.get("release", "unavailable"))
        response.headers["X-Heading-Repair-TOC-Before"] = str(heading_repair.get("toc_before", 0))
        response.headers["X-Heading-Repair-TOC-After"] = str(heading_repair.get("toc_after", 0))
        response.headers["X-Heading-Repair-Removed"] = str(heading_repair.get("removed", 0))
        response.headers["X-Heading-Repair-Review"] = str(heading_repair.get("review", 0))
        response.headers["X-Heading-Repair-EPUBCheck"] = str(heading_repair.get("epubcheck", "unavailable"))
        response.headers["X-Heading-Repair-Error"] = quote(str(heading_repair.get("error", "")))


def _candidate_job_download_url(job_id: str, job: dict) -> str | None:
    if job.get("status") == "ready":
        return f"/convert/download/{job_id}"
    return None


def _build_job_download_state(job_id: str, job: dict) -> ConversionDownloadState:
    remote_output_available = bool(_signed_output_artifact_url(job))
    return resolve_conversion_download_state(
        job_status=job.get("status"),
        output_path=job.get("output_path", ""),
        download_url=_candidate_job_download_url(job_id, job),
        output_path_exists=True if remote_output_available else None,
    )


def _job_download_url(job_id: str, job: dict) -> str | None:
    return _build_job_download_state(job_id, job).download_url


def _build_job_quality_state(job_id: str, job: dict) -> dict:
    payload = dict(job)
    output_size_bytes = _read_output_size_bytes(job)
    if output_size_bytes is not None:
        payload["output_size_bytes"] = output_size_bytes
    payload["output_path_exists"] = _build_job_download_state(job_id, job).output_path_exists
    quality_state = build_conversion_quality_state(
        payload,
        download_url=_candidate_job_download_url(job_id, job),
    )
    artifacts = dict(quality_state.get("artifacts", {}) or {})
    artifacts.update(dict(job.get("artifacts", {}) or {}))
    quality_state["artifacts"] = artifacts
    return quality_state


def _resolve_request_port_label(host_header: str | None, fallback_port: int) -> str:
    host_value = str(host_header or "").strip()
    if not host_value:
        return str(fallback_port)
    if ":" not in host_value:
        return str(fallback_port)
    return host_value.rsplit(":", 1)[-1]


def _set_conversion_job(job_id: str, **fields) -> dict | None:
    return _CONVERSION_JOB_STORE.update(
        job_id,
        fields,
        updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _get_conversion_job(job_id: str) -> dict | None:
    return _CONVERSION_JOB_STORE.get(job_id)


def _parse_job_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _compute_job_elapsed_seconds(job: dict) -> int | None:
    return lifecycle_compute_job_elapsed_seconds(job)


def _compute_job_history_elapsed_seconds(job: dict) -> int | None:
    return lifecycle_compute_job_history_elapsed_seconds(job)


def _recommended_poll_interval_ms(job: dict) -> int:
    return lifecycle_recommended_poll_interval_ms(job)


def _read_output_size_bytes(job: dict) -> int | None:
    output_size = job.get("output_size_bytes")
    output_path = str(job.get("output_path", "") or "")
    if output_path and os.path.exists(output_path):
        return os.path.getsize(output_path)
    if isinstance(output_size, (int, float)):
        return max(0, int(output_size))
    return None


def _conversion_job_sort_timestamp(job: dict) -> datetime:
    timestamp = _parse_job_timestamp(job.get("updated_at")) or _parse_job_timestamp(job.get("created_at"))
    if timestamp:
        return timestamp
    return datetime.min.replace(tzinfo=UTC)


def _resolve_conversion_job_history_limit() -> int:
    raw_limit = str(request.args.get("limit", "") or "").strip()
    if not raw_limit:
        return DEFAULT_CONVERSION_JOB_HISTORY_LIMIT
    try:
        limit = int(raw_limit)
    except ValueError:
        return DEFAULT_CONVERSION_JOB_HISTORY_LIMIT
    return max(1, min(limit, MAX_CONVERSION_JOB_HISTORY_LIMIT))


def _truthy_query_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_library_filters(*, default_include_text: bool = False) -> LibraryFilters:
    raw_limit = str(request.args.get("limit", "") or "").strip()
    try:
        limit = int(raw_limit) if raw_limit else DEFAULT_CONVERSION_JOB_HISTORY_LIMIT
    except ValueError:
        limit = DEFAULT_CONVERSION_JOB_HISTORY_LIMIT
    include_text = default_include_text or _truthy_query_flag(request.args.get("include_text"))
    return LibraryFilters(
        query=str(request.args.get("q", "") or request.args.get("query", "") or "").strip(),
        status=str(request.args.get("status", "") or "").strip().lower(),
        release_verdict=str(
            request.args.get("release_verdict", "")
            or request.args.get("verdict", "")
            or ""
        ).strip().lower(),
        include_text=include_text,
        limit=max(1, min(limit, MAX_CONVERSION_JOB_HISTORY_LIMIT)),
    )


def _build_conversion_job_history_item(job_id: str, job: dict) -> dict:
    response_job_id = str(job.get("job_id") or job_id)
    status = str(job.get("status", "queued") or "queued")
    status_key = status.strip().lower()
    download_state = _build_job_download_state(response_job_id, job)
    item = {
        "job_id": response_job_id,
        "status": status,
        "message": str(job.get("message", "") or ""),
        "source_type": str(job.get("source_type", "pdf") or "pdf"),
        "filename": str(job.get("filename", "") or ""),
        "created_at": str(job.get("created_at", "") or ""),
        "updated_at": str(job.get("updated_at", "") or ""),
        "elapsed_seconds": _compute_job_history_elapsed_seconds(job),
        "output_size_bytes": _read_output_size_bytes(job),
        "download_available": download_state.download_available,
        "download_state": download_state.to_dict(),
        "quality_state_url": f"/convert/quality/{response_job_id}",
        "runtime": dict(job.get("runtime", {}) or {}),
        "artifacts": dict(job.get("artifacts", {}) or {}),
        "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
    }
    if download_state.download_url:
        item["download_url"] = download_state.download_url
    if status_key in {"failed", "timed_out"}:
        item["error"] = str(job.get("error", "") or "")
        item["error_code"] = str(job.get("error_code", "") or "")
    return item


def _build_library_payload(*, default_include_text: bool = False) -> dict:
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    return build_library_index(
        _CONVERSION_JOB_STORE.snapshot(),
        quality_state_builder=lambda job_id, job: _build_job_quality_state(job_id, dict(job)),
        output_size_resolver=lambda job: _read_output_size_bytes(dict(job)),
        filters=_resolve_library_filters(default_include_text=default_include_text),
    )


def _active_conversion_job_count() -> int:
    with _CONVERSION_JOBS_LOCK:
        return count_active_conversion_jobs(_CONVERSION_JOBS)


def _mark_timed_out_conversion_jobs(*, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    timed_out: list[str] = []
    with _CONVERSION_JOBS_LOCK:
        for job_id, job in list(_CONVERSION_JOBS.items()):
            should_timeout, runtime_seconds, _stale_seconds = should_timeout_job(job, now=current_time)
            if not should_timeout:
                continue
            if _should_defer_stale_timeout_for_active_runtime_job(
                job_id,
                runtime_seconds=runtime_seconds,
                stale_seconds=_stale_seconds,
            ):
                continue
            elapsed = runtime_seconds
            job.update(
                build_timed_out_job_fields(
                    now=current_time,
                    message="Konwersja przekroczyla limit czasu.",
                    error="Konwersja przekroczyla limit czasu. Uruchom ja ponownie po sprawdzeniu pliku zrodlowego.",
                )
            )
            job["progress"] = _build_progress_payload(
                stage_id="timed_out",
                status="timed_out",
                message=job["error"],
                heartbeat_at=_progress_timestamp(now=current_time),
                existing=dict(job.get("progress", {}) or {}),
            )
            timed_out.append(job_id)
            _log_conversion_event(
                "convert.job.failed",
                level="error",
                job_id=job_id,
                phase="conversion",
                status="timed_out",
                error_code="conversion_timeout",
                safe_message=job["error"],
                source_type=str(job.get("source_type", "") or ""),
                elapsed_seconds=elapsed,
                output_size_bytes=0,
            )
    if timed_out:
        _CONVERSION_JOB_STORE.persist()
    return {"timed_out_jobs": len(timed_out), "job_ids": timed_out}


def _should_defer_stale_timeout_for_active_runtime_job(
    job_id: str,
    *,
    runtime_seconds: int | None,
    stale_seconds: int | None,
) -> bool:
    """Do not mark a live local worker as timed out only because its job row is stale."""

    runtime_limit_exceeded = (
        runtime_seconds is not None
        and runtime_seconds > MAX_CONVERSION_JOB_RUNTIME_SECONDS
    )
    stale_limit_exceeded = (
        stale_seconds is not None
        and stale_seconds > MAX_CONVERSION_JOB_STALE_SECONDS
    )
    if runtime_limit_exceeded or not stale_limit_exceeded:
        return False
    try:
        runtime_handle = RUNTIME_JOB_ADAPTER.get(job_id)
    except Exception:
        return False
    if runtime_handle is None:
        return False
    return runtime_handle.status == RuntimeJobStatus.RUNNING


def _worker_can_finish_job(job_id: str) -> bool:
    job = _get_conversion_job(job_id)
    if not job:
        return False
    return is_active_conversion_status(job.get("status"))


def _attach_output_size_metadata(metadata: dict, output_size_bytes: int) -> dict:
    return enrich_conversion_metadata_with_output_size(
        metadata,
        output_size_bytes,
        oversized_warning_bytes=OVERSIZED_EPUB_WARNING_BYTES,
    )


def _normalize_temp_artifact_path(path_value: str | None) -> str:
    if not path_value:
        return ""
    try:
        return str(Path(path_value).resolve())
    except OSError:
        return str(path_value)


def _cleanup_expired_conversion_jobs(*, now: datetime | None = None, force: bool = False) -> dict:
    global _LAST_CONVERSION_CLEANUP_AT

    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    if (
        not force
        and _LAST_CONVERSION_CLEANUP_AT is not None
        and (current_time - _LAST_CONVERSION_CLEANUP_AT).total_seconds() < CONVERSION_CLEANUP_MIN_INTERVAL_SECONDS
    ):
        return {
            "ran": False,
            "removed_jobs": 0,
            "removed_files": 0,
            "skipped_recently": True,
        }

    job_cutoff = current_time - timedelta(seconds=CONVERSION_JOB_RETENTION_SECONDS)
    file_cutoff = current_time - timedelta(seconds=CONVERSION_TEMP_FILE_RETENTION_SECONDS)
    active_paths: set[str] = set()
    expired_source_paths: list[str] = []
    expired_output_paths: list[str] = []
    removed_job_ids: list[str] = []
    removed_files = 0

    with _CONVERSION_JOBS_LOCK:
        for job_id, job in list(_CONVERSION_JOBS.items()):
            status = str(job.get("status", "queued") or "queued")
            updated_at = _parse_job_timestamp(job.get("updated_at")) or _parse_job_timestamp(job.get("created_at"))
            source_path = _normalize_temp_artifact_path(job.get("source_path", ""))
            output_path = _normalize_temp_artifact_path(job.get("output_path", ""))

            if status in ACTIVE_CONVERSION_JOB_STATUSES or not updated_at or updated_at >= job_cutoff:
                if source_path:
                    active_paths.add(source_path)
                if output_path:
                    active_paths.add(output_path)
                continue

            if source_path:
                expired_source_paths.append(source_path)
            if output_path:
                expired_output_paths.append(output_path)
            removed_job_ids.append(job_id)
            _CONVERSION_JOBS.pop(job_id, None)

        _LAST_CONVERSION_CLEANUP_AT = current_time

    if removed_job_ids:
        _CONVERSION_JOB_STORE.persist()

    for expired_path in [*expired_source_paths, *expired_output_paths]:
        if expired_path and expired_path not in active_paths and os.path.exists(expired_path):
            try:
                os.remove(expired_path)
                removed_files += 1
            except OSError:
                pass

    upload_root = Path(UPLOAD_DIR)
    if upload_root.exists():
        for candidate in upload_root.iterdir():
            try:
                if not candidate.is_file():
                    continue
                stat_result = candidate.stat()
            except OSError:
                continue
            resolved_path = _normalize_temp_artifact_path(str(candidate))
            if resolved_path in active_paths:
                continue
            if candidate.suffix.lower() not in {".pdf", ".docx", ".epub"}:
                continue
            modified_at = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
            if modified_at >= file_cutoff:
                continue
            try:
                candidate.unlink()
                removed_files += 1
            except OSError:
                pass

    return {
        "ran": True,
        "removed_jobs": len(removed_job_ids),
        "removed_files": removed_files,
        "skipped_recently": False,
    }


def _run_conversion_pipeline(
    *,
    source_path: str,
    source_type: str,
    original_filename: str,
    profile: str,
    force_ocr: bool,
    language: str,
    heading_repair_enabled: bool,
    route_model_mode: str = "shadow",
    quality_gate_mode: str = "draft",
    status_callback=None,
) -> dict:
    outcome = run_document_conversion(
        ConversionRequest(
            source_path=source_path,
            source_type=source_type,
            original_filename=original_filename,
            profile=profile,
            route_model_mode=route_model_mode,
            quality_gate_mode=quality_gate_mode,
            force_ocr=force_ocr,
            language=language,
            heading_repair_enabled=heading_repair_enabled,
        ),
        convert_impl=convert_document_to_epub_with_report,
        heading_repair_impl=repair_epub_headings_and_toc,
        status_callback=status_callback,
    )
    return {
        "epub_bytes": outcome.epub_bytes,
        "download_name": outcome.download_name,
        "metadata": outcome.metadata,
    }


def _spawn_conversion_job(
    *,
    job_id: str,
    source_path: str,
    source_type: str,
    original_filename: str,
    profile: str,
    force_ocr: bool,
    language: str,
    heading_repair_enabled: bool,
    route_model_mode: str = "shadow",
    quality_gate_mode: str = "draft",
) -> None:
    def _worker() -> None:
        output_path = os.path.join(UPLOAD_DIR, f"{job_id}.epub")
        progress_reporter = ConversionProgressReporter(job_id, source_type=source_type)
        progress_reporter.start()

        def _status_callback(status: str, message: str, **progress_fields) -> None:
            progress_reporter.update(status, message, **progress_fields)

        try:
            payload = _run_conversion_pipeline(
                source_path=source_path,
                source_type=source_type,
                original_filename=original_filename,
                profile=profile,
                route_model_mode=route_model_mode,
                quality_gate_mode=quality_gate_mode,
                force_ocr=force_ocr,
                language=language,
                heading_repair_enabled=heading_repair_enabled,
                status_callback=_status_callback,
            )
            if not _worker_can_finish_job(job_id):
                return
            progress_reporter.update(
                "running",
                "Pakowanie EPUB i zapisywanie artefaktów...",
                stage_id="packaging",
                stage_label="Pakowanie EPUB",
                percent_estimate=94,
            )
            with open(output_path, "wb") as handle:
                handle.write(payload["epub_bytes"])
            output_size_bytes = os.path.getsize(output_path)
            metadata = _attach_output_size_metadata(payload["metadata"], output_size_bytes)
            output_artifact = _store_artifact_bytes(
                job_id=job_id,
                kind=ArtifactKind.OUTPUT,
                filename=payload["download_name"],
                data=payload["epub_bytes"],
            )
            job_before_ready = _get_conversion_job(job_id) or {}
            artifacts = dict(job_before_ready.get("artifacts", {}) or {})
            artifacts["output"] = output_artifact
            runtime_metadata = _update_runtime_job(
                job_id,
                RuntimeJobStatus.SUCCEEDED,
                message="EPUB gotowy do pobrania.",
            )
            _set_conversion_job(
                job_id,
                status="ready",
                message="EPUB gotowy do pobrania.",
                output_path=output_path,
                download_name=payload["download_name"],
                metadata=metadata,
                runtime=runtime_metadata,
                progress=progress_reporter.terminal_progress("ready", "EPUB gotowy do pobrania."),
                artifacts=artifacts,
                artifact_storage=_artifact_storage_status(),
                output_size_bytes=output_size_bytes,
                error="",
                error_code="",
            )
            _store_quality_report_artifacts(job_id)
            _log_conversion_event(
                "convert.job.phase",
                job_id=job_id,
                phase="conversion",
                status="ready",
                safe_message="EPUB gotowy do pobrania.",
                source_type=source_type,
                output_size_bytes=output_size_bytes,
            )
        except Exception as error:
            if not _worker_can_finish_job(job_id):
                return
            sentry_event_id = capture_conversion_exception(
                error,
                context=_conversion_sentry_context(
                    job_id=job_id,
                    source_type=source_type,
                    profile=profile,
                ),
            )
            runtime_metadata = _update_runtime_job(
                job_id,
                RuntimeJobStatus.FAILED,
                error=str(error),
                message="Konwersja nie powiodla sie.",
            )
            _set_conversion_job(
                job_id,
                status="failed",
                message="Konwersja nie powiodla sie.",
                runtime=runtime_metadata,
                artifact_storage=_artifact_storage_status(),
                output_size_bytes=0,
                error=str(error),
                error_code="conversion_failed",
                sentry_event_id=sentry_event_id,
                progress=progress_reporter.terminal_progress("failed", "Konwersja nie powiodła się."),
            )
            _log_conversion_event(
                "convert.job.failed",
                level="error",
                job_id=job_id,
                phase="conversion",
                status="failed",
                error_code="conversion_failed",
                safe_message="Konwersja nie powiodla sie.",
                source_type=source_type,
                output_size_bytes=0,
                exception_class=error.__class__.__name__,
            )
        finally:
            progress_reporter.stop()
            if os.path.exists(source_path):
                os.remove(source_path)
            _set_conversion_job(job_id, source_path="")

    thread = threading.Thread(target=_worker, daemon=True, name=f"kindlemaster-convert-{job_id}")
    thread.start()


@app.route("/")
def index():
    root_path = Path(app.root_path)
    ui_asset_paths = [
        root_path / "templates" / "index.html",
        root_path / "static" / "css" / "app-shell.css",
        root_path / "static" / "js" / "conversion-ui.js",
        root_path / "static" / "js" / "quality-cockpit.js",
        root_path / "static" / "js" / "library.js",
    ]
    updated_at_timestamp = max(
        path.stat().st_mtime for path in ui_asset_paths if path.exists()
    )
    updated_at = datetime.fromtimestamp(updated_at_timestamp)
    local_app_url = build_local_app_url(
        _resolve_request_port_label(request.host, _resolve_server_port())
    )
    months_pl = [
        "sty", "lut", "mar", "kwi", "maj", "cze",
        "lip", "sie", "wrz", "paz", "lis", "gru",
    ]
    updated_at_label = (
        f"{updated_at.day} {months_pl[updated_at.month - 1]} "
        f"{updated_at.year}, {updated_at:%H:%M:%S}"
    )
    return render_template(
        "index.html",
        local_app_url=local_app_url,
        updated_at_label=updated_at_label,
    )


@app.route("/app")
@app.route("/app/<path:_path>")
def react_app(_path: str = ""):
    """Serve the Sprint 4 React shell when the Vite build is available."""

    root_path = Path(app.root_path)
    react_index = root_path / "static" / "react" / "index.html"
    local_app_url = build_local_app_url(
        _resolve_request_port_label(request.host, _resolve_server_port())
    )
    if react_index.exists():
        return react_index.read_text(encoding="utf-8")
    return render_template(
        "react_app_unbuilt.html",
        local_app_url=local_app_url,
    )


@app.route("/convert", methods=["POST"])
def convert():
    """Convert uploaded PDF or DOCX to EPUB."""
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return _json_error("Przeslij plik PDF albo DOCX.", error_code=ERROR_UPLOAD_FAILED, status_code=400, phase="upload")

    source_type = detect_supported_source_type(file.filename)
    if not source_type:
        return _json_error("Obslugiwane sa tylko pliki PDF i DOCX.", error_code=ERROR_UPLOAD_FAILED, status_code=400, phase="upload")
    source_suffix = f".{source_type}"

    # Get conversion preferences from form
    profile = request.form.get("profile", "auto-premium")
    route_model_mode = request.form.get("route_model_mode", "shadow")
    quality_gate_mode = request.form.get("quality_gate_mode", "draft")
    force_ocr = request.form.get("ocr", "false") == "true"
    language = request.form.get("language", "pl")
    heading_repair_enabled = request.form.get("heading_repair", "false") == "true"

    # Save uploaded file temporarily
    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    try:
        file.save(source_path)
    except OSError:
        return _json_error(
            "Nie udalo sie zapisac przeslanego pliku.",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=500,
            phase="upload",
            retryable=True,
        )

    try:
        payload = _run_conversion_pipeline(
            source_path=source_path,
            source_type=source_type,
            original_filename=file.filename,
            profile=profile,
            route_model_mode=route_model_mode,
            quality_gate_mode=quality_gate_mode,
            force_ocr=force_ocr,
            language=language,
            heading_repair_enabled=heading_repair_enabled,
        )

        response = send_file(
            io.BytesIO(payload["epub_bytes"]),
            mimetype="application/epub+zip",
            as_attachment=True,
            download_name=payload["download_name"],
        )
        _apply_conversion_headers(response, payload["metadata"])
        return response
    except Exception as e:
        capture_conversion_exception(
            e,
            context=_conversion_sentry_context(
                job_id=job_id,
                source_type=source_type,
                profile=profile,
            ),
        )
        return _json_error(
            f"Konwersja nie powiodla sie: {str(e)}",
            error_code="conversion_failed",
            status_code=500,
            phase="conversion",
        )
    finally:
        # Clean up
        if os.path.exists(source_path):
            os.remove(source_path)


@app.route("/convert/start", methods=["POST"])
def convert_start():
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    if _active_conversion_job_count() >= MAX_ACTIVE_CONVERSION_JOBS:
        return _json_error(
            "Kolejka konwersji jest pelna. Sprobuj ponownie za chwile.",
            error_code=ERROR_QUEUE_FAILED,
            status_code=429,
            phase="queue",
            retryable=True,
        )
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return _json_error("Przeslij plik PDF albo DOCX.", error_code=ERROR_UPLOAD_FAILED, status_code=400, phase="upload")

    source_type = detect_supported_source_type(file.filename)
    if not source_type:
        return _json_error("Obslugiwane sa tylko pliki PDF i DOCX.", error_code=ERROR_UPLOAD_FAILED, status_code=400, phase="upload")
    source_suffix = f".{source_type}"

    profile = request.form.get("profile", "auto-premium")
    route_model_mode = request.form.get("route_model_mode", "shadow")
    quality_gate_mode = request.form.get("quality_gate_mode", "draft")
    force_ocr = request.form.get("ocr", "false") == "true"
    language = request.form.get("language", "pl")
    heading_repair_enabled = request.form.get("heading_repair", "false") == "true"
    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    try:
        file.save(source_path)
    except OSError:
        return _json_error(
            "Nie udalo sie zapisac przeslanego pliku.",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=500,
            phase="upload",
            retryable=True,
        )
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    input_artifact = _store_artifact_bytes(
        job_id=job_id,
        kind=ArtifactKind.INPUT,
        filename=file.filename,
        data=Path(source_path).read_bytes(),
    )
    runtime_metadata = _submit_runtime_job(
        job_id,
        _build_replayable_conversion_command(
            job_id=job_id,
            source_type=source_type,
            original_filename=file.filename,
            profile=profile,
            force_ocr=force_ocr,
            language=language,
            heading_repair_enabled=heading_repair_enabled,
        ),
    )
    job_record = build_conversion_job_record(
        job_id=job_id,
        source_path=source_path,
        source_type=source_type,
        filename=file.filename,
        created_at=created_at,
    )
    job_record["runtime"] = runtime_metadata
    job_record["artifacts"] = {"input": input_artifact}
    job_record["artifact_storage"] = _artifact_storage_status()
    _CONVERSION_JOB_STORE.create(job_record)
    _log_conversion_event(
        "convert.job.created",
        job_id=job_id,
        phase="queue",
        status="queued",
        safe_message="Konwersja wystartowala. Trwa przygotowanie EPUB.",
        source_type=source_type,
    )

    _spawn_conversion_job(
        job_id=job_id,
        source_path=source_path,
        source_type=source_type,
        original_filename=file.filename,
        profile=profile,
        route_model_mode=route_model_mode,
        quality_gate_mode=quality_gate_mode,
        force_ocr=force_ocr,
        language=language,
        heading_repair_enabled=heading_repair_enabled,
    )

    response = jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "source_type": source_type,
            "message": "Konwersja wystartowala. Trwa przygotowanie EPUB.",
            "poll_after_ms": DEFAULT_CONVERSION_POLL_INTERVAL_MS,
            "runtime": runtime_metadata,
            "artifacts": {"input": input_artifact},
            "artifact_storage": job_record["artifact_storage"],
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/jobs", methods=["GET"])
def convert_jobs():
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    jobs = _CONVERSION_JOB_STORE.snapshot()
    limit = _resolve_conversion_job_history_limit()
    recent_jobs = sorted(
        jobs.items(),
        key=lambda item: _conversion_job_sort_timestamp(item[1]),
        reverse=True,
    )[:limit]
    response = jsonify(
        {
            "success": True,
            "jobs": [
                _build_conversion_job_history_item(job_id, job)
                for job_id, job in recent_jobs
            ],
            "count": len(recent_jobs),
            "total": len(jobs),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/library", methods=["GET"])
def convert_library():
    response = jsonify(_build_library_payload(default_include_text=False))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/archive", methods=["GET"])
def convert_archive():
    response = jsonify(_build_library_payload(default_include_text=False))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/search", methods=["GET"])
def convert_search():
    response = jsonify(_build_library_payload(default_include_text=True))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/report/<job_id>.<extension>", methods=["GET"])
def convert_quality_report(job_id: str, extension: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="recovery",
            job_id=job_id,
        )

    normalized_extension = str(extension or "").strip().lower()
    if normalized_extension not in {"json", "md"}:
        return _json_error(
            "Nieobslugiwany format raportu.",
            error_code=ERROR_UNSUPPORTED_REPORT_FORMAT,
            status_code=400,
            phase="report",
            job_id=job_id,
        )

    payload = build_quality_report_payload(
        job_id,
        job,
        quality_state=_build_job_quality_state(job_id, job),
        output_size_bytes=_read_output_size_bytes(job),
        include_text=True,
    )
    if normalized_extension == "json":
        response = jsonify(payload)
    else:
        response = app.response_class(
            render_quality_report_markdown(payload),
            mimetype="text/markdown; charset=utf-8",
        )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/status/<job_id>", methods=["GET"])
def convert_status(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="recovery",
            job_id=job_id,
        )
    job = _ensure_quality_report_artifacts(job_id, job)
    download_state = _build_job_download_state(job_id, job)
    download_url = download_state.download_url
    conversion_payload = None
    if job.get("status") == "ready":
        conversion_payload = dict(job.get("metadata", {}) or {})
        output_size_bytes = _read_output_size_bytes(job)
        if output_size_bytes is not None and "output_size_bytes" not in conversion_payload:
            conversion_payload["output_size_bytes"] = output_size_bytes
    response = jsonify(
        {
            "success": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "message": job.get("message", ""),
            "source_type": job.get("source_type", "pdf"),
            "filename": job.get("filename", ""),
            "error": job.get("error", ""),
            "error_code": job.get("error_code", ""),
            "conversion": conversion_payload,
            "download_url": download_url,
            "download_available": download_state.download_available,
            "download_state": download_state.to_dict(),
            "progress": _build_job_progress_state(job),
            "poll_after_ms": _recommended_poll_interval_ms(job),
            "elapsed_seconds": _compute_job_elapsed_seconds(job),
            "output_size_bytes": _read_output_size_bytes(job) if job.get("status") == "ready" else None,
            "quality_state": _build_job_quality_state(job_id, job),
            "quality_state_url": f"/convert/quality/{job_id}",
            "runtime": dict(job.get("runtime", {}) or {}),
            "artifacts": dict(job.get("artifacts", {}) or {}),
            "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/quality/<job_id>", methods=["GET"])
def convert_quality(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="recovery",
            job_id=job_id,
        )
    job = _ensure_quality_report_artifacts(job_id, job)

    response = jsonify(
        {
            "success": True,
            "job_id": job["job_id"],
            "quality_state": _build_job_quality_state(job_id, job),
            "runtime": dict(job.get("runtime", {}) or {}),
            "progress": _build_job_progress_state(job),
            "artifacts": dict(job.get("artifacts", {}) or {}),
            "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/feedback/<job_id>", methods=["POST"])
def convert_feedback(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="feedback",
            job_id=job_id,
        )
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _json_error(
            "Feedback musi byc obiektem JSON.",
            error_code="invalid_feedback_payload",
            status_code=400,
            phase="feedback",
            job_id=job_id,
        )
    try:
        from ml_feedback import append_user_feedback

        record = append_user_feedback(job_id=job_id, feedback=payload, job=job)
    except Exception as error:
        return _json_error(
            f"Nie udalo sie zapisac feedbacku: {error}",
            error_code="feedback_write_failed",
            status_code=500,
            phase="feedback",
            job_id=job_id,
        )
    response = jsonify(
        {
            "success": True,
            "job_id": job_id,
            "feedback_status": "recorded",
            "record_id": record.get("record_id", ""),
            "online_learning": False,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/download/<job_id>", methods=["GET"])
def convert_download(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    if job.get("status") != "ready":
        retryable = str(job.get("status", "") or "") in ACTIVE_CONVERSION_JOB_STATUSES
        return _json_error(
            "EPUB nie jest jeszcze gotowy do pobrania.",
            error_code=str(job.get("error_code") or ERROR_QUEUE_FAILED),
            status_code=409,
            phase="download",
            job_id=job_id,
            retryable=retryable,
        )

    download_state = _build_job_download_state(job_id, job)
    output_path = job.get("output_path", "")
    if not download_state.download_available:
        _set_conversion_job(
            job_id,
            status="failed",
            error="Brak pliku EPUB do pobrania.",
            error_code=ERROR_MISSING_OUTPUT,
            output_size_bytes=0,
        )
        _log_conversion_event(
            "convert.job.download_missing",
            level="error",
            job_id=job_id,
            phase="download",
            status="failed",
            error_code=ERROR_MISSING_OUTPUT,
            safe_message="Brak pliku EPUB do pobrania.",
            source_type=str(job.get("source_type", "") or ""),
            output_size_bytes=0,
        )
        return _json_error(
            "Brak pliku EPUB do pobrania.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=500,
            phase="download",
            job_id=job_id,
        )

    signed_artifact_url = _signed_output_artifact_url(job)
    if signed_artifact_url:
        return redirect(signed_artifact_url, code=302)

    response = send_file(
        output_path,
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=job.get("download_name", f"{job_id}.epub"),
    )
    _apply_conversion_headers(response, job.get("metadata", {}) or {})
    return response


@app.route("/analyze", methods=["POST"])
def analyze_document():
    """Analyze PDF or DOCX and return detailed information."""
    _cleanup_expired_conversion_jobs()
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "Przeslij plik PDF albo DOCX."}), 400

    source_type = detect_supported_source_type(file.filename)
    if not source_type:
        return jsonify({"error": "Obslugiwane sa tylko pliki PDF i DOCX."}), 400
    source_suffix = f".{source_type}"

    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    file.save(source_path)

    try:
        if source_type == "docx":
            analysis = analyze_docx(source_path)
            publication_analysis = analysis.get("publication_analysis", {})
            return jsonify(
                {
                    "success": True,
                    "filename": file.filename,
                    "source_type": "docx",
                    "analysis": analysis,
                    "publication_analysis": publication_analysis,
                    "recommended_profile": "Book",
                    "recommendations": _get_docx_recommendations(analysis),
                }
            )

        pdf_type = detect_pdf_type(source_path)
        publication_analysis = analyze_publication(source_path)
        return jsonify(
            {
                "success": True,
                "filename": file.filename,
                "source_type": "pdf",
                "analysis": pdf_type,
                "publication_analysis": publication_analysis.to_dict(),
                "recommended_profile": _get_publication_recommendation(publication_analysis.to_dict()),
                "recommendations": _get_recommendations(pdf_type),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(source_path):
            os.remove(source_path)


def _get_recommendations(pdf_type: dict) -> dict:
    """Get conversion recommendations based on PDF analysis."""
    strategy = pdf_type["recommended_strategy"]
    layout_heavy = pdf_type.get("layout_heavy", False)
    text_heavy = pdf_type.get("text_heavy", False)
    
    recommendations = {
        "fixed_layout": {
            "recommended": pdf_type["is_scanned"] or (strategy == "layout_fixed" and not pdf_type.get("has_text_layer")),
            "reason": "Fixed-layout ma sens glownie dla skanow lub dokumentow bez sensownej warstwy tekstowej.",
        },
        "reflowable": {
            "recommended": strategy == "text_reflowable" or text_heavy or pdf_type.get("has_text_layer", False),
            "reason": "Ten PDF ma warstwe tekstowa. Reflowable EPUB bedzie czytelniejszy na Kindle i pozwoli zmieniac rozmiar tekstu.",
        },
        "ocr_needed": {
            "required": pdf_type["is_scanned"],
            "reason": "Wykryto skanowane strony. OCR bedzie konieczny dla pelnej ekstrakcji tekstu.",
        },
    }
    
    return recommendations


def _get_docx_recommendations(docx_analysis: dict) -> dict:
    heading1_count = int(docx_analysis.get("heading1_count") or 0)
    estimated_sections = int(docx_analysis.get("estimated_sections") or 0)
    return {
        "reflowable": {
            "recommended": True,
            "reason": "DOCX jest konwertowany do reflowable EPUB na podstawie struktury akapitow i stylow.",
        },
        "ocr_needed": {
            "required": False,
            "reason": "DOCX nie wymaga OCR, bo zawiera warstwe tekstowa i strukture dokumentu.",
        },
        "heading_repair": {
            "recommended": heading1_count == 0 or estimated_sections <= 1,
            "reason": "Naprawa headingow i TOC jest szczegolnie przydatna, gdy dokument ma slabe lub plaskie style naglowkow.",
        },
    }


def _get_publication_recommendation(publication_analysis: dict) -> str:
    profile = publication_analysis.get("profile", "book_reflow")
    mapping = {
        "book_reflow": "Book",
        "diagram_book_reflow": "Book",
        "magazine_reflow": "Magazine",
        "scanned_reflow": "Technical/Study",
        "fixed_layout_fallback": "Preserve Layout",
    }
    return mapping.get(profile, "Auto Premium")


if __name__ == "__main__":
    host = LOCALHOST
    port = _resolve_server_port()
    debug = _resolve_debug_mode()
    print(
        f"Starting KindleMaster on {build_local_app_url(port)} (bind={host}, debug={debug})",
        flush=True,
    )
    serve_http_app(app, host=host, port=port, debug=debug, runtime="flask")
