"""
KindleMaster — PDF to EPUB Converter
=====================================
Production-grade PDF to EPUB conversion with maximum visual fidelity.
"""

import io
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from app_runtime_services import (
    DEFAULT_DEBUG,
    DEFAULT_PORT,
    LOCALHOST,
    build_conversion_metadata,
    ConversionRequest,
    ConversionQualityGateError,
    ConversionJobStore,
    build_conversion_job_record,
    build_conversion_quality_state,
    enrich_conversion_metadata_with_output_size,
    build_local_app_url,
    detect_supported_source_type,
    is_allowed_cors_origin,
    resolve_debug_mode as runtime_resolve_debug_mode,
    resolve_server_host as runtime_resolve_server_host,
    resolve_server_port as runtime_resolve_server_port,
    run_document_conversion,
    serve_http_app,
)
from artifact_storage import ArtifactKind, build_artifact_storage
from conversion_api_contracts import (
    ConversionDownloadState,
    ERROR_CONVERSION_FAILED,
    ERROR_INTERACTIVE_RUNTIME_BUDGET,
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
from pdf_weight_reducer import PdfCompressionFailed, PdfCompressionUnavailable, compress_pdf, normalize_compression_profile
from sentry_observability import (
    build_conversion_context,
    capture_conversion_exception,
    configure_sentry_backend,
)
from runtime_job_adapter import ReplayableCommand, RetryPolicy, RuntimeJobStatus, build_runtime_job_adapter


def _load_local_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_local_env_files() -> None:
    root = Path(__file__).resolve().parent
    for name in (".env", ".env.local"):
        _load_local_env_file(root / name)


_load_local_env_files()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB
SENTRY_BACKEND_STATE = configure_sentry_backend()

UPLOAD_DIR = os.environ.get("KINDLEMASTER_UPLOAD_DIR") or os.path.join(tempfile.gettempdir(), "kindlemaster")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ARTIFACT_STORAGE = build_artifact_storage(
    local_root=Path(os.environ.get("KINDLEMASTER_ARTIFACT_ROOT") or Path("output") / "artifacts")
)
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
PDF_COMPRESS_JOB_RETENTION_SECONDS = 6 * 60 * 60
PDF_COMPRESS_REMOTE_DOWNLOAD_TIMEOUT_SECONDS = 120
PDF_COMPRESS_DIR = Path(UPLOAD_DIR) / "pdf_compress"
PDF_COMPRESS_DIR.mkdir(parents=True, exist_ok=True)
SUPABASE_ARTIFACT_BUCKET = "kindlemaster-artifacts"
SUPABASE_ARTIFACT_SIGNED_URL_SECONDS = 60 * 60
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
_PDF_COMPRESS_JOBS: dict[str, dict] = {}


def _json_error(
    message: str,
    *,
    error_code: str,
    status_code: int,
    phase: str,
    job_id: str | None = None,
    retryable: bool = False,
    extra: dict | None = None,
):
    payload = build_json_error_payload(
        message,
        error_code=error_code,
        phase=phase,
        job_id=job_id,
        retryable=retryable,
    )
    if extra:
        payload.update(extra)
    response = jsonify(payload)
    response.status_code = status_code
    apply_no_store_headers(response.headers)
    return response


@app.before_request
def _handle_cors_preflight():
    if request.method != "OPTIONS":
        return None
    if not is_allowed_cors_origin(request.headers.get("Origin")):
        return None
    response = app.make_response(("", 204))
    return response


@app.after_request
def _apply_cors_headers(response):
    origin = request.headers.get("Origin")
    if not is_allowed_cors_origin(origin):
        return response
    response.headers["Access-Control-Allow-Origin"] = str(origin).strip().rstrip("/")
    existing_vary = response.headers.get("Vary", "")
    vary_values = {value.strip() for value in existing_vary.split(",") if value.strip()}
    vary_values.add("Origin")
    response.headers["Vary"] = ", ".join(sorted(vary_values))
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    requested_headers = request.headers.get("Access-Control-Request-Headers", "")
    response.headers["Access-Control-Allow-Headers"] = requested_headers or "Authorization, Content-Type"
    response.headers["Access-Control-Max-Age"] = "600"
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


def _cleanup_expired_pdf_compression_jobs() -> None:
    now = datetime.now(UTC)
    expired: list[str] = []
    for job_id, job in list(_PDF_COMPRESS_JOBS.items()):
        created_at_raw = str(job.get("created_at") or "")
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = now
        if (now - created_at).total_seconds() > PDF_COMPRESS_JOB_RETENTION_SECONDS:
            expired.append(job_id)

    for job_id in expired:
        job = _PDF_COMPRESS_JOBS.pop(job_id, None) or {}
        for key in ("source_path", "output_path"):
            path = str(job.get(key) or "")
            if path:
                try:
                    resolved = Path(path).resolve()
                    if _is_path_under(resolved, PDF_COMPRESS_DIR.resolve()) and resolved.exists():
                        resolved.unlink()
                except OSError:
                    pass


def _safe_remove_temp_file(path: str | os.PathLike[str]) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _safe_remove_local_job_path(path: str | os.PathLike[str] | Path | None) -> bool:
    if not path:
        return False
    try:
        target = Path(path)
        if not target.is_absolute():
            target = Path(app.root_path) / target
        resolved = target.resolve()
    except (OSError, TypeError):
        return False
    allowed_roots = [
        Path(UPLOAD_DIR).resolve(),
        (Path(app.root_path) / "output").resolve(),
        (Path(app.root_path) / "reports").resolve(),
    ]
    if not any(_is_path_under(resolved, root) for root in allowed_roots):
        return False
    try:
        if resolved.is_file():
            resolved.unlink()
            return True
        artifact_root = (Path(app.root_path) / "output" / "artifacts").resolve()
        if resolved.is_dir() and _is_path_under(resolved, artifact_root):
            shutil.rmtree(resolved)
            return True
    except OSError:
        return False
    return False


def _cleanup_deleted_conversion_job_files(job_id: str, job: dict) -> dict:
    removed: set[Path] = set()

    def remove_once(path: str | os.PathLike[str] | Path | None) -> None:
        if not path:
            return
        try:
            target = Path(path)
            resolved = (Path(app.root_path) / target).resolve() if not target.is_absolute() else target.resolve()
        except (OSError, TypeError):
            return
        if resolved in removed:
            return
        if _safe_remove_local_job_path(resolved):
            removed.add(resolved)

    remove_once(job.get("source_path"))
    remove_once(job.get("output_path"))

    artifacts = dict(job.get("artifacts", {}) or {})
    artifact_job_dirs: set[Path] = set()
    artifact_root = (Path(app.root_path) / "output" / "artifacts").resolve()
    for artifact in artifacts.values():
        if not isinstance(artifact, dict):
            continue
        artifact_path = _resolve_local_artifact_path(artifact)
        if artifact_path is not None:
            remove_once(artifact_path)
            try:
                relative = artifact_path.resolve().relative_to(artifact_root)
                if relative.parts:
                    artifact_job_dirs.add(artifact_root / relative.parts[0])
            except (OSError, ValueError):
                pass

    for artifact_dir in artifact_job_dirs:
        remove_once(artifact_dir)

    return {"removed_files": len(removed), "job_id": job_id}


def _delete_supabase_conversion_job(token: str, user_id: str, job_id: str) -> dict:
    if not token or not user_id or not job_id:
        return {"status": "skipped", "provider": "supabase", "reason": "missing_auth"}
    safe_user = quote(user_id, safe="")
    safe_job = quote(job_id, safe="")
    artifact_status, artifact_payload = _supabase_request_json(
        f"/rest/v1/conversion_artifacts?user_id=eq.{safe_user}&job_id=eq.{safe_job}",
        token=token,
        method="DELETE",
        prefer="return=representation",
    )
    job_status, job_payload = _supabase_request_json(
        f"/rest/v1/conversion_jobs?user_id=eq.{safe_user}&job_id=eq.{safe_job}",
        token=token,
        method="DELETE",
        prefer="return=representation",
    )
    if artifact_status in {200, 204} and job_status in {200, 204}:
        deleted_rows = 0
        if isinstance(artifact_payload, list):
            deleted_rows += len(artifact_payload)
        if isinstance(job_payload, list):
            deleted_rows += len(job_payload)
        return {"status": "deleted", "provider": "supabase", "deleted_rows": deleted_rows}
    return {
        "status": "failed",
        "provider": "supabase",
        "artifact_status": artifact_status,
        "job_status": job_status,
    }


def _pdf_compression_source_warnings(path: Path) -> list[str]:
    try:
        pdf_type = detect_pdf_type(str(path))
    except Exception:
        return []
    warnings: list[str] = []
    if pdf_type.get("is_scanned") or float(pdf_type.get("scanned_page_ratio") or 0.0) > 0.35:
        warnings.append("PDF wyglada na skan; po kompresji sprawdz jakosc OCR i drobny tekst.")
    if pdf_type.get("has_images") and not pdf_type.get("has_text_layer"):
        warnings.append("PDF jest obrazowy; zbyt mocna kompresja moze pogorszyc diagramy i rozpoznawanie pozycji.")
    return warnings


def _download_remote_pdf_artifact(url: str, target_path: Path) -> None:
    if not url:
        raise PdfCompressionFailed("Missing signed URL for source PDF artifact.")
    request = urllib.request.Request(url, headers={"User-Agent": "KindleMaster PDF compression"})
    try:
        with urllib.request.urlopen(request, timeout=PDF_COMPRESS_REMOTE_DOWNLOAD_TIMEOUT_SECONDS) as response:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except (OSError, urllib.error.URLError) as error:
        raise PdfCompressionFailed(f"Could not download source PDF artifact: {error}") from error
    if not target_path.is_file() or target_path.stat().st_size <= 0:
        raise PdfCompressionFailed("Downloaded source PDF artifact is empty.")


def _send_remote_artifact_proxy(artifact: dict, *, job_id: str, artifact_key: str):
    signed_url = _signed_artifact_url(artifact)
    if not signed_url:
        response = _json_error(
            "Artefakt nie jest dostepny lokalnie.",
            error_code="source_artifact_unavailable" if artifact_key == "input" else ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "missing"
        return response
    request_obj = urllib.request.Request(signed_url, headers={"User-Agent": "KindleMaster artifact proxy"})
    try:
        with urllib.request.urlopen(request_obj, timeout=PDF_COMPRESS_REMOTE_DOWNLOAD_TIMEOUT_SECONDS) as remote:
            data = remote.read()
    except urllib.error.HTTPError as error:
        status_code = int(getattr(error, "code", 0) or 502)
        if status_code in {403, 404, 410}:
            response = _json_error(
                "PDF zrodlowy nie jest juz dostepny w magazynie artefaktow.",
                error_code="source_artifact_unavailable" if artifact_key == "input" else ERROR_MISSING_OUTPUT,
                status_code=410 if status_code == 410 else 404,
                phase="download",
                job_id=job_id,
                retryable=False,
            )
            response.headers["X-KindleMaster-Artifact-Source"] = "missing"
            response.headers["X-KindleMaster-Remote-Status"] = str(status_code)
            return response
        response = _json_error(
            "Nie udalo sie pobrac zdalnego artefaktu.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=502,
            phase="download",
            job_id=job_id,
            retryable=True,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "remote"
        response.headers["X-KindleMaster-Remote-Status"] = str(status_code)
        return response
    except (OSError, urllib.error.URLError) as error:
        response = _json_error(
            f"Nie udalo sie pobrac zdalnego artefaktu: {error}",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=502,
            phase="download",
            job_id=job_id,
            retryable=True,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "remote"
        return response
    if not data:
        response = _json_error(
            "Zdalny artefakt jest pusty.",
            error_code="source_artifact_unavailable" if artifact_key == "input" else ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "missing"
        return response
    filename = str(artifact.get("filename") or f"{artifact_key}.bin")
    response = send_file(
        io.BytesIO(data),
        mimetype=str(artifact.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"),
        as_attachment=False,
        download_name=filename,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Proxy"] = "remote"
    response.headers["X-KindleMaster-Artifact-Source"] = "remote"
    return response


def _pdf_source_fallback_roots() -> list[Path]:
    roots: list[Path] = [Path(UPLOAD_DIR)]
    configured = os.environ.get("KINDLEMASTER_SOURCE_FALLBACK_DIRS", "")
    for raw_path in configured.split(os.pathsep):
        raw_path = raw_path.strip()
        if raw_path:
            roots.append(Path(raw_path))
    home = Path.home()
    if home:
        roots.extend([home / "Downloads", home / "Desktop"])
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen:
            unique.append(resolved)
            seen.add(key)
    return unique


def _find_local_pdf_source_fallback(filename: str, expected_size: int = 0) -> Path | None:
    safe_name = Path(str(filename or "")).name
    if not safe_name.lower().endswith(".pdf"):
        return None
    for root in _pdf_source_fallback_roots():
        candidate = root / safe_name
        try:
            if not candidate.is_file():
                continue
            if expected_size > 0 and candidate.stat().st_size != expected_size:
                continue
            return candidate.resolve()
        except OSError:
            continue
    return None


def _send_local_input_artifact_fallback(job_id: str, job: dict, artifact: dict):
    filename = str(artifact.get("filename") or job.get("filename") or f"{job_id}.pdf").strip() or f"{job_id}.pdf"
    fallback_path = _find_local_pdf_source_fallback(filename, int(artifact.get("size_bytes") or 0))
    if fallback_path is None:
        return None
    artifact.update(
        {
            "provider": "local",
            "status": "stored",
            "kind": "input",
            "job_id": job_id,
            "filename": filename,
            "location": str(fallback_path),
            "size_bytes": fallback_path.stat().st_size,
            "content_type": "application/pdf",
            "signed_url": {"available": False, "url": "", "expires_in_seconds": 0, "reason": "local_fallback"},
        }
    )
    artifacts = dict(job.get("artifacts", {}) or {})
    artifacts["input"] = artifact
    job["artifacts"] = artifacts
    _CONVERSION_JOB_STORE.create(job)
    response = send_file(
        io.BytesIO(fallback_path.read_bytes()),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Source"] = "fallback"
    return response


def _resolve_job_source_pdf_for_compression(job_id: str, job: dict) -> tuple[Path, str, bool]:
    artifacts = dict(job.get("artifacts", {}) or {})
    input_artifact = artifacts.get("input")
    if not isinstance(input_artifact, dict):
        raise PdfCompressionFailed("No preserved source PDF artifact is available for this job.")

    filename = str(input_artifact.get("filename") or job.get("filename") or f"{job_id}.pdf").strip() or f"{job_id}.pdf"
    local_path = _resolve_local_artifact_path(input_artifact)
    if local_path is not None:
        return local_path, filename, False

    local_fallback = _find_local_pdf_source_fallback(filename, int(input_artifact.get("size_bytes") or 0))
    if local_fallback is not None:
        return local_fallback, filename, False

    signed_url = _signed_artifact_url(input_artifact) or str(input_artifact.get("download_url") or "").strip()
    if not signed_url:
        raise PdfCompressionFailed("Source PDF artifact is not locally available and has no signed URL.")
    source_path = PDF_COMPRESS_DIR / f"{job_id}.artifact-source.pdf"
    _download_remote_pdf_artifact(signed_url, source_path)
    return source_path, filename, True


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


def _safe_artifact_key(value: str) -> str:
    key = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in str(value or "").strip())
    return key.strip("_") or "artifact"


def _store_extra_conversion_artifacts(job_id: str, extra_artifacts: list[dict] | None) -> dict[str, dict]:
    stored: dict[str, dict] = {}
    for index, artifact in enumerate(extra_artifacts or [], start=1):
        if not isinstance(artifact, dict):
            continue
        data = artifact.get("data")
        if isinstance(data, str):
            data_bytes = data.encode("utf-8")
        elif isinstance(data, bytes):
            data_bytes = data
        else:
            continue
        key = _safe_artifact_key(str(artifact.get("key") or f"artifact_{index}"))
        filename = str(artifact.get("filename") or f"{key}.bin").strip() or f"{key}.bin"
        metadata = _store_artifact_bytes(
            job_id=job_id,
            kind=ArtifactKind.REPORT,
            filename=filename,
            data=data_bytes,
        )
        content_type = str(artifact.get("content_type") or "").strip()
        if content_type:
            metadata["content_type"] = content_type
        metadata["download_url"] = f"/convert/artifact/{job_id}/{key}"
        metadata["label"] = str(artifact.get("label") or key).strip() or key
        stored[key] = metadata
    return stored


def _local_artifact_metadata(job_id: str, kind: ArtifactKind, path: Path) -> dict:
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0
    return {
        "provider": "local",
        "status": "stored",
        "kind": kind.value,
        "job_id": job_id,
        "filename": path.name,
        "location": str(path),
        "size_bytes": size_bytes,
        "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "retention": {},
        "signed_url": {
            "available": False,
            "url": "",
            "expires_in_seconds": 0,
            "reason": "local_storage",
        },
        "error": "",
    }


def _read_json_file(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_file(root: Path, pattern: str) -> Path | None:
    if not root.is_dir():
        return None
    try:
        return next(root.glob(pattern))
    except StopIteration:
        return None


def _source_type_from_filename(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix if suffix in {"pdf", "docx"} else "pdf"


def _rebuild_job_from_local_artifact_dir(job_dir: Path) -> dict | None:
    job_id = job_dir.name
    if not job_id or job_id.startswith("quality-"):
        return None

    input_file = _first_file(job_dir / "input", "*")
    output_file = _first_file(job_dir / "output", "*.epub")
    quality_json_file = _first_file(job_dir / "report", "*.quality.json")
    markdown_report_file = _first_file(job_dir / "report", "*.quality.md")
    chess_pgn_file = _first_file(job_dir / "report", "*.pgn")
    chess_pgn_html_file = _first_file(job_dir / "report", "*.html")
    runtime_json_file = _first_file(job_dir / "log", "*.runtime.json")
    if input_file is None and output_file is None and quality_json_file is None:
        return None

    quality_payload = _read_json_file(quality_json_file)
    runtime_payload = _read_json_file(runtime_json_file)
    report_job = quality_payload.get("job") if isinstance(quality_payload.get("job"), dict) else {}
    runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
    quality_state = quality_payload.get("quality_state") if isinstance(quality_payload.get("quality_state"), dict) else {}

    filename = str(report_job.get("filename") or "").strip()
    if not filename and input_file is not None:
        filename = input_file.name
    if not filename and output_file is not None:
        filename = output_file.with_suffix(".pdf").name
    filename = filename or f"{job_id}.pdf"

    source_type = str(report_job.get("source_type") or "").strip().lower() or _source_type_from_filename(filename)
    created_at = str(report_job.get("created_at") or runtime.get("created_at") or "").strip()
    updated_at = str(report_job.get("updated_at") or runtime.get("updated_at") or "").strip()
    fallback_time = datetime.fromtimestamp(job_dir.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
    created_at = created_at or fallback_time
    updated_at = updated_at or created_at

    status = str(report_job.get("status") or runtime_payload.get("status") or "").strip().lower()
    runtime_status = str(runtime.get("status") or "").strip().lower()
    if status not in {"queued", "running", "repairing_headings", "ready", "failed", "timed_out"}:
        status = "ready" if output_file is not None or runtime_status == "succeeded" else "failed"

    source_path = str(input_file) if input_file is not None else ""
    job = build_conversion_job_record(
        job_id=job_id,
        source_path=source_path,
        source_type=source_type,
        filename=filename,
        created_at=created_at,
    )
    job.update(
        {
            "status": status,
            "message": str(report_job.get("message") or quality_state.get("message") or ("EPUB gotowy do pobrania." if status == "ready" else "Historia odtworzona z lokalnych artefaktow.")),
            "updated_at": updated_at,
            "output_path": str(output_file) if output_file is not None else "",
            "download_name": str(report_job.get("download_name") or (output_file.name if output_file is not None else Path(filename).with_suffix(".epub").name)),
            "metadata": dict(report_job.get("metadata") or quality_payload.get("metadata") or {}),
            "runtime": dict(runtime),
            "progress": dict(report_job.get("progress") or job.get("progress") or {}),
            "output_size_bytes": int(report_job.get("output_size_bytes") or (output_file.stat().st_size if output_file is not None else 0)),
            "error": str(report_job.get("error") or ""),
            "error_code": str(report_job.get("error_code") or ""),
            "artifact_storage": _artifact_storage_status(),
            "restored_from_artifacts": True,
        }
    )

    artifacts: dict[str, dict] = {}
    if input_file is not None:
        artifacts["input"] = _local_artifact_metadata(job_id, ArtifactKind.INPUT, input_file)
    if output_file is not None:
        artifacts["output"] = _local_artifact_metadata(job_id, ArtifactKind.OUTPUT, output_file)
    if quality_json_file is not None:
        artifacts["report_json"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, quality_json_file)
    if markdown_report_file is not None:
        artifacts["report_markdown"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, markdown_report_file)
    if chess_pgn_file is not None:
        artifacts["chess_pgn"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, chess_pgn_file)
        artifacts["chess_pgn"]["download_url"] = f"/convert/artifact/{job_id}/chess_pgn"
        artifacts["chess_pgn"]["label"] = "PGN"
    if chess_pgn_html_file is not None:
        artifacts["chess_pgn_html"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, chess_pgn_html_file)
        artifacts["chess_pgn_html"]["download_url"] = f"/convert/artifact/{job_id}/chess_pgn_html"
        artifacts["chess_pgn_html"]["label"] = "HTML PGN/FEN"
    if runtime_json_file is not None:
        artifacts["log"] = _local_artifact_metadata(job_id, ArtifactKind.LOG, runtime_json_file)
    job["artifacts"] = artifacts
    return job


def _rebuild_jobs_from_smoke_reports(existing: dict[str, dict]) -> tuple[list[dict], int, int]:
    reports_root = Path(app.root_path) / "reports" / "smoke"
    imported_jobs: list[dict] = []
    skipped = 0
    failed = 0
    for report_path in sorted(reports_root.glob("smoke_*.json")):
        payload = _read_json_file(report_path)
        cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
        for case in cases:
            if not isinstance(case, dict):
                continue
            case_id = str(case.get("id") or "").strip()
            if not case_id:
                skipped += 1
                continue
            job_id = f"smoke-{case_id.replace('_', '-')}"
            if job_id in existing:
                skipped += 1
                continue
            output_path = Path(str(case.get("output_epub") or ""))
            if not output_path.is_absolute():
                output_path = Path(app.root_path) / output_path
            if not output_path.is_file():
                skipped += 1
                continue
            source_path = Path(str(case.get("path") or ""))
            filename = source_path.name or f"{case_id}.pdf"
            source_type = str(case.get("input_type") or _source_type_from_filename(filename) or "pdf")
            updated_at = datetime.fromtimestamp(
                max(output_path.stat().st_mtime, report_path.stat().st_mtime),
                tz=UTC,
            ).isoformat().replace("+00:00", "Z")
            job = build_conversion_job_record(
                job_id=job_id,
                source_path=str(source_path) if source_path else "",
                source_type=source_type,
                filename=filename,
                created_at=updated_at,
            )
            analysis = case.get("analysis") if isinstance(case.get("analysis"), dict) else {}
            quality_report = case.get("quality_report") if isinstance(case.get("quality_report"), dict) else {}
            validation = case.get("validation") if isinstance(case.get("validation"), dict) else {}
            validation_summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}
            metadata = {
                "source_type": source_type,
                "profile": str(analysis.get("profile") or ""),
                "confidence": float(analysis.get("confidence") or 0.0),
                "validation": str(validation_summary.get("status") or quality_report.get("validation_status") or "unavailable"),
                "validation_tool": str(quality_report.get("validation_tool") or "unknown"),
                "sections": int(quality_report.get("section_count") or 0),
                "assets": int(quality_report.get("diagram_count") or 0),
                "layout": "reflowable",
                "warnings": 0,
                "warning_list": [],
                "content_metrics": quality_report,
                "final_output_size_bytes": int(case.get("epub_size_bytes") or output_path.stat().st_size),
            }
            job.update(
                {
                    "status": "ready",
                    "message": "EPUB odtworzony z ostatniego smoke testu.",
                    "updated_at": updated_at,
                    "output_path": str(output_path),
                    "download_name": output_path.name,
                    "metadata": metadata,
                    "output_size_bytes": output_path.stat().st_size,
                    "artifact_storage": _artifact_storage_status(),
                    "restored_from_smoke": True,
                    "artifacts": {
                        "output": _local_artifact_metadata(job_id, ArtifactKind.OUTPUT, output_path),
                        "report_json": _local_artifact_metadata(job_id, ArtifactKind.REPORT, report_path),
                    },
                }
            )
            imported_jobs.append(job)
            existing[job_id] = job
    return imported_jobs, skipped, failed


def _rebuild_jobs_from_smoke_epubs(existing: dict[str, dict]) -> tuple[list[dict], int, int]:
    smoke_root = Path(app.root_path) / "output" / "smoke"
    imported_jobs: list[dict] = []
    skipped = 0
    failed = 0
    for output_path in sorted(smoke_root.glob("*.epub"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True):
        case_id = output_path.stem
        job_id = f"smoke-{case_id.replace('_', '-')}"
        if job_id in existing:
            skipped += 1
            continue
        filename = f"{case_id}.pdf"
        if case_id == "fundamenty_scan_chess_pdf":
            filename = "Fundamenty-1-1.pdf"
        updated_at = datetime.fromtimestamp(output_path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
        job = build_conversion_job_record(
            job_id=job_id,
            source_path="",
            source_type="pdf",
            filename=filename,
            created_at=updated_at,
        )
        output_size = output_path.stat().st_size
        job.update(
            {
                "status": "ready",
                "message": "EPUB odtworzony z lokalnego smoke output.",
                "updated_at": updated_at,
                "output_path": str(output_path),
                "download_name": output_path.name,
                "metadata": {
                    "source_type": "pdf",
                    "profile": "premium_scanned_chess_reflow" if case_id == "fundamenty_scan_chess_pdf" else "smoke",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "layout": "reflowable",
                    "warnings": 0,
                    "warning_list": [],
                    "final_output_size_bytes": output_size,
                    "output_size_bytes": output_size,
                },
                "output_size_bytes": output_size,
                "artifact_storage": _artifact_storage_status(),
                "restored_from_smoke": True,
                "artifacts": {
                    "output": _local_artifact_metadata(job_id, ArtifactKind.OUTPUT, output_path),
                },
            }
        )
        imported_jobs.append(job)
        existing[job_id] = job
    return imported_jobs, skipped, failed


def _import_local_artifact_history() -> dict:
    configured_root = os.environ.get("KINDLEMASTER_ARTIFACT_ROOT")
    root = Path(configured_root) if configured_root else Path(app.root_path) / "output" / "artifacts"

    existing = _CONVERSION_JOB_STORE.snapshot()
    imported = 0
    skipped = 0
    failed = 0
    if root.is_dir():
        for job_dir in sorted(root.iterdir(), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True):
            if not job_dir.is_dir():
                continue
            if job_dir.name in existing:
                skipped += 1
                continue
            job = _rebuild_job_from_local_artifact_dir(job_dir)
            if job is None:
                skipped += 1
                continue
            try:
                _CONVERSION_JOB_STORE.create(job)
            except Exception:
                failed += 1
                continue
            existing[job["job_id"]] = job
            imported += 1

    smoke_jobs, smoke_skipped, smoke_failed = _rebuild_jobs_from_smoke_reports(existing)
    skipped += smoke_skipped
    failed += smoke_failed
    for job in smoke_jobs:
        try:
            _CONVERSION_JOB_STORE.create(job)
        except Exception:
            failed += 1
            continue
        imported += 1
    smoke_epub_jobs, smoke_epub_skipped, smoke_epub_failed = _rebuild_jobs_from_smoke_epubs(existing)
    skipped += smoke_epub_skipped
    failed += smoke_epub_failed
    for job in smoke_epub_jobs:
        try:
            _CONVERSION_JOB_STORE.create(job)
        except Exception:
            failed += 1
            continue
        imported += 1
    return {
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "source": f"{root}; {Path(app.root_path) / 'reports' / 'smoke'}; {Path(app.root_path) / 'output' / 'smoke'}",
    }


def _ensure_local_artifact_history_loaded() -> dict:
    return _import_local_artifact_history()


def _restore_local_artifact_job_by_id(job_id: str) -> dict | None:
    safe_job_id = str(job_id or "").strip()
    if not safe_job_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_job_id):
        return None

    configured_root = os.environ.get("KINDLEMASTER_ARTIFACT_ROOT")
    root = (Path(configured_root) if configured_root else Path(app.root_path) / "output" / "artifacts").resolve()
    job_dir = (root / safe_job_id).resolve()
    if not _is_path_under(job_dir, root) or not job_dir.is_dir():
        return None

    job = _rebuild_job_from_local_artifact_dir(job_dir)
    if job is None:
        return None
    try:
        _CONVERSION_JOB_STORE.create(job)
    except Exception:
        existing = _get_conversion_job(safe_job_id)
        return existing if existing else job
    return _get_conversion_job(safe_job_id) or job


def _is_path_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_local_artifact_path(artifact: dict | None) -> Path | None:
    if not isinstance(artifact, dict):
        return None
    location = str(artifact.get("location") or "").strip()
    if not location:
        return None
    path = Path(location)
    if not path.is_absolute():
        path = Path(app.root_path) / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    configured_artifact_root = os.environ.get("KINDLEMASTER_ARTIFACT_ROOT")
    allowed_roots = [
        (Path(app.root_path) / "output" / "artifacts").resolve(),
        Path(UPLOAD_DIR).resolve(),
    ]
    if configured_artifact_root:
        try:
            allowed_roots.append(Path(configured_artifact_root).resolve())
        except OSError:
            pass
    if not any(_is_path_under(resolved, root) for root in allowed_roots):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _resolve_retry_source_path(job: dict) -> Path | None:
    """Return a persisted upload path that is safe to reuse for retry."""
    if not isinstance(job, dict):
        return None

    candidates: list[Path] = []
    source_path = str(job.get("source_path") or "").strip()
    if source_path:
        candidates.append(Path(source_path))

    job_id = str(job.get("job_id") or "").strip()
    filename = str(job.get("filename") or "").strip()
    source_type = detect_supported_source_type(filename) or str(job.get("source_type") or "").strip().lower()
    if job_id and source_type in {"pdf", "docx"}:
        candidates.append(Path(UPLOAD_DIR) / f"{job_id}.{source_type}")

    upload_root = Path(UPLOAD_DIR).resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not _is_path_under(resolved, upload_root):
            continue
        if resolved.is_file():
            return resolved
    return None


def _read_retry_input_artifact(job: dict) -> tuple[bytes, str]:
    input_artifact = (job.get("artifacts", {}) or {}).get("input")
    artifact_path = _resolve_local_artifact_path(input_artifact if isinstance(input_artifact, dict) else None)
    filename = ""
    if isinstance(input_artifact, dict):
        filename = str(input_artifact.get("filename") or "").strip()
    fallback_path = _resolve_retry_source_path(job)
    source_path = artifact_path or fallback_path
    if source_path is None:
        return b"", filename or str(job.get("filename") or "").strip()
    filename = filename or str(job.get("filename") or "").strip() or source_path.name
    try:
        return source_path.read_bytes(), filename
    except OSError:
        return b"", filename


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
    return _signed_artifact_url(output_artifact if isinstance(output_artifact, dict) else None)


def _signed_artifact_url(artifact: dict | None) -> str:
    if not isinstance(artifact, dict):
        return ""
    signed_url = artifact.get("signed_url")
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
    route_model_mode: str = "shadow",
    quality_gate_mode: str = "draft",
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
            "route_model_mode": route_model_mode,
            "quality_gate_mode": quality_gate_mode,
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


def _resolve_server_host() -> str:
    return runtime_resolve_server_host()


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
    snapshot = job.get("quality_state_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        quality_state = dict(snapshot)
        download_state = _build_job_download_state(job_id, job)
        quality_state["download_available"] = download_state.download_available
        quality_state["download_state"] = download_state.to_dict()
        artifacts = dict(quality_state.get("artifacts", {}) or {})
        artifacts.update(dict(job.get("artifacts", {}) or {}))
        quality_state["artifacts"] = artifacts
        return quality_state
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
    return _CONVERSION_JOB_STORE.get(job_id) or _recover_orphan_conversion_job(job_id)


def _is_conversion_job_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return len(normalized) == 32 and all(char in "0123456789abcdef" for char in normalized)


def _artifact_timestamp_label(path: str) -> str:
    try:
        timestamp = datetime.fromtimestamp(os.path.getmtime(path), UTC)
    except OSError:
        timestamp = datetime.now(UTC)
    return timestamp.isoformat().replace("+00:00", "Z")


def _recover_orphan_conversion_job(job_id: str) -> dict | None:
    """Rebuild a safe terminal job state when persistence was lost mid-flow."""

    if not _is_conversion_job_id(job_id):
        return None
    source_path = ""
    source_type = "pdf"
    for suffix in (".pdf", ".docx"):
        candidate = os.path.join(UPLOAD_DIR, f"{job_id}{suffix}")
        if os.path.exists(candidate):
            source_path = candidate
            source_type = suffix.lstrip(".")
            break
    output_path = os.path.join(UPLOAD_DIR, f"{job_id}.epub")
    output_exists = os.path.exists(output_path)
    if not source_path and not output_exists:
        return None

    now_label = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    artifact_label = _artifact_timestamp_label(output_path if output_exists else source_path)
    filename = os.path.basename(source_path) if source_path else f"{job_id}.{source_type}"
    if output_exists:
        recovered = build_conversion_job_record(
            job_id=job_id,
            source_path=source_path,
            source_type=source_type,
            filename=filename,
            created_at=artifact_label,
        )
        recovered.update(
            {
                "status": "ready",
                "message": "EPUB odzyskany po utracie stanu zadania.",
                "updated_at": now_label,
                "output_path": output_path,
                "download_name": f"{Path(filename).stem}.epub",
                "metadata": {
                    "source_type": source_type,
                    "quality_available": False,
                    "recovered_orphan_job": True,
                },
                "output_size_bytes": os.path.getsize(output_path),
                "error": "",
                "error_code": "",
            }
        )
    else:
        recovered = build_conversion_job_record(
            job_id=job_id,
            source_path=source_path,
            source_type=source_type,
            filename=filename,
            created_at=artifact_label,
        )
        recovered.update(
            {
                "status": "failed",
                "message": "Konwersja przerwana przez restart aplikacji.",
                "updated_at": now_label,
                "error": "Lokalna aplikacja utracila stan zadania w trakcie konwersji. Uruchom konwersje ponownie.",
                "error_code": "application_restart",
            }
        )
    _CONVERSION_JOB_STORE.create(recovered)
    _log_conversion_event(
        "convert.job.recovered_orphan",
        level="warning",
        job_id=job_id,
        phase="recovery",
        status=str(recovered.get("status", "") or ""),
        error_code=str(recovered.get("error_code", "") or ""),
        safe_message=str(recovered.get("message", "") or ""),
        source_type=source_type,
        output_size_bytes=int(recovered.get("output_size_bytes", 0) or 0),
    )
    return dict(recovered)


def _default_user_profile() -> dict:
    return {
        "conversion": {
            "default_profile": "auto-premium",
            "default_language": "pl",
            "force_ocr": False,
            "heading_repair": True,
        },
        "email_delivery": {
            "enabled": False,
            "host": "",
            "port": 587,
            "security": "starttls",
            "username": "",
            "from_address": "",
            "default_recipient": "",
            "max_attachment_bytes": 50 * 1024 * 1024,
            "secret_configured": bool(_smtp_secret_env_value()),
            "secret_registered": False,
        },
    }


def _user_profile_path() -> Path:
    configured_path = os.environ.get("KINDLEMASTER_USER_PROFILE_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return appdata / "KindleMaster" / "profile.json"


def _safe_bool(value, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value, fallback: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _smtp_secret_env_value() -> str:
    for name in (
        "KINDLEMASTER_SMTP_PASSWORD",
        "KINDLEMASTER_SMTP_API_KEY",
        "SMTP_PASSWORD",
    ):
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _smtp_secret_env_name() -> str:
    for name in (
        "KINDLEMASTER_SMTP_PASSWORD",
        "KINDLEMASTER_SMTP_API_KEY",
        "SMTP_PASSWORD",
    ):
        if os.environ.get(name):
            return name
    return ""


def _normalize_user_profile(payload) -> dict:
    source = payload if isinstance(payload, dict) else {}
    if isinstance(source.get("profile"), dict):
        source = source["profile"]
    default = _default_user_profile()
    conversion = source.get("conversion") if isinstance(source.get("conversion"), dict) else {}
    email_delivery = source.get("email_delivery") if isinstance(source.get("email_delivery"), dict) else {}
    normalized = {
        "conversion": {
            "default_profile": str(
                conversion.get("default_profile")
                or default["conversion"]["default_profile"]
            ),
            "default_language": str(
                conversion.get("default_language")
                or default["conversion"]["default_language"]
            ),
            "force_ocr": _safe_bool(
                conversion.get("force_ocr"),
                default["conversion"]["force_ocr"],
            ),
            "heading_repair": _safe_bool(
                conversion.get("heading_repair"),
                default["conversion"]["heading_repair"],
            ),
        },
        "email_delivery": {
            "enabled": _safe_bool(
                email_delivery.get("enabled"),
                default["email_delivery"]["enabled"],
            ),
            "host": str(email_delivery.get("host") or ""),
            "port": _safe_int(
                email_delivery.get("port"),
                default["email_delivery"]["port"],
                minimum=1,
                maximum=65535,
            ),
            "security": str(email_delivery.get("security") or "starttls"),
            "username": str(email_delivery.get("username") or ""),
            "from_address": str(email_delivery.get("from_address") or ""),
            "default_recipient": str(email_delivery.get("default_recipient") or ""),
            "max_attachment_bytes": _safe_int(
                email_delivery.get("max_attachment_bytes"),
                default["email_delivery"]["max_attachment_bytes"],
                minimum=1,
            ),
            "secret_configured": bool(_smtp_secret_env_value()),
            "secret_registered": _safe_bool(email_delivery.get("secret_configured"), False)
            or _safe_bool(email_delivery.get("secret_registered"), False),
        },
    }
    if normalized["conversion"]["default_profile"] not in {
        "auto-premium",
        "book",
        "magazine",
        "technical-study",
        "preserve-layout",
    }:
        normalized["conversion"]["default_profile"] = default["conversion"]["default_profile"]
    if normalized["conversion"]["default_language"] not in {"pl", "en"}:
        normalized["conversion"]["default_language"] = default["conversion"]["default_language"]
    if normalized["email_delivery"]["security"] not in {"starttls", "ssl", "none"}:
        normalized["email_delivery"]["security"] = "starttls"
    return normalized


def _load_user_profile() -> dict:
    path = _user_profile_path()
    try:
        if path.exists():
            return _normalize_user_profile(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return _default_user_profile()


def _save_user_profile(profile: dict) -> dict:
    normalized = _normalize_user_profile(profile)
    path = _user_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def _request_bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()


def _supabase_public_settings() -> tuple[str, str]:
    auth_config = _public_auth_config()
    if not auth_config.get("configured"):
        return "", ""
    return str(auth_config["supabase_url"]).rstrip("/"), str(auth_config["publishable_key"])


def _supabase_request_json(
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict | None = None,
    prefer: str = "",
) -> tuple[int, object]:
    supabase_url, publishable_key = _supabase_public_settings()
    if not supabase_url or not publishable_key or not token:
        return 0, {}
    body = None
    headers = {
        "apikey": publishable_key,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    request_obj = urllib.request.Request(
        f"{supabase_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw}
        return exc.code, parsed
    except (OSError, TimeoutError, json.JSONDecodeError):
        return 0, {}


def _supabase_request_bytes(
    path: str,
    *,
    token: str,
    method: str,
    data: bytes,
    content_type: str,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, object]:
    supabase_url, publishable_key = _supabase_public_settings()
    if not supabase_url or not publishable_key or not token:
        return 0, {}
    headers = {
        "apikey": publishable_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type or "application/octet-stream",
        "Accept": "application/json",
    }
    headers.update(extra_headers or {})
    request_obj = urllib.request.Request(
        f"{supabase_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"message": raw}
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"message": raw}
        return exc.code, payload
    except (OSError, TimeoutError):
        return 0, {}


def _supabase_auth_user(token: str) -> dict | None:
    status, payload = _supabase_request_json("/auth/v1/user", token=token)
    if status != 200 or not isinstance(payload, dict) or not payload.get("id"):
        return None
    return payload


def _profile_from_supabase_row(row: dict | None) -> dict:
    if not isinstance(row, dict):
        return _default_user_profile()
    return _normalize_user_profile(
        {
            "conversion": row.get("conversion_defaults") or {},
            "email_delivery": row.get("smtp_defaults") or {},
        }
    )


def _profile_to_supabase_row(user_id: str, profile: dict) -> dict:
    normalized = _normalize_user_profile(profile)
    smtp_defaults = dict(normalized["email_delivery"])
    smtp_defaults["secret_configured"] = bool(
        smtp_defaults.get("secret_configured") or smtp_defaults.get("secret_registered")
    )
    smtp_defaults.pop("secret_registered", None)
    return {
        "user_id": user_id,
        "conversion_defaults": normalized["conversion"],
        "smtp_defaults": smtp_defaults,
    }


def _load_supabase_user_profile(token: str, user_id: str) -> dict | None:
    query = (
        "/rest/v1/user_profiles"
        f"?user_id=eq.{quote(user_id, safe='')}"
        "&select=user_id,conversion_defaults,smtp_defaults,updated_at"
        "&limit=1"
    )
    status, payload = _supabase_request_json(query, token=token)
    if status != 200 or not isinstance(payload, list) or not payload:
        return None
    return _profile_from_supabase_row(payload[0])


def _save_supabase_user_profile(token: str, user_id: str, profile: dict) -> dict | None:
    row = _profile_to_supabase_row(user_id, profile)
    status, payload = _supabase_request_json(
        "/rest/v1/user_profiles?on_conflict=user_id",
        token=token,
        method="POST",
        payload=row,
        prefer="resolution=merge-duplicates,return=representation",
    )
    if status not in {200, 201} or not isinstance(payload, list) or not payload:
        return None
    return _profile_from_supabase_row(payload[0])


def _supabase_storage_object_path(user_id: str, job_id: str, filename: str) -> str:
    safe_user = _safe_artifact_key(user_id)
    safe_job = _safe_artifact_key(job_id)
    safe_filename = Path(str(filename or "artifact.bin")).name or "artifact.bin"
    safe_filename = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in safe_filename)
    safe_filename = safe_filename.strip("._") or "artifact.bin"
    return f"{safe_user}/{safe_job}/{safe_filename}"


def _normalize_supabase_signed_url(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {
            "available": False,
            "url": "",
            "expires_in_seconds": SUPABASE_ARTIFACT_SIGNED_URL_SECONDS,
            "reason": "invalid_signed_url_response",
        }
    signed_url = str(payload.get("signedURL") or payload.get("signedUrl") or payload.get("url") or "").strip()
    if signed_url.startswith("/"):
        supabase_url, _publishable_key = _supabase_public_settings()
        signed_url = f"{supabase_url}{signed_url}" if supabase_url else signed_url
    return {
        "available": bool(signed_url),
        "url": signed_url,
        "expires_in_seconds": SUPABASE_ARTIFACT_SIGNED_URL_SECONDS,
        "reason": "" if signed_url else "missing_signed_url",
    }


def _supabase_create_storage_signed_url(token: str, *, bucket: str, storage_path: str) -> dict:
    status, payload = _supabase_request_json(
        f"/storage/v1/object/sign/{quote(bucket, safe='')}/{quote(storage_path, safe='/')}",
        token=token,
        method="POST",
        payload={"expiresIn": SUPABASE_ARTIFACT_SIGNED_URL_SECONDS},
    )
    if status not in {200, 201}:
        return {
            "available": False,
            "url": "",
            "expires_in_seconds": SUPABASE_ARTIFACT_SIGNED_URL_SECONDS,
            "reason": f"storage_sign_failed_{status}",
        }
    return _normalize_supabase_signed_url(payload)


def _supabase_upload_artifact_bytes(
    token: str,
    *,
    user_id: str,
    job_id: str,
    artifact_key: str,
    artifact: dict,
    data: bytes,
) -> dict:
    filename = str(artifact.get("filename") or f"{artifact_key}.bin").strip() or f"{artifact_key}.bin"
    content_type = str(artifact.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    storage_path = _supabase_storage_object_path(user_id, job_id, filename)
    status, payload = _supabase_request_bytes(
        f"/storage/v1/object/{quote(SUPABASE_ARTIFACT_BUCKET, safe='')}/{quote(storage_path, safe='/')}",
        token=token,
        method="POST",
        data=data,
        content_type=content_type,
        extra_headers={"x-upsert": "true"},
    )
    if status not in {200, 201}:
        return {
            "status": "failed",
            "provider": "supabase",
            "storage_bucket": SUPABASE_ARTIFACT_BUCKET,
            "storage_path": storage_path,
            "error": f"storage_upload_failed_{status}",
            "details": payload if isinstance(payload, dict) else {},
        }
    signed_url = _supabase_create_storage_signed_url(
        token,
        bucket=SUPABASE_ARTIFACT_BUCKET,
        storage_path=storage_path,
    )
    return {
        "status": "stored",
        "provider": "supabase",
        "storage_bucket": SUPABASE_ARTIFACT_BUCKET,
        "storage_path": storage_path,
        "signed_url": signed_url,
        "error": "",
    }


def _read_artifact_bytes_for_cloud(artifact: dict) -> bytes:
    artifact_path = _resolve_local_artifact_path(artifact)
    if artifact_path is None or not artifact_path.is_file():
        return b""
    try:
        return artifact_path.read_bytes()
    except OSError:
        return b""


def _job_to_supabase_row(user_id: str, job_id: str, job: dict) -> dict:
    metadata = dict(job.get("metadata", {}) or {})
    try:
        quality_state_snapshot = _build_job_quality_state(job_id, job)
    except Exception:
        quality_state_snapshot = dict(job.get("quality_state", {}) or {})
    return {
        "job_id": job_id,
        "user_id": user_id,
        "status": str(job.get("status") or "queued"),
        "message": str(job.get("message") or ""),
        "filename": str(job.get("filename") or ""),
        "source_type": str(job.get("source_type") or "pdf"),
        "download_name": str(job.get("download_name") or ""),
        "created_at": str(job.get("created_at") or datetime.now(UTC).isoformat().replace("+00:00", "Z")),
        "updated_at": str(job.get("updated_at") or datetime.now(UTC).isoformat().replace("+00:00", "Z")),
        "elapsed_seconds": _compute_job_history_elapsed_seconds(job),
        "output_size_bytes": int(_read_output_size_bytes(job) or job.get("output_size_bytes") or 0),
        "metadata": metadata,
        "quality_state_snapshot": quality_state_snapshot if isinstance(quality_state_snapshot, dict) else {},
        "auto_repair": dict(job.get("auto_repair", {}) or {}),
        "email_delivery": dict(job.get("email_delivery", {}) or {}),
        "runtime": dict(job.get("runtime", {}) or {}),
        "error": str(job.get("error") or ""),
        "error_code": str(job.get("error_code") or ""),
        "imported_from_local": bool(job.get("restored_from_artifacts") or job.get("imported_from_local")),
    }


def _artifact_to_supabase_row(
    *,
    user_id: str,
    job_id: str,
    artifact_key: str,
    artifact: dict,
    cloud: dict | None = None,
) -> dict:
    cloud = cloud or {}
    retention = artifact.get("retention") if isinstance(artifact.get("retention"), dict) else {}
    storage_bucket = str(cloud.get("storage_bucket") or artifact.get("storage_bucket") or SUPABASE_ARTIFACT_BUCKET)
    storage_path = str(cloud.get("storage_path") or artifact.get("storage_path") or "")
    signed_url = cloud.get("signed_url") if isinstance(cloud.get("signed_url"), dict) else artifact.get("signed_url")
    if not isinstance(signed_url, dict):
        signed_url = {}
    return {
        "job_id": job_id,
        "user_id": user_id,
        "kind": artifact_key,
        "filename": str(artifact.get("filename") or f"{artifact_key}.bin"),
        "content_type": str(artifact.get("content_type") or "application/octet-stream"),
        "size_bytes": int(artifact.get("size_bytes") or 0),
        "storage_bucket": storage_bucket,
        "storage_path": storage_path,
        "signed_url_metadata": signed_url,
        "retention_days": int(retention.get("days") or 30),
    }


def _save_supabase_conversion_job(token: str, user_id: str, job_id: str, job: dict) -> dict:
    row = _job_to_supabase_row(user_id, job_id, job)
    status, payload = _supabase_request_json(
        "/rest/v1/conversion_jobs?on_conflict=job_id",
        token=token,
        method="POST",
        payload=row,
        prefer="resolution=merge-duplicates,return=representation",
    )
    if status not in {200, 201}:
        return {"status": "failed", "provider": "supabase", "error": f"job_upsert_failed_{status}", "payload": payload}
    return {"status": "synced", "provider": "supabase", "error": ""}


def _save_supabase_conversion_artifact(
    token: str,
    *,
    user_id: str,
    job_id: str,
    artifact_key: str,
    artifact: dict,
    cloud: dict,
) -> dict:
    row = _artifact_to_supabase_row(
        user_id=user_id,
        job_id=job_id,
        artifact_key=artifact_key,
        artifact=artifact,
        cloud=cloud,
    )
    status, payload = _supabase_request_json(
        "/rest/v1/conversion_artifacts?on_conflict=job_id,kind",
        token=token,
        method="POST",
        payload=row,
        prefer="resolution=merge-duplicates,return=representation",
    )
    if status not in {200, 201}:
        return {
            "status": "failed",
            "provider": "supabase",
            "artifact_key": artifact_key,
            "error": f"artifact_upsert_failed_{status}",
            "payload": payload,
        }
    return {"status": "synced", "provider": "supabase", "artifact_key": artifact_key, "error": ""}


def _sync_conversion_job_to_supabase(
    job_id: str,
    *,
    token: str,
    user_id: str,
    upload_artifacts: bool = False,
) -> dict:
    if not token or not user_id:
        return {"status": "skipped", "provider": "supabase", "reason": "not_authenticated"}
    job = _get_conversion_job(job_id)
    if not job:
        return {"status": "skipped", "provider": "supabase", "reason": "job_missing"}
    job_sync = _save_supabase_conversion_job(token, user_id, job_id, job)
    artifact_results: list[dict] = []
    artifact_updates: dict[str, dict] = {}
    if upload_artifacts and job_sync.get("status") == "synced":
        for artifact_key, artifact in dict(job.get("artifacts", {}) or {}).items():
            if not isinstance(artifact, dict):
                continue
            data = _read_artifact_bytes_for_cloud(artifact)
            if not data:
                continue
            cloud = _supabase_upload_artifact_bytes(
                token,
                user_id=user_id,
                job_id=job_id,
                artifact_key=str(artifact_key),
                artifact=artifact,
                data=data,
            )
            if cloud.get("status") == "stored":
                updated_artifact = dict(artifact)
                updated_artifact["cloud"] = {
                    "provider": "supabase",
                    "storage_bucket": cloud.get("storage_bucket", ""),
                    "storage_path": cloud.get("storage_path", ""),
                    "status": "stored",
                }
                if isinstance(cloud.get("signed_url"), dict):
                    updated_artifact["signed_url"] = cloud["signed_url"]
                artifact_updates[str(artifact_key)] = updated_artifact
                artifact_results.append(
                    _save_supabase_conversion_artifact(
                        token,
                        user_id=user_id,
                        job_id=job_id,
                        artifact_key=str(artifact_key),
                        artifact=updated_artifact,
                        cloud=cloud,
                    )
                )
            else:
                artifact_results.append(cloud)
    if artifact_updates:
        current = _get_conversion_job(job_id) or job
        artifacts = dict(current.get("artifacts", {}) or {})
        artifacts.update(artifact_updates)
        _set_conversion_job(job_id, artifacts=artifacts)
    final_status = "synced" if job_sync.get("status") == "synced" and not any(
        result.get("status") == "failed" for result in artifact_results
    ) else "partial"
    sync_payload = {
        "status": final_status,
        "provider": "supabase",
        "job": job_sync,
        "artifacts": artifact_results,
    }
    _set_conversion_job(job_id, cloud_sync=sync_payload)
    return sync_payload


def _artifact_from_supabase_row(token: str, row: dict) -> tuple[str, dict]:
    artifact_key = str(row.get("kind") or "").strip() or "artifact"
    bucket = str(row.get("storage_bucket") or SUPABASE_ARTIFACT_BUCKET)
    storage_path = str(row.get("storage_path") or "")
    signed_url = _supabase_create_storage_signed_url(token, bucket=bucket, storage_path=storage_path) if storage_path else {}
    artifact = {
        "provider": "supabase",
        "status": "stored",
        "kind": artifact_key,
        "job_id": str(row.get("job_id") or ""),
        "filename": str(row.get("filename") or f"{artifact_key}.bin"),
        "location": f"supabase://{bucket}/{storage_path}" if storage_path else "",
        "storage_bucket": bucket,
        "storage_path": storage_path,
        "size_bytes": int(row.get("size_bytes") or 0),
        "content_type": str(row.get("content_type") or "application/octet-stream"),
        "retention": {"days": int(row.get("retention_days") or 30), "expires_at": ""},
        "signed_url": signed_url,
    }
    if signed_url.get("available"):
        artifact["download_url"] = str(signed_url.get("url") or "")
    return artifact_key, artifact


def _job_from_supabase_row(row: dict, artifacts: dict[str, dict]) -> dict:
    created_at = str(row.get("created_at") or datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    job = build_conversion_job_record(
        job_id=str(row.get("job_id") or ""),
        source_path="",
        source_type=str(row.get("source_type") or "pdf"),
        filename=str(row.get("filename") or ""),
        created_at=created_at,
    )
    job.update(
        {
            "status": str(row.get("status") or "queued"),
            "message": str(row.get("message") or ""),
            "updated_at": str(row.get("updated_at") or created_at),
            "download_name": str(row.get("download_name") or job.get("download_name") or ""),
            "metadata": dict(row.get("metadata") or {}),
            "quality_state_snapshot": dict(row.get("quality_state_snapshot") or {}),
            "auto_repair": dict(row.get("auto_repair") or {}),
            "email_delivery": dict(row.get("email_delivery") or {}),
            "runtime": dict(row.get("runtime") or {}),
            "output_size_bytes": int(row.get("output_size_bytes") or 0),
            "error": str(row.get("error") or ""),
            "error_code": str(row.get("error_code") or ""),
            "artifacts": artifacts,
            "artifact_storage": {"provider": "supabase", "status": "available", "reason": ""},
            "cloud_sync": {"status": "synced", "provider": "supabase"},
        }
    )
    return job


def _load_supabase_conversion_jobs(token: str, user_id: str, *, limit: int) -> dict[str, dict]:
    if not token or not user_id:
        return {}
    jobs_path = (
        "/rest/v1/conversion_jobs"
        f"?user_id=eq.{quote(user_id, safe='')}"
        "&select=*"
        "&order=updated_at.desc"
        f"&limit={max(1, min(limit, MAX_CONVERSION_JOB_HISTORY_LIMIT))}"
    )
    status, payload = _supabase_request_json(jobs_path, token=token)
    if status != 200 or not isinstance(payload, list) or not payload:
        return {}
    artifacts_path = (
        "/rest/v1/conversion_artifacts"
        f"?user_id=eq.{quote(user_id, safe='')}"
        "&select=*"
    )
    artifact_status, artifact_payload = _supabase_request_json(artifacts_path, token=token)
    artifact_rows = artifact_payload if artifact_status == 200 and isinstance(artifact_payload, list) else []
    artifacts_by_job: dict[str, dict[str, dict]] = {}
    for row in artifact_rows:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        key, artifact = _artifact_from_supabase_row(token, row)
        artifacts_by_job.setdefault(job_id, {})[key] = artifact
    jobs: dict[str, dict] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        jobs[job_id] = _job_from_supabase_row(row, artifacts_by_job.get(job_id, {}))
    return jobs


def _merge_cloud_jobs_into_store_for_request(*, limit: int | None = None) -> dict:
    user, token = _authenticated_request_context()
    if not user or not token:
        return {"status": "local", "provider": "local", "imported": 0}
    user_id = str(user.get("id") or "")
    cloud_jobs = _load_supabase_conversion_jobs(
        token,
        user_id,
        limit=limit or MAX_CONVERSION_JOB_HISTORY_LIMIT,
    )
    imported = 0
    for job_id, cloud_job in cloud_jobs.items():
        local_job = _get_conversion_job(job_id)
        if local_job and local_job.get("status") in ACTIVE_CONVERSION_JOB_STATUSES:
            continue
        local_updated = _conversion_job_sort_timestamp(local_job) if local_job else None
        cloud_updated = _conversion_job_sort_timestamp(cloud_job)
        if local_job and local_updated and local_updated >= cloud_updated:
            continue
        _CONVERSION_JOB_STORE.create(cloud_job)
        imported += 1
    return {"status": "synced", "provider": "supabase", "imported": imported, "user_id": user_id}


def _authenticated_request_context() -> tuple[dict | None, str]:
    token = _request_bearer_token()
    if not token:
        return None, ""
    user = _supabase_auth_user(token)
    if not user:
        return None, ""
    return user, token


def _load_request_user_profile() -> tuple[dict, dict]:
    user, token = _authenticated_request_context()
    if user and token:
        profile = _load_supabase_user_profile(token, str(user["id"]))
        if profile is not None:
            return profile, {
                "profile_scope": "cloud",
                "cloud_sync": {"status": "synced", "provider": "supabase"},
                "authenticated": True,
                "auth": user,
            }
        return _load_user_profile(), {
            "profile_scope": "cloud",
            "cloud_sync": {"status": "empty", "provider": "supabase"},
            "authenticated": True,
            "auth": user,
        }
    return _load_user_profile(), {
        "profile_scope": "local",
        "cloud_sync": {"status": "local", "provider": "local"},
        "authenticated": False,
        "auth": None,
    }


def _public_auth_config() -> dict:
    supabase_url = os.environ.get("KINDLEMASTER_SUPABASE_URL", "").strip()
    publishable_key = (
        os.environ.get("KINDLEMASTER_SUPABASE_PUBLISHABLE_KEY", "")
        or os.environ.get("KINDLEMASTER_SUPABASE_ANON_KEY", "")
    ).strip()
    enabled = _safe_bool(os.environ.get("KINDLEMASTER_SUPABASE_AUTH"), False)
    configured = bool(enabled and supabase_url and publishable_key)
    return {
        "enabled": enabled,
        "configured": configured,
        "provider": "supabase" if configured else "local",
        "supabase_url": supabase_url if configured else "",
        "publishable_key": publishable_key if configured else "",
        "require_login": False,
        "missing_config": [] if configured else ["KINDLEMASTER_SUPABASE_AUTH"],
    }


def _delivery_public_config(profile: dict | None = None) -> dict:
    profile = profile or _load_user_profile()
    profile_email = dict(profile.get("email_delivery", {}) or {})
    enabled = _safe_bool(
        os.environ.get("KINDLEMASTER_EMAIL_DELIVERY"),
        _safe_bool(profile_email.get("enabled"), False),
    )
    host = os.environ.get("KINDLEMASTER_SMTP_HOST") or str(profile_email.get("host") or "")
    username = os.environ.get("KINDLEMASTER_SMTP_USERNAME") or str(profile_email.get("username") or "")
    from_address = os.environ.get("KINDLEMASTER_SMTP_FROM") or str(profile_email.get("from_address") or username)
    secret_configured = bool(_smtp_secret_env_value())
    secret_registered = _safe_bool(profile_email.get("secret_registered"), False) or _safe_bool(
        profile_email.get("secret_configured"), False
    )
    registered_or_visible_secret = bool(secret_configured or secret_registered)
    configured = bool(enabled and host and username and from_address and registered_or_visible_secret)
    send_ready = bool(enabled and host and username and from_address and secret_configured)
    missing = []
    if not enabled:
        missing.append("KINDLEMASTER_EMAIL_DELIVERY")
    if not host:
        missing.append("KINDLEMASTER_SMTP_HOST")
    if not username:
        missing.append("KINDLEMASTER_SMTP_USERNAME")
    if not from_address:
        missing.append("KINDLEMASTER_SMTP_FROM")
    if not registered_or_visible_secret:
        missing.append("KINDLEMASTER_SMTP_PASSWORD")
    return {
        "enabled": enabled,
        "configured": configured,
        "send_ready": send_ready,
        "provider": "smtp",
        "secret_configured": secret_configured,
        "secret_registered": secret_registered,
        "secret_env_name": _smtp_secret_env_name(),
        "profile_configured": bool(profile_email.get("default_recipient")),
        "config_source": "env+profile",
        "missing_config": missing,
        "default_recipient": str(profile_email.get("default_recipient") or ""),
        "max_attachment_bytes": _safe_int(
            profile_email.get("max_attachment_bytes"),
            50 * 1024 * 1024,
            minimum=1,
        ),
    }


def _mask_email_address(value: str) -> str:
    normalized = str(value or "").strip()
    if "@" not in normalized:
        return ""
    local, domain = normalized.split("@", 1)
    return f"{local[:1]}***@{domain}" if local and domain else ""


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
        "report_json_url": f"/convert/report/{response_job_id}.json",
        "report_markdown_url": f"/convert/report/{response_job_id}.md",
        "runtime": dict(job.get("runtime", {}) or {}),
        "artifacts": dict(job.get("artifacts", {}) or {}),
        "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
        "cloud_sync": dict(job.get("cloud_sync", {}) or {}),
        "auto_repair": dict(job.get("auto_repair", {}) or {}),
        "email_delivery": dict(job.get("email_delivery", {}) or {}),
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
    filters = _resolve_library_filters(default_include_text=default_include_text)
    cloud_sync = _merge_cloud_jobs_into_store_for_request(limit=filters.limit)
    import_result = _ensure_local_artifact_history_loaded()
    payload = build_library_index(
        _CONVERSION_JOB_STORE.snapshot(),
        quality_state_builder=lambda job_id, job: _build_job_quality_state(job_id, dict(job)),
        output_size_resolver=lambda job: _read_output_size_bytes(dict(job)),
        filters=filters,
    )
    payload["import"] = import_result
    payload["cloud_sync"] = cloud_sync
    return payload


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


def _build_quality_gate_failure_metadata(
    error: ConversionQualityGateError,
    *,
    source_type: str,
    profile: str,
    heading_repair_enabled: bool,
) -> dict:
    validation_report = error.validation_report
    if not isinstance(validation_report, Mapping):
        validation_report = {}
    quality_report = dict(validation_report)
    quality_report["quality_gate_mode"] = str(error.mode or "draft")
    return build_conversion_metadata(
        result={
            "analysis": {
                "profile": profile,
                "confidence": 0.0,
            },
            "quality_report": quality_report,
            "document_summary": {},
        },
        detected_source_type=source_type,
        heading_repair_enabled=bool(heading_repair_enabled),
        heading_repair_report={
            "status": "applied" if heading_repair_enabled else "skipped",
            "release_status": "unavailable",
            "toc_entries_before": 0,
            "toc_entries_after": 0,
            "headings_removed": 0,
            "manual_review_count": 0,
            "epubcheck_status": "unavailable",
            "error": "",
        },
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
    interactive_runtime_budget: bool = True,
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
            interactive_runtime_budget=interactive_runtime_budget,
        ),
        convert_impl=convert_document_to_epub_with_report,
        heading_repair_impl=repair_epub_headings_and_toc,
        status_callback=status_callback,
    )
    return {
        "epub_bytes": outcome.epub_bytes,
        "download_name": outcome.download_name,
        "metadata": outcome.metadata,
        "extra_artifacts": list(outcome.result.get("extra_artifacts") or []),
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
    cloud_user_id: str = "",
    cloud_token: str = "",
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
            artifacts.update(_store_extra_conversion_artifacts(job_id, payload.get("extra_artifacts")))
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
            if cloud_user_id and cloud_token:
                _sync_conversion_job_to_supabase(
                    job_id,
                    token=cloud_token,
                    user_id=cloud_user_id,
                    upload_artifacts=True,
                )
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
            error_code = str(getattr(error, "error_code", "") or ERROR_CONVERSION_FAILED)
            message = (
                "Konwersja przekroczyla interaktywny budzet czasu."
                if error_code == ERROR_INTERACTIVE_RUNTIME_BUDGET
                else "Konwersja nie powiodla sie."
            )
            _set_conversion_job(
                job_id,
                status="failed",
                message=message,
                output_size_bytes=0,
                error=str(error),
                error_code=error_code,
            )
            if cloud_user_id and cloud_token:
                _sync_conversion_job_to_supabase(
                    job_id,
                    token=cloud_token,
                    user_id=cloud_user_id,
                    upload_artifacts=False,
                )
            _log_conversion_event(
                "convert.job.failed",
                level="error",
                job_id=job_id,
                phase="conversion",
                status="failed",
                error_code=error_code,
                safe_message=message,
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


def _react_shell_index_path() -> Path:
    return Path(app.root_path) / "static" / "react" / "index.html"


def _render_legacy_index():
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


@app.route("/")
def index():
    if _react_shell_index_path().exists():
        return redirect("/app", code=302)
    return _render_legacy_index()


@app.route("/legacy")
def legacy_index():
    return _render_legacy_index()


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/app")
@app.route("/app/<path:_path>")
def react_app(_path: str = ""):
    """Serve the Sprint 4 React shell when the Vite build is available."""

    react_index = _react_shell_index_path()
    local_app_url = build_local_app_url(
        _resolve_request_port_label(request.host, _resolve_server_port())
    )
    if react_index.exists():
        return react_index.read_text(encoding="utf-8")
    return render_template(
        "react_app_unbuilt.html",
        local_app_url=local_app_url,
    )


@app.route("/auth/config", methods=["GET"])
def auth_config():
    response = jsonify({"success": True, "auth": _public_auth_config()})
    apply_no_store_headers(response.headers)
    return response


@app.route("/auth/me", methods=["GET"])
def auth_me():
    user, _token = _authenticated_request_context()
    if user:
        email = str(user.get("email") or "")
        response = jsonify(
            {
                "success": True,
                "auth": {
                    "authenticated": True,
                    "user_id": str(user.get("id") or ""),
                    "email": email,
                    "email_masked": _mask_email_address(email),
                    "provider": "supabase",
                },
            }
        )
        apply_no_store_headers(response.headers)
        return response
    response = jsonify(
        {
            "success": True,
            "auth": {
                "authenticated": False,
                "user_id": "",
                "email": "",
                "email_masked": "",
                "provider": "local",
            },
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/user/profile", methods=["GET"])
def user_profile_get():
    profile, context = _load_request_user_profile()
    user = context.get("auth") if isinstance(context.get("auth"), dict) else {}
    response = jsonify(
        {
            "success": True,
            "profile": profile,
            "profile_scope": context["profile_scope"],
            "profile_path_configured": bool(os.environ.get("KINDLEMASTER_USER_PROFILE_PATH")),
            "profile_path": str(_user_profile_path()) if context["profile_scope"] == "local" else "",
            "cloud_sync": context["cloud_sync"],
            "authenticated": context["authenticated"],
            "user_id": str(user.get("id") or ""),
            "email_masked": _mask_email_address(str(user.get("email") or "")),
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/user/profile", methods=["PUT"])
def user_profile_put():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error(
            "Profil uzytkownika musi byc obiektem JSON.",
            error_code="invalid_profile_request",
            status_code=400,
            phase="settings",
        )
    user, token = _authenticated_request_context()
    if user and token:
        saved_cloud = _save_supabase_user_profile(token, str(user["id"]), payload)
        if saved_cloud is not None:
            _save_user_profile(saved_cloud)
            response = jsonify(
                {
                    "success": True,
                    "profile": saved_cloud,
                    "profile_scope": "cloud",
                    "cloud_sync": {"status": "synced", "provider": "supabase"},
                    "authenticated": True,
                    "user_id": str(user.get("id") or ""),
                    "email_masked": _mask_email_address(str(user.get("email") or "")),
                }
            )
            apply_no_store_headers(response.headers)
            return response
        return _json_error(
            "Nie udalo sie zapisac profilu w Supabase.",
            error_code="profile_cloud_write_failed",
            status_code=502,
            phase="settings",
            retryable=True,
        )
    try:
        profile = _save_user_profile(payload)
    except OSError:
        return _json_error(
            "Nie udalo sie zapisac profilu uzytkownika.",
            error_code="profile_write_failed",
            status_code=500,
            phase="settings",
            retryable=True,
        )
    response = jsonify(
        {
            "success": True,
            "profile": profile,
            "profile_scope": "local",
            "cloud_sync": {"status": "local", "provider": "local"},
            "authenticated": False,
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/user/library/import-local", methods=["POST"])
def user_library_import_local():
    import_result = _import_local_artifact_history()
    response = jsonify(
        {
            "success": True,
            "import": import_result,
            "message": "Lokalna historia zostala odtworzona z zachowanych artefaktow.",
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/convert/delivery/config", methods=["GET"])
def convert_delivery_config():
    profile, context = _load_request_user_profile()
    response = jsonify(
        {
            "success": True,
            "delivery": _delivery_public_config(profile),
            "profile_scope": context["profile_scope"],
            "cloud_sync": context["cloud_sync"],
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/convert/repair/<job_id>", methods=["POST"])
def convert_repair(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji do naprawy.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="repair",
            job_id=job_id,
        )
    if job.get("status") != "ready":
        return _json_error(
            "Naprawa jest dostepna dopiero po zakonczeniu konwersji.",
            error_code="repair_not_ready",
            status_code=409,
            phase="repair",
            job_id=job_id,
        )
    auto_repair = {
        "status": "skipped",
        "reason": "delivery_repair_not_enabled_in_local_fallback",
        "actions": [],
    }
    updated_job = _set_conversion_job(job_id, auto_repair=auto_repair) or job
    quality_state = _build_job_quality_state(job_id, updated_job)
    response = jsonify(
        {
            "success": True,
            "job": _build_conversion_job_history_item(job_id, updated_job),
            "quality_state": quality_state,
            "auto_repair": auto_repair,
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/convert/delivery/<job_id>/email", methods=["POST"])
def convert_delivery_email(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job or job.get("status") != "ready":
        return _json_error(
            "Nie znaleziono gotowego zadania do wysylki.",
            error_code="delivery_not_ready",
            status_code=404,
            phase="delivery",
            job_id=job_id,
        )
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return _json_error(
            "Payload wysylki musi byc obiektem JSON.",
            error_code="invalid_delivery_request",
            status_code=400,
            phase="delivery",
            job_id=job_id,
        )
    profile, _context = _load_request_user_profile()
    recipient = str(payload.get("to", "") or profile["email_delivery"].get("default_recipient", "")).strip()
    if not recipient or "@" not in recipient:
        return _json_error(
            "Podaj poprawny adres Kindle.",
            error_code="invalid_delivery_request",
            status_code=400,
            phase="delivery",
            job_id=job_id,
        )
    delivery_config = _delivery_public_config(profile)
    if not delivery_config.get("configured"):
        return _json_error(
            "Wysylka email jest wylaczona albo konfiguracja SMTP jest niekompletna.",
            error_code="delivery_unavailable",
            status_code=503,
            phase="delivery",
            job_id=job_id,
        )
    delivery_payload = {
        "status": "configured_not_sent",
        "artifact": str(payload.get("artifact") or "epub"),
        "masked_recipient": _mask_email_address(recipient),
        "diagnostics": {
            "smtp": {"configured": True},
            "message": {"dry_run": True},
        },
    }
    _set_conversion_job(job_id, email_delivery=delivery_payload)
    response = jsonify({"success": True, "delivery": delivery_payload})
    apply_no_store_headers(response.headers)
    return response


@app.route("/pdf/compress", methods=["POST"])
def pdf_compress():
    _cleanup_expired_pdf_compression_jobs()
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return _json_error(
            "Przeslij plik PDF do zmniejszenia.",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=400,
            phase="pdf_compression",
        )
    if detect_supported_source_type(file.filename) != "pdf":
        return _json_error(
            "Zmniejszanie wagi jest dostepne tylko dla plikow PDF.",
            error_code="pdf_compression_unsupported_source",
            status_code=400,
            phase="pdf_compression",
        )

    profile = normalize_compression_profile(request.form.get("profile", "balanced"))
    job_id = uuid.uuid4().hex
    source_path = PDF_COMPRESS_DIR / f"{job_id}.source.pdf"
    try:
        file.save(source_path)
    except OSError:
        return _json_error(
            "Nie udalo sie zapisac PDF do kompresji.",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=500,
            phase="pdf_compression",
            retryable=True,
        )

    warnings = _pdf_compression_source_warnings(source_path)
    try:
        result = compress_pdf(source_path, PDF_COMPRESS_DIR, profile=profile, job_id=job_id)
    except PdfCompressionUnavailable as error:
        _safe_remove_temp_file(source_path)
        return _json_error(
            str(error),
            error_code="pdf_compression_unavailable",
            status_code=503,
            phase="pdf_compression",
            retryable=True,
        )
    except PdfCompressionFailed as error:
        _safe_remove_temp_file(source_path)
        return _json_error(
            f"Zmniejszanie PDF nie powiodlo sie: {error}",
            error_code="pdf_compression_failed",
            status_code=500,
            phase="pdf_compression",
            retryable=True,
        )

    all_warnings = [*warnings, *result.warnings]
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    download_name = f"{Path(file.filename).stem}.compressed.pdf"
    payload = {
        "success": result.success,
        "job_id": result.job_id,
        "status": result.status,
        "original_size_bytes": result.original_size_bytes,
        "compressed_size_bytes": result.compressed_size_bytes,
        "reduction_percent": result.reduction_percent,
        "quality_profile": result.quality_profile,
        "method": result.method,
        "warnings": all_warnings,
        "download_url": f"/pdf/compress/download/{result.job_id}" if result.success else "",
        "download_name": download_name if result.success else "",
    }
    if not result.success:
        payload["error_code"] = result.status
        payload["error"] = "Kompresja nie zmniejszyla pliku."
        _safe_remove_temp_file(source_path)
    else:
        _PDF_COMPRESS_JOBS[result.job_id] = {
            "job_id": result.job_id,
            "source_path": str(source_path),
            "output_path": result.output_path,
            "download_name": download_name,
            "created_at": created_at,
            "profile": result.quality_profile,
        }

    response = jsonify(payload)
    apply_no_store_headers(response.headers)
    return response


@app.route("/pdf/compress/download/<job_id>", methods=["GET"])
def pdf_compress_download(job_id: str):
    _cleanup_expired_pdf_compression_jobs()
    safe_job_id = "".join(ch for ch in str(job_id or "") if ch.isalnum() or ch in {"-", "_"})
    job = _PDF_COMPRESS_JOBS.get(safe_job_id)
    if not job:
        return _json_error(
            "Skompresowany PDF nie jest juz dostepny. Uruchom zmniejszanie ponownie.",
            error_code="pdf_compression_missing_output",
            status_code=404,
            phase="pdf_compression",
        )
    output_path = Path(str(job.get("output_path") or ""))
    try:
        resolved = output_path.resolve()
    except OSError:
        resolved = output_path
    if not _is_path_under(resolved, PDF_COMPRESS_DIR.resolve()) or not resolved.is_file():
        return _json_error(
            "Brak pliku PDF do pobrania.",
            error_code="pdf_compression_missing_output",
            status_code=404,
            phase="pdf_compression",
        )
    return send_file(
        resolved,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=str(job.get("download_name") or f"{safe_job_id}.pdf"),
    )


@app.route("/pdf/compress/job/<job_id>", methods=["POST"])
def pdf_compress_job_source(job_id: str):
    _cleanup_expired_pdf_compression_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania z PDF zrodlowym.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="pdf_compression",
            job_id=job_id,
        )
    source_type = str(job.get("source_type") or "").strip().lower()
    filename = str(job.get("filename") or "").strip()
    if source_type != "pdf" and not filename.lower().endswith(".pdf"):
        return _json_error(
            "Zmniejszanie wagi jest dostepne tylko dla zadan PDF.",
            error_code="pdf_compression_unsupported_source",
            status_code=400,
            phase="pdf_compression",
            job_id=job_id,
        )

    profile = normalize_compression_profile(request.form.get("profile", "balanced"))
    downloaded_source = False
    source_path: Path | None = None
    try:
        source_path, source_filename, downloaded_source = _resolve_job_source_pdf_for_compression(job_id, job)
        warnings = _pdf_compression_source_warnings(source_path)
        result = compress_pdf(source_path, PDF_COMPRESS_DIR, profile=profile, job_id=uuid.uuid4().hex)
    except PdfCompressionUnavailable as error:
        if downloaded_source and source_path is not None:
            _safe_remove_temp_file(source_path)
        return _json_error(
            str(error),
            error_code="pdf_compression_unavailable",
            status_code=503,
            phase="pdf_compression",
            job_id=job_id,
            retryable=True,
        )
    except PdfCompressionFailed as error:
        if downloaded_source and source_path is not None:
            _safe_remove_temp_file(source_path)
        source_unavailable = any(
            marker in str(error)
            for marker in (
                "source PDF artifact",
                "Source PDF artifact",
                "No preserved source PDF artifact",
                "Could not download source PDF artifact",
            )
        )
        return _json_error(
            (
                "Nie moge odnalezc PDF zrodlowego dla tego zadania. "
                "Wgraj plik ponownie albo upewnij sie, ze artefakt wejsciowy istnieje w chmurze."
                if source_unavailable
                else f"Zmniejszanie PDF zrodlowego nie powiodlo sie: {error}"
            ),
            error_code="pdf_source_unavailable" if source_unavailable else "pdf_compression_failed",
            status_code=409 if source_unavailable else 500,
            phase="pdf_compression",
            job_id=job_id,
            retryable=not source_unavailable,
        )

    all_warnings = [*warnings, *result.warnings]
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    download_name = f"{Path(source_filename).stem}.compressed.pdf"
    payload = {
        "success": result.success,
        "job_id": result.job_id,
        "source_job_id": job_id,
        "status": result.status,
        "original_size_bytes": result.original_size_bytes,
        "compressed_size_bytes": result.compressed_size_bytes,
        "reduction_percent": result.reduction_percent,
        "quality_profile": result.quality_profile,
        "method": result.method,
        "warnings": all_warnings,
        "download_url": f"/pdf/compress/download/{result.job_id}" if result.success else "",
        "download_name": download_name if result.success else "",
    }
    if not result.success:
        payload["error_code"] = result.status
        payload["error"] = "Kompresja nie zmniejszyla pliku."
        if downloaded_source:
            _safe_remove_temp_file(source_path)
    else:
        _PDF_COMPRESS_JOBS[result.job_id] = {
            "job_id": result.job_id,
            "source_path": str(source_path),
            "output_path": result.output_path,
            "download_name": download_name,
            "created_at": created_at,
            "profile": result.quality_profile,
        }

    response = jsonify(payload)
    apply_no_store_headers(response.headers)
    return response


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
    except ConversionQualityGateError as error:
        capture_conversion_exception(
            error,
            context=_conversion_sentry_context(
                job_id=job_id,
                source_type=source_type,
                profile=profile,
            ),
        )
        return _json_error(
            f"Walidacja EPUB zakończyła sie niepowodzeniem: {str(error)}",
            error_code=error.error_code,
            status_code=422,
            phase="quality_gate",
            retryable=False,
            extra={"validation_details": dict(error.validation_report), "quality_gate_mode": error.mode},
        )
    except Exception as e:
        error_code = str(getattr(e, "error_code", "") or ERROR_CONVERSION_FAILED)
        status_code = 422 if error_code == ERROR_INTERACTIVE_RUNTIME_BUDGET else 500
        return _json_error(
            f"Konwersja nie powiodla sie: {str(e)}",
            error_code=error_code,
            status_code=status_code,
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
    cloud_user, cloud_token = _authenticated_request_context()
    cloud_user_id = str(cloud_user.get("id") or "") if cloud_user else ""
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
            route_model_mode=route_model_mode,
            quality_gate_mode=quality_gate_mode,
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
    cloud_sync = (
        _sync_conversion_job_to_supabase(job_id, token=cloud_token, user_id=cloud_user_id, upload_artifacts=False)
        if cloud_user_id and cloud_token
        else {"status": "local", "provider": "local"}
    )
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
        cloud_user_id=cloud_user_id,
        cloud_token=cloud_token,
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
            "cloud_sync": cloud_sync,
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
    limit = _resolve_conversion_job_history_limit()
    cloud_sync = _merge_cloud_jobs_into_store_for_request(limit=limit)
    import_result = _ensure_local_artifact_history_loaded()
    jobs = _CONVERSION_JOB_STORE.snapshot()
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
            "import": import_result,
            "cloud_sync": cloud_sync,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/jobs/<job_id>", methods=["DELETE"])
def convert_job_delete(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="delete",
            job_id=job_id,
        )
    if is_active_conversion_status(str(job.get("status") or "")):
        return _json_error(
            "Nie można usunąć publikacji, która jest jeszcze przetwarzana.",
            error_code="conversion_job_active",
            status_code=409,
            phase="delete",
            job_id=job_id,
            retryable=True,
        )

    deleted = _CONVERSION_JOB_STORE.delete(job_id)
    if not deleted:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="delete",
            job_id=job_id,
        )

    cleanup = _cleanup_deleted_conversion_job_files(job_id, deleted)
    cloud_user, cloud_token = _authenticated_request_context()
    cloud_delete = (
        _delete_supabase_conversion_job(cloud_token, str(cloud_user.get("id") or ""), job_id)
        if cloud_user and cloud_token
        else {"status": "skipped", "provider": "supabase", "reason": "anonymous_or_local"}
    )
    response = jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status": "deleted",
            "cleanup": cleanup,
            "cloud_delete": cloud_delete,
        }
    )
    apply_no_store_headers(response.headers)
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


@app.route("/convert/retry/<job_id>", methods=["POST"])
def convert_retry(job_id: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    cloud_user, cloud_token = _authenticated_request_context()
    cloud_user_id = str(cloud_user.get("id") or "") if cloud_user else ""
    if _active_conversion_job_count() >= MAX_ACTIVE_CONVERSION_JOBS:
        return _json_error(
            "Kolejka konwersji jest pelna. Sprobuj ponownie za chwile.",
            error_code=ERROR_QUEUE_FAILED,
            status_code=429,
            phase="retry",
            job_id=job_id,
            retryable=True,
        )
    previous_job = _get_conversion_job(job_id)
    if not previous_job:
        return _json_error(
            "Nie znaleziono zadania do ponowienia.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="retry",
            job_id=job_id,
        )

    input_bytes, input_filename = _read_retry_input_artifact(previous_job)
    if not input_bytes:
        return _json_error(
            "Nie znaleziono zachowanego pliku wejsciowego dla tego zadania.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=409,
            phase="retry",
            job_id=job_id,
        )

    source_type = detect_supported_source_type(input_filename) or str(previous_job.get("source_type") or "").strip()
    if source_type not in {"pdf", "docx"}:
        return _json_error(
            "Nie mozna ustalic typu pliku do ponowienia.",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=400,
            phase="retry",
            job_id=job_id,
        )

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    replay = (((previous_job.get("runtime", {}) or {}).get("replay", {}) or {}).get("command", {}) or {}).get("kwargs", {}) or {}
    if not isinstance(replay, dict):
        replay = {}
    profile = str(payload.get("profile") or replay.get("profile") or "auto-premium")
    route_model_mode = str(payload.get("route_model_mode") or "shadow")
    quality_gate_mode = str(payload.get("quality_gate_mode") or "draft")
    force_ocr = _safe_bool(payload.get("force_ocr"), _safe_bool(replay.get("force_ocr"), False))
    language = str(payload.get("language") or replay.get("language") or "pl")
    heading_repair_enabled = _safe_bool(
        payload.get("heading_repair_enabled"),
        _safe_bool(replay.get("heading_repair_enabled"), False),
    )

    retry_job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{retry_job_id}.{source_type}")
    try:
        Path(source_path).write_bytes(input_bytes)
    except OSError:
        return _json_error(
            "Nie udalo sie przygotowac pliku do ponownej konwersji.",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=500,
            phase="retry",
            job_id=job_id,
            retryable=True,
        )

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    input_artifact = _store_artifact_bytes(
        job_id=retry_job_id,
        kind=ArtifactKind.INPUT,
        filename=input_filename,
        data=input_bytes,
    )
    runtime_metadata = _submit_runtime_job(
        retry_job_id,
        _build_replayable_conversion_command(
            job_id=retry_job_id,
            source_type=source_type,
            original_filename=input_filename,
            profile=profile,
            force_ocr=force_ocr,
            language=language,
            heading_repair_enabled=heading_repair_enabled,
        ),
    )
    retry_record = build_conversion_job_record(
        job_id=retry_job_id,
        source_path=source_path,
        source_type=source_type,
        filename=input_filename,
        created_at=created_at,
    )
    retry_record["runtime"] = runtime_metadata
    retry_record["artifacts"] = {"input": input_artifact}
    retry_record["artifact_storage"] = _artifact_storage_status()
    retry_record["retry_of"] = job_id
    retry_record["retry_reason"] = str(previous_job.get("error_code") or previous_job.get("status") or "")
    _CONVERSION_JOB_STORE.create(retry_record)
    cloud_sync = (
        _sync_conversion_job_to_supabase(retry_job_id, token=cloud_token, user_id=cloud_user_id, upload_artifacts=False)
        if cloud_user_id and cloud_token
        else {"status": "local", "provider": "local"}
    )
    _log_conversion_event(
        "convert.job.retry",
        job_id=retry_job_id,
        phase="retry",
        status="queued",
        safe_message="Ponowiono konwersje z zachowanego pliku wejsciowego.",
        source_type=source_type,
    )

    _spawn_conversion_job(
        job_id=retry_job_id,
        source_path=source_path,
        source_type=source_type,
        original_filename=input_filename,
        profile=profile,
        route_model_mode=route_model_mode,
        quality_gate_mode=quality_gate_mode,
        force_ocr=force_ocr,
        language=language,
        heading_repair_enabled=heading_repair_enabled,
        cloud_user_id=cloud_user_id,
        cloud_token=cloud_token,
    )

    response = jsonify(
        {
            "success": True,
            "job_id": retry_job_id,
            "retry_of": job_id,
            "status": "queued",
            "source_type": source_type,
            "filename": input_filename,
            "message": "Ponowiono konwersje z zachowanego pliku wejsciowego.",
            "poll_after_ms": DEFAULT_CONVERSION_POLL_INTERVAL_MS,
            "runtime": runtime_metadata,
            "artifacts": {"input": input_artifact},
            "artifact_storage": retry_record["artifact_storage"],
            "cloud_sync": cloud_sync,
        }
    )
    response.status_code = 202
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
    if job.get("status") == "ready" or (
        job.get("status") == "failed"
        and str(job.get("error_code", "") or "") == ConversionQualityGateError.error_code
    ):
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
            "auto_repair": dict(job.get("auto_repair", {}) or {}),
            "email_delivery": dict(job.get("email_delivery", {}) or {}),
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
            "auto_repair": dict(job.get("auto_repair", {}) or {}),
            "email_delivery": dict(job.get("email_delivery", {}) or {}),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/artifact/<job_id>/<artifact_key>", methods=["GET"])
def convert_artifact_download(job_id: str, artifact_key: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        # Details views may survive a local server restart while the durable job
        # history lives in Supabase. If the browser sends auth, rehydrate the
        # local store before deciding the artifact is missing.
        _merge_cloud_jobs_into_store_for_request(limit=MAX_CONVERSION_JOB_HISTORY_LIMIT)
        job = _get_conversion_job(job_id)
    if not job:
        job = _restore_local_artifact_job_by_id(job_id)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    key = _safe_artifact_key(artifact_key)
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get(key)
    if not isinstance(artifact, dict):
        return _json_error(
            "Nie znaleziono artefaktu zadania.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    artifact_path = _resolve_local_artifact_path(artifact)
    if artifact_path is None or not artifact_path.is_file():
        if key == "input":
            fallback_response = _send_local_input_artifact_fallback(job_id, job, artifact)
            if fallback_response is not None:
                return fallback_response
        return _send_remote_artifact_proxy(artifact, job_id=job_id, artifact_key=key)
    response = send_file(
        artifact_path,
        mimetype=str(artifact.get("content_type") or mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream"),
        as_attachment=key != "input",
        download_name=str(artifact.get("filename") or artifact_path.name),
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Source"] = "local"
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
    except ValueError as error:
        return _json_error(
            str(error),
            error_code="invalid_training_feedback",
            status_code=400,
            phase="feedback",
            job_id=job_id,
        )
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
            "include_in_training": bool((record.get("dataset") or {}).get("include_in_route_training")),
            "dataset_reason": str((record.get("dataset") or {}).get("reason", "")),
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
    host = _resolve_server_host()
    port = _resolve_server_port()
    debug = _resolve_debug_mode()
    display_url = os.environ.get("KINDLEMASTER_PUBLIC_BASE_URL") or build_local_app_url(port)
    print(
        f"Starting KindleMaster on {display_url} (bind={host}, debug={debug})",
        flush=True,
    )
    serve_http_app(app, host=host, port=port, debug=debug, runtime="flask")
