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
from flask import Flask, request, jsonify, render_template, send_file
from converter import convert_document_to_epub_with_report, detect_pdf_type
from docx_conversion import analyze_docx
from epub_heading_repair import repair_epub_headings_and_toc
from publication_analysis import analyze_publication

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "kindlemaster")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_CONVERSION_POLL_INTERVAL_MS = 1500
MAX_CONVERSION_POLL_INTERVAL_MS = 5000
OVERSIZED_EPUB_WARNING_BYTES = 25 * 1024 * 1024
CONVERSION_JOB_RETENTION_SECONDS = 6 * 60 * 60
CONVERSION_TEMP_FILE_RETENTION_SECONDS = 12 * 60 * 60
CONVERSION_CLEANUP_MIN_INTERVAL_SECONDS = 60
ACTIVE_CONVERSION_JOB_STATUSES = {"queued", "running", "repairing_headings"}
_CONVERSION_JOBS: dict[str, dict] = {}
_CONVERSION_JOBS_LOCK = threading.Lock()
_LAST_CONVERSION_CLEANUP_AT: datetime | None = None


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


def _job_download_url(job_id: str, job: dict) -> str | None:
    if job.get("status") == "ready":
        return f"/convert/download/{job_id}"
    return None


def _build_job_quality_state(job_id: str, job: dict) -> dict:
    payload = dict(job)
    output_size_bytes = _read_output_size_bytes(job)
    if output_size_bytes is not None:
        payload["output_size_bytes"] = output_size_bytes
    return build_conversion_quality_state(
        payload,
        download_url=_job_download_url(job_id, job),
    )


def _resolve_request_port_label(host_header: str | None, fallback_port: int) -> str:
    host_value = str(host_header or "").strip()
    if not host_value:
        return str(fallback_port)
    if ":" not in host_value:
        return str(fallback_port)
    return host_value.rsplit(":", 1)[-1]


def _set_conversion_job(job_id: str, **fields) -> dict | None:
    with _CONVERSION_JOBS_LOCK:
        job = _CONVERSION_JOBS.get(job_id)
        if not job:
            return None
        job.update(fields)
        job["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return dict(job)


def _get_conversion_job(job_id: str) -> dict | None:
    with _CONVERSION_JOBS_LOCK:
        job = _CONVERSION_JOBS.get(job_id)
        return dict(job) if job else None


def _parse_job_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _compute_job_elapsed_seconds(job: dict) -> int | None:
    created_at = _parse_job_timestamp(job.get("created_at"))
    if not created_at:
        return None
    return max(0, int((datetime.now(UTC) - created_at).total_seconds()))


def _recommended_poll_interval_ms(job: dict) -> int:
    status = str(job.get("status", "queued") or "queued")
    if status in {"ready", "failed"}:
        return 0

    elapsed_seconds = _compute_job_elapsed_seconds(job) or 0
    if status == "queued":
        return 1200
    if status == "running":
        return MAX_CONVERSION_POLL_INTERVAL_MS
    if elapsed_seconds >= 240:
        return MAX_CONVERSION_POLL_INTERVAL_MS
    if elapsed_seconds >= 120:
        return 3500
    if elapsed_seconds >= 45 or status == "repairing_headings":
        return 2500
    return DEFAULT_CONVERSION_POLL_INTERVAL_MS


def _read_output_size_bytes(job: dict) -> int | None:
    output_size = job.get("output_size_bytes")
    output_path = str(job.get("output_path", "") or "")
    if output_path and os.path.exists(output_path):
        return os.path.getsize(output_path)
    if isinstance(output_size, (int, float)):
        return max(0, int(output_size))
    return None


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
            if not candidate.is_file():
                continue
            resolved_path = _normalize_temp_artifact_path(str(candidate))
            if resolved_path in active_paths:
                continue
            if candidate.suffix.lower() not in {".pdf", ".docx", ".epub"}:
                continue
            modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
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
    status_callback=None,
) -> dict:
    outcome = run_document_conversion(
        ConversionRequest(
            source_path=source_path,
            source_type=source_type,
            original_filename=original_filename,
            profile=profile,
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
) -> None:
    def _worker() -> None:
        output_path = os.path.join(UPLOAD_DIR, f"{job_id}.epub")

        def _status_callback(status: str, message: str) -> None:
            _set_conversion_job(job_id, status=status, message=message)

        try:
            payload = _run_conversion_pipeline(
                source_path=source_path,
                source_type=source_type,
                original_filename=original_filename,
                profile=profile,
                force_ocr=force_ocr,
                language=language,
                heading_repair_enabled=heading_repair_enabled,
                status_callback=_status_callback,
            )
            with open(output_path, "wb") as handle:
                handle.write(payload["epub_bytes"])
            output_size_bytes = os.path.getsize(output_path)
            metadata = _attach_output_size_metadata(payload["metadata"], output_size_bytes)
            _set_conversion_job(
                job_id,
                status="ready",
                message="EPUB gotowy do pobrania.",
                output_path=output_path,
                download_name=payload["download_name"],
                metadata=metadata,
                output_size_bytes=output_size_bytes,
                error="",
            )
        except Exception as error:
            _set_conversion_job(
                job_id,
                status="failed",
                message="Konwersja nie powiodla sie.",
                output_size_bytes=0,
                error=str(error),
            )
        finally:
            if os.path.exists(source_path):
                os.remove(source_path)
            _set_conversion_job(job_id, source_path="")

    thread = threading.Thread(target=_worker, daemon=True, name=f"kindlemaster-convert-{job_id}")
    thread.start()


@app.route("/")
def index():
    template_path = Path(app.root_path) / "templates" / "index.html"
    updated_at = datetime.fromtimestamp(template_path.stat().st_mtime)
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


@app.route("/convert", methods=["POST"])
def convert():
    """Convert uploaded PDF or DOCX to EPUB."""
    _cleanup_expired_conversion_jobs()
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "Przeslij plik PDF albo DOCX."}), 400

    source_type = detect_supported_source_type(file.filename)
    if not source_type:
        return jsonify({"error": "Obslugiwane sa tylko pliki PDF i DOCX."}), 400
    source_suffix = f".{source_type}"

    # Get conversion preferences from form
    profile = request.form.get("profile", "auto-premium")
    force_ocr = request.form.get("ocr", "false") == "true"
    language = request.form.get("language", "pl")
    heading_repair_enabled = request.form.get("heading_repair", "false") == "true"

    # Save uploaded file temporarily
    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    file.save(source_path)

    try:
        payload = _run_conversion_pipeline(
            source_path=source_path,
            source_type=source_type,
            original_filename=file.filename,
            profile=profile,
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
        return jsonify({
            "error": f"Konwersja nie powiodla sie: {str(e)}",
        }), 500
    finally:
        # Clean up
        if os.path.exists(source_path):
            os.remove(source_path)


@app.route("/convert/start", methods=["POST"])
def convert_start():
    _cleanup_expired_conversion_jobs()
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "Przeslij plik PDF albo DOCX."}), 400

    source_type = detect_supported_source_type(file.filename)
    if not source_type:
        return jsonify({"error": "Obslugiwane sa tylko pliki PDF i DOCX."}), 400
    source_suffix = f".{source_type}"

    profile = request.form.get("profile", "auto-premium")
    force_ocr = request.form.get("ocr", "false") == "true"
    language = request.form.get("language", "pl")
    heading_repair_enabled = request.form.get("heading_repair", "false") == "true"
    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    file.save(source_path)
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with _CONVERSION_JOBS_LOCK:
        _CONVERSION_JOBS[job_id] = build_conversion_job_record(
            job_id=job_id,
            source_path=source_path,
            source_type=source_type,
            filename=file.filename,
            created_at=created_at,
        )

    _spawn_conversion_job(
        job_id=job_id,
        source_path=source_path,
        source_type=source_type,
        original_filename=file.filename,
        profile=profile,
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
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/status/<job_id>", methods=["GET"])
def convert_status(job_id: str):
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return jsonify({"error": "Nie znaleziono zadania konwersji."}), 404
    download_url = _job_download_url(job_id, job)
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
            "conversion": conversion_payload,
            "download_url": download_url,
            "poll_after_ms": _recommended_poll_interval_ms(job),
            "elapsed_seconds": _compute_job_elapsed_seconds(job),
            "output_size_bytes": _read_output_size_bytes(job) if job.get("status") == "ready" else None,
            "quality_state": _build_job_quality_state(job_id, job),
            "quality_state_url": f"/convert/quality/{job_id}",
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/quality/<job_id>", methods=["GET"])
def convert_quality(job_id: str):
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return jsonify({"error": "Nie znaleziono zadania konwersji."}), 404

    response = jsonify(
        {
            "success": True,
            "job_id": job["job_id"],
            "quality_state": _build_job_quality_state(job_id, job),
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/convert/download/<job_id>", methods=["GET"])
def convert_download(job_id: str):
    _cleanup_expired_conversion_jobs()
    job = _get_conversion_job(job_id)
    if not job:
        return jsonify({"error": "Nie znaleziono zadania konwersji."}), 404
    if job.get("status") != "ready":
        return jsonify({"error": "EPUB nie jest jeszcze gotowy do pobrania."}), 409

    output_path = job.get("output_path", "")
    if not output_path or not os.path.exists(output_path):
        _set_conversion_job(job_id, status="failed", error="Brak pliku EPUB do pobrania.")
        return jsonify({"error": "Brak pliku EPUB do pobrania."}), 500

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
