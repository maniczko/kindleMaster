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
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from flask import Flask, request, jsonify, render_template, send_file
from converter import ConversionConfig, convert_document_to_epub_with_report, detect_pdf_type
from docx_conversion import analyze_docx
from epub_heading_repair import repair_epub_headings_and_toc
from publication_analysis import analyze_publication

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "kindlemaster")
os.makedirs(UPLOAD_DIR, exist_ok=True)

LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 5001
DEFAULT_DEBUG = False
_CONVERSION_JOBS: dict[str, dict] = {}
_CONVERSION_JOBS_LOCK = threading.Lock()


def _encode_header_payload(payload, *, limit: int = 20) -> str:
    if isinstance(payload, list):
        payload = payload[:limit]
    return quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _resolve_server_port() -> int:
    port_raw = os.environ.get("PORT", "").strip()
    if not port_raw:
        return DEFAULT_PORT

    try:
        port = int(port_raw)
    except ValueError:
        return DEFAULT_PORT

    if 1 <= port <= 65535:
        return port
    return DEFAULT_PORT


def _resolve_debug_mode() -> bool:
    debug_raw = os.environ.get("FLASK_DEBUG", os.environ.get("DEBUG", "")).strip().lower()
    if not debug_raw:
        return DEFAULT_DEBUG
    return debug_raw in {"1", "true", "yes", "on"}


def _pick_epubcheck_error(messages) -> str:
    cleaned = [str(message).strip() for message in (messages or []) if str(message).strip()]
    for message in cleaned:
        upper = message.upper()
        if "ERROR(" in upper or upper.startswith("ERROR") or "FATAL(" in upper or upper.startswith("FATAL"):
            return message
    return cleaned[0] if cleaned else "Heading/TOC repair failed."


def _build_conversion_config(profile: str, force_ocr: bool, language: str) -> ConversionConfig:
    return ConversionConfig(
        prefer_fixed_layout=profile == "preserve-layout",
        profile=profile,
        force_ocr=force_ocr,
        language=language,
    )


def _build_conversion_metadata(
    result: dict,
    *,
    source_type: str,
    detected_source_type: str,
    heading_repair_enabled: bool,
    heading_repair_report: dict,
) -> dict:
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
        "heading_repair": {
            "status": str(heading_repair_report.get("status", "skipped" if not heading_repair_enabled else "failed")),
            "release": str(heading_repair_report.get("release_status", "unavailable")),
            "toc_before": int(heading_repair_report.get("toc_entries_before", 0) or 0),
            "toc_after": int(heading_repair_report.get("toc_entries_after", 0) or 0),
            "removed": int(heading_repair_report.get("headings_removed", 0) or 0),
            "review": int(heading_repair_report.get("manual_review_count", 0) or 0),
            "epubcheck": str(heading_repair_report.get("epubcheck_status", "unavailable")),
            "error": str(heading_repair_report.get("error", "")),
        },
    }


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
    if status_callback:
        status_callback("running", f"Konwertuje {source_type.upper()} do EPUB...")
    config = _build_conversion_config(profile, force_ocr, language)
    result = convert_document_to_epub_with_report(
        source_path,
        config=config,
        original_filename=original_filename,
        source_type=source_type,
    )
    epub_bytes = result["epub_bytes"]
    heading_repair_report = {
        "status": "skipped",
        "release_status": "unavailable",
        "toc_entries_before": 0,
        "toc_entries_after": 0,
        "headings_removed": 0,
        "manual_review_count": 0,
        "epubcheck_status": "unavailable",
        "error": "",
    }
    if heading_repair_enabled:
        if status_callback:
            status_callback("repairing_headings", "Naprawiam headingi i TOC w EPUB...")
        try:
            heading_repair_result = repair_epub_headings_and_toc(
                epub_bytes,
                title_hint=str((result.get("document_summary", {}) or {}).get("title", "") or ""),
                author_hint=str((result.get("document_summary", {}) or {}).get("author", "") or ""),
                language_hint=language,
                publication_profile=profile,
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
                heading_repair_report["error"] = _pick_epubcheck_error(
                    heading_repair_result.epubcheck.get("messages", []) or []
                )
            else:
                epub_bytes = heading_repair_result.epub_bytes
        except Exception as heading_repair_error:
            heading_repair_report["status"] = "failed"
            heading_repair_report["error"] = str(heading_repair_error)

    detected_source_type = str(result.get("source_type", source_type) or source_type)
    metadata = _build_conversion_metadata(
        result,
        source_type=source_type,
        detected_source_type=detected_source_type,
        heading_repair_enabled=heading_repair_enabled,
        heading_repair_report=heading_repair_report,
    )
    return {
        "epub_bytes": epub_bytes,
        "download_name": original_filename.rsplit(".", 1)[0] + ".epub",
        "metadata": metadata,
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
            _set_conversion_job(
                job_id,
                status="ready",
                message="EPUB gotowy do pobrania.",
                output_path=output_path,
                download_name=payload["download_name"],
                metadata=payload["metadata"],
                error="",
            )
        except Exception as error:
            _set_conversion_job(
                job_id,
                status="failed",
                message="Konwersja nie powiodla sie.",
                error=str(error),
            )
        finally:
            if os.path.exists(source_path):
                os.remove(source_path)

    thread = threading.Thread(target=_worker, daemon=True, name=f"kindlemaster-convert-{job_id}")
    thread.start()


@app.route("/")
def index():
    template_path = Path(app.root_path) / "templates" / "index.html"
    updated_at = datetime.fromtimestamp(template_path.stat().st_mtime)
    months_pl = [
        "sty", "lut", "mar", "kwi", "maj", "cze",
        "lip", "sie", "wrz", "paz", "lis", "gru",
    ]
    updated_at_label = (
        f"{updated_at.day} {months_pl[updated_at.month - 1]} "
        f"{updated_at.year}, {updated_at:%H:%M:%S}"
    )
    return render_template("index.html", updated_at_label=updated_at_label)


@app.route("/convert", methods=["POST"])
def convert():
    """Convert uploaded PDF or DOCX to EPUB."""
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "Przeslij plik PDF albo DOCX."}), 400

    source_suffix = Path(file.filename).suffix.lower()
    if source_suffix not in {".pdf", ".docx"}:
        return jsonify({"error": "Obslugiwane sa tylko pliki PDF i DOCX."}), 400
    source_type = source_suffix.lstrip(".")

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
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "Przeslij plik PDF albo DOCX."}), 400

    source_suffix = Path(file.filename).suffix.lower()
    if source_suffix not in {".pdf", ".docx"}:
        return jsonify({"error": "Obslugiwane sa tylko pliki PDF i DOCX."}), 400

    profile = request.form.get("profile", "auto-premium")
    force_ocr = request.form.get("ocr", "false") == "true"
    language = request.form.get("language", "pl")
    heading_repair_enabled = request.form.get("heading_repair", "false") == "true"
    source_type = source_suffix.lstrip(".")

    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    file.save(source_path)
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with _CONVERSION_JOBS_LOCK:
        _CONVERSION_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "Plik odebrany. Konwersja zaraz sie rozpocznie.",
            "source_type": source_type,
            "filename": file.filename,
            "created_at": created_at,
            "updated_at": created_at,
            "output_path": "",
            "download_name": file.filename.rsplit(".", 1)[0] + ".epub",
            "metadata": {},
            "error": "",
        }

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

    return jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status": "queued",
            "source_type": source_type,
            "message": "Konwersja wystartowala. Trwa przygotowanie EPUB.",
        }
    ), 202


@app.route("/convert/status/<job_id>", methods=["GET"])
def convert_status(job_id: str):
    job = _get_conversion_job(job_id)
    if not job:
        return jsonify({"error": "Nie znaleziono zadania konwersji."}), 404
    return jsonify(
        {
            "success": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "message": job.get("message", ""),
            "source_type": job.get("source_type", "pdf"),
            "filename": job.get("filename", ""),
            "error": job.get("error", ""),
            "conversion": job.get("metadata", {}) if job.get("status") == "ready" else None,
            "download_url": f"/convert/download/{job_id}" if job.get("status") == "ready" else None,
        }
    )


@app.route("/convert/download/<job_id>", methods=["GET"])
def convert_download(job_id: str):
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
    file = request.files.get("file") or request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "Przeslij plik PDF albo DOCX."}), 400

    source_suffix = Path(file.filename).suffix.lower()
    if source_suffix not in {".pdf", ".docx"}:
        return jsonify({"error": "Obslugiwane sa tylko pliki PDF i DOCX."}), 400

    job_id = uuid.uuid4().hex
    source_path = os.path.join(UPLOAD_DIR, f"{job_id}{source_suffix}")
    file.save(source_path)

    try:
        if source_suffix == ".docx":
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
    print(f"Starting KindleMaster on http://{host}:{port} (debug={debug})", flush=True)
    app.run(debug=debug, host=host, port=port)
