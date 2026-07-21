from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

ADMISSION_ERROR_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "owner_concurrency_exceeded",
        "global_capacity_exceeded",
        "storage_capacity_exceeded",
        "memory_capacity_exceeded",
        "upload_size_limit",
        "upload_magic_mismatch",
        "upload_mime_mismatch",
        "malformed_pdf",
        "password_protected_pdf",
        "empty_pdf",
        "pdf_page_limit",
        "pdf_object_limit",
        "malformed_docx",
        "invalid_docx_structure",
        "docx_path_traversal",
        "docx_member_limit",
        "docx_member_size_limit",
        "docx_uncompressed_limit",
        "archive_expansion_limit",
        "image_pixel_limit",
    }
)


def _route_class(path: str, method: str) -> str:
    normalized_path = str(path or "")
    normalized_method = str(method or "GET").upper()
    if normalized_path in {"/convert/start", "/convert"} and normalized_method == "POST":
        return "conversion_start"
    if normalized_path.startswith("/convert/retry/") and normalized_method == "POST":
        return "conversion_retry"
    if normalized_path.startswith("/convert/cancel/") and normalized_method == "POST":
        return "conversion_cancel"
    if normalized_method == "DELETE" and normalized_path.startswith("/convert/jobs/"):
        return "conversion_delete"
    if normalized_method in {"POST", "PUT", "DELETE"} and normalized_path.startswith("/convert/"):
        return "conversion_mutation"
    if normalized_path.startswith("/convert/") or normalized_path == "/convert/jobs":
        return "conversion_read"
    return "other"


def _verified_owner_class(app_module: Any) -> str:
    try:
        from flask import g

        authenticated = getattr(g, "kindlemaster_rate_authenticated", None)
        if authenticated is not None:
            return "authenticated" if bool(authenticated) else "guest"
    except Exception:
        pass
    try:
        auth_context = app_module._resolve_request_auth_context()
        if getattr(auth_context, "authenticated", False):
            return "authenticated"
    except Exception:
        pass
    return "guest"


def install_admission_security_logging(app_module: Any) -> None:
    from flask import request

    @app_module.app.after_request
    def log_admission_security_event(response):
        payload = response.get_json(silent=True) if response.is_json else None
        if not isinstance(payload, dict):
            return response
        error_code = str(payload.get("error_code") or "").strip()
        if error_code not in ADMISSION_ERROR_CODES:
            return response
        event = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event": "production_admission_denied",
            "environment": str(
                os.environ.get("SENTRY_ENVIRONMENT")
                or os.environ.get("RAILWAY_ENVIRONMENT_NAME")
                or "unknown"
            )[:80],
            "owner_class": _verified_owner_class(app_module),
            "route_class": _route_class(request.path, request.method),
            "method": request.method,
            "rule_code": error_code,
            "status_code": int(response.status_code),
            "retryable": bool(payload.get("retryable")),
        }
        app_module.app.logger.warning(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        return response

    app_module._PRODUCTION_ADMISSION_SECURITY_LOGGING = True
