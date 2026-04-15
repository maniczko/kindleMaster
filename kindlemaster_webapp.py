from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

import fitz
from flask import Flask, abort, jsonify, render_template, request, send_file

from kindlemaster_end_to_end import RELEASE_CANDIDATE_DIR, run_end_to_end
from kindlemaster_manifest import list_publications
from kindlemaster_pdf_analysis import classify_pdf
from kindlemaster_quality_score import score_epub
from kindlemaster_versioning import build_identity, read_display_version


ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "kindlemaster_templates"
RUNTIME_ROOT = ROOT / "kindlemaster_runtime"
UPLOAD_DIR = RUNTIME_ROOT / "uploads"
OUTPUT_ROOT = RUNTIME_ROOT / "output"
BASELINE_DIR = OUTPUT_ROOT / "baseline_epub"
FINAL_DIR = OUTPUT_ROOT / "final_epub"
REPORT_DIR = OUTPUT_ROOT / "reports"
SERVER_STARTED_AT = datetime.now()
CORPUS_QUALITY_REPORT_PATH = ROOT / "project_control" / "phase12_corpus_release_gate_enforcement.json"
BACKLOG_PATH = ROOT / "project_control" / "backlog.yaml"
QUALITY_LOOP_STATE_CANDIDATES = [
    ROOT / "kindlemaster_runtime" / "quality_loop_state.json",
    OUTPUT_ROOT / "quality_loop_state.json",
    REPORT_DIR / "quality_loop_state.json",
    ROOT / "project_control" / "quality_loop_state.json",
    ROOT / ".codex" / "quality_loop_state.json",
]

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def allowed_download(rel_path: str) -> Path | None:
    candidate = (ROOT / rel_path).resolve()
    allowed_roots = [
        RUNTIME_ROOT.resolve(),
        (ROOT / "samples").resolve(),
    ]
    if any(str(candidate).startswith(str(base)) for base in allowed_roots) and candidate.exists():
        return candidate
    return None


def safe_pdf_name(filename: str) -> str:
    stem = Path(filename or "input.pdf").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return (stem or "input") + ".pdf"


def which(executable: str) -> str | None:
    return shutil.which(executable)


def package_version() -> str:
    return read_display_version(ROOT / "VERSION")


def git_commit_short() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=ROOT,
        )
        value = (completed.stdout or "").strip()
        return value or "nogit"
    except Exception:
        return "nogit"


def current_build_info() -> dict:
    template_path = TEMPLATE_DIR / "index.html"
    updated_at = datetime.fromtimestamp(template_path.stat().st_mtime)
    months_pl = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paz", "lis", "gru"]
    updated_at_label = f"{updated_at.day} {months_pl[updated_at.month - 1]} {updated_at.year}, {updated_at:%H:%M:%S}"
    version = package_version()
    commit = git_commit_short()
    started_at = SERVER_STARTED_AT.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "version": version,
        "commit": commit,
        "build_label": build_identity(version, commit),
        "template_updated_at_label": updated_at_label,
        "server_started_at_label": started_at,
    }


def tesseract_languages() -> list[str]:
    tesseract = which("tesseract")
    if not tesseract:
        return []
    try:
        completed = subprocess.run(
            [tesseract, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if lines and "list of available languages" in lines[0].lower():
        lines = lines[1:]
    return lines[:20]


def quick_pdf_stats(pdf_path: Path) -> dict:
    base = classify_pdf(pdf_path)
    with fitz.open(pdf_path) as doc:
        image_pages = 0
        total_images = 0
        high_risk_pages: list[dict] = []
        for index, page in enumerate(doc, start=1):
            page_images = page.get_images(full=True)
            text = page.get_text("text").strip()
            if page_images:
                image_pages += 1
                total_images += len(page_images)
            if len(text) < 120 or len(page_images) >= 3:
                flags: list[str] = []
                if len(text) < 120:
                    flags.append("low_text_density")
                if len(page_images) >= 3:
                    flags.append("image_dense")
                if base["profile"] in {"mixed-layout", "ocr-risky", "image-heavy"}:
                    flags.append("layout_sensitive")
                high_risk_pages.append(
                    {
                        "page": index,
                        "title": f"Page {index}",
                        "kind": "page",
                        "flags": flags,
                    }
                )
        page_count = doc.page_count

    has_text_layer = base["pages_with_text"] > 0
    recommended_strategy = {
        "text-heavy": "text_reflowable",
        "mixed-layout": "hybrid_reflow",
        "ocr-risky": "ocr_reflow",
        "image-heavy": "preserve_layout",
    }.get(base["profile"], "text_reflowable")

    return {
        "file": base["file"],
        "path": base["path"],
        "page_count": base["page_count"],
        "text_pages": base["pages_with_text"],
        "image_pages": image_pages,
        "scanned_pages": max(page_count - base["pages_with_text"], 0),
        "has_text_layer": has_text_layer,
        "has_images": total_images > 0,
        "layout_heavy": base["profile"] in {"mixed-layout", "image-heavy"},
        "text_heavy": base["profile"] == "text-heavy",
        "is_scanned": base["profile"] in {"ocr-risky", "image-heavy"} and not has_text_layer,
        "recommended_strategy": recommended_strategy,
        "legacy_strategy": recommended_strategy,
        "profile": base["profile"],
        "confidence": 0.84 if base["profile"] == "text-heavy" else 0.72,
        "title": base["title"],
        "author": base["author"],
        "risks": base["risks"],
        "high_risk_pages": high_risk_pages[:20],
    }


def build_publication_analysis(analysis: dict) -> dict:
    profile = analysis["profile"]
    if profile == "text-heavy":
        publication_profile = "book_reflow"
        reason = "Dominuje warstwa tekstowa, wiec bezpieczny reflow jest preferowany."
        fallback = "preserve-layout"
        ui_profile = "book"
        recommended_profile = "Book"
    elif profile == "mixed-layout":
        publication_profile = "magazine_reflow"
        reason = "Dokument ma cechy mieszane, wiec potrzebuje ostroznego reflow z kontrola sekcji specjalnych."
        fallback = "preserve-layout"
        ui_profile = "magazine"
        recommended_profile = "Magazine"
    elif profile == "ocr-risky":
        publication_profile = "scanned_reflow"
        reason = "Slaba warstwa tekstowa sugeruje skan lub OCR-risk, wiec pipeline musi zachowac ostroznosc."
        fallback = "technical-study"
        ui_profile = "technical-study"
        recommended_profile = "Technical/Study"
    else:
        publication_profile = "fixed_layout_fallback"
        reason = "Dokument jest mocno obrazkowy, wiec preserve-layout pozostaje bezpiecznym fallbackiem."
        fallback = "preserve-layout"
        ui_profile = "preserve-layout"
        recommended_profile = "Preserve Layout"

    java_path = which("java")
    tesseract_path = which("tesseract")
    ocrmypdf_path = which("ocrmypdf")
    surya_path = which("surya")
    pdfplumber_available = bool(importlib.util.find_spec("pdfplumber"))

    publication = {
        "profile": publication_profile,
        "profile_reason": reason,
        "fallback_recommendation": fallback,
        "confidence": analysis.get("confidence", 0.72),
        "has_toc": analysis["page_count"] > 20,
        "has_tables": False,
        "has_diagrams": analysis["image_pages"] > 0,
        "has_meaningful_images": analysis["image_pages"] > 0,
        "estimated_columns": 2 if analysis["layout_heavy"] else 1,
        "estimated_sections": max(1, min(24, round(analysis["page_count"] / 8))),
        "ui_profile": ui_profile,
        "recommended_profile": recommended_profile,
        "external_tools": {
            "commands": {
                "java": bool(java_path),
                "tesseract": bool(tesseract_path),
                "ocrmypdf": bool(ocrmypdf_path),
                "surya": bool(surya_path),
            },
            "java": {
                "found": bool(java_path),
                "path": java_path,
            },
            "tesseract": {
                "path": tesseract_path,
                "languages": tesseract_languages() if tesseract_path else [],
            },
            "epubcheck": {
                "jar_found": False,
                "jar_path": None,
            },
            "pdfbox": {
                "jar_found": False,
                "jar_path": None,
            },
            "python_modules": {
                "pdfplumber": pdfplumber_available,
            },
        },
    }
    return publication


def encode_header_payload(payload) -> str:
    return quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _append_unique(items: list[str], value: str | None, *, limit: int = 5) -> None:
    if not value or value in items or len(items) >= limit:
        return
    items.append(value)


def build_premium_report(score_report: dict) -> dict:
    core_report = score_report.get("premium_report") if isinstance(score_report.get("premium_report"), dict) else None
    if core_report is not None:
        verdict = str(core_report.get("verdict") or "NEEDS_WORK").upper()
        ui_verdict = "premium" if verdict in {"PREMIUM_STRONG", "PREMIUM_PASS"} else "needs-work"
        return {
            "score_1_10": core_report.get("score_1_10", score_report.get("weighted_score")),
            "premium_target": core_report.get("premium_target", score_report.get("premium_target", 8.8)),
            "premium_gap": core_report.get("premium_gap", score_report.get("premium_gap")),
            "verdict": ui_verdict,
            "what_is_good": list(core_report.get("what_is_good") or []),
            "what_is_bad": list(core_report.get("what_is_bad") or []),
            "strengths": list(core_report.get("top_strengths") or core_report.get("strengths") or []),
            "risks": list(core_report.get("top_risks") or core_report.get("risks") or []),
        }

    smoke = score_report.get("smoke") if isinstance(score_report.get("smoke"), dict) else {}
    checks = smoke.get("checks") if isinstance(smoke.get("checks"), dict) else {}
    counts = score_report.get("text_audit") if isinstance(score_report.get("text_audit"), dict) else {}
    front_matter = score_report.get("front_matter") if isinstance(score_report.get("front_matter"), dict) else {}
    typography = score_report.get("typography") if isinstance(score_report.get("typography"), dict) else {}
    weighted_score = float(score_report.get("weighted_score") or 0.0)
    target = float(score_report.get("premium_target") or 8.8)
    gap = float(score_report.get("premium_gap") or max(0.0, target - weighted_score))

    what_is_good: list[str] = []
    what_is_bad: list[str] = []
    material_issues: list[str] = []

    if checks.get("valid_nav_paths") and checks.get("valid_ncx_paths") and checks.get("valid_anchors"):
        _append_unique(what_is_good, "Nawigacja, NCX i anchory sa poprawne.")
    else:
        _append_unique(what_is_bad, "Nawigacja lub anchory nadal wymagaja poprawy.")
        _append_unique(material_issues, "nawigacja")

    if checks.get("no_title_author_lead_page_merge") and checks.get("special_sections_not_articles"):
        _append_unique(what_is_good, "Tytul, autor, lead i sekcje specjalne pozostaja rozdzielone.")
    else:
        _append_unique(what_is_bad, "Struktura semantyczna nadal miesza tytul, autora, lead albo sekcje specjalne.")
        _append_unique(material_issues, "semantyka")

    if (
        int(counts.get("split_word_count") or 0) == 0
        and int(counts.get("joined_word_count") or 0) == 0
        and int(counts.get("boundary_count") or 0) == 0
    ):
        _append_unique(what_is_good, "Artefakty tekstowe sa niskie i nie deformuja czytania.")
    else:
        _append_unique(what_is_bad, "W tekscie nadal pozostaja split/joined/boundary artefakty.")
        _append_unique(material_issues, "tekst")

    if front_matter.get("distinctness_pass"):
        _append_unique(what_is_good, "Front matter i sekcje organizacyjne sa odseparowane od tresci glownych artykulow.")
    else:
        _append_unique(what_is_bad, "Front matter lub sekcje organizacyjne nadal zanieczyszczaja flow artykulow.")
        _append_unique(material_issues, "front matter")

    if typography.get("ux_pass"):
        _append_unique(what_is_good, "Typografia i reading flow sa Kindle-safe.")
    else:
        _append_unique(what_is_bad, "Typografia lub reading flow nadal wymagaja dopracowania.")
        _append_unique(material_issues, "ux")

    if checks.get("no_opaque_or_slug_human_title") and checks.get("creator_not_unknown_for_release"):
        _append_unique(what_is_good, "Metadane release sa czytelne i nie wygladaja jak surowy slug.")
    else:
        _append_unique(what_is_bad, "Metadane release nadal wygladaja na niepelne albo techniczne.")
        _append_unique(material_issues, "metadata")

    if gap > 0:
        _append_unique(what_is_bad, f"Quality score jest ponizej targetu premium o {gap:.2f} pkt.")
        _append_unique(material_issues, "score_gap")

    if not what_is_good:
        what_is_good.append("Brak wyraznych mocnych stron do podkreślenia.")
    if not what_is_bad:
        what_is_bad.append("Brak istotnych slabych punktow w tym runie.")

    verdict = "premium" if weighted_score >= target and not material_issues else "needs-work"
    return {
        "score_1_10": round(weighted_score, 2),
        "premium_target": round(target, 2),
        "premium_gap": round(gap, 2),
        "verdict": verdict,
        "what_is_good": what_is_good,
        "what_is_bad": what_is_bad,
        "strengths": what_is_good[:3],
        "risks": what_is_bad[:3],
    }


def _read_json_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_latest_release_report() -> tuple[Path | None, dict | None]:
    if not REPORT_DIR.exists():
        return None, None
    report_paths = sorted(REPORT_DIR.glob("*-end-to-end.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    first_report: tuple[Path | None, dict | None] = (None, None)
    valid_nonrelease_fallback: tuple[Path | None, dict | None] = (None, None)
    for report_path in report_paths:
        report = _read_json_file(report_path)
        if not isinstance(report, dict):
            continue
        if first_report == (None, None):
            first_report = (report_path, report)
        final_validation = report.get("final_validation") or {}
        release_gate = report.get("release_gate") or {}
        final_epub = report.get("final_epub")
        if (
            final_validation.get("pass")
            and release_gate.get("release_eligible")
            and final_epub
            and Path(final_epub).exists()
        ):
            return report_path, report
        if valid_nonrelease_fallback == (None, None) and final_validation.get("pass") and final_epub and Path(final_epub).exists():
            valid_nonrelease_fallback = (report_path, report)
    if valid_nonrelease_fallback != (None, None):
        return valid_nonrelease_fallback
    return first_report


def _find_quality_loop_state() -> tuple[Path | None, dict | None]:
    for candidate in QUALITY_LOOP_STATE_CANDIDATES:
        if candidate.exists():
            state = _read_json_file(candidate)
            if isinstance(state, dict):
                return candidate, state
    return None, None


def _active_backlog_task_ids() -> set[str]:
    try:
        import yaml

        payload = yaml.safe_load(BACKLOG_PATH.read_text(encoding="utf-8")) or {}
        return {str(task.get("id")) for task in payload.get("tasks", []) if task.get("id")}
    except Exception:
        return set()


def _summarize_quality_loop_state(state: dict | None) -> dict:
    if not state:
        return {
            "found": False,
            "iteration_id": None,
            "candidate_build": None,
            "candidate_status": None,
            "mutation_lane": None,
            "promotion_decision": None,
            "selected_task": None,
            "blockers": [],
        }

    candidate = state.get("candidate") if isinstance(state.get("candidate"), dict) else {}
    selected_task = state.get("selected_issue_or_task") or state.get("selected_task") or state.get("task")
    active_backlog_task_ids = _active_backlog_task_ids()
    active_backlog_match = bool(selected_task and selected_task in active_backlog_task_ids)
    state_kind = str(state.get("state_kind") or state.get("repo_mode") or "").strip() or None
    if selected_task and not active_backlog_match and not state_kind:
        state_kind = "stale_task_reference"
    return {
        "found": True,
        "iteration_id": state.get("iteration_id") or state.get("run_id") or state.get("id"),
        "candidate_build": candidate.get("build_label")
        or candidate.get("build")
        or state.get("candidate_build")
        or state.get("candidate_version"),
        "candidate_status": candidate.get("status")
        or state.get("candidate_status")
        or state.get("promotion_decision")
        or state.get("decision"),
        "mutation_lane": state.get("mutation_lane") or state.get("lane"),
        "promotion_decision": state.get("promotion_decision") or state.get("decision"),
        "selected_task": selected_task,
        "active_backlog_match": active_backlog_match,
        "state_kind": state_kind,
        "pytest_suite_count": state.get("pytest_suite_count"),
        "blockers": state.get("blockers") if isinstance(state.get("blockers"), list) else [],
    }


def _build_corpus_quality_summary() -> dict:
    payload = _read_json_file(CORPUS_QUALITY_REPORT_PATH)
    if not isinstance(payload, dict):
        return {
            "found": False,
            "verdict": "UNKNOWN",
            "quality_first": False,
            "publications_total": 0,
            "release_eligible_total": 0,
            "text_first_pass_total": 0,
            "unjustified_fallback_total": 0,
            "publications": [],
            "blockers": [],
            "report_path": None,
        }

    publications = []
    for item in payload.get("publications", []):
        if not isinstance(item, dict):
            continue
        publications.append(
            {
                "publication_id": item.get("publication_id"),
                "profile": item.get("profile"),
                "release_eligible": bool(item.get("release_eligible")),
                "quality_score": item.get("quality_score"),
                "quality_target": item.get("quality_target"),
                "premium_gap": item.get("premium_gap"),
                "text_first_pass": bool(item.get("text_first_pass")),
                "fallback_count": int(item.get("fallback_count") or 0),
                "unjustified_fallback_count": int(item.get("unjustified_fallback_count") or 0),
                "coverage_pass": bool(item.get("coverage_pass")),
                "coverage_ratio": item.get("coverage_ratio"),
                "verdict": item.get("verdict"),
                "what_is_good": list(item.get("what_is_good") or []),
                "what_is_bad": list(item.get("what_is_bad") or []),
                "effective_failed_checks": list(item.get("effective_failed_checks") or []),
            }
        )

    verdict = str(payload.get("verdict") or "UNKNOWN").upper()
    return {
        "found": True,
        "verdict": verdict,
        "quality_first": bool(payload.get("quality_first")),
        "publications_total": int(payload.get("publications_total") or len(publications)),
        "release_eligible_total": int(
            payload.get("release_eligible_total")
            or sum(1 for publication in publications if publication["release_eligible"])
        ),
        "text_first_pass_total": int(
            payload.get("text_first_pass_total")
            or sum(1 for publication in publications if publication["text_first_pass"])
        ),
        "unjustified_fallback_total": int(
            payload.get("unjustified_fallback_total")
            or sum(int(publication["unjustified_fallback_count"]) for publication in publications)
        ),
        "publications": publications,
        "blockers": list(payload.get("blockers") or []),
        "report_path": rel(CORPUS_QUALITY_REPORT_PATH),
        "generated_at": payload.get("generated_at"),
    }


def _build_quality_state() -> dict:
    report_path, report = _find_latest_release_report()
    loop_path, loop_state = _find_quality_loop_state()
    loop_summary = _summarize_quality_loop_state(loop_state)
    corpus_summary = _build_corpus_quality_summary()

    accepted = {
        "publication_id": None,
        "artifact_path": None,
        "artifact_relative": None,
        "artifact_exists": False,
        "artifact_status": "missing",
        "validation_pass": False,
        "release_eligible": False,
        "score": None,
        "premium_gap": None,
        "report_path": None,
        "blockers": [],
    }

    if isinstance(report, dict):
        publication = report.get("publication") if isinstance(report.get("publication"), dict) else {}
        final_epub = report.get("final_epub")
        final_validation = report.get("final_validation") if isinstance(report.get("final_validation"), dict) else {}
        release_gate = report.get("release_gate") if isinstance(report.get("release_gate"), dict) else {}
        accepted["publication_id"] = publication.get("publication_id")
        accepted["artifact_path"] = rel(Path(final_epub)) if final_epub else None
        accepted["artifact_relative"] = rel(Path(final_epub)) if final_epub else None
        accepted["artifact_exists"] = bool(final_epub and Path(final_epub).exists())
        accepted["artifact_status"] = "ready" if accepted["artifact_exists"] and final_validation.get("pass") else "needs-attention"
        accepted["validation_pass"] = bool(final_validation.get("pass"))
        accepted["release_eligible"] = bool(release_gate.get("release_eligible"))
        accepted["blockers"] = release_gate.get("blockers") if isinstance(release_gate.get("blockers"), list) else []
        accepted["report_path"] = rel(report_path) if report_path else None

        if final_epub and Path(final_epub).exists():
            try:
                score_report = (
                    report.get("quality_assessment")
                    if isinstance(report.get("quality_assessment"), dict)
                    else score_epub(Path(final_epub), publication_id=accepted["publication_id"])
                )
                accepted["score"] = score_report.get("weighted_score")
                accepted["premium_gap"] = score_report.get("premium_gap")
                accepted["premium_report"] = build_premium_report(score_report)
            except Exception:
                accepted["score"] = None
                accepted["premium_gap"] = None
                accepted["premium_report"] = None

    blockers: list[str] = list(accepted["blockers"])
    if not accepted["artifact_exists"]:
        blockers.append("Accepted final EPUB is missing from the current output path.")
    if not accepted["validation_pass"]:
        blockers.append("Accepted final EPUB does not have a passing final validation report.")
    if accepted["score"] is None:
        blockers.append("Current quality score is unavailable for the accepted final EPUB.")
    if corpus_summary["found"] and corpus_summary["verdict"] != "PASS":
        blockers.append("Corpus-wide quality-first gate is not passing.")
    if corpus_summary["found"] and corpus_summary["unjustified_fallback_total"] > 0:
        blockers.append("Corpus-wide unjustified screenshot or page-image fallback remains.")

    repository_truth = {
        "status": "READY"
        if accepted["validation_pass"] and accepted["release_eligible"] and accepted["score"] is not None and not blockers
        else "NOT_READY",
        "quality_mode": "quality_first_corpus_gate" if corpus_summary["found"] else "single_publication_only",
        "corpus_gate_verdict": corpus_summary["verdict"],
    }

    return {
        "repository_truth": repository_truth,
        "accepted": accepted,
        "quality": {
            "score": accepted["score"],
            "premium_target": 8.8,
            "premium_gap": accepted["premium_gap"],
            "available": accepted["score"] is not None,
            "premium_report": accepted.get("premium_report"),
        },
        "corpus": corpus_summary,
        "loop_state": {
            "path": rel(loop_path) if loop_path else None,
            **loop_summary,
        },
        "release_blockers": blockers,
        "report_source": rel(report_path) if report_path else None,
    }


def save_uploaded_pdf(file_storage) -> tuple[Path, str]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = safe_pdf_name(file_storage.filename or "input.pdf")
    stored_name = f"{uuid.uuid4().hex[:8]}-{safe_name}"
    stored_path = UPLOAD_DIR / stored_name
    file_storage.save(stored_path)
    return stored_path, safe_name


@app.route("/")
def index():
    build = current_build_info()
    manifest_publications = [
        {
            "publication_id": publication.get("publication_id"),
            "profile": publication.get("profile"),
            "release_eligible": bool((publication.get("status") or {}).get("release_eligible")),
        }
        for publication in list_publications()
    ]
    response = app.make_response(render_template("index.html", build=build, manifest_publications=manifest_publications))
    response.headers["X-KM-App-Version"] = build["version"]
    response.headers["X-KM-App-Commit"] = build["commit"]
    response.headers["X-KM-App-Build"] = build["build_label"]
    return response


@app.route("/version")
def version():
    build = current_build_info()
    return jsonify(
        {
            "project": "kindle-master",
            "version": build["version"],
            "commit": build["commit"],
            "build_label": build["build_label"],
            "server_started_at": build["server_started_at_label"],
            "template_updated_at": build["template_updated_at_label"],
            "runtime_root": rel(RUNTIME_ROOT),
            "release_candidate_root": rel(RELEASE_CANDIDATE_DIR),
        }
    )


@app.route("/quality-state")
def quality_state():
    build = current_build_info()
    state = _build_quality_state()
    state["build"] = build
    response = jsonify(state)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/download")
def download():
    rel_path = request.args.get("path", "")
    target = allowed_download(unquote(rel_path))
    if target is None:
        abort(404)
    return send_file(target, as_attachment=True, download_name=target.name)


@app.route("/analyze", methods=["POST"])
def analyze():
    file = request.files.get("pdf")
    if not file or not (file.filename or "").lower().endswith(".pdf"):
        return jsonify({"error": "Przeslij plik PDF."}), 400

    stored_path, _ = save_uploaded_pdf(file)
    publication_id = (request.form.get("publication_id") or "").strip() or None
    try:
        analysis = quick_pdf_stats(stored_path)
        publication = build_publication_analysis(analysis)
        build = current_build_info()
        recommendations = {
            "fixed_layout": {
                "recommended": analysis["profile"] == "image-heavy",
                "reason": "Fixed-layout ma sens dla mocno obrazkowych lub skanowanych dokumentow.",
            },
            "reflowable": {
                "recommended": analysis["profile"] in {"text-heavy", "mixed-layout"},
                "reason": "Reflowable EPUB daje najlepsza czytelnosc na Kindle, jesli tekst jest dostepny.",
            },
            "ocr_needed": {
                "required": analysis["profile"] in {"ocr-risky", "image-heavy"} and not analysis["has_text_layer"],
                "reason": "OCR jest potrzebny tylko wtedy, gdy warstwa tekstowa jest zbyt slaba lub nie istnieje.",
            },
        }
        response = jsonify(
            {
                "success": True,
                "filename": file.filename,
                "build": build,
                "publication_id": publication_id,
                "analysis": analysis,
                "publication_analysis": publication,
                "recommended_profile": publication["recommended_profile"],
                "recommendations": recommendations,
            }
        )
        response.headers["X-KM-App-Build"] = build["build_label"]
        return response
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/convert", methods=["POST"])
def convert():
    file = request.files.get("pdf")
    if not file or not (file.filename or "").lower().endswith(".pdf"):
        return jsonify({"error": "Przeslij plik PDF."}), 400

    stored_path, safe_name = save_uploaded_pdf(file)
    publication_id = (request.form.get("publication_id") or "").strip() or None
    profile = (request.form.get("profile") or "auto-premium").strip() or "auto-premium"
    force_ocr = (request.form.get("ocr") or "").strip().lower() == "true"
    release_mode = (request.form.get("release_mode") or "").strip().lower() == "true"
    language = (request.form.get("language") or "").strip() or None

    try:
        analysis = quick_pdf_stats(stored_path)
        publication = build_publication_analysis(analysis)
        report = run_end_to_end(
            stored_path,
            publication_id=publication_id,
            language=language,
            profile=profile,
            force_ocr=force_ocr,
            release_mode=release_mode,
        )
        build = current_build_info()
        final_path = Path(report["final_epub"]).resolve()
        baseline_path = Path(report["baseline_epub"]).resolve()
        report_path = REPORT_DIR / f"{stored_path.stem}-end-to-end.json"
        coverage = report.get("coverage") or {}
        conversion_options = report.get("conversion_options") or {}

        warning_list = list(analysis.get("risks", []))
        warning_list.extend(conversion_options.get("warnings") or [])
        high_risk_pages = analysis.get("high_risk_pages", [])
        high_risk_sections = []
        if analysis["layout_heavy"]:
            high_risk_sections.append(
                {
                    "title": "Sekcje layout-sensitive",
                    "pages": [1, analysis["page_count"]],
                    "flags": ["layout_sensitive", "manual_review_recommended"],
                }
            )

        response = send_file(
            final_path,
            mimetype="application/epub+zip",
            as_attachment=True,
            download_name=safe_name.replace(".pdf", ".epub"),
        )
        response.headers["X-PDF-Type"] = analysis["recommended_strategy"]
        response.headers["X-Publication-Profile"] = publication["profile"]
        response.headers["X-Publication-Confidence"] = f"{publication['confidence']:.2f}"
        response.headers["X-EPUB-Validation"] = "passed" if report["final_validation"]["pass"] else "failed"
        response.headers["X-EPUB-Validation-Tool"] = "internal-zip-structure"
        response.headers["X-Publication-Sections"] = str(publication["estimated_sections"])
        response.headers["X-Publication-Assets"] = str(report["baseline_report"]["image_fallback_pages"])
        response.headers["X-Publication-Layout"] = "mixed" if analysis["layout_heavy"] else "reflowable"
        response.headers["X-Publication-Warnings"] = str(len(warning_list))
        response.headers["X-Publication-HighRiskPages"] = str(len(high_risk_pages))
        response.headers["X-Publication-HighRiskSections"] = str(len(high_risk_sections))
        response.headers["X-Publication-Warning-List"] = encode_header_payload(warning_list)
        response.headers["X-Publication-HighRiskPageList"] = encode_header_payload(high_risk_pages)
        response.headers["X-Publication-HighRiskSectionList"] = encode_header_payload(high_risk_sections)
        response.headers["X-KM-Baseline-Path"] = quote(rel(baseline_path))
        response.headers["X-KM-Final-Path"] = quote(rel(final_path))
        response.headers["X-KM-Report-Path"] = quote(rel(report_path))
        response.headers["X-KM-Source-PDF"] = quote(rel(stored_path))
        response.headers["X-KM-Publication-Id"] = report["publication"].get("publication_id") or ""
        response.headers["X-KM-Manifest-Matched"] = "true" if report["publication"].get("manifest_matched") else "false"
        response.headers["X-KM-Release-Mode"] = "true" if release_mode else "false"
        response.headers["X-KM-Release-Eligible"] = "true" if report["release_gate"]["release_eligible"] else "false"
        response.headers["X-KM-Release-Blockers"] = encode_header_payload(report["release_gate"]["blockers"])
        response.headers["X-KM-Release-Candidate-Path"] = quote(report.get("release_candidate_epub_relative") or "")
        response.headers["X-KM-Source-Pages"] = str(coverage.get("source_pdf_page_count") or "")
        response.headers["X-KM-Baseline-Pages"] = str(coverage.get("baseline_page_records") or "")
        response.headers["X-KM-Final-Pages"] = str(coverage.get("final_page_documents") or "")
        response.headers["X-KM-Page-Coverage-Pass"] = "true" if coverage.get("coverage_pass") else "false"
        response.headers["X-KM-Page-Coverage-Ratio"] = str(coverage.get("coverage_ratio") or "")
        response.headers["X-KM-Text-Pages"] = str(report["baseline_report"].get("text_pages") or 0)
        response.headers["X-KM-Hybrid-Pages"] = str(report["baseline_report"].get("hybrid_pages") or 0)
        response.headers["X-KM-Image-Pages"] = str(report["baseline_report"].get("image_fallback_pages") or 0)
        response.headers["X-KM-Text-First-Pages"] = str(report["baseline_report"].get("text_first_pages") or 0)
        response.headers["X-KM-Justified-Fallback-Pages"] = str(
            report["baseline_report"].get("justified_fallback_pages") or 0
        )
        response.headers["X-KM-Unjustified-Fallback-Pages"] = str(
            report["baseline_report"].get("unjustified_fallback_pages") or 0
        )
        response.headers["X-KM-Profile-Requested"] = str(conversion_options.get("profile_requested") or "")
        response.headers["X-KM-Profile-Applied"] = str(conversion_options.get("profile_applied") or "")
        response.headers["X-KM-OCR-Requested"] = "true" if conversion_options.get("force_ocr_requested") else "false"
        response.headers["X-KM-OCR-Applied"] = "true" if conversion_options.get("force_ocr_applied") else "false"
        response.headers["X-KM-Conversion-Duration-Ms"] = str(report.get("conversion_duration_ms") or "")
        try:
            premium_report = build_premium_report(
                report.get("quality_assessment")
                if isinstance(report.get("quality_assessment"), dict)
                else score_epub(final_path, publication_id=report["publication"].get("publication_id") or publication_id)
            )
        except Exception:
            premium_report = {
                "score_1_10": None,
                "premium_target": 8.8,
                "premium_gap": None,
                "verdict": "unavailable",
                "what_is_good": [],
                "what_is_bad": ["Premium audit failed to compute for this run."],
                "strengths": [],
                "risks": ["Premium audit failed to compute for this run."],
            }
        response.headers["X-KM-Premium-Score"] = "" if premium_report["score_1_10"] is None else f"{premium_report['score_1_10']:.2f}"
        response.headers["X-KM-Premium-Verdict"] = str(premium_report["verdict"])
        response.headers["X-KM-Premium-Target"] = f"{premium_report['premium_target']:.2f}"
        response.headers["X-KM-Premium-Gap"] = "" if premium_report["premium_gap"] is None else f"{premium_report['premium_gap']:.2f}"
        response.headers["X-KM-Premium-Report"] = encode_header_payload(premium_report)
        response.headers["X-KM-Text-First-Pass"] = "true" if premium_report["verdict"] == "premium" or (
            isinstance(report.get("quality_assessment"), dict) and report["quality_assessment"].get("text_first_pass")
        ) else "false"
        response.headers["X-KM-App-Version"] = build["version"]
        response.headers["X-KM-App-Commit"] = build["commit"]
        response.headers["X-KM-App-Build"] = build["build_label"]
        return response
    except Exception as exc:
        return jsonify({"error": f"Konwersja nie powiodla sie: {exc}"}), 500


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kindle Master local webapp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
