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
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

from app_runtime_services import (
    DEFAULT_DEBUG,
    DEFAULT_PORT,
    DELETED_ARTIFACT_MARKER,
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
    ERROR_DELIVERY_FAILED,
    ERROR_DELIVERY_NOT_READY,
    ERROR_DELIVERY_UNAVAILABLE,
    ERROR_INVALID_DELIVERY_REQUEST,
    ERROR_INVALID_PROFILE_REQUEST,
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
from conversion_job_access import (
    GUEST_ID_HEADER,
    JOB_ACCESS_QUERY_PARAM,
    LEGACY_LOCAL_OWNER_ID,
    JobOwner,
    JobOwnerResolutionError,
    apply_job_owner,
    is_local_request_host,
    job_owned_by,
    legacy_local_guest_allowed,
    resolve_job_owner,
    verify_job_access_token,
)
from conversion_library import (
    LibraryFilters,
    build_library_index,
    build_quality_report_payload,
    render_quality_report_markdown,
)
from flask import Flask, request, jsonify, render_template, redirect, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
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
from supabase_auth import (
    AuthContext,
    load_supabase_auth_config,
    public_auth_config,
    resolve_bearer_token,
    validate_bearer_token,
)
from supabase_library import SupabaseLibraryClient, load_supabase_library_config
from supabase_profile import load_cloud_user_profile, save_cloud_user_profile


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
# Keep the storage writer and artifact routes on the same persistent root in hosted runtimes.
ARTIFACT_ROOT = Path(os.environ.get("KINDLEMASTER_ARTIFACT_ROOT") or Path(app.root_path) / "output" / "artifacts")
ARTIFACT_STORAGE = build_artifact_storage(local_root=ARTIFACT_ROOT)
DEFAULT_CONVERSION_JOB_STORE_PATH = Path(UPLOAD_DIR) / "conversion_jobs.json"
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
PDF_LAYOUT_PREVIEW_MAX_INLINE_BYTES = 10 * 1024 * 1024
FINAL_READER_ARTIFACT_TYPE = "final_pdf_two_crop_reader"
SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE = "source_html_evidence_only"
ERROR_FINAL_READER_MISSING = "final_reader_missing"
ERROR_FINAL_READER_HEALTH_GATE_FAILED = "final_reader_health_gate_failed"
ERROR_CHESS_PGN_UNAVAILABLE = "chess_pgn_unavailable"
CHESS_PGN_UNAVAILABLE_MESSAGE = "PGN niedostepny: brak zaakceptowanych partii"
CHESS_PGN_AVAILABLE_MESSAGE = "PGN gotowy do pobrania."
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
    "auto_repair": ("Naprawa dostawy", 88),
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
    persistence_path=DEFAULT_CONVERSION_JOB_STORE_PATH,
    active_statuses=ACTIVE_CONVERSION_JOB_STATUSES,
)
_CONVERSION_JOB_STORE.load()
_LAST_CONVERSION_CLEANUP_AT: datetime | None = None
_LOCAL_ARTIFACT_HISTORY_RECOVERED = False


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
    auth_context: AuthContext | None = None,
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
        user_id=auth_context.user_id if auth_context and auth_context.authenticated else "",
        auth_provider="supabase" if auth_context and auth_context.authenticated else "",
        auth_state="authenticated" if auth_context and auth_context.authenticated else "guest",
    )


def _resolve_request_auth_context() -> AuthContext:
    config = load_supabase_auth_config()
    token = resolve_bearer_token(request.headers.get("Authorization"))
    if not token:
        if config.enabled and config.require_login:
            return AuthContext(
                error="Logowanie jest wymagane dla tej akcji.",
                error_code="auth_required",
                status_code=401,
            )
        return AuthContext()
    return validate_bearer_token(token, config=config)


def _resolve_request_job_owner(auth_context: AuthContext) -> JobOwner:
    return resolve_job_owner(
        authenticated=auth_context.authenticated,
        user_id=auth_context.user_id,
        guest_id=request.headers.get(GUEST_ID_HEADER),
        request_host=request.host,
    )


def _json_job_owner_error(error: JobOwnerResolutionError):
    return _json_error(
        "Nie moĹĽna potwierdziÄ‡ wĹ‚aĹ›ciciela sesji konwersji.",
        error_code=error.error_code,
        status_code=401,
        phase="auth",
    )


def _json_auth_error(context: AuthContext):
    return _json_error(
        context.error or "Nieprawidlowa sesja logowania.",
        error_code=context.error_code or "invalid_auth",
        status_code=context.status_code or 401,
        phase="auth",
    )


def _supabase_library_client() -> SupabaseLibraryClient:
    return SupabaseLibraryClient(load_supabase_library_config())


def _profile_with_secret_status(profile: dict) -> dict:
    normalized = _normalize_user_profile(profile)
    email_delivery = normalized["email_delivery"]
    secret_registered = _safe_bool(email_delivery.get("secret_registered"), False)
    secret_configured = bool(_smtp_secret_env_value())
    email_delivery["secret_configured"] = secret_configured
    email_delivery["secret_registered"] = secret_registered or secret_configured
    return normalized


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


def _artifact_should_download_as_attachment(artifact_key: str, artifact: dict) -> bool:
    key = _safe_artifact_key(artifact_key)
    if key in {"input", "pdf_layout_preview", "chess_reader", "chess_pgn_html", "chess_fen_review", "chess_glyph_diagnostics", "deepseek_audit"}:
        return False
    content_type = str(artifact.get("content_type") or "").strip().lower()
    if key.startswith("chess_") and (content_type.startswith("text/html") or content_type.startswith("application/json")):
        return False
    return True


def _pdf_layout_preview_warning_payload(job_id: str, job: dict) -> dict[str, object]:
    reader_payload = _enrich_job_chess_reader_artifact_routing(job_id, job)
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get("chess_pgn_html") if isinstance(artifacts.get("chess_pgn_html"), Mapping) else {}
    artifact_mapping = dict(artifact or {})
    artifact_type = str(reader_payload.get("artifact_type") or artifact_mapping.get("artifact_type") or "").strip()
    href = str(
        artifact_mapping.get("download_url")
        or artifact_mapping.get("downloadUrl")
        or _artifact_signed_url(artifact_mapping)
        or ""
    ).strip()
    if artifact_type == FINAL_READER_ARTIFACT_TYPE:
        href = f"/convert/artifact/{job_id}/chess_reader"
    blockers = [str(blocker) for blocker in list(reader_payload.get("final_reader_blockers", []) or []) if str(blocker)]
    final_reader_available = bool(reader_payload.get("final_reader_available", False)) and bool(href)
    if not final_reader_available and not blockers:
        blockers = ["final_reader_missing"]
    return {
        "artifact_type": artifact_type,
        "final_reader_available": final_reader_available,
        "final_reader_href": href if final_reader_available else "",
        "final_reader_blockers": blockers,
        "quality_href": f"/convert/quality/{job_id}",
    }


def _render_pdf_layout_preview_shell(job_id: str, job: dict, artifact: dict, artifact_path: Path | None = None):
    filename = str(artifact.get("filename") or (artifact_path.name if artifact_path is not None else "") or "pdf_layout_preview.html")
    title = str(job.get("title") or job.get("filename") or filename or "Audit View / Source Preview").strip()
    local_app_url = build_local_app_url(
        _resolve_request_port_label(request.host, _resolve_server_port())
    )
    preview_handoff: dict[str, object] = {
        "available": True,
        "mode": "srcdoc",
        "srcdoc": "",
        "frame_src": "",
        "badge": "Artefakt lokalny",
        "message": "",
        "size_bytes": int(artifact.get("size_bytes") or 0),
    }
    artifact_source = "local-shell"
    if artifact_path is not None and artifact_path.is_file():
        try:
            preview_html = artifact_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            preview_html = artifact_path.read_text(encoding="utf-8", errors="replace")
        preview_handoff["srcdoc"] = preview_html
    else:
        preview_handoff = _build_pdf_layout_preview_handoff(artifact)
        artifact_source = "app-shell"
    response = app.make_response(
        render_template(
            "artifact_preview_shell.html",
            title=title,
            job_id=job_id,
            local_app_url=local_app_url,
            preview_html=str(preview_handoff.get("srcdoc") or ""),
            preview_handoff=preview_handoff,
            pdf_layout_preview_warning=_pdf_layout_preview_warning_payload(job_id, job),
            static_asset_version=_legacy_static_asset_version(),
        )
    )
    response.headers["X-KindleMaster-Artifact-View"] = "app-shell"
    response.headers["X-KindleMaster-Artifact-Source"] = artifact_source
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


def _send_remote_artifact_proxy(artifact: dict, *, job_id: str, artifact_key: str):
    signed_url = _signed_artifact_url(artifact) or str(artifact.get("download_url") or "").strip()
    if not signed_url:
        response = _json_error(
            "Artefakt zdalny nie ma aktywnego podpisanego URL.",
            error_code="source_artifact_unavailable" if artifact_key == "input" else ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "missing"
        return response
    try:
        with urllib.request.urlopen(signed_url, timeout=20) as remote_response:
            data = remote_response.read()
            remote_status = int(getattr(remote_response, "status", 200) or 200)
    except urllib.error.HTTPError as error:
        response = _json_error(
            "Nie udało się pobrać zdalnego artefaktu źródłowego.",
            error_code="source_artifact_unavailable" if artifact_key == "input" else ERROR_MISSING_OUTPUT,
            status_code=404 if error.code == 404 else 502,
            phase="download",
            job_id=job_id,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "missing"
        response.headers["X-KindleMaster-Remote-Status"] = str(error.code)
        return response
    except (OSError, TimeoutError) as error:
        response = _json_error(
            f"Nie udało się pobrać zdalnego artefaktu: {error}",
            error_code="source_artifact_unavailable" if artifact_key == "input" else ERROR_MISSING_OUTPUT,
            status_code=503,
            phase="download",
            job_id=job_id,
        )
        response.headers["X-KindleMaster-Artifact-Source"] = "missing"
        return response
    mimetype = str(artifact.get("content_type") or mimetypes.guess_type(str(artifact.get("filename") or ""))[0] or "application/octet-stream")
    response = app.response_class(data, mimetype=mimetype)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Proxy"] = "remote"
    response.headers["X-KindleMaster-Artifact-Source"] = "remote"
    response.headers["X-KindleMaster-Remote-Status"] = str(remote_status)
    filename = str(artifact.get("filename") or Path(str(urllib.parse.urlparse(signed_url).path)).name or "artifact")
    if _artifact_should_download_as_attachment(artifact_key, artifact):
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _reader_health_blocks_whole_artifact(health_gate: Mapping[str, object] | None) -> bool:
    """Only structural reader failures block the whole reader.

    Content-quality blockers such as unknown side-to-move, missing FEN, or missing
    PGN are rendered as component status inside Chess Reader. This keeps the book
    readable while preserving the audit trail.
    """
    if not _final_reader_health_gate_failed(health_gate):
        return False
    blockers = {str(blocker) for blocker in (health_gate or {}).get("blockers", []) or [] if str(blocker)}
    hard_blockers = {
        "final_reader_health_gate_missing",
        "final_reader_missing",
        "artifact_manifest_missing",
        "semantic_chess_reader_missing",
        "diagram_cards_missing",
    }
    return bool(blockers & hard_blockers)


def _sanitize_chess_reader_html(html_text: str) -> str:
    replacements = {
        "fen_not_recognized": "FEN unavailable",
        "mass_side_to_move_unknown": "Side marker review required",
        "board_crop_quality=fail": "Board crop needs review",
        "marker_crop_quality=fail": "Marker crop needs review",
        "Side to move: unknown": "Side to move unavailable",
        "side_to_move_unknown": "side to move unavailable",
    }
    for raw, replacement in replacements.items():
        html_text = html_text.replace(raw, replacement)
    html_text = html_text.replace('src=""', 'data-empty-src="true"')
    html_text = html_text.replace("src=''", "data-empty-src='true'")
    return html_text


def _render_chess_pgn_semantic_artifact(
    job_id: str,
    job: dict,
    artifact: dict,
    artifact_path: Path,
    *,
    asset_route: str = "chess_pgn_html_asset",
):
    semantic_index = _ensure_semantic_chess_html_artifact(job_id, job, artifact_path)
    if semantic_index is None or not semantic_index.is_file():
        return None
    health_gate = _semantic_chess_reader_health_gate(job_id)
    if not health_gate:
        health_gate = _missing_final_reader_health_gate(semantic_index)
    if _reader_health_blocks_whole_artifact(health_gate):
        return _final_reader_health_gate_failed_response(job_id, semantic_index, artifact_path, artifact, health_gate)
    try:
        html_text = semantic_index.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        html_text = semantic_index.read_text(encoding="utf-8", errors="replace")
    html_text = _sanitize_chess_reader_html(html_text)
    asset_base = f"/convert/artifact/{quote(job_id)}/{asset_route}/"
    html_text = _rewrite_semantic_chess_asset_urls(
        html_text,
        asset_base=asset_base,
        semantic_index=semantic_index,
    )
    response = app.make_response(html_text)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Source"] = "semantic-chess-reader"
    response.headers["X-KindleMaster-Artifact-View"] = "chess_reader"
    if _final_reader_health_gate_failed(health_gate):
        response.headers["X-KindleMaster-Reader-Health"] = "component-review"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


def _ensure_semantic_chess_html_artifact(job_id: str, job: dict, artifact_path: Path) -> Path | None:
    artifact_path = _resolve_local_artifact_path({"location": str(artifact_path)}) or artifact_path
    semantic_index = _semantic_chess_index_for_artifact_path(artifact_path)
    if semantic_index is not None and _semantic_chess_index_is_final_reader(semantic_index):
        return semantic_index
    artifact_routing = _chess_reader_routing_metadata(job_id, artifact_path, {})
    if artifact_routing.get("artifact_type") == FINAL_READER_ARTIFACT_TYPE:
        if semantic_index is not None and _semantic_chess_index_is_final_reader(semantic_index):
            return semantic_index
        return artifact_path
    if artifact_routing.get("artifact_type") == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE:
        return None
    job_dir = _artifact_job_dir_from_path(artifact_path)
    semantic_dir = job_dir / "semantic_chess_html" if job_dir is not None else artifact_path.parent / "semantic_chess_html"
    try:
        from chess_study_export import rebuild_chess_source_html_export

        source_pdf = _job_input_path(job)
        rebuild_chess_source_html_export(
            artifact_path,
            semantic_dir,
            pdf_path=source_pdf if source_pdf and source_pdf.is_file() else None,
        )
    except Exception:
        return None
    semantic_index = _semantic_chess_index_for_artifact_path(artifact_path)
    if semantic_index is None:
        return None
    routing = _chess_reader_routing_metadata(job_id, semantic_index, {})
    return semantic_index if routing.get("artifact_type") == FINAL_READER_ARTIFACT_TYPE else None


_SEMANTIC_READER_ASSET_ATTR_RE = re.compile(
    r"(?P<prefix>\b(?:src|href|poster)\s*=\s*(?P<quote>['\"]))(?P<url>.*?)(?P=quote)",
    flags=re.IGNORECASE,
)
_SEMANTIC_READER_ASSET_HEALTH_CACHE: dict[str, dict[str, object]] = {}
_SEMANTIC_READER_ASSET_HEALTH_CACHE_LOCK = threading.Lock()
_SEMANTIC_READER_ASSET_HEALTH_CACHE_MAX = 128
_SEMANTIC_READER_JOB_ROOT_PREFIXES = ("input/", "log/", "output/", "report/", "reports/", "review/")
_SEMANTIC_READER_ASSET_RECOVERY_CACHE: dict[str, dict[str, object]] = {}
_SEMANTIC_READER_ASSET_RECOVERY_LOCK = threading.Lock()
_TWO_CROP_REVIEW_ZIP_NAME = "chess_fen_two_crop_review_artifacts.zip"
_TWO_CROP_REVIEW_MEMBER_RE = re.compile(
    r"review/chess_fen/two_crop/(?P<filename>[A-Za-z0-9_.-]+_(?P<kind>board|marker|overlay)\.png)"
)
_TWO_CROP_RECOVERY_MAX_MEMBERS = 4_096
_TWO_CROP_RECOVERY_MAX_MEMBER_BYTES = 20 * 1024 * 1024
_TWO_CROP_RECOVERY_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_TWO_CROP_RECOVERY_MAX_COMPRESSION_RATIO = 200


def _safe_semantic_reader_asset_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith(("#", "/", "//")):
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        return ""
    decoded_path = urllib.parse.unquote(parsed.path).replace("\\", "/")
    parts: list[str] = []
    for part in decoded_path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                parts.append(part)
            elif parts[-1] == "..":
                parts.append(part)
            else:
                parts.pop()
            continue
        if ":" in part or "\x00" in part:
            return ""
        parts.append(part)
    return "/".join(parts)


def _named_artifact_child(directory: Path, name: str, *, directory_only: bool = False) -> Path | None:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name != name:
                    continue
                if directory_only and not entry.is_dir(follow_symlinks=False):
                    return None
                if not directory_only and not entry.is_file(follow_symlinks=False):
                    return None
                return Path(entry.path)
    except OSError:
        return None
    return None


def _semantic_reader_index_for_job(job_id: object) -> Path | None:
    safe_job_id = str(job_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_job_id):
        return None
    job_root = _artifact_job_root_for_id(safe_job_id)
    if job_root is None:
        return None
    semantic_root = _named_artifact_child(job_root, "semantic_chess_html", directory_only=True)
    if semantic_root is None:
        return None
    return _named_artifact_child(semantic_root, "index.html")


def _artifact_job_root_for_id(job_id: object) -> Path | None:
    safe_job_id = str(job_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_job_id):
        return None
    return _named_artifact_child(ARTIFACT_ROOT, safe_job_id, directory_only=True)


def _canonical_artifact_route_id(value: object) -> str | None:
    candidate = str(value or "").strip()
    sanitized = secure_filename(candidate)
    if sanitized != candidate or not re.fullmatch(r"[A-Za-z0-9_.-]+", sanitized):
        return None
    return sanitized


def _semantic_reader_asset_route_path(value: object, semantic_index: Path | None = None) -> str:
    del semantic_index  # Compatibility argument; paths are normalized independently of the filesystem.
    safe_path = _safe_semantic_reader_asset_path(value)
    return "" if safe_path.startswith("../") else safe_path


def _rewrite_semantic_chess_asset_urls(
    html_text: str,
    *,
    asset_base: str,
    semantic_index: Path | None = None,
) -> str:
    normalized_base = asset_base.rstrip("/") + "/"

    def replace(match: re.Match[str]) -> str:
        raw_url = match.group("url")
        route_path = _semantic_reader_asset_route_path(raw_url, semantic_index)
        if not route_path:
            return match.group(0)
        suffix = ""
        parsed = urllib.parse.urlsplit(raw_url)
        if parsed.query:
            suffix += f"?{parsed.query}"
        if parsed.fragment:
            suffix += f"#{parsed.fragment}"
        rewritten = f"{normalized_base}{quote(route_path, safe='/')}{suffix}"
        return f"{match.group('prefix')}{rewritten}{match.group('quote')}"

    return _SEMANTIC_READER_ASSET_ATTR_RE.sub(replace, html_text)


def _artifact_job_dir_from_path(path: Path) -> Path | None:
    resolved = path.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "report":
            return parent.parent
    return None


def _job_input_path(job: dict) -> Path | None:
    artifacts = dict(job.get("artifacts", {}) or {})
    input_artifact = artifacts.get("input")
    if isinstance(input_artifact, dict):
        location = str(input_artifact.get("location") or "").strip()
        if location:
            return Path(location)
    source_path = str(job.get("source_path") or "").strip()
    return Path(source_path) if source_path else None


def _pdf_source_fallback_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get("KINDLEMASTER_PDF_SOURCE_FALLBACK_ROOTS", "")
    for raw_root in configured.split(os.pathsep):
        raw_root = raw_root.strip()
        if raw_root:
            roots.append(Path(raw_root))
    roots.extend([Path(UPLOAD_DIR), Path(app.root_path) / "output" / "artifacts"])
    return roots


def _find_local_pdf_source_fallback(filename: str, size_bytes: int = 0) -> Path | None:
    safe_filename = Path(str(filename or "")).name
    if not safe_filename:
        return None
    for root in _pdf_source_fallback_roots():
        try:
            root_path = Path(root).resolve()
        except OSError:
            continue
        if not root_path.exists():
            continue
        candidates = [root_path / safe_filename]
        try:
            candidates.extend(root_path.rglob(safe_filename))
        except OSError:
            pass
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not _is_path_under(resolved, root_path) or not resolved.is_file():
                continue
            if size_bytes and resolved.stat().st_size != size_bytes:
                continue
            return resolved
    return None


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


def _app_trusted_marker_status(value: object) -> bool:
    status = str(value or "").strip().lower()
    return status == "trusted_marker" or status.startswith("trusted_")


def _side_to_move_from_diagram_record(record: Mapping[str, object], *, trusted_marker: bool) -> str:
    human_verified = record.get("fen_human_verified") is True
    if not trusted_marker and not human_verified:
        return "unknown"
    raw = str(record.get("side_to_move") or record.get("side_to_move_code") or "").strip().lower()
    if raw in {"w", "white"}:
        return "w"
    if raw in {"b", "black"}:
        return "b"
    full_fen = str(record.get("full_fen") or record.get("fen") or "").strip().split()
    if len(full_fen) > 1 and full_fen[1] in {"w", "b"}:
        return full_fen[1]
    return "unknown"


def _full_fen_status_accepted(value: object) -> bool:
    status = str(value or "").strip().lower()
    return status in {
        "accepted",
        "full_fen_accepted",
        "fen_full_accepted",
        "trusted",
        "trusted_marker",
        "fen_machine_accepted",
        "fen_corpus_verified",
        "fen_human_verified",
    }


def _diagram_record_to_reader_position(record: Mapping[str, object], index: int) -> dict[str, object]:
    human_verified = record.get("fen_human_verified") is True
    marker_semantic_status = str(record.get("marker_semantic_status") or "").strip().lower()
    raw_marker_status = str(record.get("side_marker_status") or "").strip().lower()
    trusted_marker = (
        marker_semantic_status == "trusted"
        if marker_semantic_status
        else _app_trusted_marker_status(record.get("side_marker_status"))
    )
    if not marker_semantic_status:
        marker_semantic_status = (
            "trusted"
            if trusted_marker
            else "missing"
            if raw_marker_status in {"", "missing", "marker_missing", "none"}
            else "review"
        )
    full_fen = str(record.get("full_fen") or "").strip()
    placement_fen = str(record.get("fen") or record.get("fen_candidate") or "").strip()
    fen_value = full_fen or placement_fen
    requires_review = bool(record.get("requires_review"))
    status_text = str(record.get("status") or "").strip().lower()
    accepted_full_fen = (
        human_verified or record.get("full_fen_allowed") is True
        if "full_fen_allowed" in record
        else human_verified or _full_fen_status_accepted(record.get("full_fen_status"))
    )
    accepted = bool(fen_value and not requires_review and accepted_full_fen and status_text not in {"review", "requires_review"})
    source_crop = str(
        record.get("board_crop_path")
        or record.get("source_crop")
        or record.get("image_data_uri")
        or record.get("filename")
        or ""
    ).strip()
    page = int(record.get("page") or record.get("page_number") or record.get("page_index") or 0)
    identifier = str(record.get("id") or record.get("diagram_id") or record.get("filename") or f"diagram-{index}").strip()
    label = str(record.get("diagram_number") or record.get("caption") or record.get("filename") or identifier).strip()
    blockers = record.get("blockers") if isinstance(record.get("blockers"), list) else []
    warnings = record.get("warnings") if isinstance(record.get("warnings"), list) else []
    review_reason = (
        str(record.get("review_reason") or record.get("fen_suppressed_reason") or "").strip()
        or ", ".join(str(item) for item in [*blockers, *warnings] if str(item).strip())
        or ("accepted" if accepted else "requires_review")
    )
    return {
        "id": identifier or f"diagram-{index}",
        "status": "accepted" if accepted else "needs_review",
        "label": label or f"Diagram {index}",
        "chapter_title": "Chess diagrams",
        "diagram_page": page,
        "solution_page": "",
        "side_to_move": _side_to_move_from_diagram_record(record, trusted_marker=trusted_marker),
        "fen": fen_value if accepted else "",
        "fen_candidate": fen_value if not accepted else "",
        "source_crop": source_crop,
        "board_crop_path": str(record.get("board_crop_path") or source_crop or "").strip(),
        "side_marker_crop_path": str(record.get("side_marker_crop_path") or "").strip(),
        "debug_overlay_path": str(record.get("debug_overlay_path") or "").strip(),
        "side_marker_status": str(record.get("side_marker_status") or "").strip(),
        "side_marker_symbol": str(record.get("side_marker_symbol") or "").strip(),
        "side_marker_bbox": record.get("side_marker_bbox") or record.get("side_marker_bbox_pixels") or [],
        "side_marker_assignment_trace": record.get("side_marker_assignment_trace") or record.get("acceptance_trace") or [],
        "marker_semantic_status": marker_semantic_status,
        "marker_semantic_side": str(
            record.get("marker_semantic_side")
            or _side_to_move_from_diagram_record(record, trusted_marker=trusted_marker)
        ),
        "marker_semantic_confidence": record.get("marker_semantic_confidence") or 0.0,
        "marker_ownership_status": str(record.get("marker_ownership_status") or "unassigned"),
        "board_placement_status": str(record.get("board_placement_status") or "review"),
        "full_fen_allowed": bool(accepted_full_fen),
        "full_fen_blockers": list(record.get("full_fen_blockers") or []),
        "fen_source": "human_verified" if human_verified else str(record.get("fen_source") or "automatic"),
        "human_verified": bool(record.get("human_verified")),
        "fen_human_verified": human_verified,
        "verification_source": str(record.get("verification_source") or ""),
        "verified_by": str(record.get("verified_by") or ""),
        "verified_at": str(record.get("verified_at") or ""),
        "warnings": list(warnings),
        "review_reason": review_reason,
    }


def _stored_chess_diagram_records(artifact: Mapping[str, object] | None) -> list[dict[str, object]]:
    artifact_path = _resolve_local_artifact_path(dict(artifact or {}))
    payload = _read_json_file(artifact_path)
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    return [dict(record) for record in records if isinstance(record, Mapping)]


def _reader_sidecar_summary(positions: list[dict[str, object]]) -> dict[str, object]:
    side_unknown_count = len(
        [item for item in positions if str(item.get("side_to_move") or "unknown").lower() not in {"w", "b", "white", "black"}]
    )
    accepted = [item for item in positions if item.get("status") == "accepted" and item.get("fen")]
    human_verified = [item for item in accepted if item.get("fen_human_verified") is True]
    automatic = [item for item in accepted if item.get("fen_human_verified") is not True]
    return {
        "pages": max([int(item.get("diagram_page") or 0) for item in positions] or [0]),
        "diagrams_total": len(positions),
        "fen_accepted": len(accepted),
        "fen_human_verified": len(human_verified),
        "fen_automatic": len(automatic),
        "fen_unrecognized": len(positions) - len(accepted),
        "needs_review_count": len([item for item in positions if item.get("status") != "accepted"]),
        "side_unknown_count": side_unknown_count,
        "trusted_marker_count": len([item for item in positions if _app_trusted_marker_status(item.get("side_marker_status"))]),
        "side_marker_crop_count": len([item for item in positions if str(item.get("side_marker_crop_path") or "").strip()]),
        "board_crop_count": len([item for item in positions if str(item.get("board_crop_path") or item.get("source_crop") or "").strip()]),
        "empty_img_src_count": 0,
        "accepted_pgn": 0,
    }


def _publish_verified_fen_review_artifacts(
    job_id: str,
    job: Mapping[str, object],
    *,
    review_dir: Path,
    review_payload: Mapping[str, object],
) -> dict[str, object]:
    current_job = _get_conversion_job(job_id)
    effective_job = current_job if isinstance(current_job, Mapping) else job
    artifacts = dict(effective_job.get("artifacts", {}) or {})
    diagrams_artifact = artifacts.get("chess_diagrams")
    diagrams_path = _resolve_local_artifact_path(
        diagrams_artifact if isinstance(diagrams_artifact, dict) else None
    )
    job_root = _artifact_job_dir_from_path(diagrams_path) if diagrams_path else None
    if job_root is None:
        raise ValueError("chess_artifact_root_missing")

    from chess_verified_fen_publication import publish_verified_fen_artifacts

    report = publish_verified_fen_artifacts(
        artifact_id=job_id,
        artifact_root=job_root,
        review_payload=review_payload,
        review_dir=review_dir,
    )
    report_artifacts = dict(report.get("artifacts") or {})
    artifact_specs = {
        "chess_diagrams_verified": (
            report_artifacts.get("verified_diagrams"),
            ArtifactKind.REPORT,
            "Zweryfikowane diagramy",
        ),
        "chess_verified_positions_pgn": (
            report_artifacts.get("verified_positions_pgn"),
            ArtifactKind.REPORT,
            "PGN zweryfikowanych pozycji",
        ),
        "chess_verified_positions_epub": (
            report_artifacts.get("verified_positions_epub"),
            ArtifactKind.OUTPUT,
            "EPUB zweryfikowanych pozycji",
        ),
        "chess_verified_fen_publication": (
            report_artifacts.get("publication_report"),
            ArtifactKind.REPORT,
            "Raport publikacji FEN",
        ),
    }
    for key, (raw_path, kind, label) in artifact_specs.items():
        path = Path(str(raw_path or ""))
        if not path.is_file() or not _is_path_under(path, job_root):
            raise ValueError(f"verified_fen_artifact_missing:{key}")
        metadata = _local_artifact_metadata(job_id, kind, path)
        metadata["download_url"] = f"/convert/artifact/{job_id}/{key}"
        metadata["label"] = label
        metadata["available"] = True
        metadata["status"] = "available"
        artifacts[key] = metadata

    verified_payload = _read_json_file(Path(str(report_artifacts.get("verified_diagrams") or "")))
    diagram_records = [
        dict(record)
        for record in verified_payload.get("records") or []
        if isinstance(record, Mapping)
    ]
    positions = [
        _diagram_record_to_reader_position(record, index)
        for index, record in enumerate(diagram_records, start=1)
    ]
    summary = _reader_sidecar_summary(positions)
    publication_summary = dict(report.get("summary") or {})
    summary.update(publication_summary)
    qa_report = {
        "status": "PASS" if not summary["needs_review_count"] else "PASS_WITH_REVIEW_ITEMS",
        "summary": summary,
        "problems": [],
        "status_policy": "human_verified_or_deterministic_machine_acceptance",
    }
    html_artifact = artifacts.get("chess_pgn_html")
    html_path = _resolve_local_artifact_path(html_artifact if isinstance(html_artifact, dict) else None)
    if html_path is None:
        raise ValueError("chess_reader_artifact_missing")
    from chess_study_export import render_study_html

    render_study_html(
        job_root / "semantic_chess_html",
        structure={"chapters": [{"chapter_no": 1, "title": "Chess diagrams"}]},
        positions={"positions": positions},
        qa_report=qa_report,
        source_pdf=_job_input_path(dict(effective_job)),
        source_html=html_path,
        source_gate={
            "decision": "use_source_bound_verified_positions_as_final_reader",
            "source_html_evidence_only": False,
            "used_as_final_reader": True,
            "reasons": [],
        },
    )
    artifacts["chess_pgn_html"] = _enrich_chess_reader_artifact_metadata(
        job_id,
        dict(html_artifact),
        html_path,
    )
    updated = _set_conversion_job(
        job_id,
        artifacts=artifacts,
        verified_fen_publication=report,
    )
    if updated is None:
        raise ValueError("conversion_job_update_failed")
    return report


def _source_sha256_for_job(job: Mapping[str, object]) -> str:
    source_path = _job_input_path(dict(job))
    if source_path is None or not source_path.is_file():
        return ""
    digest = sha256()
    try:
        with source_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _maybe_publish_source_bound_verified_fen(job_id: str) -> dict[str, object]:
    """Apply a completed exact-source review to a newly converted artifact."""
    job = _get_conversion_job(job_id)
    if not job:
        return {"status": "skipped", "reason": "conversion_job_missing"}
    owner_user_id = str(job.get("user_id") or "").strip()
    if not owner_user_id:
        return {"status": "skipped", "reason": "authenticated_owner_required"}
    review_dir = _resolve_local_fen_review_dir(job_id, job)
    if review_dir is None:
        return {"status": "skipped", "reason": "fen_review_artifact_missing"}
    source_digest = _source_sha256_for_job(job)
    if not source_digest:
        return {"status": "skipped", "reason": "source_document_missing"}

    from chess_verified_fen_reuse import bind_complete_review_to_artifact
    from supabase_fen_review import SupabaseFenReviewClient

    client = SupabaseFenReviewClient()
    if not client.available:
        return {"status": "skipped", "reason": "supabase_fen_review_unavailable"}
    review_payload = client.load_review(
        artifact_id=job_id,
        source_document_sha256=source_digest,
        owner_user_id=owner_user_id,
    )
    if not isinstance(review_payload, Mapping):
        return {"status": "skipped", "reason": "completed_source_review_missing"}
    if str(review_payload.get("session_status") or "").strip().lower() != "complete":
        return {"status": "skipped", "reason": "source_review_not_complete"}
    bound_payload = bind_complete_review_to_artifact(
        review_payload,
        artifact_id=job_id,
        source_document_sha256=source_digest,
    )
    report = _publish_verified_fen_review_artifacts(
        job_id,
        job,
        review_dir=review_dir,
        review_payload=bound_payload,
    )
    return {
        **report,
        "reused_from_artifact_id": str(bound_payload.get("reused_from_artifact_id") or ""),
    }


def _create_semantic_chess_reader_sidecar(
    job_id: str,
    stored: dict[str, dict],
    *,
    job: Mapping[str, object] | None = None,
) -> dict[str, dict]:
    html_artifact = stored.get("chess_pgn_html")
    if not isinstance(html_artifact, dict):
        return stored
    html_path = _resolve_local_artifact_path(html_artifact)
    if html_path is None:
        return stored
    diagram_records = _stored_chess_diagram_records(stored.get("chess_diagrams"))
    if not diagram_records:
        stored["chess_pgn_html"] = _enrich_chess_reader_artifact_metadata(job_id, dict(html_artifact), html_path)
        return stored
    job_dir = _artifact_job_dir_from_path(html_path)
    if job_dir is None:
        stored["chess_pgn_html"] = _enrich_chess_reader_artifact_metadata(job_id, dict(html_artifact), html_path)
        return stored
    positions = [_diagram_record_to_reader_position(record, index) for index, record in enumerate(diagram_records, start=1)]
    summary = _reader_sidecar_summary(positions)
    qa_report = {
        "status": "PASS" if not summary["needs_review_count"] else "PASS_WITH_REVIEW_ITEMS",
        "summary": summary,
        "problems": [],
        "status_policy": "accepted_requires_deterministic_validation",
    }
    source_gate = {
        "decision": "use_conversion_records_as_final_reader",
        "source_html_evidence_only": False,
        "used_as_final_reader": True,
        "reasons": [],
    }
    try:
        from chess_study_export import render_study_html

        render_study_html(
            job_dir / "semantic_chess_html",
            structure={"chapters": [{"chapter_no": 1, "title": "Chess diagrams"}]},
            positions={"positions": positions},
            qa_report=qa_report,
            source_pdf=_job_input_path(dict(job or {})),
            source_html=html_path,
            source_gate=source_gate,
        )
    except Exception:
        stored["chess_pgn_html"] = _enrich_chess_reader_artifact_metadata(job_id, dict(html_artifact), html_path)
        return stored
    stored["chess_pgn_html"] = _enrich_chess_reader_artifact_metadata(job_id, dict(html_artifact), html_path)
    return stored


def _store_extra_conversion_artifacts(
    job_id: str,
    extra_artifacts: list[dict] | None,
    *,
    job: Mapping[str, object] | None = None,
) -> dict[str, dict]:
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
        if key == "chess_pgn_html":
            metadata = _enrich_chess_reader_artifact_metadata(job_id, metadata, _resolve_local_artifact_path(metadata))
        if key == "chess_pgn":
            metadata["available"] = True
            metadata["status"] = "available"
            metadata["message"] = CHESS_PGN_AVAILABLE_MESSAGE
            metadata["label"] = "PGN"
        if key == "engine_analysis_gate":
            metadata["content_type"] = "application/json; charset=utf-8"
            metadata["label"] = "Engine analysis gate"
        stored[key] = metadata
    stored = _create_semantic_chess_reader_sidecar(job_id, stored, job=job)
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


def _normalize_engine_analysis_gate(payload: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(payload, Mapping) or not payload:
        return {}
    top_reasons = payload.get("top_reasons") if isinstance(payload.get("top_reasons"), list) else []
    return {
        "schema": str(payload.get("schema") or "kindlemaster.chess_engine.gate.v1"),
        "diagram_count": int(payload.get("diagram_count") or 0),
        "eligible_count": int(payload.get("eligible_count") or 0),
        "analyzed_count": int(payload.get("analyzed_count") or 0),
        "unavailable_count": int(payload.get("unavailable_count") or 0),
        "engine_available": bool(payload.get("engine_available")),
        "top_reasons": [dict(item) for item in top_reasons if isinstance(item, Mapping)],
        "engine_reader_available": bool(payload.get("engine_reader_available")),
        "availability": str(payload.get("availability") or ("available" if payload.get("engine_reader_available") else "unavailable")),
        "message": str(payload.get("message") or ""),
    }


def _candidate_engine_analysis_gate_paths(path: Path | None) -> list[Path]:
    if path is None:
        return []
    candidates: list[Path] = []
    if path.name == "engine_analysis_gate.json":
        candidates.append(path)
    parent = path.parent
    candidates.extend(
        [
            parent / "reports" / "chess_engine" / "engine_analysis_gate.json",
            parent / "chess_engine" / "engine_analysis_gate.json",
            parent / "engine_analysis_gate.json",
        ]
    )
    if parent.name == "reports":
        candidates.append(parent / "chess_engine" / "engine_analysis_gate.json")
    job_dir = _artifact_job_dir_from_path(path)
    if job_dir is not None:
        candidates.extend(
            [
                job_dir / "reports" / "chess_engine" / "engine_analysis_gate.json",
                job_dir / "semantic_chess_html" / "reports" / "chess_engine" / "engine_analysis_gate.json",
                job_dir / "report" / "engine_analysis_gate.json",
            ]
        )
    return candidates


def _read_engine_analysis_gate_from_path(path: Path | None) -> dict[str, object]:
    for candidate in _candidate_engine_analysis_gate_paths(path):
        gate = _normalize_engine_analysis_gate(_read_json_file(candidate))
        if gate:
            return gate
    return {}


def _engine_analysis_gate_from_job(job: Mapping[str, object]) -> dict[str, object]:
    containers: list[Mapping[str, object]] = []
    for value in (
        job,
        job.get("metadata"),
        job.get("quality_state"),
        job.get("quality_state_snapshot"),
        job.get("conversion"),
    ):
        if isinstance(value, Mapping):
            containers.append(value)
    for container in list(containers):
        nested = container.get("metadata")
        if isinstance(nested, Mapping):
            containers.append(nested)
    for container in containers:
        for key in ("engine_analysis_gate", "engine_analysis_availability", "chess_engine_analysis_gate"):
            gate = _normalize_engine_analysis_gate(container.get(key) if isinstance(container.get(key), Mapping) else None)
            if gate:
                return gate
    artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), Mapping) else {}
    gate_artifact = artifacts.get("engine_analysis_gate") if isinstance(artifacts.get("engine_analysis_gate"), Mapping) else None
    gate = _read_engine_analysis_gate_from_path(_resolve_local_artifact_path(dict(gate_artifact or {})))
    if gate:
        return gate
    for artifact in artifacts.values():
        if not isinstance(artifact, Mapping):
            continue
        gate = _read_engine_analysis_gate_from_path(_resolve_local_artifact_path(dict(artifact)))
        if gate:
            return gate
    return {}


def _candidate_chess_reader_manifest_paths(path: Path | None) -> list[Path]:
    if path is None:
        return []
    parent = path.parent
    candidates = [
        parent / "data" / "artifact_manifest.json",
        parent / "source_html_evidence_manifest.json",
        parent / "reports" / "source_html_evidence_manifest.json",
    ]
    if parent.name == "reports":
        candidates.extend(
            [
                parent / "source_html_evidence_manifest.json",
                parent.parent / "data" / "artifact_manifest.json",
            ]
        )
    job_dir = _artifact_job_dir_from_path(path)
    if job_dir is not None:
        candidates.extend(
            [
                job_dir / "semantic_chess_html" / "data" / "artifact_manifest.json",
                job_dir / "semantic_chess_html" / "reports" / "source_html_evidence_manifest.json",
            ]
        )
    return candidates


def _read_chess_reader_manifest(path: Path | None) -> dict:
    for candidate in _candidate_chess_reader_manifest_paths(path):
        payload = _read_json_file(candidate)
        if payload:
            return payload
    return {}


def _candidate_source_html_gate_paths(path: Path | None) -> list[Path]:
    if path is None:
        return []
    parent = path.parent
    candidates = [
        parent / "reports" / "source_html_quality_gate.json",
        parent / "source_html_quality_gate.json",
    ]
    if parent.name == "reports":
        candidates.append(parent / "source_html_quality_gate.json")
    else:
        candidates.append(parent.parent / "reports" / "source_html_quality_gate.json")
    job_dir = _artifact_job_dir_from_path(path)
    if job_dir is not None:
        candidates.append(job_dir / "semantic_chess_html" / "reports" / "source_html_quality_gate.json")
    return candidates


def _read_chess_reader_source_gate(path: Path | None) -> dict:
    for candidate in _candidate_source_html_gate_paths(path):
        payload = _read_json_file(candidate)
        if payload:
            return payload
    return {}


def _semantic_chess_index_for_artifact_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    if path.name == "index.html" and path.parent.name == "semantic_chess_html":
        return path
    job_dir = _artifact_job_dir_from_path(path)
    if job_dir is None:
        return None
    semantic_index = job_dir / "semantic_chess_html" / "index.html"
    return semantic_index if semantic_index.is_file() else None


def _semantic_chess_index_is_final_reader(path: Path | None) -> bool:
    semantic_index = _semantic_chess_index_for_artifact_path(path)
    if semantic_index is None:
        return False
    manifest = _read_json_file(semantic_index.parent / "data" / "artifact_manifest.json")
    return manifest.get("artifact_type") == FINAL_READER_ARTIFACT_TYPE


def _semantic_chess_reader_health_gate(job_id: object) -> dict:
    semantic_index = _semantic_reader_index_for_job(job_id)
    if semantic_index is None:
        return {}
    asset_recovery = _recover_semantic_reader_assets_from_zip(job_id)
    stored = _read_json_file(semantic_index.parent / "reports" / "final_reader_health_gate.json")
    if not stored:
        return {}
    asset_health = _semantic_reader_asset_health(job_id)
    blockers = [str(value) for value in stored.get("blockers", []) or [] if str(value)]
    warnings = [str(value) for value in stored.get("warnings", []) or [] if str(value)]
    if asset_health["missing_required_asset_count"]:
        blockers.append("missing_reader_assets")
    if asset_health["missing_optional_asset_count"]:
        warnings.append("missing_optional_reader_assets")
    stored.update(asset_health)
    stored["asset_recovery"] = asset_recovery
    stored["blockers"] = list(dict.fromkeys(blockers))
    stored["warnings"] = list(dict.fromkeys(warnings))
    if stored["blockers"]:
        stored["decision"] = "fail"
        stored["status"] = "FAIL"
    elif stored["warnings"]:
        stored["decision"] = "pass"
        stored["status"] = "PASS_WITH_WARNINGS"
    return stored


def _semantic_reader_asset_is_optional(node: object, asset_path: str) -> bool:
    classes = " ".join(getattr(node, "get", lambda *_: [])("class", []) or []).lower()
    alt = str(getattr(node, "get", lambda *_: "")("alt", "") or "").lower()
    normalized = asset_path.lower()
    return "debug" in classes or "debug overlay" in alt or normalized.endswith("_overlay.png")


def _semantic_reader_asset_candidate_paths(semantic_index: Path, safe_path: str) -> tuple[Path, ...]:
    semantic_root = semantic_index.parent
    job_root = semantic_root.parent if semantic_root.name == "semantic_chess_html" else semantic_root
    roots = (
        (job_root, semantic_root)
        if safe_path.lower().startswith(_SEMANTIC_READER_JOB_ROOT_PREFIXES)
        else (semantic_root, job_root)
    )
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / safe_path)
    return tuple(candidates)


def _named_artifact_descendant(root: Path, safe_path: str) -> Path | None:
    parts = [part for part in safe_path.split("/") if part]
    if not parts:
        return None
    current = root
    for index, part in enumerate(parts):
        current = _named_artifact_child(current, part, directory_only=index < len(parts) - 1)
        if current is None:
            return None
    return current


def _resolve_semantic_chess_asset_path(semantic_index: Path, asset_path: object) -> Path | None:
    safe_path = _safe_semantic_reader_asset_path(asset_path)
    if not safe_path or safe_path.startswith("../"):
        return None
    semantic_root = semantic_index.parent
    job_root = semantic_root.parent if semantic_root.name == "semantic_chess_html" else semantic_root
    roots = (
        (job_root, semantic_root)
        if safe_path.lower().startswith(_SEMANTIC_READER_JOB_ROOT_PREFIXES)
        else (semantic_root, job_root)
    )
    for root in roots:
        candidate = _named_artifact_descendant(root, safe_path)
        if candidate is not None:
            return candidate
    return None


def _cached_semantic_reader_image_path(job_id: object, safe_path: str) -> Path | None:
    semantic_index = _semantic_reader_index_for_job(job_id)
    if semantic_index is None:
        return None
    _semantic_reader_asset_health(job_id)
    cache_key = str(semantic_index)
    with _SEMANTIC_READER_ASSET_HEALTH_CACHE_LOCK:
        cached = _SEMANTIC_READER_ASSET_HEALTH_CACHE.get(cache_key)
    asset_paths = cached.get("asset_paths") if isinstance(cached, dict) else None
    if not isinstance(asset_paths, dict):
        return None
    resolved = asset_paths.get(safe_path)
    return Path(resolved) if isinstance(resolved, str) and resolved else None


def _path_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _two_crop_recovery_source(job_id: object) -> tuple[Path, Path] | None:
    job_root = _artifact_job_root_for_id(job_id)
    if job_root is None:
        return None
    report_root = _named_artifact_child(job_root, "report", directory_only=True)
    if report_root is None:
        return None
    archive_path = _named_artifact_child(report_root, _TWO_CROP_REVIEW_ZIP_NAME)
    return (job_root, archive_path) if archive_path is not None else None


def _two_crop_recovery_directory(job_root: Path) -> Path:
    return job_root / "review" / "chess_fen" / "two_crop"


def _two_crop_recovery_cache_key(job_root: Path) -> str:
    return str(job_root)


def _recover_semantic_reader_assets_from_zip(job_id: object) -> dict[str, object]:
    source = _two_crop_recovery_source(job_id)
    if source is None:
        return {"status": "unavailable", "reason": "two_crop_review_zip_missing", "recovered_count": 0}
    job_root, archive_path = source
    target_dir = _two_crop_recovery_directory(job_root)
    cache_key = _two_crop_recovery_cache_key(job_root)
    archive_signature = _path_signature(archive_path)
    target_signature = _path_signature(target_dir)
    with _SEMANTIC_READER_ASSET_RECOVERY_LOCK:
        cached = _SEMANTIC_READER_ASSET_RECOVERY_CACHE.get(cache_key)
        if (
            cached
            and cached.get("archive_signature") == archive_signature
            and cached.get("target_signature") == target_signature
        ):
            result = dict(cached.get("result") or {})
            result["cached"] = True
            return result

        recovered_count = 0
        existing_count = 0
        ignored_count = 0
        rejected_count = 0
        total_uncompressed_bytes = 0
        kind_counts = {"board": 0, "marker": 0, "overlay": 0}
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > _TWO_CROP_RECOVERY_MAX_MEMBERS:
                    raise ValueError("two_crop_zip_member_limit_exceeded")
                for info in members:
                    if info.is_dir():
                        ignored_count += 1
                        continue
                    match = _TWO_CROP_REVIEW_MEMBER_RE.fullmatch(info.filename)
                    if match is None:
                        ignored_count += 1
                        continue
                    if info.file_size < 1 or info.file_size > _TWO_CROP_RECOVERY_MAX_MEMBER_BYTES:
                        rejected_count += 1
                        continue
                    total_uncompressed_bytes += info.file_size
                    if total_uncompressed_bytes > _TWO_CROP_RECOVERY_MAX_TOTAL_BYTES:
                        raise ValueError("two_crop_zip_uncompressed_limit_exceeded")
                    compression_ratio = info.file_size / max(1, info.compress_size)
                    if compression_ratio > _TWO_CROP_RECOVERY_MAX_COMPRESSION_RATIO:
                        rejected_count += 1
                        continue
                    filename = match.group("filename")
                    target = target_dir / filename
                    if target.exists():
                        existing_count += 1
                        kind_counts[match.group("kind")] += 1
                        continue
                    payload = archive.read(info)
                    if len(payload) != info.file_size:
                        rejected_count += 1
                        continue
                    try:
                        with target.open("xb") as output:
                            output.write(payload)
                    except FileExistsError:
                        existing_count += 1
                    except OSError:
                        target.unlink(missing_ok=True)
                        raise
                    else:
                        recovered_count += 1
                    kind_counts[match.group("kind")] += 1
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
            result = {
                "status": "failed",
                "reason": str(error),
                "recovered_count": recovered_count,
                "existing_count": existing_count,
                "ignored_count": ignored_count,
                "rejected_count": rejected_count,
            }
        else:
            result = {
                "status": "recovered" if recovered_count else "already_recovered",
                "reason": "",
                "recovered_count": recovered_count,
                "existing_count": existing_count,
                "ignored_count": ignored_count,
                "rejected_count": rejected_count,
                "board_count": kind_counts["board"],
                "marker_count": kind_counts["marker"],
                "overlay_count": kind_counts["overlay"],
            }

        semantic_index = _semantic_reader_index_for_job(job_id)
        if semantic_index is not None:
            with _SEMANTIC_READER_ASSET_HEALTH_CACHE_LOCK:
                _SEMANTIC_READER_ASSET_HEALTH_CACHE.pop(str(semantic_index), None)
        _SEMANTIC_READER_ASSET_RECOVERY_CACHE[cache_key] = {
            "archive_signature": archive_signature,
            "target_signature": _path_signature(target_dir),
            "result": dict(result),
        }
        return result


def _cached_semantic_reader_asset_health(semantic_index: Path) -> dict[str, object] | None:
    cache_key = str(semantic_index)
    with _SEMANTIC_READER_ASSET_HEALTH_CACHE_LOCK:
        cached = _SEMANTIC_READER_ASSET_HEALTH_CACHE.get(cache_key)
    if not cached or cached.get("index_signature") != _path_signature(semantic_index):
        return None
    directory_signatures = cached.get("directory_signatures")
    if not isinstance(directory_signatures, dict):
        return None
    for raw_path, signature in directory_signatures.items():
        if _path_signature(Path(raw_path)) != signature:
            return None
    health = cached.get("health")
    return dict(health) if isinstance(health, dict) else None


def _store_semantic_reader_asset_health(
    semantic_index: Path,
    *,
    directories: set[Path],
    health: dict[str, object],
    asset_paths: dict[str, Path],
) -> None:
    cache_key = str(semantic_index)
    entry = {
        "index_signature": _path_signature(semantic_index),
        "directory_signatures": {str(path): _path_signature(path) for path in sorted(directories)},
        "health": dict(health),
        "asset_paths": {key: str(path) for key, path in asset_paths.items()},
    }
    with _SEMANTIC_READER_ASSET_HEALTH_CACHE_LOCK:
        if len(_SEMANTIC_READER_ASSET_HEALTH_CACHE) >= _SEMANTIC_READER_ASSET_HEALTH_CACHE_MAX:
            oldest_key = next(iter(_SEMANTIC_READER_ASSET_HEALTH_CACHE))
            _SEMANTIC_READER_ASSET_HEALTH_CACHE.pop(oldest_key, None)
        _SEMANTIC_READER_ASSET_HEALTH_CACHE[cache_key] = entry


def _semantic_reader_asset_health(job_id: object) -> dict[str, object]:
    semantic_index = _semantic_reader_index_for_job(job_id)
    if semantic_index is None:
        return {
            "referenced_image_asset_count": 0,
            "missing_required_asset_count": 1,
            "missing_optional_asset_count": 0,
            "missing_required_asset_paths": ["index.html"],
            "missing_optional_asset_paths": [],
        }
    cached = _cached_semantic_reader_asset_health(semantic_index)
    if cached is not None:
        return cached
    try:
        html_text = semantic_index.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        html_text = semantic_index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {
            "referenced_image_asset_count": 0,
            "missing_required_asset_count": 1,
            "missing_optional_asset_count": 0,
            "missing_required_asset_paths": ["index.html"],
            "missing_optional_asset_paths": [],
        }
    soup = BeautifulSoup(html_text, "html.parser")
    referenced_count = 0
    missing_required: list[str] = []
    missing_optional: list[str] = []
    asset_directories: set[Path] = set()
    image_references: list[tuple[object, str, tuple[Path, ...]]] = []
    for image in soup.find_all("img"):
        raw_src = str(image.get("src") or "").strip()
        route_path = _semantic_reader_asset_route_path(raw_src, semantic_index)
        if not route_path:
            if raw_src and urllib.parse.urlsplit(raw_src).scheme in {"http", "https", "data"}:
                continue
            missing_required.append(raw_src or "<empty-src>")
            continue
        referenced_count += 1
        candidates = _semantic_reader_asset_candidate_paths(semantic_index, route_path)
        asset_directories.update(candidate.parent for candidate in candidates)
        image_references.append((image, route_path, candidates))

    directory_files: dict[Path, dict[str, Path]] = {}
    for directory in asset_directories:
        try:
            with os.scandir(directory) as entries:
                directory_files[directory] = {
                    entry.name: Path(entry.path)
                    for entry in entries
                    if entry.is_file(follow_symlinks=False)
                }
        except OSError:
            directory_files[directory] = {}

    resolved_asset_paths: dict[str, Path] = {}
    for image, route_path, candidates in image_references:
        resolved_candidate = next(
            (
                directory_files.get(candidate.parent, {}).get(candidate.name)
                for candidate in candidates
                if candidate.name in directory_files.get(candidate.parent, {})
            ),
            None,
        )
        if resolved_candidate is not None:
            resolved_asset_paths[route_path] = resolved_candidate
            continue
        target = missing_optional if _semantic_reader_asset_is_optional(image, route_path) else missing_required
        target.append(route_path)
    health = {
        "referenced_image_asset_count": referenced_count,
        "missing_required_asset_count": len(missing_required),
        "missing_optional_asset_count": len(missing_optional),
        "missing_required_asset_paths": missing_required[:25],
        "missing_optional_asset_paths": missing_optional[:25],
    }
    _store_semantic_reader_asset_health(
        semantic_index,
        directories=asset_directories,
        health=health,
        asset_paths=resolved_asset_paths,
    )
    return health


def _missing_final_reader_health_gate(semantic_index: Path | None) -> dict:
    if semantic_index is None:
        return {}
    manifest = _read_json_file(semantic_index.parent / "data" / "artifact_manifest.json")
    if manifest.get("artifact_type") != FINAL_READER_ARTIFACT_TYPE:
        return {}
    return {
        "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
        "decision": "fail",
        "status": "FAIL",
        "artifact_type": FINAL_READER_ARTIFACT_TYPE,
        "pipeline_mode": str(manifest.get("pipeline_mode") or ""),
        "blockers": ["final_reader_health_gate_missing"],
        "warnings": [],
    }


def _final_reader_health_gate_failed(health_gate: Mapping[str, object] | None) -> bool:
    if not isinstance(health_gate, Mapping):
        return False
    decision = str(health_gate.get("decision") or "").strip().lower()
    status = str(health_gate.get("status") or "").strip().upper()
    return decision == "fail" or status == "FAIL"


def _final_reader_health_summary(
    manifest: Mapping[str, object] | None,
    health_gate: Mapping[str, object] | None,
    artifact_health: Mapping[str, object] | None = None,
) -> dict[str, object]:
    manifest_mapping = manifest if isinstance(manifest, Mapping) else {}
    source = health_gate if isinstance(health_gate, Mapping) and health_gate else artifact_health if isinstance(artifact_health, Mapping) else {}
    summary: dict[str, object] = {}
    for key in (
        "schema",
        "decision",
        "status",
        "artifact_type",
        "pipeline_mode",
        "blockers",
        "warnings",
        "diagram_cards_count",
        "side_unknown_count",
        "data_side_marker_attr_count",
        "trusted_marker_count",
        "side_marker_crop_count",
        "board_crop_count",
        "empty_img_src_count",
        "asset_missing_empty_src_count",
        "referenced_image_asset_count",
        "missing_required_asset_count",
        "missing_optional_asset_count",
        "missing_required_asset_paths",
        "missing_optional_asset_paths",
        "asset_recovery",
        "diagrams_total",
        "fen_accepted",
    ):
        if key in source:
            summary[key] = source[key]
        elif key in manifest_mapping:
            summary[key] = manifest_mapping[key]
    return summary


def _bool_from_payload(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    if value is None:
        return default
    return bool(value)


def _resolved_reader_side_path(path: Path | None, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = _resolve_local_artifact_path({"location": str(candidate)})
        return str(resolved) if resolved is not None else ""
    normalized = raw.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} or ":" in part for part in parts):
        return ""
    return "/".join(parts)


def _chess_reader_routing_metadata(
    job_id: str,
    artifact_path: Path | None,
    artifact: Mapping[str, object] | None = None,
) -> dict[str, object]:
    manifest = _read_chess_reader_manifest(artifact_path)
    semantic_index = _semantic_chess_index_for_artifact_path(artifact_path)
    if semantic_index is not None and _semantic_chess_index_is_final_reader(semantic_index):
        semantic_manifest = _read_json_file(semantic_index.parent / "data" / "artifact_manifest.json")
        if semantic_manifest.get("artifact_type") == FINAL_READER_ARTIFACT_TYPE:
            manifest = semantic_manifest
    source_gate = _read_chess_reader_source_gate(artifact_path)
    artifact_mapping = dict(artifact or {})
    manifest_gate = manifest.get("source_html_quality_gate") if isinstance(manifest.get("source_html_quality_gate"), Mapping) else {}

    artifact_type = str(
        artifact_mapping.get("artifact_type")
        or manifest.get("artifact_type")
        or ""
    ).strip()
    gate_decision = str(
        source_gate.get("decision")
        or (manifest_gate.get("decision") if isinstance(manifest_gate, Mapping) else "")
        or ""
    ).strip()
    source_evidence_only = _bool_from_payload(
        source_gate.get("source_html_evidence_only")
        if source_gate
        else (manifest_gate.get("source_html_evidence_only") if isinstance(manifest_gate, Mapping) else None),
        default=artifact_type == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE,
    )
    used_as_final_reader = _bool_from_payload(
        source_gate.get("used_as_final_reader")
        if source_gate
        else (manifest_gate.get("used_as_final_reader") if isinstance(manifest_gate, Mapping) else None),
        default=artifact_type == FINAL_READER_ARTIFACT_TYPE,
    )
    if artifact_type == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE:
        source_evidence_only = True
        used_as_final_reader = False
    if artifact_type == FINAL_READER_ARTIFACT_TYPE and not source_gate and not manifest_gate:
        used_as_final_reader = True

    source_html_quality_gate = {
        "decision": gate_decision,
        "source_html_evidence_only": source_evidence_only,
        "used_as_final_reader": used_as_final_reader,
        "reasons": list(source_gate.get("reasons") or (manifest_gate.get("reasons") if isinstance(manifest_gate, Mapping) else []) or []),
        "summary": dict(source_gate.get("summary") or {}),
    }
    final_reader_path = ""
    final_reader_path_obj: Path | None = None
    if artifact_type == FINAL_READER_ARTIFACT_TYPE:
        semantic_index = _semantic_chess_index_for_artifact_path(artifact_path)
        if semantic_index is not None and _semantic_chess_index_is_final_reader(semantic_index):
            final_reader_path_obj = semantic_index
        elif artifact_path is not None:
            final_reader_path_obj = artifact_path
        final_reader_path = str(final_reader_path_obj or "")
    source_html_evidence_path = _resolved_reader_side_path(
        artifact_path,
        source_gate.get("source_html_evidence_path") or artifact_mapping.get("source_html_evidence_path"),
    )
    if not source_html_evidence_path and (source_evidence_only or artifact_type == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE):
        source_html_evidence_path = str(artifact_path) if artifact_path is not None else ""
    if (
        not source_html_evidence_path
        and artifact_type == FINAL_READER_ARTIFACT_TYPE
        and artifact_path is not None
        and final_reader_path_obj is not None
        and artifact_path.suffix.lower() in {".html", ".htm"}
        and artifact_path != final_reader_path_obj
    ):
        source_html_evidence_path = str(artifact_path)
    health_gate = _semantic_chess_reader_health_gate(job_id)
    if final_reader_path_obj is not None and not health_gate:
        health_gate = _missing_final_reader_health_gate(final_reader_path_obj)
    artifact_health = artifact_mapping.get("final_reader_health")
    final_reader_health = _final_reader_health_summary(
        manifest,
        health_gate,
        artifact_health if isinstance(artifact_health, Mapping) else None,
    )
    final_reader_health_failed = _final_reader_health_gate_failed(health_gate) or _final_reader_health_gate_failed(
        artifact_health if isinstance(artifact_health, Mapping) else None
    )
    signed_url = artifact_mapping.get("signed_url") if isinstance(artifact_mapping.get("signed_url"), Mapping) else {}
    remote_reader_available = bool(
        artifact_mapping.get("download_url")
        or artifact_mapping.get("downloadUrl")
        or artifact_mapping.get("url")
        or (signed_url.get("url") if isinstance(signed_url, Mapping) else "")
    )
    final_reader_available = bool(
        artifact_type == FINAL_READER_ARTIFACT_TYPE
        and (final_reader_path or remote_reader_available)
        and not _reader_health_blocks_whole_artifact(health_gate)
    )
    final_reader_blockers: list[str] = []
    if final_reader_health_failed:
        raw_blockers = final_reader_health.get("blockers", [])
        if isinstance(raw_blockers, list):
            final_reader_blockers = [str(blocker) for blocker in raw_blockers if str(blocker)]
        if not final_reader_blockers:
            final_reader_blockers = ["final_reader_health_gate_failed"]
    if not final_reader_available:
        if artifact_type != FINAL_READER_ARTIFACT_TYPE:
            final_reader_blockers = [str(reason) for reason in source_html_quality_gate.get("reasons", []) if str(reason)]
            if not final_reader_blockers:
                final_reader_blockers = ["final_reader_missing"]
        elif not final_reader_path and not remote_reader_available:
            final_reader_blockers = ["final_reader_missing"]
    routing: dict[str, object] = {
        "artifact_type": artifact_type,
        "final_reader_path": final_reader_path,
        "final_reader_available": final_reader_available,
        "final_reader_health": final_reader_health,
        "final_reader_blockers": final_reader_blockers,
        "source_html_evidence_path": source_html_evidence_path,
        "source_html_quality_gate": source_html_quality_gate,
    }
    for key in ("side_unknown_count", "trusted_marker_count", "empty_img_src_count", "diagrams_total", "fen_accepted"):
        if key in final_reader_health:
            routing[key] = final_reader_health[key]
        elif key in manifest:
            routing[key] = manifest[key]
    return routing


def _enrich_chess_reader_artifact_metadata(job_id: str, artifact: dict, artifact_path: Path | None) -> dict:
    routing = _chess_reader_routing_metadata(job_id, artifact_path, artifact)
    artifact.update(routing)
    return artifact


def _enrich_job_chess_reader_artifact_routing(job_id: str, job: dict) -> dict[str, object]:
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get("chess_pgn_html")
    if not isinstance(artifact, dict):
        return {}
    artifact_path = _resolve_local_artifact_path(artifact)
    enriched = _enrich_chess_reader_artifact_metadata(job_id, dict(artifact), artifact_path)
    artifacts["chess_pgn_html"] = enriched
    job["artifacts"] = artifacts
    return {
        "final_reader_path": enriched.get("final_reader_path", ""),
        "final_reader_available": bool(enriched.get("final_reader_available", False)),
        "final_reader_health": dict(enriched.get("final_reader_health", {}) or {}),
        "final_reader_blockers": list(enriched.get("final_reader_blockers", []) or []),
        "source_html_evidence_path": enriched.get("source_html_evidence_path", ""),
        "artifact_type": enriched.get("artifact_type", ""),
        "source_html_quality_gate": dict(enriched.get("source_html_quality_gate", {}) or {}),
        "side_unknown_count": enriched.get("side_unknown_count"),
        "trusted_marker_count": enriched.get("trusted_marker_count"),
        "empty_img_src_count": enriched.get("empty_img_src_count"),
        "diagrams_total": enriched.get("diagrams_total"),
        "fen_accepted": enriched.get("fen_accepted"),
    }


def _job_chess_pgn_summary(job: Mapping[str, object]) -> dict[str, object]:
    containers: list[Mapping[str, object]] = []
    for value in (
        job,
        job.get("metadata"),
        job.get("quality_state"),
        job.get("conversion"),
    ):
        if isinstance(value, Mapping):
            containers.append(value)
    for container in list(containers):
        for nested_key in ("metadata", "quality_report", "publication_report", "conversion"):
            nested = container.get(nested_key)
            if isinstance(nested, Mapping):
                containers.append(nested)
    for container in containers:
        for key in ("chess_pgn", "chess_pgn_summary", "pgn_summary", "pgn"):
            value = container.get(key)
            if isinstance(value, Mapping):
                return dict(value)
    return {}


def _job_has_chess_delivery_context(
    job: Mapping[str, object],
    artifacts: Mapping[str, object],
    pgn_summary: Mapping[str, object],
) -> bool:
    if any(key in artifacts for key in ("chess_pgn", "chess_pgn_html", "chess_exercises_pgn")):
        return True
    if pgn_summary:
        known_keys = {
            "candidate_game_count",
            "valid_pgn_count",
            "legal_pgn_count",
            "strict_export_count",
            "exportable_pgn_count",
            "manual_review_count",
            "exercise_export_count",
        }
        if any(key in pgn_summary for key in known_keys):
            return True
    metadata = job.get("metadata")
    profile = str(metadata.get("profile", "") if isinstance(metadata, Mapping) else "")
    return "chess" in profile.lower()


def _artifact_signed_url(artifact: Mapping[str, object]) -> str:
    signed_url = artifact.get("signed_url")
    if isinstance(signed_url, Mapping):
        return str(signed_url.get("url") or "").strip()
    return ""


def _chess_pgn_status_payload(
    job_id: str,
    artifact: Mapping[str, object] | None,
    pgn_summary: Mapping[str, object],
) -> dict[str, object]:
    source = dict(artifact or {})
    status = str(source.get("status") or "").strip().lower()
    existing_url = str(source.get("download_url") or source.get("downloadUrl") or _artifact_signed_url(source) or "").strip()
    available = bool(source) and status != "unavailable" and bool(
        existing_url
        or str(source.get("location") or "").strip()
        or _resolve_local_artifact_path(source) is not None
    )
    download_url = existing_url if available else ""
    if available and not download_url:
        download_url = f"/convert/artifact/{job_id}/chess_pgn"
    payload: dict[str, object] = {
        "key": "chess_pgn",
        "label": "PGN",
        "filename": str(source.get("filename") or "chess_games.pgn"),
        "content_type": str(source.get("content_type") or "application/x-chess-pgn; charset=utf-8"),
        "available": available,
        "status": "available" if available else "unavailable",
        "download_url": download_url,
        "message": CHESS_PGN_AVAILABLE_MESSAGE if available else CHESS_PGN_UNAVAILABLE_MESSAGE,
    }
    if not available:
        payload["reason"] = "no_accepted_pgn_records"
    for key in (
        "candidate_game_count",
        "valid_pgn_count",
        "legal_pgn_count",
        "strict_export_count",
        "exportable_pgn_count",
        "manual_review_count",
        "exercise_export_count",
        "derived_final_fen_count",
        "fen_count",
    ):
        if key in pgn_summary:
            payload[key] = pgn_summary.get(key)
    return payload


def _enrich_job_chess_pgn_artifact_routing(job_id: str, job: dict) -> dict[str, object]:
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get("chess_pgn")
    source_artifact = artifact if isinstance(artifact, Mapping) else None
    pgn_summary = _job_chess_pgn_summary(job)
    if not _job_has_chess_delivery_context(job, artifacts, pgn_summary):
        return {}
    payload = _chess_pgn_status_payload(job_id, source_artifact, pgn_summary)
    if source_artifact:
        enriched = dict(source_artifact)
        enriched.update(payload)
        artifacts["chess_pgn"] = enriched
    else:
        artifacts["chess_pgn"] = dict(payload)
    job["artifacts"] = artifacts
    return dict(artifacts["chess_pgn"])


def _chess_reader_file_payload(job_id: str, job: Mapping[str, object], reader_payload: Mapping[str, object]) -> dict[str, object]:
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get("chess_pgn_html") if isinstance(artifacts.get("chess_pgn_html"), Mapping) else {}
    href = str(dict(artifact).get("download_url") or dict(artifact).get("downloadUrl") or "").strip()
    if not href and reader_payload.get("artifact_type") == FINAL_READER_ARTIFACT_TYPE:
        href = f"/convert/artifact/{job_id}/chess_pgn_html"
    blockers = list(reader_payload.get("final_reader_blockers", []) or [])
    available = bool(reader_payload.get("final_reader_available", False)) and bool(href)
    payload: dict[str, object] = {
        "key": "chess_pgn_html",
        "label": "HTML PGN/FEN",
        "filename": str(dict(artifact).get("filename") or "chess_games.html"),
        "content_type": str(dict(artifact).get("content_type") or "text/html; charset=utf-8"),
        "artifact_type": str(reader_payload.get("artifact_type") or ""),
        "available": available,
        "status": "available" if available else "blocked",
        "download_url": href if available else "",
        "message": "HTML PGN/FEN gotowy do otwarcia." if available else "HTML PGN/FEN niedostepny.",
        "final_reader_available": bool(reader_payload.get("final_reader_available", False)),
        "final_reader_health": dict(reader_payload.get("final_reader_health", {}) or {}),
        "final_reader_blockers": blockers,
        "source_html_quality_gate": dict(reader_payload.get("source_html_quality_gate", {}) or {}),
    }
    for key in (
        "final_reader_path",
        "source_html_evidence_path",
        "side_unknown_count",
        "trusted_marker_count",
        "empty_img_src_count",
        "diagrams_total",
        "fen_accepted",
    ):
        if key in reader_payload:
            payload[key] = reader_payload.get(key)
    return payload


def _chess_reader_primary_file_payload(job_id: str, reader_payload: Mapping[str, object]) -> dict[str, object]:
    href = f"/convert/artifact/{job_id}/chess_reader"
    blockers = list(reader_payload.get("final_reader_blockers", []) or [])
    available = bool(reader_payload.get("final_reader_available", False))
    message = (
        "Chess Reader gotowy; czesc FEN/PGN moze wymagac review."
        if available and blockers
        else ("Chess Reader gotowy do otwarcia." if available else "Chess Reader niedostepny.")
    )
    payload: dict[str, object] = {
        "key": "chess_reader",
        "label": "Chess Reader",
        "filename": "chess_reader.html",
        "content_type": "text/html; charset=utf-8",
        "artifact_type": str(reader_payload.get("artifact_type") or ""),
        "available": available,
        "status": "available" if available else "blocked",
        "download_url": href if available else "",
        "message": message,
        "final_reader_available": available,
        "final_reader_health": dict(reader_payload.get("final_reader_health", {}) or {}),
        "final_reader_blockers": blockers,
        "source_html_quality_gate": dict(reader_payload.get("source_html_quality_gate", {}) or {}),
    }
    for key in (
        "final_reader_path",
        "source_html_evidence_path",
        "side_unknown_count",
        "trusted_marker_count",
        "empty_img_src_count",
        "diagrams_total",
        "fen_accepted",
    ):
        if key in reader_payload:
            payload[key] = reader_payload.get(key)
    return payload


def _enrich_job_chess_delivery_artifacts(job_id: str, job: dict) -> dict[str, object]:
    reader_payload = _enrich_job_chess_reader_artifact_routing(job_id, job)
    pgn_payload = _enrich_job_chess_pgn_artifact_routing(job_id, job)
    if not reader_payload and not pgn_payload:
        return {}
    payload: dict[str, object] = dict(reader_payload)
    chess_files: dict[str, object] = {}
    if pgn_payload:
        payload["chess_pgn"] = pgn_payload
        chess_files["chess_pgn"] = pgn_payload
    if reader_payload:
        reader_file_payload = _chess_reader_primary_file_payload(job_id, reader_payload)
        payload["chess_reader"] = reader_file_payload
        chess_files["chess_reader"] = reader_file_payload
        artifacts = dict(job.get("artifacts", {}) or {})
        artifacts["chess_reader"] = reader_file_payload
        job["artifacts"] = artifacts
        html_payload = _chess_reader_file_payload(job_id, job, reader_payload)
        payload["chess_pgn_html"] = html_payload
        chess_files["chess_pgn_html"] = html_payload
    if chess_files:
        payload["chess_files"] = chess_files
    return payload


def _chess_pgn_unavailable_response(job_id: str, artifact: Mapping[str, object] | None = None):
    payload = dict(artifact or {})
    if not payload:
        payload = _chess_pgn_status_payload(job_id, None, {})
    return _json_error(
        CHESS_PGN_UNAVAILABLE_MESSAGE,
        error_code=ERROR_CHESS_PGN_UNAVAILABLE,
        status_code=409,
        phase="download",
        job_id=job_id,
        retryable=False,
        extra={
            "chess_pgn": payload,
            "chess_files": {"chess_pgn": payload},
        },
    )


def _final_reader_missing_response(job_id: str, artifact_path: Path | None, artifact: Mapping[str, object] | None = None):
    routing = _chess_reader_routing_metadata(job_id, artifact_path, artifact)
    return _json_error(
        "Final chess HTML reader is not available for this conversion.",
        error_code=ERROR_FINAL_READER_MISSING,
        status_code=409,
        phase="download",
        job_id=job_id,
        retryable=False,
        extra=routing,
    )


def _final_reader_health_gate_failed_response(
    job_id: str,
    semantic_index: Path | None,
    artifact_path: Path | None,
    artifact: Mapping[str, object] | None,
    health_gate: Mapping[str, object],
):
    routing = _chess_reader_routing_metadata(job_id, artifact_path or semantic_index, artifact)
    routing["final_reader_health_gate"] = dict(health_gate)
    return _json_error(
        "Final chess HTML reader failed its health gate.",
        error_code=ERROR_FINAL_READER_HEALTH_GATE_FAILED,
        status_code=409,
        phase="download",
        job_id=job_id,
        retryable=False,
        extra=routing,
    )


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
    output_candidates = sorted((job_dir / "output").glob("*.epub")) if (job_dir / "output").is_dir() else []
    verified_positions_epub_file = next(
        (path for path in output_candidates if path.name == "chess_verified_positions.epub"),
        None,
    )
    output_file = next(
        (path for path in output_candidates if path.name != "chess_verified_positions.epub"),
        None,
    )
    quality_json_file = _first_file(job_dir / "report", "*.quality.json")
    markdown_report_file = _first_file(job_dir / "report", "*.quality.md")
    report_pgn_files = sorted((job_dir / "report").glob("*.pgn")) if (job_dir / "report").is_dir() else []
    chess_pgn_file = next((path for path in report_pgn_files if path.name == "chess_games.pgn"), None)
    if chess_pgn_file is None:
        chess_pgn_file = next((path for path in report_pgn_files if path.name != "chess_exercises.pgn"), None)
    chess_exercises_pgn_file = next((path for path in report_pgn_files if path.name == "chess_exercises.pgn"), None)
    report_html_files = sorted((job_dir / "report").glob("*.html")) if (job_dir / "report").is_dir() else []
    pdf_layout_preview_file = next(
        (path for path in report_html_files if path.name == "pdf_layout_preview.html"),
        None,
    )
    chess_pgn_html_file = next(
        (path for path in report_html_files if path.name != "pdf_layout_preview.html"),
        None,
    )
    chess_glyph_diagnostics_file = _first_file(job_dir / "report", "chess_glyph_diagnostics.json")
    chess_diagrams_file = _first_file(job_dir / "report", "chess_diagrams.json")
    chess_diagrams_verified_file = _first_file(job_dir / "report", "chess_diagrams_verified.json")
    chess_verified_positions_pgn_file = _first_file(job_dir / "report", "chess_verified_positions.pgn")
    chess_verified_fen_publication_file = _first_file(job_dir / "report", "chess_verified_fen_publication.json")
    chess_fen_two_crop_review_artifacts_file = _first_file(
        job_dir / "report",
        _TWO_CROP_REVIEW_ZIP_NAME,
    )
    chess_fen_review_file = _first_file(job_dir / "review", "fen_manual_review.html")
    runtime_json_file = _first_file(job_dir / "log", "*.runtime.json")
    if input_file is None and output_file is None and quality_json_file is None and chess_fen_review_file is None:
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
        status = (
            "ready"
            if output_file is not None or chess_fen_review_file is not None or runtime_status == "succeeded"
            else "failed"
        )

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
            "message": str(
                report_job.get("message")
                or quality_state.get("message")
                or (
                    "Zestaw do oznaczania FEN jest gotowy."
                    if chess_fen_review_file is not None and output_file is None
                    else "EPUB gotowy do pobrania."
                    if status == "ready"
                    else "Historia odtworzona z lokalnych artefaktow."
                )
            ),
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
            "recovered_from_artifacts": True,
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
        artifacts["chess_pgn"]["available"] = True
        artifacts["chess_pgn"]["status"] = "available"
        artifacts["chess_pgn"]["message"] = CHESS_PGN_AVAILABLE_MESSAGE
    if chess_exercises_pgn_file is not None:
        artifacts["chess_exercises_pgn"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, chess_exercises_pgn_file)
        artifacts["chess_exercises_pgn"]["download_url"] = f"/convert/artifact/{job_id}/chess_exercises_pgn"
        artifacts["chess_exercises_pgn"]["label"] = "Exercises PGN"
    if chess_pgn_html_file is not None:
        artifacts["chess_pgn_html"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, chess_pgn_html_file)
        artifacts["chess_pgn_html"]["download_url"] = f"/convert/artifact/{job_id}/chess_pgn_html"
        artifacts["chess_pgn_html"]["label"] = "HTML PGN/FEN"
        artifacts["chess_pgn_html"] = _enrich_chess_reader_artifact_metadata(
            job_id,
            artifacts["chess_pgn_html"],
            chess_pgn_html_file,
        )
    if chess_glyph_diagnostics_file is not None:
        artifacts["chess_glyph_diagnostics"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_glyph_diagnostics_file,
        )
        artifacts["chess_glyph_diagnostics"]["download_url"] = f"/convert/artifact/{job_id}/chess_glyph_diagnostics"
        artifacts["chess_glyph_diagnostics"]["label"] = "Chess glyph diagnostics"
    if chess_diagrams_file is not None:
        artifacts["chess_diagrams"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_diagrams_file,
        )
        artifacts["chess_diagrams"]["download_url"] = f"/convert/artifact/{job_id}/chess_diagrams"
        artifacts["chess_diagrams"]["label"] = "Chess diagrams"
    if chess_diagrams_verified_file is not None:
        artifacts["chess_diagrams_verified"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_diagrams_verified_file,
        )
        artifacts["chess_diagrams_verified"]["download_url"] = f"/convert/artifact/{job_id}/chess_diagrams_verified"
        artifacts["chess_diagrams_verified"]["label"] = "Zweryfikowane diagramy"
    if chess_verified_positions_pgn_file is not None:
        artifacts["chess_verified_positions_pgn"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_verified_positions_pgn_file,
        )
        artifacts["chess_verified_positions_pgn"]["download_url"] = f"/convert/artifact/{job_id}/chess_verified_positions_pgn"
        artifacts["chess_verified_positions_pgn"]["label"] = "PGN zweryfikowanych pozycji"
    if verified_positions_epub_file is not None:
        artifacts["chess_verified_positions_epub"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.OUTPUT,
            verified_positions_epub_file,
        )
        artifacts["chess_verified_positions_epub"]["download_url"] = f"/convert/artifact/{job_id}/chess_verified_positions_epub"
        artifacts["chess_verified_positions_epub"]["label"] = "EPUB zweryfikowanych pozycji"
    if chess_verified_fen_publication_file is not None:
        artifacts["chess_verified_fen_publication"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_verified_fen_publication_file,
        )
        artifacts["chess_verified_fen_publication"]["download_url"] = f"/convert/artifact/{job_id}/chess_verified_fen_publication"
        artifacts["chess_verified_fen_publication"]["label"] = "Raport publikacji FEN"
    if chess_fen_two_crop_review_artifacts_file is not None:
        artifacts["chess_fen_two_crop_review_artifacts"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_fen_two_crop_review_artifacts_file,
        )
        artifacts["chess_fen_two_crop_review_artifacts"]["download_url"] = (
            f"/convert/artifact/{job_id}/chess_fen_two_crop_review_artifacts"
        )
        artifacts["chess_fen_two_crop_review_artifacts"]["label"] = "Chess FEN two-crop review artifacts"
    if chess_fen_review_file is not None:
        artifacts["chess_fen_review"] = _local_artifact_metadata(
            job_id,
            ArtifactKind.REPORT,
            chess_fen_review_file,
        )
        artifacts["chess_fen_review"]["download_url"] = f"/convert/artifact/{job_id}/chess_fen_review"
        artifacts["chess_fen_review"]["label"] = "Oznaczanie FEN i markerow"
    if pdf_layout_preview_file is not None:
        artifacts["pdf_layout_preview"] = _local_artifact_metadata(job_id, ArtifactKind.REPORT, pdf_layout_preview_file)
        artifacts["pdf_layout_preview"]["download_url"] = f"/convert/artifact/{job_id}/pdf_layout_preview"
        artifacts["pdf_layout_preview"]["label"] = "Audit View / Source Preview"
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
            if _named_artifact_child(job_dir, DELETED_ARTIFACT_MARKER) is not None:
                skipped += 1
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

    job_dir = _artifact_job_root_for_id(safe_job_id)
    if job_dir is None or _named_artifact_child(job_dir, DELETED_ARTIFACT_MARKER) is not None:
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


def _ensure_local_fen_review_artifact(job_id: str, job: dict) -> dict | None:
    safe_job_id = str(job_id or "").strip()
    if not safe_job_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_job_id):
        return None
    artifacts = dict(job.get("artifacts", {}) or {})
    job_root = _artifact_job_root_for_id(safe_job_id)
    if job_root is None:
        return None
    review_dir = _named_artifact_child(job_root, "review", directory_only=True)
    review_path = (
        _named_artifact_child(review_dir, "fen_manual_review.html")
        if review_dir is not None
        else None
    )
    if review_path is None:
        diagrams_artifact = artifacts.get("chess_diagrams")
        diagrams_path = _resolve_local_artifact_path(
            diagrams_artifact if isinstance(diagrams_artifact, dict) else None
        )
        if diagrams_path is None:
            report_dir = _named_artifact_child(job_root, "report", directory_only=True)
            diagrams_path = (
                _named_artifact_child(report_dir, "chess_diagrams.json")
                if report_dir is not None
                else None
            )
        if diagrams_path is None or not _is_path_under(diagrams_path, job_root):
            return None
        try:
            from chess_fen_review_builder import build_conversion_fen_review

            result = build_conversion_fen_review(
                artifact_id=safe_job_id,
                diagrams_path=diagrams_path,
            )
        except Exception as exc:
            app.logger.warning("Automatic FEN review build failed: %s", type(exc).__name__)
            return None
        review_path = Path(str(result.get("review_html") or ""))
        if not review_path.is_file() or not _is_path_under(review_path, job_root):
            return None
    artifact = _local_artifact_metadata(safe_job_id, ArtifactKind.REPORT, review_path)
    artifact["download_url"] = f"/convert/artifact/{safe_job_id}/chess_fen_review"
    artifact["label"] = "Oznaczanie FEN i markerow"
    artifacts = dict(job.get("artifacts", {}) or {})
    artifacts["chess_fen_review"] = artifact
    job["artifacts"] = artifacts
    _set_conversion_job(safe_job_id, artifacts=artifacts)
    return artifact


def _resolve_local_fen_review_dir(job_id: str, job: dict) -> Path | None:
    artifact = dict(job.get("artifacts", {}) or {}).get("chess_fen_review")
    if not isinstance(artifact, dict):
        artifact = _ensure_local_fen_review_artifact(job_id, job)
    artifact_path = _resolve_local_artifact_path(artifact)
    if artifact_path is None or not artifact_path.is_file():
        return None
    return artifact_path.parent


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


def _cloud_output_artifact(job: dict) -> dict:
    output_artifact = (job.get("artifacts", {}) or {}).get("output")
    if isinstance(output_artifact, dict) and output_artifact.get("provider") == "supabase":
        return dict(output_artifact)
    return {}


def _cloud_output_artifact_available(job: dict) -> bool:
    artifact = _cloud_output_artifact(job)
    return bool(artifact.get("storage_path") and artifact.get("storage_bucket"))


def _sign_cloud_output_artifact(job: dict) -> dict:
    artifact = _cloud_output_artifact(job)
    storage_path = str(artifact.get("storage_path", "") or "")
    if not storage_path:
        return {"available": False, "url": "", "expires_in_seconds": 0, "reason": "missing_cloud_output"}
    try:
        return _supabase_library_client().create_signed_artifact_url(storage_path=storage_path)
    except Exception as error:
        return {"available": False, "url": "", "expires_in_seconds": 0, "reason": str(error)}


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
    remote_output_available = bool(_signed_output_artifact_url(job)) or _cloud_output_artifact_available(job)
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
        quality_state.setdefault("job_id", job_id)
        download_state = _build_job_download_state(job_id, job)
        quality_state["download_url"] = download_state.download_url or ""
        quality_state["download_available"] = download_state.download_available
        quality_state["download_ready"] = download_state.download_ready
        quality_state["download_state"] = download_state.to_dict()
        artifacts = dict(quality_state.get("artifacts", {}) or {})
        artifacts.update(dict(job.get("artifacts", {}) or {}))
        quality_state["artifacts"] = artifacts
        quality_state["auto_repair"] = _build_job_auto_repair_state(job)
        engine_gate = _engine_analysis_gate_from_job({**job, "quality_state_snapshot": quality_state})
        if engine_gate:
            quality_state["engine_analysis_gate"] = engine_gate
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
    quality_state["auto_repair"] = _build_job_auto_repair_state(job)
    engine_gate = _engine_analysis_gate_from_job({**job, "quality_state": quality_state})
    if engine_gate:
        quality_state["engine_analysis_gate"] = engine_gate
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


def _get_local_conversion_job_unscoped(job_id: str) -> dict | None:
    """Read raw local state only for ownership-aware reconciliation."""
    with _CONVERSION_JOBS_LOCK:
        job = _CONVERSION_JOBS.get(job_id)
        return dict(job) if isinstance(job, dict) else None


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


def _delete_supabase_conversion_job(token: str, user_id: str, job_id: str) -> dict:
    """Delete one owned cloud job and its stored artifacts without orphaning history rows."""
    if not token or not user_id or not job_id:
        return {"status": "skipped", "provider": "supabase", "reason": "missing_identity"}

    artifact_path = (
        "/rest/v1/conversion_artifacts"
        f"?user_id=eq.{quote(user_id, safe='')}"
        f"&job_id=eq.{quote(job_id, safe='')}"
        "&select=storage_bucket,storage_path"
    )
    artifact_status, artifact_payload = _supabase_request_json(artifact_path, token=token)
    if artifact_status != 200 or not isinstance(artifact_payload, list):
        return {
            "status": "failed",
            "provider": "supabase",
            "error": f"artifact_lookup_failed_{artifact_status}",
        }

    paths_by_bucket: dict[str, list[str]] = {}
    for artifact in artifact_payload:
        if not isinstance(artifact, dict):
            continue
        bucket = str(artifact.get("storage_bucket") or SUPABASE_ARTIFACT_BUCKET).strip()
        storage_path = str(artifact.get("storage_path") or "").strip()
        if not bucket or not storage_path:
            continue
        paths_by_bucket.setdefault(bucket, []).append(storage_path)

    deleted_object_count = 0
    for bucket, storage_paths in paths_by_bucket.items():
        unique_paths = list(dict.fromkeys(storage_paths))
        for offset in range(0, len(unique_paths), 1000):
            batch = unique_paths[offset : offset + 1000]
            storage_status, _storage_payload = _supabase_request_json(
                f"/storage/v1/object/{quote(bucket, safe='')}",
                token=token,
                method="DELETE",
                payload={"prefixes": batch},
            )
            if storage_status != 200:
                return {
                    "status": "failed",
                    "provider": "supabase",
                    "error": f"storage_delete_failed_{storage_status}",
                    "deleted_object_count": deleted_object_count,
                }
            deleted_object_count += len(batch)

    job_path = (
        "/rest/v1/conversion_jobs"
        f"?user_id=eq.{quote(user_id, safe='')}"
        f"&job_id=eq.{quote(job_id, safe='')}"
    )
    job_status, job_payload = _supabase_request_json(
        job_path,
        token=token,
        method="DELETE",
        prefer="return=representation",
    )
    if job_status != 200:
        return {
            "status": "failed",
            "provider": "supabase",
            "error": f"job_delete_failed_{job_status}",
            "deleted_object_count": deleted_object_count,
        }
    if not isinstance(job_payload, list) or not job_payload:
        return {
            "status": "missing",
            "provider": "supabase",
            "reason": "job_not_found",
            "deleted_object_count": deleted_object_count,
        }
    return {
        "status": "deleted",
        "provider": "supabase",
        "deleted_object_count": deleted_object_count,
    }


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


def _restore_cloud_job_for_signed_access(job_id: str) -> dict | None:
    safe_job_id = _canonical_artifact_route_id(job_id)
    access_token = str(request.args.get(JOB_ACCESS_QUERY_PARAM) or "").strip()
    if safe_job_id is None or not verify_job_access_token(safe_job_id, access_token):
        return None
    try:
        cloud_job = _supabase_library_client().get_job_by_id(job_id=safe_job_id)
    except Exception as error:
        app.logger.warning("Signed cloud job restore failed for %s: %s", safe_job_id, error)
        return None
    if not isinstance(cloud_job, dict):
        return None
    if (
        str(cloud_job.get("job_id") or "").strip() != safe_job_id
        or not str(cloud_job.get("user_id") or "").strip()
        or cloud_job.get("cloud") is not True
    ):
        return None
    try:
        _CONVERSION_JOB_STORE.create(cloud_job)
    except Exception as error:
        app.logger.warning("Signed cloud job cache failed for %s: %s", safe_job_id, error)
        return None
    return _get_conversion_job(safe_job_id)


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


def _empty_auto_repair_state() -> dict:
    try:
        from epub_delivery_repair import empty_auto_repair_payload

        return empty_auto_repair_payload()
    except Exception:
        return {
            "status": "not_run",
            "actions": [],
            "quality_selection": {},
            "selected_candidate": "",
            "rejected_candidate": "",
            "before_blockers": [],
            "after_blockers": [],
            "error": "",
        }


def _build_job_auto_repair_state(job: dict) -> dict:
    payload = job.get("auto_repair")
    if isinstance(payload, dict) and payload:
        return dict(payload)
    metadata_payload = (job.get("metadata", {}) or {}).get("auto_repair") if isinstance(job.get("metadata"), dict) else None
    if isinstance(metadata_payload, dict) and metadata_payload:
        return dict(metadata_payload)
    return _empty_auto_repair_state()


def _empty_email_delivery_state() -> dict:
    return {
        "status": "not_sent",
        "channel": "email",
        "target": "send_to_kindle",
    }


def _build_job_email_delivery_state(job: dict) -> dict:
    payload = job.get("email_delivery")
    if isinstance(payload, dict) and payload:
        safe_payload = dict(payload)
        safe_payload.pop("recipient", None)
        safe_payload.pop("to", None)
        return safe_payload
    return _empty_email_delivery_state()


def _json_delivery_error(
    message: str,
    *,
    error_code: str,
    status_code: int,
    job_id: str | None = None,
    delivery: dict | None = None,
):
    payload = build_json_error_payload(
        message,
        error_code=error_code,
        phase="delivery",
        job_id=job_id,
        retryable=False,
    )
    if delivery is not None:
        payload["delivery"] = delivery
    response = jsonify(payload)
    response.status_code = status_code
    apply_no_store_headers(response.headers)
    return response


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
    chess_delivery_payload = _enrich_job_chess_delivery_artifacts(response_job_id, job)
    download_state = _build_job_download_state(response_job_id, job)
    source_preview_url = _source_pdf_preview_url(response_job_id, job)
    artifacts = dict(job.get("artifacts", {}) or {})
    if not isinstance(artifacts.get("chess_fen_review"), Mapping):
        job_root = _artifact_job_root_for_id(response_job_id)
        review_path = job_root / "review" / "fen_manual_review.html" if job_root is not None else None
        if review_path is not None and review_path.is_file():
            review_artifact = _local_artifact_metadata(response_job_id, ArtifactKind.REPORT, review_path)
            review_artifact["download_url"] = f"/convert/artifact/{response_job_id}/chess_fen_review"
            review_artifact["label"] = "Oznaczanie FEN i markerow"
            artifacts["chess_fen_review"] = review_artifact
        elif isinstance(artifacts.get("chess_rebuild_bundle"), Mapping):
            artifacts["chess_fen_review"] = {
                "provider": "cloud_rebuild",
                "status": "restorable",
                "kind": "chess_fen_review",
                "job_id": response_job_id,
                "filename": "fen_manual_review.html",
                "content_type": "text/html; charset=utf-8",
                "download_url": f"/convert/artifact/{response_job_id}/chess_fen_review",
                "label": "Oznaczanie FEN i markerow",
            }
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
        "auto_repair": _build_job_auto_repair_state(job),
        "email_delivery": _build_job_email_delivery_state(job),
        "runtime": dict(job.get("runtime", {}) or {}),
        "artifacts": artifacts,
        "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
        "cloud_sync": dict(job.get("cloud_sync", {}) or {}),
    }
    if chess_delivery_payload:
        item.update(chess_delivery_payload)
    if source_preview_url:
        item["source_preview_url"] = source_preview_url
    if status_key in {"ready", "failed", "timed_out"}:
        quality_state = _build_job_quality_state(response_job_id, job)
        quality_state.setdefault("job_id", response_job_id)
        item["quality_state"] = quality_state
    if download_state.download_url:
        item["download_url"] = download_state.download_url
    if status_key in {"failed", "timed_out"}:
        item["error"] = str(job.get("error", "") or "")
        item["error_code"] = str(job.get("error_code", "") or "")
    return item


_INTERNAL_LIBRARY_FILENAMES = {"ocr_probe.pdf"}


def _is_internal_library_job(job: dict) -> bool:
    filename = str(job.get("filename", "") or "").strip().lower()
    if filename in _INTERNAL_LIBRARY_FILENAMES:
        return True
    runtime = job.get("runtime", {}) if isinstance(job.get("runtime"), dict) else {}
    workflow = runtime.get("workflow", {}) if isinstance(runtime.get("workflow"), dict) else {}
    kwargs = workflow.get("kwargs", {}) if isinstance(workflow.get("kwargs"), dict) else {}
    original_filename = str(kwargs.get("original_filename", "") or "").strip().lower()
    return original_filename in _INTERNAL_LIBRARY_FILENAMES


def _visible_conversion_jobs_snapshot(*, owner: JobOwner | None = None) -> dict:
    jobs = {
        job_id: job
        for job_id, job in _CONVERSION_JOB_STORE.snapshot().items()
        if not _is_internal_library_job(dict(job))
    }
    if owner is None:
        return jobs
    return {job_id: job for job_id, job in jobs.items() if job_owned_by(job, owner)}


def _input_pdf_artifact(job: dict) -> dict:
    if str(job.get("source_type", "") or "").strip().lower() != "pdf":
        return {}
    artifact = (job.get("artifacts", {}) or {}).get("input")
    if not isinstance(artifact, dict):
        return {}
    content_type = str(artifact.get("content_type", "") or "").strip().lower()
    filename = str(artifact.get("filename", "") or "").strip().lower()
    if content_type != "application/pdf" and not filename.endswith(".pdf"):
        return {}
    return dict(artifact)


def _local_pdf_artifact_path(artifact: dict) -> Path | None:
    if str(artifact.get("provider", "") or "").strip().lower() != "local":
        return None
    location = str(artifact.get("location", "") or "").strip()
    if not location:
        return None
    try:
        path = Path(location).resolve()
        artifact_root = (Path("output") / "artifacts").resolve()
        path.relative_to(artifact_root)
    except (OSError, ValueError):
        return None
    if not path.is_file():
        return None
    return path


def _local_input_artifact_path(artifact: dict) -> Path | None:
    return _local_pdf_artifact_path(artifact)


def _pdf_artifact_candidate(job: dict, key: str) -> tuple[dict, Path | None]:
    artifacts = job.get("artifacts", {}) if isinstance(job.get("artifacts"), dict) else {}
    artifact = artifacts.get(key)
    if not isinstance(artifact, dict):
        return {}, None
    content_type = str(artifact.get("content_type", "") or "").strip().lower()
    filename = str(artifact.get("filename", "") or "").strip().lower()
    if content_type != "application/pdf" and not filename.endswith(".pdf"):
        return {}, None
    path = _local_pdf_artifact_path(dict(artifact))
    if not path:
        return {}, None
    return dict(artifact), path


def _pdf_delivery_artifact(job: dict, requested_artifact: str) -> tuple[dict, Path | None, str]:
    candidate_keys = ["cropped_pdf"] if requested_artifact == "cropped_pdf" else ["cropped_pdf", "pdf", "source_pdf", "input"]
    for key in candidate_keys:
        artifact, path = _pdf_artifact_candidate(job, key)
        if artifact and path:
            return artifact, path, key
    if requested_artifact in {"pdf", "source_pdf", "input_pdf"}:
        input_artifact = _input_pdf_artifact(job)
        input_path = _local_input_artifact_path(input_artifact) if input_artifact else None
        if input_artifact and input_path:
            return input_artifact, input_path, "input"
    return {}, None, ""


def _normalize_delivery_artifact_request(payload: dict) -> str:
    raw = str(payload.get("artifact") or payload.get("attachment") or "epub").strip().lower()
    if raw in {"", "epub", "final_epub", "final-epub"}:
        return "epub"
    if raw in {"pdf", "source_pdf", "source-pdf", "input_pdf", "input-pdf"}:
        return "pdf"
    if raw in {"cropped_pdf", "cropped-pdf", "crop_pdf", "crop-pdf"}:
        return "cropped_pdf"
    return raw


def _source_pdf_preview_url(job_id: str, job: dict) -> str:
    artifact = _input_pdf_artifact(job)
    if not artifact:
        return ""
    if _local_input_artifact_path(artifact):
        return f"/convert/preview/{quote(str(job_id), safe='')}/input"
    signed_url = artifact.get("signed_url")
    if isinstance(signed_url, dict) and signed_url.get("available") and signed_url.get("url"):
        return str(signed_url.get("url") or "")
    return ""


def _build_library_payload(*, default_include_text: bool = False) -> dict:
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    filters = _resolve_library_filters(default_include_text=default_include_text)
    return build_library_index(
        _visible_conversion_jobs_snapshot(),
        quality_state_builder=lambda job_id, job: _build_job_quality_state(job_id, dict(job)),
        output_size_resolver=lambda job: _read_output_size_bytes(dict(job)),
        filters=filters,
    )


def _build_scoped_library_payload(
    *,
    auth_context: AuthContext,
    default_include_text: bool = False,
) -> dict:
    if not auth_context.authenticated:
        payload = _build_library_payload(default_include_text=default_include_text)
        payload["library_scope"] = "local"
        payload["authenticated"] = False
        return payload
    try:
        jobs = {
            job["job_id"]: job
            for job in _supabase_library_client().list_user_jobs(
                user_id=auth_context.user_id,
                limit=_resolve_library_filters(default_include_text=default_include_text).limit,
            )
            if not _is_internal_library_job(dict(job))
        }
        payload = build_library_index(
            jobs,
            quality_state_builder=lambda job_id, job: _build_job_quality_state(job_id, dict(job)),
            output_size_resolver=lambda job: _read_output_size_bytes(dict(job)),
            filters=_resolve_library_filters(default_include_text=default_include_text),
        )
        payload["library_scope"] = "account"
        payload["authenticated"] = True
        payload["cloud_sync"] = {"status": "available", "provider": "supabase"}
        return payload
    except Exception as error:
        payload = _build_library_payload(default_include_text=default_include_text)
        payload["library_scope"] = "local_fallback"
        payload["authenticated"] = True
        payload["cloud_sync"] = {"status": "failed", "provider": "supabase", "error": str(error)}
        return payload


def _get_conversion_job_for_auth(job_id: str, auth_context: AuthContext) -> dict | None:
    local_job = _get_conversion_job(job_id)
    if not auth_context.authenticated:
        return local_job
    if local_job:
        owner = str(local_job.get("user_id", "") or "").strip()
        if owner == auth_context.user_id:
            return local_job
        if not owner:
            return local_job
        if owner:
            return None
    try:
        return _supabase_library_client().get_user_job(user_id=auth_context.user_id, job_id=job_id)
    except Exception:
        return None


def _get_existing_conversion_job_for_auth(job_id: str, auth_context: AuthContext) -> dict | None:
    local_job = _CONVERSION_JOB_STORE.get(job_id)
    if not auth_context.authenticated:
        return local_job
    if local_job:
        owner = str(local_job.get("user_id", "") or "").strip()
        if owner == auth_context.user_id or not owner:
            return local_job
        return None
    try:
        return _supabase_library_client().get_user_job(user_id=auth_context.user_id, job_id=job_id)
    except Exception:
        return None


def _delete_local_job_after_cloud_confirmation(job_id: str, user_id: str) -> dict:
    """Remove stale ownerless local state after Supabase proves ownership."""
    with _CONVERSION_JOBS_LOCK:
        raw_job = _CONVERSION_JOBS.get(job_id)
        if not isinstance(raw_job, dict):
            return {"status": "absent", "job": None}
        local_user_id = str(raw_job.get("user_id") or "").strip()
        local_guest_owner = str(raw_job.get("guest_owner_id") or "").strip()
        if (local_user_id and local_user_id != user_id) or local_guest_owner:
            return {"status": "protected", "job": None}
        deleted = dict(_CONVERSION_JOBS.pop(job_id))
    persist_result = _CONVERSION_JOB_STORE.persist()
    return {"status": "deleted", "job": deleted, "persist": persist_result}


def _local_artifact_job_dir(job_id: str) -> Path | None:
    root = ARTIFACT_ROOT.resolve()
    if not root.is_dir():
        return None
    try:
        candidates = root.iterdir()
    except OSError:
        return None
    for candidate in candidates:
        try:
            if candidate.is_dir() and candidate.name == job_id:
                return candidate.resolve()
        except OSError:
            continue
    return None


def _cleanup_deleted_conversion_job_files(
    job_id: str,
    job: dict,
    *,
    remove_artifact_job_dir: bool = True,
) -> dict:
    """Remove local files owned by a deleted conversion job."""
    deleted_paths: list[str] = []
    missing_paths: list[str] = []
    failed_paths: list[dict] = []
    candidate_paths: list[str] = []

    output_path = str(job.get("output_path") or "")
    if output_path:
        candidate_paths.append(output_path)
    for artifact in (job.get("artifacts", {}) or {}).values():
        if not isinstance(artifact, dict):
            continue
        if artifact.get("provider") not in {"", "local", None}:
            continue
        location = str(artifact.get("location") or "")
        if location:
            candidate_paths.append(location)

    for raw_path in dict.fromkeys(candidate_paths):
        try:
            path = Path(raw_path)
            if not path.exists():
                missing_paths.append(str(path))
                continue
            if path.is_file():
                path.unlink()
                deleted_paths.append(str(path))
            elif path.is_dir():
                shutil.rmtree(path)
                deleted_paths.append(str(path))
        except Exception:
            failed_paths.append({"path": raw_path, "error": "local_artifact_cleanup_failed"})

    tombstone_path = ""
    artifact_job_dir = _local_artifact_job_dir(job_id) if remove_artifact_job_dir else None
    if artifact_job_dir is not None:
        try:
            shutil.rmtree(artifact_job_dir)
            deleted_paths.append(str(artifact_job_dir))
        except OSError:
            failed_paths.append({"path": str(artifact_job_dir), "error": "artifact_directory_cleanup_failed"})
        try:
            artifact_job_dir.mkdir(parents=True, exist_ok=True)
            marker = artifact_job_dir / DELETED_ARTIFACT_MARKER
            marker.write_text(datetime.now(UTC).isoformat().replace("+00:00", "Z"), encoding="utf-8")
            tombstone_path = str(marker)
        except OSError:
            failed_paths.append(
                {
                    "path": str(artifact_job_dir / DELETED_ARTIFACT_MARKER),
                    "error": "artifact_tombstone_write_failed",
                }
            )

    return {
        "job_id": job_id,
        "deleted_paths": deleted_paths,
        "missing_paths": missing_paths,
        "failed_paths": failed_paths,
        "tombstone_path": tombstone_path,
    }


def _build_cloud_jobs_payload(auth_context: AuthContext, *, limit: int) -> dict:
    try:
        jobs = [
            job
            for job in _supabase_library_client().list_user_jobs(user_id=auth_context.user_id, limit=limit)
            if not _is_internal_library_job(dict(job))
        ]
        return {
            "success": True,
            "jobs": [_build_conversion_job_history_item(str(job.get("job_id") or ""), job) for job in jobs],
            "count": len(jobs),
            "total": len(jobs),
            "library_scope": "account",
            "authenticated": True,
            "cloud_sync": {"status": "available", "provider": "supabase"},
        }
    except Exception as error:
        owner = resolve_job_owner(authenticated=True, user_id=auth_context.user_id)
        jobs = _visible_conversion_jobs_snapshot(owner=owner)
        if is_local_request_host(request.host) and legacy_local_guest_allowed(request.host):
            legacy_owner = JobOwner(kind="legacy_local", owner_id=LEGACY_LOCAL_OWNER_ID)
            jobs.update(_visible_conversion_jobs_snapshot(owner=legacy_owner))
        recent_jobs = sorted(
            jobs.items(),
            key=lambda item: _conversion_job_sort_timestamp(item[1]),
            reverse=True,
        )[:limit]
        return {
            "success": True,
            "jobs": [_build_conversion_job_history_item(job_id, job) for job_id, job in recent_jobs],
            "count": len(recent_jobs),
            "total": len(jobs),
            "library_scope": "local_fallback",
            "authenticated": True,
            "cloud_sync": {"status": "failed", "provider": "supabase", "error": str(error)},
        }


class DurableArtifactSyncError(RuntimeError):
    def __init__(self, *, uploaded: list[dict[str, object]], failures: list[dict[str, str]]) -> None:
        super().__init__("durable_artifact_sync_incomplete")
        self.uploaded = uploaded
        self.failures = failures


def _durable_storage_chunk_size_bytes(default_bytes: int) -> int:
    try:
        configured = int(os.environ.get("KINDLEMASTER_SUPABASE_OBJECT_MAX_BYTES", default_bytes))
    except (TypeError, ValueError):
        configured = default_bytes
    return min(max(configured, 1), default_bytes)


def _sync_job_to_cloud(job_id: str) -> dict:
    job = _get_conversion_job(job_id)
    if not job:
        return {"status": "skipped", "reason": "missing_job"}
    user_id = str(job.get("user_id", "") or "").strip()
    if not user_id:
        return {"status": "skipped", "reason": "guest_job"}
    try:
        client = _supabase_library_client()
        quality_state = _build_job_quality_state(job_id, job)
        client.upsert_job_snapshot(user_id=user_id, job=job, quality_state=quality_state, imported_from_local=False)
        uploaded = _upload_durable_job_artifacts(
            client,
            user_id=user_id,
            job_id=job_id,
            job=job,
            quality_state=quality_state,
        )

        cloud_sync = {
            "status": "synced",
            "provider": "supabase",
            "synced_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "artifacts": uploaded,
        }
        _set_conversion_job(job_id, cloud_sync=cloud_sync)
        return cloud_sync
    except DurableArtifactSyncError as error:
        cloud_sync = {
            "status": "partial" if error.uploaded else "failed",
            "provider": "supabase",
            "error": str(error),
            "synced_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "artifacts": error.uploaded,
            "failures": error.failures,
        }
        _set_conversion_job(job_id, cloud_sync=cloud_sync)
        return cloud_sync
    except Exception as error:
        cloud_sync = {
            "status": "failed",
            "provider": "supabase",
            "error": str(error),
            "synced_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        capture_conversion_exception(
            error,
            context=build_conversion_context(
                job_id=job_id,
                input_type=str(job.get("source_type", "") or ""),
                source_type=str(job.get("source_type", "") or ""),
                profile=str((job.get("metadata", {}) or {}).get("profile", "") if isinstance(job.get("metadata"), dict) else ""),
                user_id=user_id,
                auth_provider="supabase",
                auth_state="authenticated",
                cloud_library_enabled=True,
                cloud_sync_status="failed",
            ),
        )
        _set_conversion_job(job_id, cloud_sync=cloud_sync)
        return cloud_sync


def _upload_durable_job_artifacts(
    client: SupabaseLibraryClient,
    *,
    user_id: str,
    job_id: str,
    job: Mapping[str, object],
    quality_state: Mapping[str, object],
) -> list[dict[str, object]]:
    artifacts = dict(job.get("artifacts", {}) or {})
    previous_rows = {
        str(row.get("kind") or ""): dict(row)
        for row in (dict(job.get("cloud_sync", {}) or {}).get("artifacts") or [])
        if isinstance(row, Mapping) and row.get("kind")
    }
    uploaded: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    direct_keys = (
        "input",
        "output",
        "report_json",
        "report_markdown",
        "chess_verified_positions_pgn",
        "chess_verified_positions_epub",
        "chess_verified_fen_publication",
        "chess_diagrams_verified",
    )
    for key in direct_keys:
        artifact = artifacts.get(key)
        path = _resolve_local_artifact_path(artifact if isinstance(artifact, dict) else None)
        if path is None or not path.is_file():
            continue
        payload = path.read_bytes()
        payload_sha = sha256(payload).hexdigest()
        previous = previous_rows.get(key, {})
        if previous.get("content_sha256") == payload_sha:
            uploaded.append(previous)
            continue
        try:
            record = client.upload_artifact_bytes(
                user_id=user_id,
                job_id=job_id,
                kind=key,
                filename=str((artifact or {}).get("filename") or path.name),
                data=payload,
                content_type=str((artifact or {}).get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
                retention_days=365 if key == "input" else 90 if key.startswith("chess_") or key.startswith("report_") else 30,
            )
            uploaded.append({**record, "content_sha256": payload_sha})
        except Exception as error:
            failures.append({"kind": key, "error": str(error)})

    if job.get("status") == "ready" and "report_json" not in {str(row.get("kind") or "") for row in uploaded}:
        report_payload = build_quality_report_payload(
            job_id,
            job,
            quality_state=quality_state,
            output_size_bytes=_read_output_size_bytes(dict(job)),
            include_text=False,
        )
        try:
            uploaded.append(
                client.upload_artifact_bytes(
                    user_id=user_id,
                    job_id=job_id,
                    kind="report_json",
                    filename=f"{job_id}.quality.json",
                    data=json.dumps(report_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    content_type="application/json",
                )
            )
            failures = [row for row in failures if row.get("kind") != "report_json"]
        except Exception as error:
            if not any(row.get("kind") == "report_json" for row in failures):
                failures.append({"kind": "report_json", "error": str(error)})

    job_root = _local_artifact_job_dir(job_id)
    if job_root is not None and (job_root / "review").is_dir():
        from conversion_rebuild_bundle import (
            DEFAULT_STORAGE_CHUNK_BYTES,
            build_conversion_rebuild_bundle,
            encode_conversion_rebuild_chunk_manifest,
            split_conversion_rebuild_bundle,
        )

        try:
            bundle, manifest = build_conversion_rebuild_bundle(job_root)
            chunk_size = _durable_storage_chunk_size_bytes(DEFAULT_STORAGE_CHUNK_BYTES)
            previous = previous_rows.get("chess_rebuild_bundle", {})
            previous_manifest = previous.get("manifest") if isinstance(previous.get("manifest"), Mapping) else {}
            if previous_manifest.get("bundle_sha256") == manifest["bundle_sha256"]:
                uploaded.extend(
                    row
                    for kind, row in previous_rows.items()
                    if kind == "chess_rebuild_bundle" or kind.startswith("chess_rebuild_bundle_part_")
                )
            elif len(bundle) <= chunk_size:
                record = client.upload_artifact_bytes(
                    user_id=user_id,
                    job_id=job_id,
                    kind="chess_rebuild_bundle",
                    filename=f"{job_id}.chess-rebuild.zip",
                    data=bundle,
                    content_type="application/zip",
                    retention_days=365,
                )
                uploaded.append({**record, "manifest": manifest})
            else:
                parts, chunk_manifest = split_conversion_rebuild_bundle(bundle, chunk_size_bytes=chunk_size)
                part_upload_failed = False
                for row, payload in zip(chunk_manifest["parts"], parts, strict=True):
                    kind = str(row["kind"])
                    try:
                        record = client.upload_artifact_bytes(
                            user_id=user_id,
                            job_id=job_id,
                            kind=kind,
                            filename=f"{job_id}.chess-rebuild.zip.part{int(row['index']):04d}",
                            data=payload,
                            content_type="application/octet-stream",
                            retention_days=365,
                        )
                        uploaded.append({**record, "content_sha256": row["sha256"]})
                    except Exception as error:
                        part_upload_failed = True
                        failures.append({"kind": kind, "error": str(error)})
                if not part_upload_failed:
                    manifest_payload = encode_conversion_rebuild_chunk_manifest(chunk_manifest)
                    record = client.upload_artifact_bytes(
                        user_id=user_id,
                        job_id=job_id,
                        kind="chess_rebuild_bundle",
                        filename=f"{job_id}.chess-rebuild.json",
                        data=manifest_payload,
                        content_type="application/json",
                        retention_days=365,
                    )
                    uploaded.append({**record, "manifest": chunk_manifest})
        except Exception as error:
            failures.append({"kind": "chess_rebuild_bundle", "error": str(error)})

    if failures:
        raise DurableArtifactSyncError(uploaded=uploaded, failures=failures)
    return uploaded


def _materialize_cloud_job_for_local_processing(job_id: str, cloud_job: dict) -> dict | None:
    if not cloud_job.get("cloud"):
        return cloud_job
    existing = _get_conversion_job(job_id)
    if existing:
        return existing
    artifact = _cloud_output_artifact(cloud_job)
    storage_path = str(artifact.get("storage_path", "") or "")
    if not storage_path:
        return None
    try:
        data = _supabase_library_client().download_artifact_bytes(storage_path=storage_path)
    except Exception:
        return None
    output_path = os.path.join(UPLOAD_DIR, f"{job_id}.epub")
    try:
        with open(output_path, "wb") as handle:
            handle.write(data)
    except OSError:
        return None
    job = dict(cloud_job)
    job["cloud"] = False
    job["output_path"] = output_path
    job["output_size_bytes"] = len(data)
    _CONVERSION_JOB_STORE.create(job)
    return _get_conversion_job(job_id) or job


def _materialize_cloud_rebuild_bundle(job_id: str, job: Mapping[str, object]) -> dict | None:
    safe_job_id = _canonical_artifact_route_id(job_id)
    if safe_job_id is None:
        return None
    artifacts = dict(job.get("artifacts", {}) or {})
    from conversion_rebuild_bundle import RESTORE_MARKER_FILENAME

    local_root = _artifact_job_root_for_id(safe_job_id)
    if local_root is not None and (
        (local_root / RESTORE_MARKER_FILENAME).is_file()
        and (local_root / "review" / "fen_manual_review.html").is_file()
        and (local_root / "semantic_chess_html" / "index.html").is_file()
    ):
        return _get_conversion_job(safe_job_id) or dict(job)
    bundle = artifacts.get("chess_rebuild_bundle")
    if not isinstance(bundle, Mapping):
        return None
    storage_path = str(bundle.get("storage_path") or "").strip()
    if not storage_path:
        return None
    destination = (ARTIFACT_ROOT.resolve() / safe_job_id).resolve()
    if destination.parent != ARTIFACT_ROOT.resolve():
        return None
    try:
        from conversion_rebuild_bundle import (
            assemble_conversion_rebuild_bundle,
            decode_conversion_rebuild_chunk_manifest,
            restore_conversion_rebuild_bundle,
        )

        client = _supabase_library_client()
        data = client.download_artifact_bytes(storage_path=storage_path)
        if str(bundle.get("content_type") or "").lower() == "application/json" or str(
            bundle.get("filename") or ""
        ).lower().endswith(".json"):
            chunk_manifest = decode_conversion_rebuild_chunk_manifest(data)
            part_payloads: dict[str, bytes] = {}
            for row in chunk_manifest["parts"]:
                kind = str(row["kind"])
                part = artifacts.get(kind)
                if not isinstance(part, Mapping) or not str(part.get("storage_path") or "").strip():
                    raise ValueError(f"cloud_rebuild_chunk_missing:{kind}")
                part_payloads[kind] = client.download_artifact_bytes(storage_path=str(part["storage_path"]))
            data = assemble_conversion_rebuild_bundle(chunk_manifest, part_payloads)
        restore_report = restore_conversion_rebuild_bundle(
            data,
            destination_root=destination,
            expected_job_id=safe_job_id,
        )
        rebuilt = _rebuild_job_from_local_artifact_dir(destination)
    except Exception as error:
        app.logger.warning("Cloud rebuild bundle restore failed for %s: %s", safe_job_id, error)
        return None
    if not rebuilt:
        return None

    rebuilt_artifacts = dict(rebuilt.get("artifacts", {}) or {})
    merged_artifacts = dict(artifacts)
    for key, artifact in rebuilt_artifacts.items():
        if key in {"input", "output"} and key in merged_artifacts:
            continue
        merged_artifacts[key] = artifact
    current = _get_conversion_job(safe_job_id)
    if current is None:
        restored_job = dict(job)
        restored_job["artifacts"] = merged_artifacts
        restored_job["cloud_rebuild"] = restore_report
        try:
            _CONVERSION_JOB_STORE.create(restored_job)
        except Exception:
            return None
    else:
        _set_conversion_job(
            safe_job_id,
            artifacts=merged_artifacts,
            cloud_rebuild=restore_report,
        )
    return _get_conversion_job(safe_job_id) or {**dict(job), "artifacts": merged_artifacts}


def _active_conversion_job_count() -> int:
    with _CONVERSION_JOBS_LOCK:
        return count_active_conversion_jobs(_CONVERSION_JOBS)


def _mark_timed_out_conversion_jobs(*, now: datetime | None = None) -> dict:
    _CONVERSION_JOB_STORE.reload_if_changed()
    _recover_missing_local_artifact_jobs()
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


def _recover_missing_local_artifact_jobs() -> dict:
    global _LOCAL_ARTIFACT_HISTORY_RECOVERED
    if _LOCAL_ARTIFACT_HISTORY_RECOVERED:
        return {"recovered": False, "job_count": 0, "error": ""}
    if _CONVERSION_JOB_STORE.persistence_path != DEFAULT_CONVERSION_JOB_STORE_PATH:
        return {"recovered": False, "job_count": 0, "error": "non_default_store"}
    _LOCAL_ARTIFACT_HISTORY_RECOVERED = True
    return _CONVERSION_JOB_STORE.recover_from_artifacts(Path("output") / "artifacts")


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

            if job.get("recovered_from_artifacts") or job.get("restored_from_artifacts"):
                if source_path:
                    active_paths.add(source_path)
                if output_path:
                    active_paths.add(output_path)
                continue

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
    conversion_id: str = "",
) -> dict:
    outcome = run_document_conversion(
        ConversionRequest(
            conversion_id=conversion_id or _sync_conversion_id(source_path, original_filename),
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
                conversion_id=job_id,
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
            artifacts.update(_store_extra_conversion_artifacts(job_id, payload.get("extra_artifacts"), job=job_before_ready))
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
                auto_repair=dict(metadata.get("auto_repair", {}) or _empty_auto_repair_state()),
                error="",
                error_code="",
            )
            try:
                verified_fen_reuse = _maybe_publish_source_bound_verified_fen(job_id)
            except Exception as error:
                app.logger.warning("Automatic verified FEN publication failed for %s: %s", job_id, error)
                verified_fen_reuse = {
                    "status": "failed",
                    "reason": "verified_fen_reuse_failed",
                }
            _set_conversion_job(job_id, verified_fen_reuse=verified_fen_reuse)
            _store_quality_report_artifacts(job_id)
            _sync_job_to_cloud(job_id)
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


def _sync_conversion_id(source_path: str, original_filename: str) -> str:
    try:
        stat = Path(source_path).stat()
        seed = f"{original_filename}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        seed = str(original_filename or source_path or "conversion")
    return f"sync_{sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _react_shell_index_path() -> Path:
    return Path(app.root_path) / "static" / "react" / "index.html"


def _legacy_ui_asset_paths() -> list[Path]:
    root_path = Path(app.root_path)
    return [
        root_path / "templates" / "index.html",
        root_path / "templates" / "artifact_preview_shell.html",
        root_path / "static" / "css" / "app-shell.css",
        root_path / "static" / "js" / "artifact-links.js",
        root_path / "static" / "js" / "conversion-ui.js",
        root_path / "static" / "js" / "quality-cockpit.js",
        root_path / "static" / "js" / "library.js",
    ]


def _legacy_ui_enabled() -> bool:
    return str(os.environ.get("KINDLEMASTER_ENABLE_LEGACY_UI", "")).strip().lower() in {"1", "true", "yes", "on"}


def _legacy_static_asset_version() -> str:
    ui_asset_paths = _legacy_ui_asset_paths()
    updated_at_timestamp = max(
        path.stat().st_mtime for path in ui_asset_paths if path.exists()
    )
    return str(int(updated_at_timestamp))


def _render_legacy_index(**context_overrides):
    updated_at_timestamp = float(_legacy_static_asset_version())
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
        static_asset_version=str(int(updated_at_timestamp)),
        updated_at_label=updated_at_label,
        **context_overrides,
    )


def _build_pdf_layout_preview_handoff(artifact: dict) -> dict:
    artifact_path = _resolve_local_artifact_path(artifact)
    if artifact_path is not None and artifact_path.is_file():
        try:
            size_bytes = artifact_path.stat().st_size
        except OSError:
            size_bytes = 0
        if size_bytes > PDF_LAYOUT_PREVIEW_MAX_INLINE_BYTES:
            return {
                "available": False,
                "mode": "too_large",
                "srcdoc": "",
                "frame_src": "",
                "badge": "Artefakt duzy",
                "message": "Podglad ukladu PDF jest zbyt duzy do osadzenia w shellu.",
                "size_bytes": size_bytes,
            }
        try:
            return {
                "available": True,
                "mode": "srcdoc",
                "srcdoc": artifact_path.read_text(encoding="utf-8", errors="replace"),
                "frame_src": "",
                "badge": "Artefakt lokalny",
                "message": "",
                "size_bytes": size_bytes,
            }
        except OSError as error:
            return {
                "available": False,
                "mode": "read_failed",
                "srcdoc": "",
                "frame_src": "",
                "badge": "Niedostepny",
                "message": f"Nie udalo sie wczytac podgladu ukladu PDF: {error}",
                "size_bytes": size_bytes,
            }

    signed_url = _signed_artifact_url(artifact)
    if signed_url.startswith(("http://", "https://")):
        return {
            "available": True,
            "mode": "remote",
            "srcdoc": "",
            "frame_src": signed_url,
            "badge": "Artefakt zdalny",
            "message": "",
            "size_bytes": int(artifact.get("size_bytes") or 0),
        }

    return {
        "available": False,
        "mode": "missing",
        "srcdoc": "",
        "frame_src": "",
        "badge": "Niedostepny",
        "message": "Nie znaleziono lokalnego ani zdalnego podgladu ukladu PDF.",
        "size_bytes": int(artifact.get("size_bytes") or 0),
    }


@app.route("/")
def index():
    return react_app()


@app.route("/legacy")
def legacy_index():
    if not _legacy_ui_enabled():
        return redirect("/app", code=302)
    response = app.make_response(_render_legacy_index())
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/favicon.ico")
def favicon():
    favicon_path = Path(app.static_folder or "") / "favicon.ico"
    if favicon_path.is_file():
        return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon")
    return ("", 204)


@app.route("/app")
@app.route("/app/<path:_path>")
def react_app(_path: str = ""):
    """Serve the Sprint 4 React shell when the Vite build is available."""

    react_index = _react_shell_index_path()
    if react_index.exists():
        response = app.make_response(react_index.read_text(encoding="utf-8"))
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response
    response = app.make_response(
        "<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>KindleMaster UI build missing</title></head><body>"
        "<main><h1>KindleMaster UI build missing</h1>"
        "<p>Uruchom <code>npm run build:ui</code> albo wystartuj serwer przez "
        "<code>python kindlemaster.py serve</code>. Stary layout nie jest już "
        "domyślnym fallbackiem.</p></main></body></html>"
    )
    response.status_code = 503
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


@app.route("/auth/config", methods=["GET"])
def auth_config():
    response = jsonify({"success": True, "auth": public_auth_config()})
    apply_no_store_headers(response.headers)
    return response


@app.route("/auth/me", methods=["GET"])
def auth_me():
    config = load_supabase_auth_config()
    token = resolve_bearer_token(request.headers.get("Authorization"))
    if not token:
        response = jsonify({"success": True, "auth": AuthContext().to_public_dict()})
        apply_no_store_headers(response.headers)
        return response
    context = validate_bearer_token(token, config=config)
    if context.error:
        return _json_auth_error(context)
    response = jsonify({"success": True, "auth": context.to_public_dict()})
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
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    try:
        request_owner = _resolve_request_job_owner(auth_context)
    except JobOwnerResolutionError as error:
        return _json_job_owner_error(error)
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
    apply_job_owner(job_record, request_owner)
    if auth_context.authenticated:
        job_record["auth"] = {
            "provider": "supabase",
            "state": "authenticated",
            "email_masked": auth_context.email_masked,
        }
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
            "source_preview_url": _source_pdf_preview_url(job_id, job_record),
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
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    try:
        request_owner = _resolve_request_job_owner(auth_context)
    except JobOwnerResolutionError as error:
        return _json_job_owner_error(error)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    if auth_context.authenticated:
        response = jsonify(_build_cloud_jobs_payload(auth_context, limit=_resolve_conversion_job_history_limit()))
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
    limit = _resolve_conversion_job_history_limit()
    local_recovery_allowed = is_local_request_host(request.host) and legacy_local_guest_allowed(request.host)
    if local_recovery_allowed:
        cloud_sync = _merge_cloud_jobs_into_store_for_request(limit=limit)
        import_result = _ensure_local_artifact_history_loaded()
    else:
        cloud_sync = {"status": "skipped", "reason": "public_guest_isolated"}
        import_result = {"imported": 0, "skipped": 0, "failed": 0, "source": "disabled_for_public_guest"}
    jobs = _visible_conversion_jobs_snapshot(owner=request_owner)
    recent_jobs = sorted(
        jobs.items(),
        key=lambda item: _conversion_job_sort_timestamp(item[1]),
        reverse=True,
    )[:limit]
    recent_jobs = [
        (job_id, _ensure_quality_report_artifacts(job_id, dict(job)))
        for job_id, job in recent_jobs
    ]
    response = jsonify(
        {
            "success": True,
            "jobs": [
                _build_conversion_job_history_item(job_id, job)
                for job_id, job in recent_jobs
            ],
            "count": len(recent_jobs),
            "total": len(jobs),
            "library_scope": "local" if local_recovery_allowed else "guest",
            "authenticated": False,
            "import": import_result,
            "cloud_sync": cloud_sync,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/jobs/<job_id>", methods=["DELETE"])
def convert_job_delete(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    try:
        request_owner = _resolve_request_job_owner(auth_context)
    except JobOwnerResolutionError as error:
        if request.args.get("access"):
            return _json_error(
                "Nie znaleziono zadania konwersji.",
                error_code=ERROR_MISSING_OUTPUT,
                status_code=404,
                phase="delete",
                job_id=job_id,
            )
        return _json_job_owner_error(error)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    raw_local_job = _get_local_conversion_job_unscoped(job_id)
    job = dict(raw_local_job) if job_owned_by(raw_local_job, request_owner) else None
    cloud_user, cloud_token = _authenticated_request_context()
    if not job and cloud_user and cloud_token:
        job = _load_supabase_conversion_jobs(
            cloud_token,
            str(cloud_user.get("id") or ""),
            limit=MAX_CONVERSION_JOB_HISTORY_LIMIT,
        ).get(job_id)
        if job:
            job["cloud"] = True
    if not job and raw_local_job is not None:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="delete",
            job_id=job_id,
        )
    if not job:
        if cloud_user and cloud_token:
            cloud_delete = _delete_supabase_conversion_job(
                cloud_token,
                str(cloud_user.get("id") or ""),
                job_id,
            )
            if cloud_delete.get("status") != "failed":
                response = jsonify(
                    {
                        "success": True,
                        "job_id": job_id,
                        "status": "already_missing",
                        "cleanup": {"status": "skipped", "reason": "job_missing"},
                        "cloud_delete": cloud_delete,
                    }
                )
                apply_no_store_headers(response.headers)
                return response
            return _json_error(
                "Nie udalo sie usunac publikacji z historii konta.",
                error_code="conversion_job_cloud_delete_failed",
                status_code=502,
                phase="delete",
                job_id=job_id,
                retryable=True,
                extra={"cloud_delete": cloud_delete},
            )
        response = jsonify(
            {
                "success": True,
                "job_id": job_id,
                "status": "already_missing",
                "cleanup": {"status": "skipped", "reason": "job_missing"},
                "cloud_delete": {"status": "skipped", "provider": "supabase", "reason": "anonymous_or_local"},
            }
        )
        apply_no_store_headers(response.headers)
        return response
    if is_active_conversion_status(str(job.get("status") or "")):
        return _json_error(
            "Nie można usunąć publikacji, która jest jeszcze przetwarzana.",
            error_code="conversion_job_active",
            status_code=409,
            phase="delete",
            job_id=job_id,
            retryable=True,
        )

    cloud_delete = (
        _delete_supabase_conversion_job(cloud_token, str(cloud_user.get("id") or ""), job_id)
        if cloud_user and cloud_token
        else {"status": "skipped", "provider": "supabase", "reason": "anonymous_or_local"}
    )
    if cloud_delete.get("status") == "failed":
        return _json_error(
            "Nie udało się usunąć publikacji z historii konta. Spróbuj ponownie.",
            error_code="conversion_job_cloud_delete_failed",
            status_code=502,
            phase="delete",
            job_id=job_id,
            retryable=True,
            extra={"cloud_delete": cloud_delete},
        )

    deleted = _CONVERSION_JOB_STORE.delete(job_id)
    local_state_cleanup = {"status": "deleted" if deleted else "absent", "job": deleted}
    if not deleted and cloud_user and cloud_delete.get("status") in {"deleted", "missing"}:
        local_state_cleanup = _delete_local_job_after_cloud_confirmation(
            job_id,
            str(cloud_user.get("id") or ""),
        )
        deleted = local_state_cleanup.get("job")
    if not deleted and not bool(job.get("cloud")):
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="delete",
            job_id=job_id,
        )

    cleanup = _cleanup_deleted_conversion_job_files(
        job_id,
        deleted or job,
        remove_artifact_job_dir=local_state_cleanup.get("status") != "protected",
    )
    behavior_signal = _record_user_behavior_signal_safely(
        job_id=job_id,
        job=deleted or job,
        event_type="job_deleted",
    )
    response = jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status": "deleted",
            "cleanup": cleanup,
            "local_state_cleanup": {"status": str(local_state_cleanup.get("status") or "unknown")},
            "cloud_delete": cloud_delete,
            "behavior_signal": behavior_signal,
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/convert/library", methods=["GET"])
def convert_library():
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    response = jsonify(_build_scoped_library_payload(auth_context=auth_context, default_include_text=False))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/archive", methods=["GET"])
def convert_archive():
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    response = jsonify(_build_scoped_library_payload(auth_context=auth_context, default_include_text=False))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/search", methods=["GET"])
def convert_search():
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    response = jsonify(_build_scoped_library_payload(auth_context=auth_context, default_include_text=True))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/report/<job_id>.<extension>", methods=["GET"])
def convert_quality_report(job_id: str, extension: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
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
    behavior_signal = _record_user_behavior_signal_safely(
        job_id=job_id,
        job=previous_job,
        event_type="conversion_retried",
        signal_strength="medium",
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
            "behavior_signal": behavior_signal,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/status/<job_id>", methods=["GET"])
def convert_status(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="recovery",
            job_id=job_id,
        )
    if not job.get("cloud"):
        job = _ensure_quality_report_artifacts(job_id, job)
    chess_delivery_payload = _enrich_job_chess_delivery_artifacts(job_id, job)
    engine_analysis_gate = _engine_analysis_gate_from_job(job)
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
        if chess_delivery_payload:
            conversion_payload.update(chess_delivery_payload)
        if engine_analysis_gate:
            conversion_payload["engine_analysis_gate"] = engine_analysis_gate
    quality_state = _build_job_quality_state(job_id, job)
    if engine_analysis_gate:
        quality_state["engine_analysis_gate"] = engine_analysis_gate
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
            "source_preview_url": _source_pdf_preview_url(job_id, job),
            "download_url": download_url,
            "download_available": download_state.download_available,
            "download_state": download_state.to_dict(),
            "progress": _build_job_progress_state(job),
            "poll_after_ms": _recommended_poll_interval_ms(job),
            "elapsed_seconds": _compute_job_elapsed_seconds(job),
            "output_size_bytes": _read_output_size_bytes(job) if job.get("status") == "ready" else None,
            "quality_state": quality_state,
            "quality_state_url": f"/convert/quality/{job_id}",
            "engine_analysis_gate": engine_analysis_gate,
            "engine_analysis_availability": engine_analysis_gate.get("availability", "") if engine_analysis_gate else "",
            "engine_reader_available": bool(engine_analysis_gate.get("engine_reader_available")) if engine_analysis_gate else False,
            "auto_repair": _build_job_auto_repair_state(job),
            "email_delivery": _build_job_email_delivery_state(job),
            "runtime": dict(job.get("runtime", {}) or {}),
            "artifacts": dict(job.get("artifacts", {}) or {}),
            "final_reader_path": chess_delivery_payload.get("final_reader_path", ""),
            "final_reader_available": bool(chess_delivery_payload.get("final_reader_available", False)),
            "final_reader_health": dict(chess_delivery_payload.get("final_reader_health", {}) or {}),
            "final_reader_blockers": list(chess_delivery_payload.get("final_reader_blockers", []) or []),
            "source_html_evidence_path": chess_delivery_payload.get("source_html_evidence_path", ""),
            "artifact_type": chess_delivery_payload.get("artifact_type", ""),
            "source_html_quality_gate": dict(chess_delivery_payload.get("source_html_quality_gate", {}) or {}),
            "side_unknown_count": chess_delivery_payload.get("side_unknown_count"),
            "trusted_marker_count": chess_delivery_payload.get("trusted_marker_count"),
            "empty_img_src_count": chess_delivery_payload.get("empty_img_src_count"),
            "diagrams_total": chess_delivery_payload.get("diagrams_total"),
            "fen_accepted": chess_delivery_payload.get("fen_accepted"),
            "chess_pgn": dict(chess_delivery_payload.get("chess_pgn", {}) or {}),
            "chess_pgn_html": dict(chess_delivery_payload.get("chess_pgn_html", {}) or {}),
            "chess_files": dict(chess_delivery_payload.get("chess_files", {}) or {}),
            "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
            "cloud_sync": dict(job.get("cloud_sync", {}) or {}),
            "authenticated": auth_context.authenticated,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/quality/<job_id>", methods=["GET"])
def convert_quality(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="recovery",
            job_id=job_id,
        )
    if not job.get("cloud"):
        job = _ensure_quality_report_artifacts(job_id, job)
    chess_delivery_payload = _enrich_job_chess_delivery_artifacts(job_id, job)

    response = jsonify(
        {
            "success": True,
            "job_id": job["job_id"],
            "quality_state": _build_job_quality_state(job_id, job),
            "source_preview_url": _source_pdf_preview_url(job_id, job),
            "auto_repair": _build_job_auto_repair_state(job),
            "email_delivery": _build_job_email_delivery_state(job),
            "runtime": dict(job.get("runtime", {}) or {}),
            "progress": _build_job_progress_state(job),
            "artifacts": dict(job.get("artifacts", {}) or {}),
            "chess_pgn": dict(chess_delivery_payload.get("chess_pgn", {}) or {}),
            "chess_pgn_html": dict(chess_delivery_payload.get("chess_pgn_html", {}) or {}),
            "chess_files": dict(chess_delivery_payload.get("chess_files", {}) or {}),
            "artifact_storage": dict(job.get("artifact_storage", {}) or {}),
            "cloud_sync": dict(job.get("cloud_sync", {}) or {}),
            "authenticated": auth_context.authenticated,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/user/profile", methods=["GET"])
def user_profile_get():
    from user_profile import public_user_profile, resolve_user_profile_path, save_user_profile

    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)

    profile = public_user_profile()
    profile_scope = "local"
    cloud_sync = {"status": "local", "provider": "local"}
    if auth_context.authenticated:
        token = resolve_bearer_token(request.headers.get("Authorization"))
        try:
            cloud_profile = load_cloud_user_profile(user_id=auth_context.user_id, access_token=token)
            if cloud_profile:
                profile = _profile_with_secret_status(cloud_profile)
                profile_scope = "account"
                cloud_sync = {"status": "synced", "provider": "supabase"}
                try:
                    save_user_profile(cloud_profile)
                except Exception:
                    cloud_sync = {"status": "synced", "provider": "supabase", "local_cache": "failed"}
            else:
                profile_scope = "account_default"
                cloud_sync = {"status": "empty", "provider": "supabase"}
        except Exception:
            profile_scope = "local_fallback"
            cloud_sync = {"status": "failed", "provider": "supabase", "error_code": "cloud_profile_load_failed"}

    response = jsonify(
        {
            "success": True,
            "profile": profile,
            "profile_scope": profile_scope,
            "profile_path_configured": bool(os.environ.get("KINDLEMASTER_USER_PROFILE_PATH")),
            "profile_path": str(resolve_user_profile_path()),
            "cloud_sync": cloud_sync,
            "authenticated": auth_context.authenticated,
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/user/profile", methods=["PUT"])
def user_profile_put():
    from user_profile import public_user_profile, save_user_profile

    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error(
            "Profil uzytkownika musi byc obiektem JSON.",
            error_code=ERROR_INVALID_PROFILE_REQUEST,
            status_code=400,
            phase="settings",
        )
    sanitized_profile = save_user_profile(payload)
    profile = public_user_profile()
    profile_scope = "local"
    cloud_sync = {"status": "local", "provider": "local"}
    if auth_context.authenticated:
        token = resolve_bearer_token(request.headers.get("Authorization"))
        try:
            cloud_profile = save_cloud_user_profile(
                user_id=auth_context.user_id,
                access_token=token,
                profile=sanitized_profile,
            )
            profile = _profile_with_secret_status(cloud_profile)
            profile_scope = "account"
            cloud_sync = {"status": "synced", "provider": "supabase"}
        except Exception:
            return _json_error(
                "Nie udalo sie zapisac ustawien profilu w bazie Supabase.",
                error_code="cloud_profile_save_failed",
                status_code=503,
                phase="settings",
            )

    response = jsonify(
        {
            "success": True,
            "profile": profile,
            "profile_scope": profile_scope,
            "cloud_sync": cloud_sync,
            "authenticated": auth_context.authenticated,
        }
    )
    apply_no_store_headers(response.headers)
    return response


@app.route("/user/library/import-local", methods=["POST"])
def user_library_import_local():
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    if not auth_context.authenticated:
        return _json_error(
            "Import lokalnej historii wymaga logowania.",
            error_code="auth_required",
            status_code=401,
            phase="auth",
        )
    try:
        result = _supabase_library_client().import_local_jobs(
            user_id=auth_context.user_id,
            jobs=_CONVERSION_JOB_STORE.snapshot(),
            quality_state_builder=lambda job_id, job: _build_job_quality_state(job_id, dict(job)),
        )
    except Exception as error:
        return _json_error(
            f"Nie udalo sie zaimportowac lokalnej historii: {error}",
            error_code="cloud_import_failed",
            status_code=503,
            phase="library_import",
        )
    response = jsonify({"success": True, "import": result})
    apply_no_store_headers(response.headers)
    return response


@app.route("/convert/delivery/config", methods=["GET"])
def convert_delivery_config():
    from email_delivery import load_email_delivery_config

    config = load_email_delivery_config()
    response = jsonify(
        {
            "success": True,
            "delivery": config.to_public_dict(),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _refresh_repaired_job_metadata(job: dict, epub_bytes: bytes, auto_repair: dict) -> dict:
    metadata = dict(job.get("metadata", {}) or {})
    metadata["auto_repair"] = dict(auto_repair)
    metadata.pop("asset_summary", None)
    try:
        from epub_premium_scoring import score_epub_premium_quality

        epubcheck = {
            "status": str(metadata.get("validation", "") or metadata.get("epubcheck_status", "") or ""),
            "messages": list(((metadata.get("validation_details", {}) or {}).get("validation_messages") or [])[:12])
            if isinstance(metadata.get("validation_details"), dict)
            else [],
            "tool": str(metadata.get("validation_tool", "unknown") or "unknown"),
        }
        metadata["premium_scoring"] = score_epub_premium_quality(epub_bytes, epubcheck=epubcheck)
    except Exception as error:
        metadata["auto_repair_scoring_error"] = str(error)
    return metadata


def _build_repair_job_response(job_id: str, job: dict, auto_repair: dict) -> dict:
    quality_state = _build_job_quality_state(job_id, job)
    return {
        "success": True,
        "job_id": job_id,
        "job": _build_conversion_job_history_item(job_id, job),
        "quality_state": quality_state,
        "auto_repair": auto_repair,
        "actions": list(auto_repair.get("actions", []) or []),
        "selected_candidate": str(auto_repair.get("selected_candidate", "") or ""),
        "rejected_candidate": str(auto_repair.get("rejected_candidate", "") or ""),
        "before_blockers": list(auto_repair.get("before_blockers", []) or []),
        "after_blockers": list(auto_repair.get("after_blockers", []) or []),
    }


@app.route("/convert/repair/<job_id>", methods=["POST"])
def convert_repair(job_id: str):
    from epub_delivery_repair import repair_epub_for_delivery

    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji do naprawy.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="repair",
            job_id=job_id,
        )
    if job.get("cloud"):
        job = _materialize_cloud_job_for_local_processing(job_id, job)
        if not job:
            return _json_error(
                "Nie udalo sie pobrac cloud EPUB-a do lokalnej naprawy.",
                error_code=ERROR_MISSING_OUTPUT,
                status_code=409,
                phase="repair",
                job_id=job_id,
            )
    if job.get("status") != "ready":
        return _json_error(
            "Naprawa jest dostępna dopiero po zakończeniu konwersji.",
            error_code="repair_not_ready",
            status_code=409,
            phase="repair",
            job_id=job_id,
        )

    output_path = str(job.get("output_path", "") or "")
    if not output_path or not os.path.isfile(output_path):
        return _json_error(
            "Brak aktywnego EPUB-a do naprawy.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=409,
            phase="repair",
            job_id=job_id,
        )

    repair_started_signal = _record_user_behavior_signal_safely(
        job_id=job_id,
        job=job,
        event_type="repair_started",
        artifact_type="epub",
    )
    before_quality_state = _build_job_quality_state(job_id, job)
    before_blockers = [
        dict(item)
        for item in before_quality_state.get("send_to_kindle_blockers", []) or []
        if isinstance(item, dict)
    ]
    with open(output_path, "rb") as handle:
        original_bytes = handle.read()

    metadata = dict(job.get("metadata", {}) or {})
    document_summary = metadata.get("document_summary") if isinstance(metadata.get("document_summary"), dict) else {}
    result = repair_epub_for_delivery(
        original_bytes,
        title_hint=str(document_summary.get("title") or metadata.get("title") or ""),
        author_hint=str(document_summary.get("author") or metadata.get("creator") or ""),
        language_hint=str(document_summary.get("language") or metadata.get("language") or ""),
        publication_profile=str(metadata.get("profile") or "") or None,
        expected_description=str(document_summary.get("description") or metadata.get("description") or ""),
        strict_premium=False,
    )
    auto_repair = result.to_public_dict(before_blockers=before_blockers)

    updated_bytes = original_bytes
    artifacts = dict(job.get("artifacts", {}) or {})
    output_size_bytes = _read_output_size_bytes(job) or len(original_bytes)
    if result.status == "applied":
        updated_bytes = result.epub_bytes
        with open(output_path, "wb") as handle:
            handle.write(updated_bytes)
        output_size_bytes = os.path.getsize(output_path)
        output_artifact = _store_artifact_bytes(
            job_id=job_id,
            kind=ArtifactKind.OUTPUT,
            filename=str(job.get("download_name") or f"{job_id}.epub"),
            data=updated_bytes,
        )
        artifacts["output"] = output_artifact

    refreshed_metadata = _refresh_repaired_job_metadata(job, updated_bytes, auto_repair)
    updated_job = _set_conversion_job(
        job_id,
        metadata=refreshed_metadata,
        output_size_bytes=output_size_bytes,
        artifacts=artifacts,
        artifact_storage=_artifact_storage_status(),
        auto_repair=auto_repair,
        email_delivery=_empty_email_delivery_state(),
    )
    updated_job = updated_job or _get_conversion_job(job_id) or job
    after_quality_state = _build_job_quality_state(job_id, updated_job)
    auto_repair["after_blockers"] = [
        dict(item)
        for item in after_quality_state.get("send_to_kindle_blockers", []) or []
        if isinstance(item, dict)
    ]
    refreshed_metadata = _refresh_repaired_job_metadata(updated_job, updated_bytes, auto_repair)
    updated_job = _set_conversion_job(
        job_id,
        metadata=refreshed_metadata,
        auto_repair=auto_repair,
        output_size_bytes=output_size_bytes,
        artifacts=artifacts,
        artifact_storage=_artifact_storage_status(),
    ) or updated_job
    _store_quality_report_artifacts(job_id)
    _sync_job_to_cloud(job_id)
    updated_job = _get_conversion_job(job_id) or updated_job
    repair_completed_signal = _record_user_behavior_signal_safely(
        job_id=job_id,
        job=updated_job,
        event_type="repair_completed",
        artifact_type="epub",
    )
    response_payload = _build_repair_job_response(job_id, updated_job, auto_repair)
    response_payload["behavior_signals"] = [repair_started_signal, repair_completed_signal]
    response = jsonify(response_payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/delivery/<job_id>/email", methods=["POST"])
def convert_delivery_email(job_id: str):
    from email_delivery import (
        EmailDeliveryError,
        load_email_delivery_config,
        mask_email_address,
        recipient_hash,
        send_attachment_email,
        validate_single_email_address,
    )

    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        return _json_delivery_error(
            "Nie znaleziono gotowego zadania do wysylki.",
            error_code=ERROR_DELIVERY_NOT_READY,
            status_code=404,
            job_id=job_id,
        )
    if job.get("cloud"):
        job = _materialize_cloud_job_for_local_processing(job_id, job)
        if not job:
            return _json_delivery_error(
                "Nie udalo sie pobrac cloud EPUB-a do wysylki.",
                error_code=ERROR_DELIVERY_NOT_READY,
                status_code=409,
                job_id=job_id,
            )

    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _json_delivery_error(
            "Payload wysylki musi byc obiektem JSON.",
            error_code=ERROR_INVALID_DELIVERY_REQUEST,
            status_code=400,
            job_id=job_id,
        )

    raw_recipient = str(payload.get("to", "") or "")
    try:
        recipient = validate_single_email_address(raw_recipient)
    except EmailDeliveryError as error:
        return _json_delivery_error(
            error.message,
            error_code=ERROR_INVALID_DELIVERY_REQUEST,
            status_code=400,
            job_id=job_id,
        )

    config = load_email_delivery_config()
    if not config.configured:
        return _json_delivery_error(
            "Wysylka email jest wylaczona albo konfiguracja SMTP jest niekompletna.",
            error_code=ERROR_DELIVERY_UNAVAILABLE,
            status_code=503,
            job_id=job_id,
            delivery=config.to_public_dict(),
        )

    requested_artifact = _normalize_delivery_artifact_request(payload)
    if requested_artifact not in {"epub", "pdf", "cropped_pdf"}:
        return _json_delivery_error(
            "Nieznany typ zalacznika. Dostepne: epub, pdf, cropped_pdf.",
            error_code=ERROR_INVALID_DELIVERY_REQUEST,
            status_code=400,
            job_id=job_id,
        )

    if job.get("status") != "ready":
        return _json_delivery_error(
            "Zadanie nie jest jeszcze gotowe do wysylki email.",
            error_code=ERROR_DELIVERY_NOT_READY,
            status_code=409,
            job_id=job_id,
            delivery={
                "status": "blocked",
                "reason": "job_not_ready",
                "artifact": requested_artifact,
            },
        )

    download_state = _build_job_download_state(job_id, job)
    attachment_path = ""
    attachment_filename = ""
    attachment_content_type = "application/epub+zip"
    attachment_label = "EPUB"
    attachment_subject_label = "EPUB"
    attachment_artifact = "epub"
    attachment_size_bytes: int | None = None
    if requested_artifact == "epub":
        output_path = str(job.get("output_path", "") or "")
        if not download_state.download_available or not output_path or not os.path.isfile(output_path):
            return _json_delivery_error(
                "EPUB nie jest gotowy do wysylki email.",
                error_code=ERROR_DELIVERY_NOT_READY,
                status_code=409,
                job_id=job_id,
                delivery={
                    "status": "blocked",
                    "reason": download_state.reason or "epub_not_ready",
                    "artifact": "epub",
                    "download_state": download_state.to_dict(),
                },
            )
        attachment_path = output_path
        attachment_filename = str(job.get("download_name") or f"{job_id}.epub")
        attachment_size_bytes = _read_output_size_bytes(job)
    else:
        artifact, pdf_path, artifact_key = _pdf_delivery_artifact(job, requested_artifact)
        if not artifact or not pdf_path:
            reason = "cropped_pdf_not_ready" if requested_artifact == "cropped_pdf" else "pdf_not_ready"
            return _json_delivery_error(
                "PDF nie jest dostepny do wysylki email.",
                error_code=ERROR_DELIVERY_NOT_READY,
                status_code=409,
                job_id=job_id,
                delivery={
                    "status": "blocked",
                    "reason": reason,
                    "artifact": requested_artifact,
                },
            )
        attachment_path = str(pdf_path)
        attachment_filename = str(artifact.get("filename") or job.get("filename") or f"{job_id}.pdf")
        attachment_content_type = "application/pdf"
        attachment_label = "PDF"
        attachment_subject_label = "PDF"
        attachment_artifact = "cropped_pdf" if artifact_key == "cropped_pdf" else "pdf"
        attachment_size_bytes = pdf_path.stat().st_size

    if attachment_size_bytes is None or attachment_size_bytes > config.max_attachment_bytes:
        return _json_delivery_error(
            f"{attachment_label} przekracza limit zalacznika albo nie ma raportowanego rozmiaru.",
            error_code=ERROR_DELIVERY_NOT_READY,
            status_code=409,
            job_id=job_id,
            delivery={
                "status": "blocked",
                "reason": "attachment_size_limit",
                "artifact": attachment_artifact,
                "max_attachment_bytes": config.max_attachment_bytes,
                "attachment_size_bytes": attachment_size_bytes,
            },
        )

    quality_state = _build_job_quality_state(job_id, job)
    quality_gate_payload = {
        "delivery_allowed": True,
        "warning_only": quality_state.get("send_to_kindle_ready") is not True,
        "artifact": attachment_artifact,
        "release_verdict": quality_state.get("release_verdict", ""),
        "send_to_kindle_ready": quality_state.get("send_to_kindle_ready"),
        "send_to_kindle_blockers": list(quality_state.get("send_to_kindle_blockers", []) or []),
    }

    subject = str(payload.get("subject") or f"KindleMaster {attachment_subject_label}: {attachment_filename}")
    message = str(payload.get("message") or f"{attachment_label} z KindleMaster jest w zalaczniku.")
    try:
        result = send_attachment_email(
            config=config,
            to_address=recipient,
            subject=subject,
            body=message,
            attachment_path=attachment_path,
            attachment_filename=attachment_filename,
            attachment_content_type=attachment_content_type,
            default_subject=f"KindleMaster {attachment_subject_label}",
            default_body=f"{attachment_label} z KindleMaster jest w zalaczniku.",
            attachment_label=attachment_label,
        )
    except EmailDeliveryError as error:
        status_code = 502 if error.code == ERROR_DELIVERY_FAILED else 409
        if error.code == ERROR_DELIVERY_UNAVAILABLE:
            status_code = 503
        masked = ""
        hashed = ""
        try:
            masked = mask_email_address(recipient)
            hashed = recipient_hash(recipient)
        except EmailDeliveryError:
            pass
        failed_delivery = {
            "status": "failed",
            "channel": "email",
            "target": "send_to_kindle",
            "masked_recipient": masked,
            "recipient_hash": hashed,
            "attempted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "error_code": error.code,
        }
        if getattr(error, "diagnostics", None):
            failed_delivery["diagnostics"] = error.diagnostics
        _set_conversion_job(job_id, email_delivery=failed_delivery)
        _sync_job_to_cloud(job_id)
        return _json_delivery_error(
            error.message,
            error_code=error.code,
            status_code=status_code,
            job_id=job_id,
            delivery=failed_delivery,
        )

    delivery_payload = result.to_public_dict()
    delivery_payload["artifact"] = attachment_artifact
    delivery_payload["attachment_content_type"] = attachment_content_type
    delivery_payload["quality_gate"] = quality_gate_payload
    _set_conversion_job(job_id, email_delivery=delivery_payload)
    _sync_job_to_cloud(job_id)
    behavior_signal = _record_user_behavior_signal_safely(
        job_id=job_id,
        job=job,
        event_type="send_to_kindle_clicked",
        artifact_type=attachment_artifact,
        signal_strength="medium",
    )
    response = jsonify(
        {
            "success": True,
            "job_id": job_id,
            "delivery": delivery_payload,
            "behavior_signal": behavior_signal,
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
        _ensure_local_artifact_history_loaded()
        job = _get_conversion_job(job_id)
    if not job:
        # Details views may survive a local server restart while the durable job
        # history lives in Supabase. If the browser sends auth, rehydrate the
        # local store before deciding the artifact is missing.
        _merge_cloud_jobs_into_store_for_request(limit=MAX_CONVERSION_JOB_HISTORY_LIMIT)
        job = _get_conversion_job(job_id)
    if not job:
        job = _restore_cloud_job_for_signed_access(job_id)
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
    if key in {
        "chess_reader",
        "chess_pgn_html",
        "chess_fen_review",
        "chess_diagrams",
        "chess_diagrams_verified",
        "chess_verified_positions_pgn",
        "chess_verified_positions_epub",
        "chess_verified_fen_publication",
    }:
        restored_job = _materialize_cloud_rebuild_bundle(job_id, job)
        if restored_job is not None:
            job = restored_job
    chess_delivery_payload = _enrich_job_chess_delivery_artifacts(job_id, job)
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get(key)
    if key == "chess_fen_review" and not isinstance(artifact, dict):
        artifact = _ensure_local_fen_review_artifact(job_id, job)
    if key == "chess_reader":
        source_reader_artifact = artifacts.get("chess_pgn_html")
        if isinstance(source_reader_artifact, dict):
            artifact = source_reader_artifact
    if not isinstance(artifact, dict):
        if key == "chess_pgn" and isinstance(chess_delivery_payload.get("chess_pgn"), Mapping):
            return _chess_pgn_unavailable_response(job_id, chess_delivery_payload.get("chess_pgn"))
        return _json_error(
            "Nie znaleziono artefaktu zadania.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    _artifact_behavior_signal(job_id, job, key, artifact)
    if key == "pdf_layout_preview":
        artifact_path = _resolve_local_artifact_path(artifact)
        return _render_pdf_layout_preview_shell(job_id, job, artifact, artifact_path)
    artifact_path = _resolve_local_artifact_path(artifact)
    if key == "chess_pgn" and not bool(artifact.get("available", True)):
        return _chess_pgn_unavailable_response(job_id, artifact)
    if artifact_path is None or not artifact_path.is_file():
        if key == "chess_pgn" and not bool(artifact.get("download_url") or _artifact_signed_url(artifact)):
            return _chess_pgn_unavailable_response(job_id, artifact)
        if key == "chess_pgn_html" and str(artifact.get("artifact_type") or "") == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE:
            return _final_reader_missing_response(job_id, None, artifact)
        if key == "input":
            fallback_response = _send_local_input_artifact_fallback(job_id, job, artifact)
            if fallback_response is not None:
                return fallback_response
        return _send_remote_artifact_proxy(artifact, job_id=job_id, artifact_key=key)
    if key == "pdf_layout_preview":
        return _render_pdf_layout_preview_shell(job_id, job, artifact, artifact_path)
    if key in {"chess_reader", "chess_pgn_html"}:
        semantic_response = _render_chess_pgn_semantic_artifact(
            job_id,
            job,
            artifact,
            artifact_path,
            asset_route="chess_reader_asset" if key == "chess_reader" else "chess_pgn_html_asset",
        )
        if semantic_response is not None:
            return semantic_response
        return _final_reader_missing_response(job_id, artifact_path, artifact)
    if key == "chess_fen_review":
        from chess_fen_review_repository import ChessFenReviewRepository
        from chess_fen_review_store import load_fen_review_progress
        from chess_fen_review_ui import render_fen_manual_review_html

        try:
            auth_context = _resolve_request_auth_context()
            authorized_job = (
                _get_conversion_job_for_auth(job_id, auth_context)
                if auth_context.authenticated and not auth_context.error
                else None
            )
            review_payload = (
                ChessFenReviewRepository(
                    artifact_path.parent,
                    artifact_id=job_id,
                    owner_user_id=auth_context.user_id,
                ).load()
                if authorized_job is not None
                else load_fen_review_progress(artifact_path.parent)
            )
            review_rows = list(review_payload.get("rows") or [])
            response = app.response_class(
                render_fen_manual_review_html(
                    review_rows,
                    source_identity=review_rows[0] if review_rows else {},
                    artifact_id=job_id,
                ),
                mimetype="text/html",
            )
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-KindleMaster-Artifact-Source"] = str(
                review_payload.get("storage") or "local"
            )
            return response
        except Exception as exc:
            app.logger.warning(
                "FEN review dynamic render failed for %s; serving stored HTML: %s",
                job_id,
                exc,
            )
    response = send_file(
        artifact_path,
        mimetype=str(artifact.get("content_type") or mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream"),
        as_attachment=_artifact_should_download_as_attachment(key, artifact),
        download_name=str(artifact.get("filename") or artifact_path.name),
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Source"] = "local"
    return response


@app.route("/convert/artifact/<job_id>/chess_fen_review_progress", methods=["GET", "PUT"])
def convert_fen_manual_review_progress(job_id: str):
    canonical_job_id = _canonical_artifact_route_id(job_id)
    if canonical_job_id is None:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="fen_review",
        )
    job_id = canonical_job_id
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    auth_config = load_supabase_auth_config()
    if auth_config.enabled and auth_config.configured and not auth_context.authenticated:
        return _json_auth_error(
            AuthContext(
                error="Logowanie jest wymagane do zapisu oznaczeń w bazie.",
                error_code="auth_required",
                status_code=401,
            )
        )
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        _ensure_local_artifact_history_loaded()
        job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        restored = _restore_local_artifact_job_by_id(job_id)
        job = _get_conversion_job_for_auth(job_id, auth_context) if restored else None
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="fen_review",
            job_id=job_id,
        )
    review_dir = _resolve_local_fen_review_dir(job_id, job)
    if review_dir is None:
        restored_job = _materialize_cloud_rebuild_bundle(job_id, job)
        if restored_job is not None:
            job = restored_job
            review_dir = _resolve_local_fen_review_dir(job_id, job)
    if review_dir is None:
        return _json_error(
            "Nie znaleziono zestawu do oznaczania FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="fen_review",
            job_id=job_id,
        )

    from chess_fen_review_repository import ChessFenReviewRepository
    from chess_fen_review_store import (
        FenReviewConflictError,
        FenReviewOwnershipError,
        FenReviewSessionClosedError,
        FenReviewStoreError,
    )

    repository = ChessFenReviewRepository(
        review_dir,
        artifact_id=job_id,
        owner_user_id=(auth_context.user_id if auth_context.authenticated else str(job.get("user_id") or "")),
    )

    action = ""
    try:
        if request.method == "GET":
            payload = repository.load()
        else:
            submitted = request.get_json(silent=True)
            if not isinstance(submitted, dict):
                raise FenReviewStoreError("Prze?lij obiekt JSON z polem rows.")
            rows = submitted.get("rows")
            if not isinstance(rows, list):
                raise FenReviewStoreError("Pole rows musi by? list? rekord?w.")
            try:
                expected_revision = int(submitted.get("expected_revision") or 0)
            except (TypeError, ValueError) as error:
                raise FenReviewStoreError("Pole expected_revision musi być liczbą całkowitą.") from error
            if expected_revision < 0:
                raise FenReviewStoreError("Pole expected_revision nie może być ujemne.")
            action = str(submitted.get("action") or "save").strip().lower()
            change_source = str(submitted.get("change_source") or "autosave").strip().lower()
            payload = repository.save(
                rows,
                source_digest=str(submitted.get("source_digest") or ""),
                owner_user_id=(auth_context.user_id if auth_context.authenticated else str(job.get("user_id") or "")),
                expected_revision=expected_revision,
                action=action,
                change_source=change_source,
            )
    except FenReviewConflictError as exc:
        return _json_error(
            str(exc),
            error_code="fen_review_revision_conflict",
            status_code=409,
            phase="fen_review",
            job_id=job_id,
        )
    except FenReviewSessionClosedError as exc:
        return _json_error(
            str(exc),
            error_code="fen_review_session_closed",
            status_code=409,
            phase="fen_review",
            job_id=job_id,
        )
    except FenReviewOwnershipError as exc:
        return _json_error(
            str(exc),
            error_code="fen_review_owner_mismatch",
            status_code=403,
            phase="fen_review",
            job_id=job_id,
        )
    except FenReviewStoreError as exc:
        return _json_error(
            str(exc),
            error_code=ERROR_UPLOAD_FAILED,
            status_code=400,
            phase="fen_review",
            job_id=job_id,
        )
    except OSError as exc:
        return _json_error(
            f"Nie uda?o si? zapisa? post?pu oznaczania: {exc}",
            error_code=ERROR_UPLOAD_FAILED,
            status_code=500,
            phase="fen_review",
            job_id=job_id,
        )
    if request.method == "PUT" and action == "close" and str(payload.get("session_status") or "").lower() == "complete":
        try:
            complete_review_payload = repository.load()
            payload["verified_fen_publication"] = _publish_verified_fen_review_artifacts(
                job_id,
                job,
                review_dir=review_dir,
                review_payload=complete_review_payload,
            )
            _store_quality_report_artifacts(job_id)
            _sync_job_to_cloud(job_id)
        except Exception:
            app.logger.error("Verified FEN publication failed")
            payload["verified_fen_publication"] = {
                "status": "failed",
                "error": "verified_fen_publication_failed",
            }
    response = jsonify({"success": True, "job_id": job_id, **payload})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/artifact/<job_id>/chess_fen_publish", methods=["POST"])
def convert_publish_verified_fen(job_id: str):
    canonical_job_id = _canonical_artifact_route_id(job_id)
    if canonical_job_id is None:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="fen_publish",
        )
    job_id = canonical_job_id
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    auth_config = load_supabase_auth_config()
    if auth_config.enabled and auth_config.configured and not auth_context.authenticated:
        return _json_auth_error(
            AuthContext(
                error="Logowanie jest wymagane do publikacji zweryfikowanych FEN.",
                error_code="auth_required",
                status_code=401,
            )
        )
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        _ensure_local_artifact_history_loaded()
        job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        restored = _restore_local_artifact_job_by_id(job_id)
        job = _get_conversion_job_for_auth(job_id, auth_context) if restored else None
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="fen_publish",
            job_id=job_id,
        )
    review_dir = _resolve_local_fen_review_dir(job_id, job)
    if review_dir is None:
        restored_job = _materialize_cloud_rebuild_bundle(job_id, job)
        if restored_job is not None:
            job = restored_job
            review_dir = _resolve_local_fen_review_dir(job_id, job)
    if review_dir is None:
        return _json_error(
            "Nie znaleziono zestawu do oznaczania FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="fen_publish",
            job_id=job_id,
        )

    from chess_fen_review_repository import ChessFenReviewRepository

    repository = ChessFenReviewRepository(
        review_dir,
        artifact_id=job_id,
        owner_user_id=(auth_context.user_id if auth_context.authenticated else str(job.get("user_id") or "")),
    )
    try:
        review_payload = repository.load()
        if review_payload.get("reused_from_artifact_id"):
            report = _maybe_publish_source_bound_verified_fen(job_id)
            if report.get("status") != "published":
                raise ValueError("verified_fen_source_reuse_not_publishable")
        else:
            report = _publish_verified_fen_review_artifacts(
                job_id,
                job,
                review_dir=review_dir,
                review_payload=review_payload,
            )
        _store_quality_report_artifacts(job_id)
        _sync_job_to_cloud(job_id)
    except Exception:
        app.logger.error("Verified FEN publication failed")
        return _json_error(
            "Nie udało się opublikować zweryfikowanych FEN.",
            error_code="verified_fen_publication_failed",
            status_code=400,
            phase="fen_publish",
            job_id=job_id,
        )
    response = jsonify({"success": True, "job_id": job_id, "publication": report})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/artifact/<job_id>/fen_manual_assets/<path:asset_path>", methods=["GET"])
def convert_fen_manual_review_asset(job_id: str, asset_path: str):
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        _ensure_local_artifact_history_loaded()
        job = _get_conversion_job(job_id)
    if not job:
        job = _restore_local_artifact_job_by_id(job_id)
    if job:
        restored_job = _materialize_cloud_rebuild_bundle(job_id, job)
        if restored_job is not None:
            job = restored_job
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    artifact = dict(job.get("artifacts", {}) or {}).get("chess_fen_review")
    if not isinstance(artifact, dict):
        artifact = _ensure_local_fen_review_artifact(job_id, job)
    if not isinstance(artifact, dict):
        return _json_error(
            "Nie znaleziono zestawu do oznaczania FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    artifact_path = _resolve_local_artifact_path(artifact)
    if artifact_path is None or not artifact_path.is_file():
        return _json_error(
            "Nie znaleziono lokalnego zestawu do oznaczania FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    root = (artifact_path.parent / "fen_manual_assets").resolve()
    requested = (root / asset_path).resolve()
    if (root not in requested.parents and requested != root) or not requested.is_file():
        return _json_error(
            "Nieprawidlowa sciezka cropa FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    response = send_file(
        requested,
        mimetype=mimetypes.guess_type(requested.name)[0] or "application/octet-stream",
        as_attachment=False,
        download_name=requested.name,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Source"] = "fen-manual-review-asset"
    return response


@app.route("/convert/artifact/<job_id>/chess_reader_asset/<path:asset_path>", methods=["GET"])
@app.route("/convert/artifact/<job_id>/chess_pgn_html_asset/<path:asset_path>", methods=["GET"])
def convert_chess_pgn_html_asset(job_id: str, asset_path: str):
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
    restored_job = _materialize_cloud_rebuild_bundle(job_id, job)
    if restored_job is not None:
        job = restored_job
    artifacts = dict(job.get("artifacts", {}) or {})
    artifact = artifacts.get("chess_pgn_html")
    if not isinstance(artifact, dict):
        return _json_error(
            "Nie znaleziono artefaktu HTML PGN/FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    artifact_path = _resolve_local_artifact_path(artifact)
    if artifact_path is None or not artifact_path.is_file():
        return _json_error(
            "Nie znaleziono lokalnego artefaktu HTML PGN/FEN.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    _ensure_semantic_chess_html_artifact(job_id, job, artifact_path)
    semantic_index = _semantic_reader_index_for_job(job_id)
    if semantic_index is None:
        return _final_reader_missing_response(job_id, artifact_path, artifact)
    safe_asset_path = _safe_semantic_reader_asset_path(asset_path)
    if not safe_asset_path or safe_asset_path.startswith("../"):
        return _json_error(
            "Nieprawidłowa ścieżka artefaktu.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    requested = _cached_semantic_reader_image_path(job_id, safe_asset_path)
    if requested is None:
        requested = _resolve_semantic_chess_asset_path(semantic_index, safe_asset_path)
    if requested is None:
        return _json_error(
            "Nie znaleziono assetu semantycznego HTML.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="download",
            job_id=job_id,
        )
    response = send_file(
        requested,
        mimetype=mimetypes.guess_type(requested.name)[0] or "application/octet-stream",
        as_attachment=False,
        download_name=requested.name,
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-KindleMaster-Artifact-Source"] = "semantic-chess-reader-asset"
    return response


def _feedback_log_path_override() -> str | None:
    raw = os.environ.get("KINDLEMASTER_FEEDBACK_LOG", "").strip()
    return raw or None


def _learning_ledger_root_override() -> str:
    raw = os.environ.get("KINDLEMASTER_LEARNING_LEDGER_ROOT", "").strip()
    return raw or "."


def _record_user_behavior_signal(
    *,
    job_id: str,
    job: Mapping[str, object] | None,
    event_type: str,
    artifact_type: str = "",
    view_mode: str = "",
    signal_strength: str = "",
) -> dict[str, object]:
    from learning_ledger import record_user_behavior_signal

    return record_user_behavior_signal(
        conversion_id=job_id,
        event_type=event_type,
        artifact_type=artifact_type,
        view_mode=view_mode,
        signal_strength=signal_strength,
        job=job,
        repo_root=_learning_ledger_root_override(),
    )


def _record_user_behavior_signal_safely(
    *,
    job_id: str,
    job: Mapping[str, object] | None,
    event_type: str,
    artifact_type: str = "",
    view_mode: str = "",
    signal_strength: str = "",
) -> dict[str, object]:
    try:
        record = _record_user_behavior_signal(
            job_id=job_id,
            job=job,
            event_type=event_type,
            artifact_type=artifact_type,
            view_mode=view_mode,
            signal_strength=signal_strength,
        )
        return {"status": "recorded", "event_id": str(record.get("event_id") or "")}
    except Exception:
        return {"status": "failed", "reason": "ledger_write_failed"}


def _artifact_behavior_signal(job_id: str, job: Mapping[str, object], key: str, artifact: Mapping[str, object] | None = None) -> dict[str, object]:
    artifact_payload = dict(artifact or {})
    artifact_type = str(artifact_payload.get("artifact_type") or artifact_payload.get("kind") or key or "").strip()
    if key == "chess_pgn_html":
        event_type = "html_reader_opened"
        artifact_type = artifact_type or FINAL_READER_ARTIFACT_TYPE
    elif key == "input":
        event_type = "original_preview_opened"
        artifact_type = "source_pdf"
    elif key in {"pdf_layout_preview", "chess_glyph_diagnostics", "deepseek_audit"} or key.endswith("diagnostics"):
        event_type = "diagnostics_opened"
    else:
        event_type = "artifact_downloaded"
    return _record_user_behavior_signal_safely(
        job_id=job_id,
        job=job,
        event_type=event_type,
        artifact_type=artifact_type or key,
    )


def _feedback_validation_payload(error: ValueError) -> dict[str, object]:
    message = str(error)
    if message.startswith("training_feedback_invalid:"):
        missing = [part for part in message.split(":", 1)[1].split(",") if part]
        return {
            "message": (
                "Feedback oznaczony do uczenia wymaga recenzenta, etykiety jakości, tagów problemu i poprawnej trasy. "
                f"Brakuje: {', '.join(missing)}."
            ),
            "missing": missing,
        }
    return {"message": message, "missing": []}


@app.route("/convert/feedback/<job_id>", methods=["GET", "POST"])
def convert_feedback(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        if request.method == "GET":
            response = jsonify(
                {
                    "success": True,
                    "job_id": job_id,
                    "feedback_records": [],
                    "latest_feedback": None,
                    "feedback_count": 0,
                    "skipped": [{"source": "feedback", "reason": "conversion_job_missing"}],
                    "online_learning": False,
                }
            )
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            return response
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="feedback",
            job_id=job_id,
        )
    if request.method == "GET":
        try:
            from ml_feedback import feedback_records_for_job

            records, skipped = feedback_records_for_job(
                job_id,
                log_paths=[_feedback_log_path_override()] if _feedback_log_path_override() else None,
            )
        except Exception as error:
            return _json_error(
                f"Nie udalo sie odczytac feedbacku: {error}",
                error_code="feedback_read_failed",
                status_code=500,
                phase="feedback",
                job_id=job_id,
            )
        response = jsonify(
            {
                "success": True,
                "job_id": job_id,
                "feedback_records": records,
                "latest_feedback": records[-1] if records else None,
                "feedback_count": len(records),
                "skipped": skipped,
                "online_learning": False,
            }
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
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
        from ml_feedback import append_user_feedback, feedback_public_record

        record = append_user_feedback(
            job_id=job_id,
            feedback=payload,
            job=job,
            event_path=_feedback_log_path_override(),
            ledger_repo_root=_learning_ledger_root_override(),
        )
        public_record = feedback_public_record(record)
    except ValueError as error:
        validation = _feedback_validation_payload(error)
        return _json_error(
            str(validation["message"]),
            error_code="invalid_training_feedback",
            status_code=400,
            phase="feedback",
            job_id=job_id,
            extra={"missing": validation["missing"]},
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
            "feedback_record": public_record,
            "learning_ledger": public_record.get("learning_ledger", {}),
            "online_learning": False,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/learning/behavior/<job_id>", methods=["POST"])
def learning_behavior_signal(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    if not _is_conversion_job_id(job_id):
        return _json_error(
            "Nieprawidlowy identyfikator zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="learning_behavior",
            job_id=job_id,
        )
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_existing_conversion_job_for_auth(job_id, auth_context)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji dla sygnalu zachowania.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="learning_behavior",
            job_id=job_id,
        )
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _json_error(
            "SygnaĹ‚ zachowania musi byÄ‡ obiektem JSON.",
            error_code="invalid_behavior_signal_payload",
            status_code=400,
            phase="learning_behavior",
            job_id=job_id,
        )
    event_type = str(payload.get("event_type") or "").strip()
    try:
        record = _record_user_behavior_signal(
            job_id=job_id,
            job=job,
            event_type=event_type,
            artifact_type=str(payload.get("artifact_type") or ""),
            view_mode=str(payload.get("view_mode") or ""),
            signal_strength=str(payload.get("signal_strength") or ""),
        )
    except ValueError as error:
        return _json_error(
            "Nieznany typ sygnalu zachowania.",
            error_code="invalid_behavior_signal_type",
            status_code=400,
            phase="learning_behavior",
            job_id=job_id,
        )
    except Exception:
        return _json_error(
            "Nie udalo sie zapisac sygnalu zachowania.",
            error_code="behavior_signal_write_failed",
            status_code=500,
            phase="learning_behavior",
            job_id=job_id,
        )
    event = dict(record.get("event", {}) or {})
    response = jsonify(
        {
            "success": True,
            "job_id": job_id,
            "behavior_signal": {
                "status": "recorded",
                "event_id": str(record.get("event_id") or ""),
                "event_type": str(event.get("event_type") or event_type),
                "signal_strength": str(event.get("signal_strength") or ""),
                "training_label": bool(event.get("training_label")),
                "training_eligible": bool(event.get("training_eligible")),
                "privacy": dict(event.get("privacy", {}) or {}),
            },
            "online_learning": False,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/learning/feedback/summary", methods=["GET"])
def learning_feedback_summary():
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    try:
        from ml_feedback import load_feedback_records, summarize_feedback_records

        records, skipped = load_feedback_records(
            log_paths=[_feedback_log_path_override()] if _feedback_log_path_override() else None
        )
        summary = summarize_feedback_records(records, skipped)
    except Exception as error:
        return _json_error(
            f"Nie udalo sie odczytac podsumowania feedbacku: {error}",
            error_code="feedback_summary_failed",
            status_code=500,
            phase="feedback",
        )
    response = jsonify({"success": True, "summary": summary, "online_learning": False})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/preview/<job_id>/input", methods=["GET"])
def convert_input_pdf_preview(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
    if not job:
        return _json_error(
            "Nie znaleziono zadania konwersji.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="preview",
            job_id=job_id,
        )

    artifact = _input_pdf_artifact(job)
    artifact_path = _local_input_artifact_path(artifact) if artifact else None
    if not artifact_path:
        return _json_error(
            "Podglad PDF nie jest dostepny dla tego zadania.",
            error_code=ERROR_MISSING_OUTPUT,
            status_code=404,
            phase="preview",
            job_id=job_id,
        )

    _record_user_behavior_signal_safely(
        job_id=job_id,
        job=job,
        event_type="original_preview_opened",
        artifact_type="source_pdf",
    )
    response = send_file(
        artifact_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=str(artifact.get("filename") or job.get("filename") or f"{job_id}.pdf"),
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Source-Type"] = "pdf"
    return response


@app.route("/convert/download/<job_id>", methods=["GET"])
def convert_download(job_id: str):
    auth_context = _resolve_request_auth_context()
    if auth_context.error:
        return _json_auth_error(auth_context)
    _mark_timed_out_conversion_jobs()
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job_for_auth(job_id, auth_context)
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
    if not signed_artifact_url and job.get("cloud"):
        signed = _sign_cloud_output_artifact(job)
        signed_artifact_url = str(signed.get("url", "") or "") if signed.get("available") else ""
    if signed_artifact_url:
        _record_user_behavior_signal_safely(
            job_id=job_id,
            job=job,
            event_type="artifact_downloaded",
            artifact_type="epub",
        )
        return redirect(signed_artifact_url, code=302)

    _record_user_behavior_signal_safely(
        job_id=job_id,
        job=job,
        event_type="artifact_downloaded",
        artifact_type="epub",
    )
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


from chess_evidence_review_routes import register_chess_evidence_review_routes

register_chess_evidence_review_routes(
    app,
    mark_timed_out_conversion_jobs=_mark_timed_out_conversion_jobs,
    cleanup_expired_conversion_jobs=_cleanup_expired_conversion_jobs,
    get_conversion_job=_get_conversion_job,
    ensure_local_artifact_history_loaded=_ensure_local_artifact_history_loaded,
    restore_local_artifact_job_by_id=_restore_local_artifact_job_by_id,
    json_error=_json_error,
    error_missing_output=ERROR_MISSING_OUTPUT,
    error_upload_failed=ERROR_UPLOAD_FAILED,
)


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
