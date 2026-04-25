from __future__ import annotations

import os
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping


LOCALHOST = "127.0.0.1"
LOCAL_APP_HOSTNAME = "kindlemaster.localhost"
DEFAULT_PORT = 5001
DEFAULT_DEBUG = False
DEFAULT_OVERSIZED_EPUB_WARNING_BYTES = 25 * 1024 * 1024
SUPPORTED_SOURCE_SUFFIXES = frozenset({".pdf", ".docx"})

ConvertFunction = Callable[..., dict[str, Any]]
HeadingRepairFunction = Callable[..., Any]
StatusCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class ConversionRequest:
    source_path: str
    original_filename: str
    profile: str
    language: str
    force_ocr: bool = False
    heading_repair_enabled: bool = False
    source_type: str | None = None


@dataclass(frozen=True)
class ConversionOutcome:
    result: dict[str, Any]
    epub_bytes: bytes
    heading_repair_report: dict[str, Any]
    detected_source_type: str
    download_name: str
    metadata: dict[str, Any]


def detect_supported_source_type(filename: str | None) -> str | None:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in SUPPORTED_SOURCE_SUFFIXES:
        return None
    return suffix.lstrip(".")


def build_conversion_job_record(
    *,
    job_id: str,
    source_path: str,
    source_type: str,
    filename: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Plik odebrany. Konwersja zaraz sie rozpocznie.",
        "source_type": source_type,
        "filename": filename,
        "created_at": created_at,
        "updated_at": created_at,
        "source_path": source_path,
        "output_path": "",
        "download_name": filename.rsplit(".", 1)[0] + ".epub",
        "metadata": {},
        "output_size_bytes": 0,
        "error": "",
    }


class ConversionJobStore:
    """Small persistence boundary for async conversion jobs.

    The Flask app still owns process-local workers, but terminal job state is
    durable enough for status/quality/download routes after a dev-server
    restart. Active jobs cannot be resumed safely, so reload marks them failed
    instead of pretending they are still running.
    """

    def __init__(
        self,
        jobs: MutableMapping[str, dict[str, Any]],
        lock: Any,
        *,
        persistence_path: str | os.PathLike[str] | None = None,
        active_statuses: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._jobs = jobs
        self._lock = lock
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._active_statuses = set(active_statuses or {"queued", "running", "repairing_headings"})

    @property
    def persistence_path(self) -> Path | None:
        return self._persistence_path

    def load(self) -> dict[str, Any]:
        if not self._persistence_path or not self._persistence_path.exists():
            return {"loaded": False, "job_count": 0, "interrupted_jobs": 0, "error": ""}

        try:
            payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return {"loaded": False, "job_count": 0, "interrupted_jobs": 0, "error": str(error)}

        raw_jobs = payload.get("jobs", {}) if isinstance(payload, Mapping) else {}
        if not isinstance(raw_jobs, Mapping):
            return {"loaded": False, "job_count": 0, "interrupted_jobs": 0, "error": "Invalid job store shape."}

        interrupted_jobs = 0
        loaded_jobs: dict[str, dict[str, Any]] = {}
        now = _utc_now_label()
        for raw_job_id, raw_job in raw_jobs.items():
            if not isinstance(raw_job, Mapping):
                continue
            job = dict(raw_job)
            job_id = str(job.get("job_id") or raw_job_id).strip()
            if not job_id:
                continue
            job["job_id"] = job_id
            status = str(job.get("status", "") or "").strip().lower()
            if status in self._active_statuses:
                interrupted_jobs += 1
                job["status"] = "failed"
                job["message"] = "Konwersja przerwana przez restart aplikacji."
                job["error"] = "Async conversion job was interrupted by application restart."
                job["source_path"] = ""
                job["updated_at"] = now
            loaded_jobs[job_id] = job

        with self._lock:
            self._jobs.update(loaded_jobs)

        if interrupted_jobs:
            self.persist()

        return {"loaded": True, "job_count": len(loaded_jobs), "interrupted_jobs": interrupted_jobs, "error": ""}

    def create(self, job: Mapping[str, Any]) -> dict[str, Any]:
        job_id = str(job.get("job_id", "") or "").strip()
        if not job_id:
            raise ValueError("Conversion job requires a non-empty job_id.")
        payload = dict(job)
        with self._lock:
            self._jobs[job_id] = payload
            snapshot = dict(payload)
        self.persist()
        return snapshot

    def update(self, job_id: str, fields: Mapping[str, Any], *, updated_at: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.update(dict(fields))
            job["updated_at"] = updated_at or _utc_now_label()
            snapshot = dict(job)
        self.persist()
        return snapshot

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def delete(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            snapshot = dict(job) if job else None
        if snapshot is not None:
            self.persist()
        return snapshot

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {job_id: dict(job) for job_id, job in self._jobs.items()}

    def persist(self) -> dict[str, Any]:
        if not self._persistence_path:
            return {"persisted": False, "job_count": 0, "error": ""}

        snapshot = self.snapshot()
        payload = {"version": 1, "updated_at": _utc_now_label(), "jobs": snapshot}
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._persistence_path.with_suffix(self._persistence_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self._persistence_path)
        except OSError as error:
            return {"persisted": False, "job_count": len(snapshot), "error": str(error)}
        return {"persisted": True, "job_count": len(snapshot), "error": ""}


def _utc_now_label() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_local_app_url(port: int | str | None = None, *, path: str = "/") -> str:
    port_label = str(port).strip() if port is not None else ""
    netloc = LOCAL_APP_HOSTNAME
    if port_label:
        netloc = f"{netloc}:{port_label}"
    normalized_path = path or "/"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    return f"http://{netloc}{normalized_path}"


def resolve_server_port(environ: Mapping[str, str] | None = None) -> int:
    environment = os.environ if environ is None else environ
    port_raw = str(environment.get("PORT", "")).strip()
    if not port_raw:
        return DEFAULT_PORT

    try:
        port = int(port_raw)
    except ValueError:
        return DEFAULT_PORT

    if 1 <= port <= 65535:
        return port
    return DEFAULT_PORT


def resolve_debug_mode(environ: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environ is None else environ
    debug_raw = str(environment.get("FLASK_DEBUG", environment.get("DEBUG", ""))).strip().lower()
    if not debug_raw:
        return DEFAULT_DEBUG
    return debug_raw in {"1", "true", "yes", "on"}


def serve_http_app(application: Any, *, host: str, port: int, debug: bool, runtime: str) -> int:
    if runtime == "waitress":
        from waitress import serve

        serve(application, host=host, port=port)
        return 0

    application.run(debug=debug, host=host, port=port)
    return 0


def build_conversion_config(request: ConversionRequest) -> Any:
    from converter import ConversionConfig

    return ConversionConfig(
        prefer_fixed_layout=request.profile == "preserve-layout",
        profile=request.profile,
        force_ocr=request.force_ocr,
        language=request.language,
    )


def pick_epubcheck_error(messages: list[Any] | tuple[Any, ...] | None) -> str:
    cleaned = [str(message).strip() for message in (messages or []) if str(message).strip()]
    for message in cleaned:
        upper = message.upper()
        if "ERROR(" in upper or upper.startswith("ERROR") or "FATAL(" in upper or upper.startswith("FATAL"):
            return message
    return cleaned[0] if cleaned else "Heading/TOC repair failed."


def _default_heading_repair_report() -> dict[str, Any]:
    return {
        "status": "skipped",
        "release_status": "unavailable",
        "toc_entries_before": 0,
        "toc_entries_after": 0,
        "headings_removed": 0,
        "manual_review_count": 0,
        "epubcheck_status": "unavailable",
        "error": "",
    }


def _fallback_source_type(request: ConversionRequest) -> str:
    if request.source_type:
        return str(request.source_type)
    suffix = Path(request.original_filename).suffix.lower().lstrip(".")
    return suffix or "pdf"


def _extract_analysis_profile(result: Mapping[str, Any]) -> str:
    analysis = result.get("analysis", {}) or {}
    if isinstance(analysis, Mapping):
        return str(analysis.get("profile", "") or "").strip().lower()
    return str(getattr(analysis, "profile", "") or "").strip().lower()


def _should_skip_heading_repair(
    request: ConversionRequest,
    result: Mapping[str, Any],
) -> tuple[bool, str]:
    if not request.heading_repair_enabled:
        return False, ""

    profile = _extract_analysis_profile(result)
    if profile == "diagram_book_reflow":
        return (
            True,
            "Pominieto heading repair dla diagram-heavy training book, aby zachowac stabilne TOC i uniknac bardzo dlugiego post-processingu",
        )
    return False, ""


def build_conversion_metadata(
    *,
    result: dict[str, Any],
    detected_source_type: str,
    heading_repair_enabled: bool,
    heading_repair_report: dict[str, Any],
) -> dict[str, Any]:
    analysis = result.get("analysis", {}) or {}
    quality_report = result.get("quality_report", {}) or {}
    document_summary = result.get("document_summary", {}) or {}
    profile_name = (
        analysis.get("profile")
        if isinstance(analysis, dict)
        else getattr(analysis, "profile", "unknown")
    )
    confidence = (
        analysis.get("confidence")
        if isinstance(analysis, dict)
        else getattr(analysis, "confidence", 0)
    )
    warning_list = (quality_report.get("warnings", []) or [])[:12]
    high_risk_page_list = [
        {
            "page": item.get("page_index"),
            "title": item.get("title"),
            "kind": item.get("content_type"),
            "flags": item.get("risk_flags", [])[:4],
        }
        for item in (quality_report.get("high_risk_pages", []) or [])
    ][:20]
    high_risk_section_list = [
        {
            "title": item.get("title"),
            "pages": item.get("page_range"),
            "flags": item.get("risk_flags", [])[:4],
        }
        for item in (quality_report.get("high_risk_sections", []) or [])
    ][:20]
    render_budget_class = str(
        quality_report.get("render_budget_class")
        or quality_report.get("size_budget_key")
        or (
            analysis.get("render_budget_class")
            if isinstance(analysis, dict)
            else getattr(analysis, "render_budget_class", "")
        )
        or ""
    )
    return {
        "source_type": detected_source_type,
        "profile": str(profile_name),
        "confidence": float(confidence) if confidence is not None else 0.0,
        "validation": str(quality_report.get("validation_status", "unavailable")),
        "validation_tool": str(quality_report.get("validation_tool", "unknown")),
        "strategy": (
            str(analysis.get("legacy_strategy", "premium"))
            if detected_source_type == "pdf" and isinstance(analysis, dict)
            else None
        ),
        "sections": int(document_summary.get("section_count", 0) or 0),
        "assets": int(document_summary.get("asset_count", 0) or 0),
        "layout": str(document_summary.get("layout_mode", "reflowable")),
        "warnings": len(quality_report.get("warnings", []) or []),
        "warning_list": warning_list,
        "high_risk_pages": len(quality_report.get("high_risk_pages", []) or []),
        "high_risk_page_list": high_risk_page_list,
        "high_risk_sections": len(quality_report.get("high_risk_sections", []) or []),
        "high_risk_section_list": high_risk_section_list,
        "render_budget_class": render_budget_class,
        "render_budget_attempt": str(quality_report.get("render_budget_attempt", "")),
        "size_budget_status": str(quality_report.get("size_budget_status", "")),
        "size_budget_message": str(quality_report.get("size_budget_message", "")),
        "target_warn_bytes": int(quality_report.get("target_warn_bytes", 0) or 0),
        "target_hard_bytes": int(quality_report.get("target_hard_bytes", 0) or 0),
        "final_output_size_bytes": int(quality_report.get("final_output_size_bytes", 0) or 0),
        "heading_repair": {
            "status": str(
                heading_repair_report.get(
                    "status",
                    "skipped" if not heading_repair_enabled else "failed",
                )
            ),
            "release": str(heading_repair_report.get("release_status", "unavailable")),
            "toc_before": int(heading_repair_report.get("toc_entries_before", 0) or 0),
            "toc_after": int(heading_repair_report.get("toc_entries_after", 0) or 0),
            "removed": int(heading_repair_report.get("headings_removed", 0) or 0),
            "review": int(heading_repair_report.get("manual_review_count", 0) or 0),
            "epubcheck": str(heading_repair_report.get("epubcheck_status", "unavailable")),
            "error": str(heading_repair_report.get("error", "")),
        },
    }


def build_conversion_quality_state(
    payload: Mapping[str, Any],
    *,
    download_url: str | None = None,
) -> dict[str, Any]:
    from quality_state_service import ConversionQualityStateRequest, assemble_quality_state_dict

    request = ConversionQualityStateRequest.from_job_payload(
        payload,
        download_url=download_url,
    )
    return assemble_quality_state_dict(request)


def enrich_conversion_metadata_with_output_size(
    metadata: Mapping[str, Any] | None,
    output_size_bytes: int | None,
    *,
    oversized_warning_bytes: int = DEFAULT_OVERSIZED_EPUB_WARNING_BYTES,
) -> dict[str, Any]:
    enriched = dict(metadata or {})
    if output_size_bytes is None:
        return enriched

    normalized_output_size = max(0, int(output_size_bytes))
    enriched["output_size_bytes"] = normalized_output_size
    if normalized_output_size < oversized_warning_bytes:
        return enriched

    warning_text = (
        f"EPUB ma {normalized_output_size / (1024 * 1024):.1f} MB. "
        "Na Kindle pobranie i otwarcie moze byc wolniejsze."
    )
    warnings = list(enriched.get("warning_list", []) or [])
    if warning_text not in warnings:
        warnings = warnings[:11] + [warning_text] if len(warnings) >= 12 else warnings + [warning_text]
    enriched["warning_list"] = warnings
    enriched["warnings"] = max(int(enriched.get("warnings", 0) or 0), len(warnings))
    return enriched


def build_conversion_summary(
    outcome: ConversionOutcome,
    *,
    filename: str,
    output_size_bytes: int | None = None,
    download_url: str | None = None,
    job_status: str = "ready",
    message: str = "",
    error: str = "",
) -> dict[str, Any]:
    metadata = enrich_conversion_metadata_with_output_size(outcome.metadata, output_size_bytes)
    payload = {
        key: value
        for key, value in outcome.result.items()
        if key != "epub_bytes"
    }
    payload["source_type"] = outcome.detected_source_type
    payload["download_name"] = outcome.download_name
    payload["heading_repair_report"] = dict(outcome.heading_repair_report)
    payload["metadata"] = metadata
    payload["conversion"] = dict(metadata)
    if metadata.get("output_size_bytes") is not None:
        payload["output_size_bytes"] = metadata.get("output_size_bytes")
    payload["quality_state"] = build_conversion_quality_state(
        {
            "status": job_status,
            "source_type": outcome.detected_source_type,
            "filename": filename,
            "message": message,
            "error": error,
            "metadata": metadata,
            "output_size_bytes": metadata.get("output_size_bytes"),
        },
        download_url=download_url,
    )
    return payload


def run_document_conversion(
    request: ConversionRequest,
    *,
    convert_impl: ConvertFunction,
    heading_repair_impl: HeadingRepairFunction,
    status_callback: StatusCallback | None = None,
) -> ConversionOutcome:
    source_type = _fallback_source_type(request)
    if status_callback:
        status_callback("running", f"Konwertuje {source_type.upper()} do EPUB...")

    convert_kwargs: dict[str, Any] = {
        "config": build_conversion_config(request),
        "original_filename": request.original_filename,
    }
    if request.source_type:
        convert_kwargs["source_type"] = request.source_type

    result = convert_impl(request.source_path, **convert_kwargs)
    epub_bytes = result["epub_bytes"]
    heading_repair_report = _default_heading_repair_report()

    if request.heading_repair_enabled:
        skip_heading_repair, skip_reason = _should_skip_heading_repair(request, result)
        if skip_heading_repair:
            heading_repair_report.update(
                {
                    "status": "skipped",
                    "release_status": "skipped",
                    "epubcheck_status": "skipped",
                    "error": skip_reason,
                }
            )
        else:
            if status_callback:
                status_callback("repairing_headings", "Naprawiam headingi i TOC w EPUB...")
            try:
                heading_repair_result = heading_repair_impl(
                    epub_bytes,
                    title_hint=str((result.get("document_summary", {}) or {}).get("title", "") or ""),
                    author_hint=str((result.get("document_summary", {}) or {}).get("author", "") or ""),
                    language_hint=request.language,
                    publication_profile=request.profile,
                )
                heading_repair_report = {
                    "status": "applied",
                    "release_status": heading_repair_result.summary.get("release_status", "unavailable"),
                    "toc_entries_before": heading_repair_result.summary.get("toc_entries_before", 0),
                    "toc_entries_after": heading_repair_result.summary.get("toc_entries_after", 0),
                    "headings_removed": heading_repair_result.summary.get("headings_removed", 0),
                    "manual_review_count": heading_repair_result.summary.get("manual_review_count", 0),
                    "epubcheck_status": heading_repair_result.summary.get("epubcheck_status", "unavailable"),
                    "error": "",
                }
                if heading_repair_result.epubcheck.get("status") == "failed":
                    heading_repair_report["status"] = "failed"
                    heading_repair_report["error"] = pick_epubcheck_error(
                        heading_repair_result.epubcheck.get("messages", []) or []
                    )
                else:
                    epub_bytes = heading_repair_result.epub_bytes
            except Exception as error:
                heading_repair_report["status"] = "failed"
                heading_repair_report["error"] = str(error)

    detected_source_type = str(result.get("source_type", source_type) or source_type)
    metadata = build_conversion_metadata(
        result=result,
        detected_source_type=detected_source_type,
        heading_repair_enabled=request.heading_repair_enabled,
        heading_repair_report=heading_repair_report,
    )
    return ConversionOutcome(
        result=result,
        epub_bytes=epub_bytes,
        heading_repair_report=heading_repair_report,
        detected_source_type=detected_source_type,
        download_name=request.original_filename.rsplit(".", 1)[0] + ".epub",
        metadata=metadata,
    )
