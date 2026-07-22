from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import zipfile
from typing import Any, Mapping

from flask import Flask, Response, has_request_context, jsonify, request

from admission_control import (
    AdmissionDecision,
    DistributedAdmissionController,
    policy_from_env,
)


_INSTALL_MARKER = "_kindlemaster_admission_controls_installed"
_BOUND_APP_MODULE: Any | None = None
_BOUND_DURABLE_QUEUE: Any | None = None
_CONTROLLER: DistributedAdmissionController | None = None

_MUTATION_PREFIXES = (
    "/convert/start",
    "/convert/retry/",
    "/convert/jobs/",
    "/convert/repair/",
    "/convert/delivery/",
)
_POLL_PREFIXES = (
    "/convert/status/",
    "/convert/quality/",
    "/convert/progress/",
)
_READ_PREFIXES = (
    "/convert/jobs",
    "/convert/library",
    "/convert/archive",
    "/convert/search",
    "/convert/report/",
    "/convert/artifact/",
    "/convert/download/",
    "/convert/preview/",
)


def admission_database_path(app_module: Any | None = None) -> Path:
    configured = str(os.environ.get("KINDLEMASTER_ADMISSION_DB_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    upload_dir = Path(getattr(app_module, "UPLOAD_DIR", "/data/uploads"))
    root = upload_dir.parent if upload_dir.name == "uploads" else upload_dir
    return root / "queue" / "admission.sqlite3"


def build_admission_controller(app_module: Any | None = None) -> DistributedAdmissionController:
    global _CONTROLLER
    expected_path = admission_database_path(app_module)
    if _CONTROLLER is None or _CONTROLLER.path != expected_path:
        _CONTROLLER = DistributedAdmissionController(expected_path, policy_from_env(os.environ))
    return _CONTROLLER


def reset_admission_controller_for_tests() -> None:
    global _CONTROLLER, _BOUND_APP_MODULE, _BOUND_DURABLE_QUEUE
    _CONTROLLER = None
    _BOUND_APP_MODULE = None
    _BOUND_DURABLE_QUEUE = None


def install_admission_controls(
    *,
    app_module: Any | None = None,
    durable_queue: Any | None = None,
) -> None:
    global _BOUND_APP_MODULE, _BOUND_DURABLE_QUEUE
    if app_module is not None:
        _BOUND_APP_MODULE = app_module
    if durable_queue is not None:
        _BOUND_DURABLE_QUEUE = durable_queue
    if getattr(Flask, _INSTALL_MARKER, False):
        return

    original_full_dispatch_request = Flask.full_dispatch_request

    def guarded_full_dispatch_request(self: Flask):
        blocked = _guard_request()
        if blocked is not None:
            return blocked
        return original_full_dispatch_request(self)

    Flask.full_dispatch_request = guarded_full_dispatch_request
    setattr(Flask, _INSTALL_MARKER, True)


def _app_module() -> Any:
    if _BOUND_APP_MODULE is not None:
        return _BOUND_APP_MODULE
    import app as app_module

    return app_module


def _durable_queue(app_module: Any) -> Any | None:
    if _BOUND_DURABLE_QUEUE is not None:
        return _BOUND_DURABLE_QUEUE
    extensions = getattr(app_module.app, "extensions", {})
    return extensions.get("kindlemaster_durable_queue")


def _guard_request() -> Response | None:
    if not has_request_context() or request.method == "OPTIONS":
        return None
    category = _request_category(request.method, request.path)
    if not category:
        return None

    app_module = _app_module()
    owner_id, authenticated = _owner_identity(app_module)
    controller = build_admission_controller(app_module)
    rate = controller.check_request(
        owner_id=owner_id or "anonymous:missing",
        route=_normalized_route(request.path),
        authenticated=authenticated,
        category=category,
    )
    if not rate.allowed:
        _log_decision(app_module, rate, owner_id, category)
        return _decision_response(rate)

    if request.method == "POST" and request.path == "/convert/start":
        queue_decision = _job_admission(
            app_module=app_module,
            controller=controller,
            owner_id=owner_id or "anonymous:missing",
        )
        if not queue_decision.allowed:
            _log_decision(app_module, queue_decision, owner_id, "capacity")
            return _decision_response(queue_decision)

        upload_decision = _validate_request_upload(controller)
        if upload_decision is not None and not upload_decision.allowed:
            _log_decision(app_module, upload_decision, owner_id, "upload")
            return _decision_response(upload_decision)

    if request.method == "POST" and request.path.startswith("/convert/retry/"):
        queue_decision = _job_admission(
            app_module=app_module,
            controller=controller,
            owner_id=owner_id or "anonymous:missing",
        )
        if not queue_decision.allowed:
            _log_decision(app_module, queue_decision, owner_id, "capacity")
            return _decision_response(queue_decision)
    return None


def _request_category(method: str, path: str) -> str:
    normalized_method = str(method or "").upper()
    if any(path.startswith(prefix) for prefix in _POLL_PREFIXES):
        return "poll"
    if normalized_method in {"POST", "PUT", "PATCH", "DELETE"} and any(
        path.startswith(prefix) for prefix in _MUTATION_PREFIXES
    ):
        return "mutation"
    if normalized_method in {"GET", "HEAD"} and any(
        path.startswith(prefix) for prefix in _READ_PREFIXES
    ):
        return "read"
    return ""


def _normalized_route(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part]
    if len(parts) >= 3 and parts[0] == "convert":
        if parts[1] in {
            "status",
            "quality",
            "progress",
            "retry",
            "report",
            "artifact",
            "download",
            "preview",
            "repair",
            "jobs",
        }:
            parts[2] = ":job"
    return "/" + "/".join(parts)


def _owner_identity(app_module: Any) -> tuple[str, bool]:
    try:
        from durable_runtime_integration import resolve_request_owner_id

        owner = resolve_request_owner_id(app_module)
    except ImportError:
        owner = ""
    authenticated = False
    try:
        auth_context = app_module._resolve_request_auth_context()
        authenticated = bool(getattr(auth_context, "authenticated", False))
        if not owner and authenticated:
            user_id = str(getattr(auth_context, "user_id", "") or "")
            owner = f"user:{user_id}" if user_id else ""
    except Exception:
        pass
    if not owner:
        raw_guest = str(
            request.headers.get("X-KindleMaster-Guest-Id")
            or request.args.get("km_guest")
            or ""
        ).strip()
        if raw_guest:
            import hashlib

            owner = "guest:" + hashlib.sha256(raw_guest.encode("utf-8")).hexdigest()
    return owner, authenticated


def _job_admission(
    *,
    app_module: Any,
    controller: DistributedAdmissionController,
    owner_id: str,
) -> AdmissionDecision:
    queue = _durable_queue(app_module)
    if queue is not None and hasattr(queue, "counts"):
        owner_counts = queue.counts(owner_id=owner_id)
        global_counts = queue.counts()
        active_jobs = int(owner_counts.active)
        queued_jobs = int(owner_counts.queued)
        global_jobs = int(global_counts.active + global_counts.queued)
    else:
        jobs = app_module._visible_conversion_jobs_snapshot()
        owner_jobs = [job for job in jobs.values() if _job_matches_owner(job, owner_id)]
        active_jobs = sum(
            1
            for job in owner_jobs
            if str(job.get("status") or "").lower()
            in {"running", "extracting", "repairing_headings"}
        )
        queued_jobs = sum(
            1 for job in owner_jobs if str(job.get("status") or "").lower() == "queued"
        )
        global_jobs = sum(
            1
            for job in jobs.values()
            if str(job.get("status") or "").lower()
            in {"queued", "running", "extracting", "repairing_headings"}
        )
    free_disk = shutil.disk_usage(str(app_module.UPLOAD_DIR)).free
    return controller.check_job_admission(
        owner_id=owner_id,
        active_jobs=active_jobs,
        queued_jobs=queued_jobs,
        global_jobs=global_jobs,
        free_disk_bytes=free_disk,
    )


def _job_matches_owner(job: Mapping[str, Any], owner_id: str) -> bool:
    if owner_id.startswith("user:"):
        return str(job.get("user_id") or "") == owner_id.removeprefix("user:")
    if owner_id.startswith("guest:"):
        return str(job.get("guest_owner_id") or "") == owner_id
    return not job.get("user_id") and not job.get("guest_owner_id")


def _validate_request_upload(
    controller: DistributedAdmissionController,
) -> AdmissionDecision | None:
    uploaded = request.files.get("file") or request.files.get("pdf")
    if uploaded is None or not uploaded.filename:
        return None
    stream = uploaded.stream
    original_position = stream.tell() if hasattr(stream, "tell") else 0
    try:
        stream.seek(0, os.SEEK_END)
        size_bytes = int(stream.tell())
        stream.seek(0)
        prefix = stream.read(8)
        stream.seek(0)
        detected = (
            "pdf"
            if prefix.startswith(b"%PDF-")
            else "docx"
            if prefix.startswith(b"PK\x03\x04")
            else "unknown"
        )
        metrics: dict[str, Any] = {}
        if detected == "pdf":
            metrics.update(_inspect_pdf(stream, controller.policy.max_file_bytes))
        elif detected == "docx":
            metrics.update(_inspect_docx(stream))
        stream.seek(0)
        return controller.validate_upload(
            filename=str(uploaded.filename),
            declared_mime=str(uploaded.mimetype or ""),
            prefix=prefix,
            size_bytes=size_bytes,
            **metrics,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        return AdmissionDecision(
            False,
            "malformed_document",
            422,
            0,
            {"exception": error.__class__.__name__},
        )
    finally:
        try:
            stream.seek(original_position)
        except (OSError, ValueError):
            pass


def _inspect_pdf(stream: Any, max_bytes: int) -> dict[str, Any]:
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        return {}
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        try:
            return {
                "pdf_pages": int(document.page_count),
                "pdf_objects": int(document.xref_length()),
                "encrypted": bool(document.needs_pass),
            }
        finally:
            document.close()
    except ImportError:
        return {}


def _inspect_docx(stream: Any) -> dict[str, Any]:
    with zipfile.ZipFile(stream) as archive:
        entries = archive.infolist()
        uncompressed = sum(max(0, int(item.file_size)) for item in entries)
        compressed = sum(max(0, int(item.compress_size)) for item in entries)
        encrypted = any(bool(item.flag_bits & 0x1) for item in entries)
        ratio = float(uncompressed) / float(max(1, compressed)) if entries else 0.0
        return {
            "archive_entries": len(entries),
            "archive_uncompressed_bytes": uncompressed,
            "archive_ratio": ratio,
            "encrypted": encrypted,
        }


def _decision_response(decision: AdmissionDecision) -> Response:
    response = jsonify(
        {
            "success": False,
            "error": _public_message(decision.code),
            "error_code": decision.code,
            "phase": "admission",
            "retryable": decision.status_code in {429, 503},
            "retry_after_seconds": decision.retry_after_seconds,
        }
    )
    response.status_code = decision.status_code
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    if decision.retry_after_seconds:
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
    return response


def _public_message(code: str) -> str:
    return {
        "rate_limited": "Limit żądań został przekroczony. Spróbuj ponownie później.",
        "owner_active_job_limit": "Masz już maksymalną liczbę aktywnych konwersji.",
        "owner_queue_limit": "Twoja kolejka konwersji jest pełna.",
        "global_capacity_exhausted": "Usługa jest chwilowo przeciążona. Spróbuj ponownie później.",
        "insufficient_storage_capacity": "Usługa chwilowo nie może przyjąć kolejnego pliku.",
        "upload_too_large": "Plik przekracza dozwolony limit rozmiaru.",
        "empty_upload": "Przesłany plik jest pusty.",
        "unsupported_magic_bytes": "Format pliku nie jest obsługiwany.",
        "extension_magic_mismatch": "Rozszerzenie pliku nie odpowiada jego zawartości.",
        "mime_magic_mismatch": "Typ pliku nie odpowiada jego zawartości.",
        "encrypted_document": "Pliki zabezpieczone hasłem nie są obsługiwane.",
        "pdf_page_limit": "Dokument PDF ma zbyt wiele stron.",
        "pdf_object_limit": "Struktura PDF jest zbyt złożona do bezpiecznego przetworzenia.",
        "archive_entry_limit": "Archiwum DOCX zawiera zbyt wiele elementów.",
        "archive_expansion_limit": "Rozpakowany dokument przekracza bezpieczny limit.",
        "archive_ratio_limit": "Dokument ma niebezpiecznie wysoki współczynnik kompresji.",
        "malformed_document": "Nie udało się bezpiecznie odczytać struktury dokumentu.",
    }.get(code, "Żądanie zostało odrzucone przez zabezpieczenia usługi.")


def _log_decision(
    app_module: Any,
    decision: AdmissionDecision,
    owner_id: str,
    category: str,
) -> None:
    try:
        app_module.app.logger.warning(
            json.dumps(
                {
                    "event": "admission.denied",
                    "code": decision.code,
                    "status_code": decision.status_code,
                    "category": category,
                    "owner_class": (
                        "user"
                        if owner_id.startswith("user:")
                        else "guest"
                        if owner_id.startswith("guest:")
                        else "unknown"
                    ),
                    "details": decision.details,
                },
                separators=(",", ":"),
            )
        )
    except Exception:
        pass


install_admission_controls()
