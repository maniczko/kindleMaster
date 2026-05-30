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
DEFAULT_LOCAL_DEV_CORS_ORIGINS = frozenset(
    {
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://kindlemaster.localhost:5173",
        "http://kindlemaster.localhost:5174",
    }
)
DEFAULT_OVERSIZED_EPUB_WARNING_BYTES = 25 * 1024 * 1024
SUPPORTED_SOURCE_SUFFIXES = frozenset({".pdf", ".docx"})
METADATA_LIST_LIMIT = 20
METADATA_MESSAGE_LIMIT = 12
METADATA_DEPTH_LIMIT = 4

ConvertFunction = Callable[..., dict[str, Any]]
HeadingRepairFunction = Callable[..., Any]
StatusCallback = Callable[..., None]


@dataclass(frozen=True)
class ConversionRequest:
    source_path: str
    original_filename: str
    profile: str
    language: str
    force_ocr: bool = False
    heading_repair_enabled: bool = False
    source_type: str | None = None
    text_cleanup_domain_dictionary_path: str | None = None
    route_model_mode: str = "shadow"
    quality_gate_mode: str = "draft"
    feedback_enabled: bool = True


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
        "runtime": {},
        "progress": {
            "stage_id": "queued",
            "stage_label": "Przygotowanie",
            "percent_estimate": 5,
            "health": "working",
            "heartbeat_at": created_at,
            "updated_at": created_at,
        },
        "artifacts": {},
        "artifact_storage": {},
        "output_size_bytes": 0,
        "error": "",
        "error_code": "",
        "sentry_event_id": "",
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
                job["error"] = "Konwersja zostala przerwana przez restart aplikacji. Uruchom konwersje ponownie."
                job["error_code"] = "application_restart"
                source_path = str(job.get("source_path") or "").strip()
                if source_path and not Path(source_path).is_file():
                    source_path = ""
                job["source_path"] = source_path
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


def resolve_server_host(environ: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environ is None else environ
    host = str(
        environment.get("KINDLEMASTER_BIND_HOST")
        or environment.get("HOST")
        or ""
    ).strip()
    return host or LOCALHOST


def resolve_debug_mode(environ: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environ is None else environ
    debug_raw = str(environment.get("FLASK_DEBUG", environment.get("DEBUG", ""))).strip().lower()
    if not debug_raw:
        return DEFAULT_DEBUG
    return debug_raw in {"1", "true", "yes", "on"}


def resolve_allowed_cors_origins(environ: Mapping[str, str] | None = None) -> set[str]:
    environment = os.environ if environ is None else environ
    raw = str(environment.get("KINDLEMASTER_ALLOWED_ORIGINS", "") or "")
    configured = {origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()}
    configured.discard("*")

    local_dev_raw = str(environment.get("KINDLEMASTER_ALLOW_LOCAL_DEV_CORS", "1") or "").strip().lower()
    if local_dev_raw in {"1", "true", "yes", "on"}:
        configured.update(DEFAULT_LOCAL_DEV_CORS_ORIGINS)
    return configured


def is_allowed_cors_origin(origin: str | None, environ: Mapping[str, str] | None = None) -> bool:
    normalized_origin = str(origin or "").strip().rstrip("/")
    if not normalized_origin:
        return False
    return normalized_origin in resolve_allowed_cors_origins(environ)


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
        text_cleanup_domain_dictionary_path=request.text_cleanup_domain_dictionary_path,
        route_model_mode=request.route_model_mode,
        quality_gate_mode=request.quality_gate_mode,
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


def _resolved_publication_profile(
    *,
    request: ConversionRequest,
    result: Mapping[str, Any],
) -> str:
    analysis_profile = _extract_analysis_profile(result)
    if analysis_profile:
        return analysis_profile
    summary = result.get("document_summary", {}) or {}
    if isinstance(summary, Mapping):
        summary_profile = str(summary.get("profile", "") or "").strip()
        if summary_profile:
            return summary_profile
    return request.profile


def _should_skip_heading_repair(
    request: ConversionRequest,
    result: Mapping[str, Any],
) -> tuple[bool, str]:
    if not request.heading_repair_enabled:
        return False, ""

    profile = _extract_analysis_profile(result)
    detected_features = _result_detected_features(result)
    document_summary = result.get("document_summary", {}) or {}
    publication_kind = ""
    if isinstance(document_summary, Mapping):
        publication_kind = str(document_summary.get("publication_kind", "") or "")
        layout_mode = str(document_summary.get("layout_mode", "") or "").strip().lower()
    else:
        layout_mode = ""
    resolved_profile = _resolved_publication_profile(request=request, result=result).strip().lower()
    if (
        resolved_profile in {"fixed_layout_fallback", "preserve-layout", "preserve_layout"}
        or request.profile in {"preserve-layout", "fixed_layout_fallback"}
        or layout_mode == "fixed-layout"
    ):
        return (
            True,
            "Pominieto heading repair dla fixed-layout EPUB; TOC jest stronowy, a semantyczna naprawa naglowkow moze uszkodzic layout.",
        )
    if profile == "book_reflow" and "chess-notation-collection" in {
        str(feature).strip().lower() for feature in detected_features
    }:
        return (
            True,
            "Pominieto heading repair dla chess-notation-collection, aby nie klasyfikowac notacji PGN jako bibliografii.",
        )
    if "chess-notation-collection" in publication_kind.lower():
        return (
            True,
            "Pominieto heading repair dla chess-notation-collection, aby nie klasyfikowac notacji PGN jako bibliografii.",
        )
    if profile == "diagram_book_reflow":
        return (
            True,
            "Pominieto heading repair dla diagram-heavy training book, aby zachowac stabilne TOC i uniknac bardzo dlugiego post-processingu",
        )
    return False, ""


def _result_detected_features(result: Mapping[str, Any]) -> list[Any]:
    analysis = result.get("analysis", {}) or {}
    if isinstance(analysis, Mapping):
        features = analysis.get("detected_features") or []
        return list(features) if isinstance(features, (list, tuple, set)) else []
    features = getattr(analysis, "detected_features", []) or []
    return list(features) if isinstance(features, (list, tuple, set)) else []


def _to_mapping_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _json_safe_metadata_value(
    value: Any,
    *,
    list_limit: int = METADATA_LIST_LIMIT,
    depth: int = METADATA_DEPTH_LIMIT,
) -> Any:
    if depth <= 0:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_metadata_value(item, list_limit=list_limit, depth=depth - 1)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _json_safe_metadata_value(item, list_limit=list_limit, depth=depth - 1)
            for item in list(value)[:list_limit]
        ]

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_safe_metadata_value(to_dict(), list_limit=list_limit, depth=depth - 1)

    return str(value)


def _quality_gate_epubcheck_payload(
    *,
    quality_report: Mapping[str, Any],
    heading_repair_report: Mapping[str, Any],
) -> dict[str, Any]:
    heading_status = str(heading_repair_report.get("status", "") or "").strip().lower()
    heading_epubcheck = str(heading_repair_report.get("epubcheck_status", "") or "").strip()
    if heading_status == "applied" and heading_epubcheck:
        return {
            "status": heading_epubcheck,
            "messages": [],
            "tool": "epubcheck",
        }
    return {
        "status": str(quality_report.get("validation_status", "unavailable") or "unavailable"),
        "messages": list(quality_report.get("validation_messages", []) or []),
        "tool": str(quality_report.get("validation_tool", "unknown") or "unknown"),
    }


def _apply_runtime_quality_gate(
    *,
    result: dict[str, Any],
    epub_bytes: bytes,
    request: ConversionRequest,
    heading_repair_report: Mapping[str, Any],
) -> dict[str, Any]:
    mode = str(request.quality_gate_mode or "draft").strip().lower()
    if mode == "off":
        return result

    from epub_premium_scoring import (
        apply_magazine_premium_quality_to_scoring,
        build_magazine_premium_quality_contract,
        refresh_magazine_article_map_from_epub,
        score_epub_premium_quality,
    )
    from ml_quality_verifier import build_ai_quality_verification

    updated_result = dict(result)
    quality_report = _to_mapping_payload(updated_result.get("quality_report", {}) or {})
    analysis = _to_mapping_payload(updated_result.get("analysis", {}) or {})
    epubcheck_payload = _quality_gate_epubcheck_payload(
        quality_report=quality_report,
        heading_repair_report=heading_repair_report,
    )
    if str(heading_repair_report.get("status", "") or "").strip().lower() == "applied":
        heading_epubcheck = str(heading_repair_report.get("epubcheck_status", "") or "").strip().lower()
        if heading_epubcheck == "passed":
            quality_report["validation_status"] = "passed"
            quality_report["validation_tool"] = "epubcheck"
            quality_report["validation_messages"] = []
            quality_report["epubcheck_status"] = "passed"
            quality_report["error_count"] = 0
            stale_warnings = []
            for warning in list(quality_report.get("warnings", []) or []):
                warning_text = str(warning)
                if "EPUBCheck" in warning_text or "epubcheck" in warning_text.lower():
                    continue
                stale_warnings.append(warning)
            quality_report["warnings"] = stale_warnings
    premium_scoring = score_epub_premium_quality(epub_bytes, epubcheck=epubcheck_payload)
    magazine_quality = _to_mapping_payload(quality_report.get("magazine_premium_quality") or {})
    if magazine_quality:
        article_map = _to_mapping_payload(magazine_quality.get("article_map") or {})
        if article_map:
            article_map = refresh_magazine_article_map_from_epub(article_map, epub_bytes)
        magazine_quality = build_magazine_premium_quality_contract(
            premium_scoring=premium_scoring,
            magazine_audit={"article_map": article_map},
            validation_status=str(quality_report.get("validation_status", "") or ""),
        )
        premium_scoring = apply_magazine_premium_quality_to_scoring(premium_scoring, magazine_quality)
        quality_report["magazine_premium_quality"] = magazine_quality
    quality_report["premium_scoring"] = premium_scoring
    dense_summary = ((premium_scoring.get("metrics") or {}) or {}).get("dense_handbook_navigation_summary")
    if isinstance(dense_summary, Mapping) and dense_summary:
        quality_report["dense_handbook_navigation_summary"] = dict(dense_summary)
    quality_report["ai_quality_verification"] = build_ai_quality_verification(
        premium_scoring=premium_scoring,
        quality_report=quality_report,
        analysis=analysis,
        quality_gate_mode=mode,
    )
    quality_report["quality_gate_mode"] = mode
    updated_result["quality_report"] = quality_report
    return updated_result


def _runtime_epubcheck_payload_from_quality_report(quality_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(quality_report.get("validation_status", "unavailable") or "unavailable"),
        "messages": list(quality_report.get("validation_messages", []) or []),
        "tool": str(quality_report.get("validation_tool", "unknown") or "unknown"),
    }


def _apply_heading_quality_selection(
    *,
    result: dict[str, Any],
    baseline_epub_bytes: bytes,
    candidate_epub_bytes: bytes,
    candidate_epubcheck: Mapping[str, Any],
    request: ConversionRequest,
    heading_repair_report: dict[str, Any],
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    mode = str(request.quality_gate_mode or "draft").strip().lower()
    if mode == "off":
        return candidate_epub_bytes, result, heading_repair_report

    from epub_quality_selection import select_epub_by_quality

    updated_result = dict(result)
    quality_report = _to_mapping_payload(updated_result.get("quality_report", {}) or {})
    selection = select_epub_by_quality(
        baseline_epub_bytes,
        candidate_epub_bytes,
        baseline_label="pre_heading",
        candidate_label="heading_repair",
        baseline_epubcheck=_runtime_epubcheck_payload_from_quality_report(quality_report),
        candidate_epubcheck=candidate_epubcheck,
    )
    quality_selection = {**selection.report, "phase": "heading_repair"}
    quality_report["quality_selection"] = quality_selection
    updated_result["quality_report"] = quality_report
    if selection.report["status"] == "rejected":
        updated_heading_report = dict(heading_repair_report)
        updated_heading_report["status"] = "rejected"
        updated_heading_report["release_status"] = "pass_with_review"
        updated_heading_report["quality_selection_status"] = "rejected"
        updated_heading_report["error"] = "Heading/TOC repair rejected because it reduced premium quality."
        return selection.selected_bytes, updated_result, updated_heading_report
    return selection.selected_bytes, updated_result, heading_repair_report


def _compact_mapping(
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    list_limit: int = METADATA_LIST_LIMIT,
) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key in keys:
        if key in payload:
            compacted[key] = _json_safe_metadata_value(payload.get(key), list_limit=list_limit)
    return compacted


def _build_content_metrics_payload(quality_report: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_mapping(
        quality_report,
        (
            "section_count",
            "figure_count",
            "diagram_count",
            "table_count",
            "page_marker_count",
            "detected_figures",
            "detected_diagrams",
            "detected_tables",
            "source_toc_entries",
            "source_table_count",
            "xhtml_table_count",
            "fallback_pages",
            "fallback_sections",
            "fallback_regions",
            "tiny_tail_sections",
            "tiny_tail_section_count",
            "asset_budget_status",
            "archive_entry_count",
            "archive_image_count",
            "largest_assets",
            "table_cell_count",
            "table_row_count",
            "table_cell_coverage",
            "table_page_count",
            "multi_page_table_count",
            "wide_table_count",
            "low_confidence_table_count",
            "fragment_table_count",
            "table_summary",
            "figure_summary",
            "reading_flow",
            "magazine_premium_quality",
            "chess_fen",
            "chess_pgn",
            "dense_handbook_navigation_summary",
        ),
    )


def _build_validation_details_payload(quality_report: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_mapping(
        quality_report,
        (
            "epubcheck_status",
            "validation_status",
            "validation_tool",
            "validation_messages",
            "error_count",
            "warning_count",
            "internal_link_error_count",
            "external_link_error_count",
            "broken_href_error_count",
            "duplicate_id_error_count",
            "size_budget_inspection",
        ),
        list_limit=METADATA_MESSAGE_LIMIT,
    )


def _build_document_summary_payload(document_summary: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_mapping(
        document_summary,
        (
            "title",
            "author",
            "language",
            "profile",
            "layout_mode",
            "section_count",
            "asset_count",
        ),
    )


def build_conversion_metadata(
    *,
    result: dict[str, Any],
    detected_source_type: str,
    heading_repair_enabled: bool,
    heading_repair_report: dict[str, Any],
) -> dict[str, Any]:
    analysis = _to_mapping_payload(result.get("analysis", {}) or {})
    quality_report = _to_mapping_payload(result.get("quality_report", {}) or {})
    document_summary = _to_mapping_payload(result.get("document_summary", {}) or {})
    profile_name = analysis.get("profile", "unknown")
    confidence = analysis.get("confidence", 0)
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
        )
        or ""
    )
    metadata = {
        "source_type": detected_source_type,
        "profile": str(profile_name),
        "confidence": float(confidence) if confidence is not None else 0.0,
        "validation": str(quality_report.get("validation_status", "unavailable")),
        "validation_tool": str(quality_report.get("validation_tool", "unknown")),
        "strategy": (
            str(analysis.get("legacy_strategy", "premium"))
            if detected_source_type == "pdf" and isinstance(analysis, Mapping)
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

    content_metrics = _build_content_metrics_payload(quality_report)
    if content_metrics:
        metadata["content_metrics"] = content_metrics

    text_cleanup = _json_safe_metadata_value(quality_report.get("text_cleanup") or {})
    if isinstance(text_cleanup, Mapping) and text_cleanup:
        metadata["text_cleanup"] = dict(text_cleanup)
        ai_quality = text_cleanup.get("ai_quality")
        if isinstance(ai_quality, Mapping) and ai_quality:
            metadata["ai_quality"] = dict(_json_safe_metadata_value(ai_quality, list_limit=METADATA_MESSAGE_LIMIT))
        reference_cleanup = text_cleanup.get("reference_cleanup")
        if isinstance(reference_cleanup, Mapping) and reference_cleanup:
            metadata["reference_cleanup"] = dict(
                _json_safe_metadata_value(reference_cleanup, list_limit=METADATA_MESSAGE_LIMIT)
            )

    for metadata_key, candidates in (
        (
            "semantic_cleanup",
            (
                quality_report.get("semantic_cleanup"),
                text_cleanup.get("semantic_cleanup") if isinstance(text_cleanup, Mapping) else None,
            ),
        ),
        (
            "ocr_quality",
            (
                quality_report.get("ocr_quality"),
                quality_report.get("ocr_degradation"),
                analysis.get("ocr_quality"),
                analysis.get("ocr_degradation"),
            ),
        ),
        (
            "reading_order",
            (
                quality_report.get("reading_order"),
                text_cleanup.get("reading_order") if isinstance(text_cleanup, Mapping) else None,
                analysis.get("reading_order"),
            ),
        ),
    ):
        gate_payload = next((candidate for candidate in candidates if isinstance(candidate, Mapping) and candidate), None)
        if gate_payload is not None:
            metadata[metadata_key] = dict(
                _json_safe_metadata_value(gate_payload, list_limit=METADATA_MESSAGE_LIMIT)
            )

    source_analysis = _json_safe_metadata_value(analysis)
    if isinstance(source_analysis, Mapping) and source_analysis:
        metadata["source_analysis"] = dict(source_analysis)

    cockpit_document_summary = _build_document_summary_payload(document_summary)
    if cockpit_document_summary:
        metadata["document_summary"] = cockpit_document_summary

    validation_details = _build_validation_details_payload(quality_report)
    if validation_details:
        metadata["validation_details"] = validation_details

    premium_scoring = _json_safe_metadata_value(quality_report.get("premium_scoring") or {})
    if isinstance(premium_scoring, Mapping) and premium_scoring:
        metadata["premium_scoring"] = dict(premium_scoring)
    quality_selection = _json_safe_metadata_value(quality_report.get("quality_selection") or {})
    if isinstance(quality_selection, Mapping) and quality_selection:
        metadata["quality_selection"] = dict(quality_selection)
    ai_quality_verification = _json_safe_metadata_value(quality_report.get("ai_quality_verification") or {})
    if isinstance(ai_quality_verification, Mapping) and ai_quality_verification:
        metadata["ai_quality_verification"] = dict(ai_quality_verification)
    if quality_report.get("quality_gate_mode"):
        metadata["quality_gate_mode"] = str(quality_report.get("quality_gate_mode") or "")

    return metadata


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


def _emit_status(callback: StatusCallback, status: str, message: str, **progress_fields: Any) -> None:
    try:
        callback(status, message, **progress_fields)
    except TypeError:
        callback(status, message)


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
        _emit_status(
            status_callback,
            "running",
            f"Ekstrakcja tekstu z {source_type.upper()}...",
            stage_id="extracting",
            stage_label="Ekstrakcja tekstu",
            percent_estimate=20,
        )

    convert_kwargs: dict[str, Any] = {
        "config": build_conversion_config(request),
        "original_filename": request.original_filename,
    }
    if request.source_type:
        convert_kwargs["source_type"] = request.source_type

    result = convert_impl(request.source_path, **convert_kwargs)
    if status_callback:
        _emit_status(
            status_callback,
            "running",
            "Składanie artykułów i struktury EPUB...",
            stage_id="assembling",
            stage_label="Składanie artykułów",
            percent_estimate=45,
        )
    epub_bytes = result["epub_bytes"]
    pre_heading_repair_epub_bytes = epub_bytes
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
                _emit_status(
                    status_callback,
                    "repairing_headings",
                    "Naprawiam headingi i TOC w EPUB...",
                    stage_id="repairing_toc",
                    stage_label="Naprawa TOC",
                    percent_estimate=65,
                )
            try:
                heading_repair_result = heading_repair_impl(
                    epub_bytes,
                    title_hint=str((result.get("document_summary", {}) or {}).get("title", "") or ""),
                    author_hint=str((result.get("document_summary", {}) or {}).get("author", "") or ""),
                    language_hint=request.language,
                    publication_profile=_resolved_publication_profile(request=request, result=result),
                    already_semantic_cleaned=True,
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
                    epub_bytes, result, heading_repair_report = _apply_heading_quality_selection(
                        result=result,
                        baseline_epub_bytes=pre_heading_repair_epub_bytes,
                        candidate_epub_bytes=heading_repair_result.epub_bytes,
                        candidate_epubcheck=heading_repair_result.epubcheck,
                        request=request,
                        heading_repair_report=heading_repair_report,
                    )
            except Exception as error:
                heading_repair_report["status"] = "failed"
                heading_repair_report["error"] = str(error)

    detected_source_type = str(result.get("source_type", source_type) or source_type)
    if status_callback:
        _emit_status(
            status_callback,
            "running",
            "Uruchamiam audyt premium EPUB...",
            stage_id="premium_audit",
            stage_label="Audyt premium",
            percent_estimate=82,
        )
    result = _apply_runtime_quality_gate(
        result=result,
        epub_bytes=epub_bytes,
        request=request,
        heading_repair_report=heading_repair_report,
    )
    metadata = build_conversion_metadata(
        result=result,
        detected_source_type=detected_source_type,
        heading_repair_enabled=request.heading_repair_enabled,
        heading_repair_report=heading_repair_report,
    )
    if request.feedback_enabled:
        try:
            from ml_feedback import append_conversion_feedback_event

            feedback_record = append_conversion_feedback_event(
                source_path=request.source_path,
                original_filename=request.original_filename,
                source_type=detected_source_type,
                metadata=metadata,
                result=result,
            )
            metadata["ml_feedback"] = {
                "status": "recorded",
                "recommended_record": {
                    "record_id": str(feedback_record.get("record_id", "") or ""),
                    "case_id": str(feedback_record.get("case_id", "") or ""),
                    "quality_label": str((_to_mapping_payload(feedback_record.get("feedback"))).get("quality_label", "") or ""),
                    "quality_score": (_to_mapping_payload(feedback_record.get("feedback"))).get("quality_score"),
                    "issue_tags": list((_to_mapping_payload(feedback_record.get("feedback"))).get("issue_tags") or [])[:12],
                },
                "learning_mode": "feedback_retrain_no_online_updates",
            }
            metadata["feedback_learning"] = {
                "status": "recorded",
                "learning_mode": "feedback_retrain_no_online_updates",
            }
        except Exception as error:
            metadata["ml_feedback"] = {
                "status": "failed",
                "learning_mode": "feedback_retrain_no_online_updates",
                "error": str(error),
            }
            metadata["feedback_learning"] = {
                "status": "failed",
                "learning_mode": "feedback_retrain_no_online_updates",
                "error": str(error),
            }
    return ConversionOutcome(
        result=result,
        epub_bytes=epub_bytes,
        heading_repair_report=heading_repair_report,
        detected_source_type=detected_source_type,
        download_name=request.original_filename.rsplit(".", 1)[0] + ".epub",
        metadata=metadata,
    )
