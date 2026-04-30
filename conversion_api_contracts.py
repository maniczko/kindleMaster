from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ERROR_UPLOAD_FAILED = "upload_failed"
ERROR_QUEUE_FAILED = "queue_failed"
ERROR_CONVERSION_FAILED = "conversion_failed"
ERROR_CONVERSION_TIMEOUT = "conversion_timeout"
ERROR_APPLICATION_RESTART = "application_restart"
ERROR_MISSING_OUTPUT = "missing_output"
ERROR_UNSUPPORTED_REPORT_FORMAT = "unsupported_report_format"

CONVERT_ERROR_CODES = frozenset(
    {
        ERROR_UPLOAD_FAILED,
        ERROR_QUEUE_FAILED,
        ERROR_CONVERSION_FAILED,
        ERROR_CONVERSION_TIMEOUT,
        ERROR_APPLICATION_RESTART,
        ERROR_MISSING_OUTPUT,
        ERROR_UNSUPPORTED_REPORT_FORMAT,
    }
)

CACHE_CONTROL_NO_STORE = "no-store, max-age=0"
DOWNLOAD_STATE_AVAILABLE = "available"
DOWNLOAD_STATE_PENDING = "pending"
DOWNLOAD_STATE_MISSING_OUTPUT = "missing_output"
DOWNLOAD_STATE_MISSING_URL = "missing_url"
DOWNLOAD_STATE_UNAVAILABLE = "unavailable"

READY_DOWNLOAD_JOB_STATUS = "ready"
ACTIVE_DOWNLOAD_JOB_STATUSES = frozenset({"queued", "running", "repairing_headings"})


def _coerce_text(value: Any, *, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _path_exists(path_value: Any) -> bool:
    path_text = _coerce_text(path_value)
    if not path_text:
        return False
    try:
        return Path(path_text).is_file()
    except OSError:
        return False


@dataclass(frozen=True)
class ConversionDownloadState:
    status: str
    download_available: bool
    download_url: str | None
    reason: str
    output_path_exists: bool = False

    @property
    def download_ready(self) -> bool:
        return self.download_available

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "available": self.download_available,
            "ready": self.download_ready,
            "download_available": self.download_available,
            "download_ready": self.download_ready,
            "download_url": self.download_url,
            "reason": self.reason,
            "output_path_exists": self.output_path_exists,
        }


def resolve_conversion_download_state(
    *,
    job_status: Any,
    output_path: Any = "",
    download_url: Any = "",
    output_path_exists: bool | None = None,
) -> ConversionDownloadState:
    """Resolve the API download state from the job lifecycle and artifact presence."""

    status = _coerce_text(job_status, default="unknown").lower() or "unknown"
    url = _coerce_text(download_url) or None
    resolved_output_exists = (
        bool(output_path_exists)
        if output_path_exists is not None
        else _path_exists(output_path)
    )

    if status == READY_DOWNLOAD_JOB_STATUS:
        if not resolved_output_exists:
            return ConversionDownloadState(
                status=DOWNLOAD_STATE_MISSING_OUTPUT,
                download_available=False,
                download_url=None,
                reason="output_path_missing",
                output_path_exists=False,
            )
        if not url:
            return ConversionDownloadState(
                status=DOWNLOAD_STATE_MISSING_URL,
                download_available=False,
                download_url=None,
                reason="download_url_missing",
                output_path_exists=True,
            )
        return ConversionDownloadState(
            status=DOWNLOAD_STATE_AVAILABLE,
            download_available=True,
            download_url=url,
            reason="",
            output_path_exists=True,
        )

    if status in ACTIVE_DOWNLOAD_JOB_STATUSES:
        return ConversionDownloadState(
            status=DOWNLOAD_STATE_PENDING,
            download_available=False,
            download_url=None,
            reason=f"job_{status}",
            output_path_exists=resolved_output_exists,
        )

    reason = "job_not_ready"
    if status == "failed":
        reason = "conversion_failed"
    elif status == "timed_out":
        reason = "conversion_timeout"
    return ConversionDownloadState(
        status=DOWNLOAD_STATE_UNAVAILABLE,
        download_available=False,
        download_url=None,
        reason=reason,
        output_path_exists=resolved_output_exists,
    )


def build_json_error_payload(
    message: str,
    *,
    error_code: str,
    phase: str,
    job_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Return the stable JSON error DTO used by /convert endpoints."""

    payload: dict[str, Any] = {
        "success": False,
        "error": str(message),
        "error_code": str(error_code),
        "phase": str(phase),
        "retryable": bool(retryable),
    }
    if job_id:
        payload["job_id"] = str(job_id)
    return payload


def apply_no_store_headers(headers: Any) -> None:
    headers["Cache-Control"] = CACHE_CONTROL_NO_STORE
    headers["Pragma"] = "no-cache"
