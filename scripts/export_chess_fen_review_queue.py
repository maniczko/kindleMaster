from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_position_recognizer import load_piece_templates, recognize_chess_position_from_image, validate_fen
from openai_chess_fen_reviewer import POLICY_ACKNOWLEDGEMENT

DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES = 1_500_000


def export_chess_fen_review_queue(
    smoke_report: str | Path = "reports/smoke/smoke_full.json",
    *,
    output_dir: str | Path = "reports/chess_fen/review_queue/latest",
    max_items: int = 64,
    template_dir: str | Path | None = None,
    min_confidence: float = 0.70,
    crop_source_dirs: Iterable[str | Path] | None = None,
    openai_model: str = DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL,
    openai_max_image_bytes: int = DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES,
) -> dict[str, Any]:
    """Export unresolved scanned-board FEN cases for human/OpenAI review.

    This is intentionally review-only: it never writes labels or mutates EPUB
    output. Reviewed cases must be promoted into the canonical JSONL labels by
    a separate deterministic step.
    """
    report_path = Path(smoke_report)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    case = _select_chess_case(payload)
    chess_fen = _case_chess_fen_summary(case)
    records = list(chess_fen.get("records") or [])
    epub_path_value = str(case.get("output_epub") or case.get("final_epub") or "").strip()
    epub_path = Path(epub_path_value) if epub_path_value else Path("__missing_epub_artifact__")

    review_records = [_review_item(record) for record in records if record.get("requires_review")]
    review_records.sort(key=_review_sort_key)
    selected = review_records[: max(0, int(max_items))]

    target = Path(output_dir)
    crops_dir = target / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    source_dirs = _resolve_crop_source_dirs(report_path, crop_source_dirs)
    crop_copy_stats = _copy_review_crops(
        epub_path,
        selected,
        crops_dir,
        crop_source_dirs=source_dirs,
    )
    _attach_review_crop_recognition(
        selected,
        crops_dir=crops_dir,
        template_dir=template_dir,
        min_confidence=min_confidence,
    )

    openai_requests = _build_openai_label_assist_requests(
        selected,
        crops_dir=crops_dir,
        model=openai_model,
        max_image_bytes=openai_max_image_bytes,
    )
    manual_draft_rows = _build_manual_verification_draft(selected)
    deterministic_suggestion_count = sum(1 for row in manual_draft_rows if row.get("deterministic_suggested_fen"))
    review_sheet_path = target / "manual_review_sheet.html"
    manual_draft_path = target / "manual_verification_draft.jsonl"
    verified_labels_path = Path("reference_inputs/chess_fen/labels/manual_verified_from_review_queue.jsonl")
    template_profile_path = Path("reference_inputs/chess_fen/templates/manual_verified_from_review_queue")
    label_aids_path = Path("reports/chess_fen/label_aids/latest")
    next_commands = _next_review_commands(
        manual_draft_path=manual_draft_path,
        verified_labels_path=verified_labels_path,
        template_profile_path=template_profile_path,
        label_aids_path=label_aids_path,
    )

    summary = {
        "status": "ok",
        "source_report": str(report_path),
        "source_epub": str(epub_path),
        "diagram_count": int(chess_fen.get("diagram_count") or len(records)),
        "fen_count": int(chess_fen.get("fen_count") or 0),
        "manual_review_count": len(review_records),
        "exported_count": len(selected),
        "crop_file_count": crop_copy_stats["copied_count"],
        "missing_crop_count": crop_copy_stats["missing_count"],
        "crop_source_dirs": [str(path) for path in source_dirs],
        "reason_counts": _count_reasons(review_records),
        "openai_policy": "label_assist_review_only_no_epub_mutation",
        "openai_request_count": len(openai_requests),
        "openai_requests_path": str(target / "openai_label_assist_requests.jsonl"),
        "manual_verification_draft_count": len(manual_draft_rows),
        "deterministic_suggestion_count": deterministic_suggestion_count,
        "manual_verification_draft_path": str(manual_draft_path),
        "manual_review_sheet_path": str(review_sheet_path),
        "review_priority_counts": _review_priority_counts(manual_draft_rows),
        "label_aids_command": next_commands["label_aids_command"],
        "label_promote_command": next_commands["label_promote_command"],
        "template_build_command": next_commands["template_build_command"],
        "profile_eval_command": next_commands["profile_eval_command"],
        "next_commands": next_commands,
        "queue": selected,
    }
    (target / "queue.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "queue.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected),
        encoding="utf-8",
    )
    (target / "openai_label_assist_requests.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in openai_requests),
        encoding="utf-8",
    )
    manual_draft_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in manual_draft_rows),
        encoding="utf-8",
    )
    review_sheet_path.write_text(
        _manual_review_sheet_html(summary, manual_draft_rows),
        encoding="utf-8",
    )
    (target / "openai_review_prompt.md").write_text(_review_prompt(summary), encoding="utf-8")
    return summary


def _select_chess_case(payload: dict[str, Any]) -> dict[str, Any]:
    cases = list(payload.get("cases") or [])
    for case in cases:
        chess_fen = _case_chess_fen_summary(case)
        if chess_fen.get("diagram_count"):
            return case
    if cases:
        return cases[0]
    raise ValueError("Smoke report does not contain cases.")


def _case_chess_fen_summary(case: dict[str, Any]) -> dict[str, Any]:
    """Return the chess FEN report from smoke or premium-corpus payloads."""
    for container_key in ("quality_report", "quality"):
        summary = (case.get(container_key) or {}).get("chess_fen") or {}
        if summary:
            return summary
    return {}


def _review_item(record: dict[str, Any]) -> dict[str, Any]:
    placement = str(record.get("placement") or "").strip()
    candidate_fen = f"{placement} w - - 0 1" if placement else ""
    valid, fen_warnings = validate_fen(candidate_fen) if candidate_fen else (False, ["missing_placement"])
    warnings = list(record.get("warnings") or [])
    if "white_king_count_invalid" in warnings or "black_king_count_invalid" in warnings:
        reason = "invalid_king_count"
    elif valid:
        reason = "valid_below_threshold"
    else:
        reason = "invalid_candidate_fen"

    filename = str(record.get("filename") or "")
    item_id = f"p{int(record.get('page') or 0):03d}_{Path(filename).stem}"
    return {
        "id": item_id,
        "page": record.get("page"),
        "filename": filename,
        "crop_path": f"crops/{filename}" if filename else "",
        "confidence": round(float(record.get("confidence") or 0.0), 3),
        "reason": reason,
        "candidate_fen": candidate_fen if valid else "",
        "candidate_placement": placement,
        "fen_warnings": fen_warnings,
        "recognizer_warnings": warnings,
        "bbox": record.get("bbox"),
        "method": record.get("method"),
        "review_policy": "review_only_no_epub_mutation",
    }


def _review_sort_key(item: dict[str, Any]) -> tuple[int, float, int, str]:
    priority = {
        "valid_below_threshold": 0,
        "invalid_candidate_fen": 1,
        "invalid_king_count": 2,
    }.get(str(item.get("reason") or ""), 3)
    return (priority, -float(item.get("confidence") or 0.0), int(item.get("page") or 0), str(item.get("filename") or ""))


def _resolve_crop_source_dirs(
    report_path: Path,
    crop_source_dirs: Iterable[str | Path] | None,
) -> list[Path]:
    explicit = [Path(path) for path in (crop_source_dirs or []) if str(path).strip()]
    defaults = [
        report_path.parent / "crops",
        Path("reference_inputs/chess_fen/crops"),
        Path("reports/chess_fen/review_queue_latest/crops"),
        Path("reports/chess_fen/review_queue"),
        Path("reports/chess_fen/fundamenty_crops"),
    ]
    seen: set[str] = set()
    resolved: list[Path] = []
    for path in [*explicit, *defaults]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def _copy_review_crops(
    epub_path: Path,
    selected: list[dict[str, Any]],
    crops_dir: Path,
    *,
    crop_source_dirs: Iterable[Path] = (),
) -> dict[str, int]:
    wanted = {str(item.get("filename") or "") for item in selected if item.get("filename")}
    if not wanted:
        return {"copied_count": 0, "missing_count": 0}

    copied: set[str] = set()
    for filename in sorted(wanted):
        existing = crops_dir / filename
        if existing.is_file():
            copied.add(filename)
            _mark_crop_source(selected, filename, f"existing:{existing}")

    if epub_path.is_file():
        with zipfile.ZipFile(epub_path) as archive:
            by_name = {Path(name).name: name for name in archive.namelist()}
            for filename in wanted - copied:
                source_name = by_name.get(filename)
                if not source_name:
                    continue
                with archive.open(source_name) as src, (crops_dir / filename).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                copied.add(filename)
                _mark_crop_source(selected, filename, f"epub:{epub_path}")

    missing = wanted - copied
    if missing:
        fallback_index = _index_crop_sources(crop_source_dirs, missing)
        for filename in sorted(missing):
            source = fallback_index.get(filename)
            if not source:
                continue
            destination = crops_dir / filename
            if source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
            copied.add(filename)
            _mark_crop_source(selected, filename, str(source))

    return {"copied_count": len(copied), "missing_count": len(wanted - copied)}


def _index_crop_sources(source_dirs: Iterable[Path], filenames: set[str]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    remaining = set(filenames)
    for source_dir in source_dirs:
        if not remaining or not source_dir.exists() or not source_dir.is_dir():
            continue
        for filename in sorted(list(remaining)):
            direct = source_dir / filename
            if direct.is_file():
                index[filename] = direct
                remaining.remove(filename)
        if not remaining:
            break
        for candidate in source_dir.rglob("*.png"):
            name = candidate.name
            if name in remaining and candidate.is_file():
                index[name] = candidate
                remaining.remove(name)
                if not remaining:
                    break
    return index


def _mark_crop_source(selected: list[dict[str, Any]], filename: str, source: str) -> None:
    for item in selected:
        if str(item.get("filename") or "") == filename:
            item["crop_source"] = source


def _attach_review_crop_recognition(
    selected: list[dict[str, Any]],
    *,
    crops_dir: Path,
    template_dir: str | Path | None,
    min_confidence: float,
) -> None:
    """Add deterministic recognition evidence for the actual exported crop.

    Smoke records can be produced from a raw board bbox while the review queue
    copies the reader-visible EPUB crop. Those geometries can differ. Exposing
    the review-crop result prevents human/OpenAI label-assist from promoting a
    FEN that does not match the image being reviewed.
    """
    if not template_dir:
        return
    template_path = Path(template_dir)
    if not template_path.exists():
        return
    try:
        templates = load_piece_templates(template_path)
    except Exception:
        return
    if not templates:
        return

    for item in selected:
        filename = str(item.get("filename") or "")
        crop_path = crops_dir / filename
        if not filename or not crop_path.exists():
            continue
        try:
            result = recognize_chess_position_from_image(
                crop_path.read_bytes(),
                piece_templates=templates,
                min_confidence=float(min_confidence),
            )
        except Exception as exc:
            item["review_crop_warnings"] = [f"review_crop_recognition_failed:{type(exc).__name__}"]
            continue

        review_placement = str(result.placement or "")
        candidate_placement = str(item.get("candidate_placement") or "")
        candidate_matches = bool(candidate_placement and review_placement and candidate_placement == review_placement)
        review_warnings = list(result.warnings or [])
        if candidate_placement and review_placement and not candidate_matches:
            review_warnings.append("review_crop_candidate_mismatch")
        item.update(
            {
                "review_crop_fen": str(result.fen or ""),
                "review_crop_placement": review_placement,
                "review_crop_confidence": round(float(result.confidence or 0.0), 3),
                "review_crop_requires_review": bool(result.requires_review),
                "review_crop_warnings": sorted(set(review_warnings)),
                "candidate_matches_review_crop": candidate_matches,
            }
        )


def _count_reasons(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        reason = str(record.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _review_priority_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "ready_for_human_acceptance": 0,
        "candidate_matches_review_crop": 0,
        "needs_manual_fen": 0,
    }
    for row in rows:
        if row.get("deterministic_suggested_fen"):
            counts["ready_for_human_acceptance"] += 1
        elif row.get("candidate_matches_review_crop") and row.get("original_candidate_fen"):
            counts["candidate_matches_review_crop"] += 1
        else:
            counts["needs_manual_fen"] += 1
    return counts


def _next_review_commands(
    *,
    manual_draft_path: Path,
    verified_labels_path: Path,
    template_profile_path: Path,
    label_aids_path: Path,
) -> dict[str, str]:
    draft = _ps_path(manual_draft_path)
    verified = _ps_path(verified_labels_path)
    templates = _ps_path(template_profile_path)
    aids = _ps_path(label_aids_path)
    return {
        "label_aids_command": f"python scripts/build_chess_fen_label_aids.py {draft} --output-dir {aids}",
        "label_promote_command": (
            f"python scripts/promote_chess_fen_label_draft.py {draft} "
            f"--output {verified} --verified-by <name>"
        ),
        "template_build_command": f"python scripts/build_chess_piece_templates.py {verified} --output-dir {templates}",
        "profile_eval_command": (
            "python scripts/evaluate_chess_fen_corpus.py "
            f"--template-dir {templates} --min-profile-count 2 --min-seed-label-count 20"
        ),
    }


def _ps_path(path: Path) -> str:
    value = str(path)
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def _build_openai_label_assist_requests(
    selected: list[dict[str, Any]],
    *,
    crops_dir: Path,
    model: str,
    max_image_bytes: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for item in selected:
        filename = str(item.get("filename") or "")
        crop_path = crops_dir / filename
        if not filename or not crop_path.exists():
            continue
        try:
            image_url = _image_data_url(crop_path, max_bytes=max_image_bytes)
        except OSError:
            continue
        request_body = _openai_label_assist_body(
            item,
            image_url=image_url,
            model=model,
        )
        requests.append(
            {
                "custom_id": f"kindlemaster_chess_fen_review:{item.get('id')}",
                "method": "POST",
                "url": "/v1/responses",
                "body": request_body,
                "review_policy": "label_assist_review_only_no_epub_mutation",
                "accepted_for_corpus": False,
            }
        )
    return requests


def _build_manual_verification_draft(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in selected:
        deterministic_fen = str(item.get("review_crop_fen") or "").strip()
        deterministic_is_publishable = bool(deterministic_fen and item.get("review_crop_requires_review") is False)
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "page": item.get("page"),
                "diagram_index": _diagram_index_from_filename(str(item.get("filename") or "")),
                "crop_path": str(item.get("crop_path") or ""),
                "fen": "",
                "deterministic_suggested_fen": deterministic_fen if deterministic_is_publishable else "",
                "deterministic_confidence": item.get("review_crop_confidence"),
                "deterministic_warnings": item.get("review_crop_warnings") or [],
                "original_candidate_fen": item.get("candidate_fen") or "",
                "original_candidate_placement": item.get("candidate_placement") or "",
                "candidate_matches_review_crop": bool(item.get("candidate_matches_review_crop")),
                "label_status": "needs_manual_fen",
                "verified_by": "",
                "verified_at": "",
                "accepted_for_corpus": False,
                "notes": "Review-only draft. Copy a checked FEN into fen, then fill verified_by and verified_at before promotion.",
            }
        )
    rows.sort(key=_manual_draft_sort_key)
    return rows


def _manual_draft_sort_key(row: dict[str, Any]) -> tuple[int, float, int, str]:
    if row.get("deterministic_suggested_fen"):
        priority = 0
    elif row.get("candidate_matches_review_crop") and row.get("original_candidate_fen"):
        priority = 1
    else:
        priority = 2
    confidence = float(row.get("deterministic_confidence") or 0.0)
    return (priority, -confidence, int(row.get("page") or 0), str(row.get("id") or ""))


def _diagram_index_from_filename(filename: str) -> int | str:
    stem = Path(filename).stem
    suffix = stem.rsplit("_", 1)[-1] if "_" in stem else ""
    if suffix.isdigit():
        return int(suffix)
    return ""


def _manual_review_sheet_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_manual_review_card(row) for row in rows)
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            "<title>KindleMaster Chess FEN Review</title>",
            "<style>",
            "body{margin:0;background:#f6efe5;color:#1d241c;font-family:Georgia,'Times New Roman',serif;}",
            "main{max-width:1180px;margin:0 auto;padding:32px 20px 56px;}",
            "h1{font-size:34px;margin:0 0 8px;} .meta{color:#5f675d;margin:0 0 24px;}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;}",
            ".card{background:#fffaf2;border:1px solid #dfcdb7;border-radius:18px;padding:14px;box-shadow:0 18px 50px rgba(47,38,24,.10);}",
            ".card img{width:100%;aspect-ratio:1/1;object-fit:contain;background:#efe7d8;border-radius:12px;border:1px solid #ead8c2;}",
            ".tag{display:inline-block;margin:10px 8px 8px 0;padding:4px 9px;border-radius:999px;background:#e8f5e9;color:#116a31;font-weight:700;font-size:12px;}",
            ".tag.review{background:#fff1d6;color:#9a4b00}.tag.mismatch{background:#ffe7e1;color:#a8361e}",
            "dl{display:grid;grid-template-columns:96px 1fr;gap:6px 10px;margin:10px 0 0;font-size:13px;}dt{font-weight:700;color:#5f675d;}dd{margin:0;word-break:break-word;}",
            "code{font-family:'Cascadia Mono','Courier New',monospace;font-size:12px;color:#20251f;background:#f1eadf;padding:2px 4px;border-radius:5px;}",
            "textarea{width:100%;box-sizing:border-box;min-height:54px;margin-top:10px;border:1px solid #d8c5ae;border-radius:10px;background:#fffdf8;padding:8px;font-family:'Cascadia Mono','Courier New',monospace;font-size:12px;}",
            "</style>",
            "</head>",
            "<body><main>",
            "<h1>Chess FEN Manual Review</h1>",
            f"<p class=\"meta\">{_html(summary.get('exported_count'))} crops, {_html(summary.get('deterministic_suggestion_count'))} deterministic suggestions. Review-only: copying into <code>fen</code> still requires human verification.</p>",
            "<section class=\"grid\">",
            cards,
            "</section>",
            "</main></body></html>",
        ]
    )


def _manual_review_card(row: dict[str, Any]) -> str:
    suggestion = str(row.get("deterministic_suggested_fen") or "")
    original = str(row.get("original_candidate_fen") or "")
    crop = str(row.get("crop_path") or "")
    tags = [
        "<span class=\"tag review\">needs manual FEN</span>",
        "<span class=\"tag\">suggestion</span>" if suggestion else "<span class=\"tag review\">no safe suggestion</span>",
    ]
    if not row.get("candidate_matches_review_crop"):
        tags.append("<span class=\"tag mismatch\">candidate mismatch</span>")
    return "\n".join(
        [
            "<article class=\"card\">",
            f"<img src=\"{_attr(crop)}\" alt=\"{_attr(row.get('id'))}\">" if crop else "",
            "".join(tags),
            "<dl>",
            f"<dt>ID</dt><dd><code>{_html(row.get('id'))}</code></dd>",
            f"<dt>Page</dt><dd>{_html(row.get('page'))}</dd>",
            f"<dt>Crop</dt><dd><code>{_html(crop)}</code></dd>",
            f"<dt>Suggested</dt><dd><code>{_html(suggestion or '-')}</code></dd>",
            f"<dt>Original</dt><dd><code>{_html(original or '-')}</code></dd>",
            f"<dt>Warnings</dt><dd>{_html(', '.join(str(item) for item in row.get('deterministic_warnings') or []) or '-')}</dd>",
            "</dl>",
            f"<textarea aria-label=\"Verified FEN for {_attr(row.get('id'))}\" placeholder=\"Paste verified FEN here after checking the crop\">{_html(suggestion)}</textarea>",
            "</article>",
        ]
    )


def _html(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _attr(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _image_data_url(path: Path, *, max_bytes: int) -> str:
    data = path.read_bytes()
    if len(data) > max(1, int(max_bytes)):
        raise OSError("image_too_large_for_openai_review_request")
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _openai_label_assist_body(item: dict[str, Any], *, image_url: str, model: str) -> dict[str, Any]:
    review_context = {
        "id": item.get("id"),
        "page": item.get("page"),
        "candidate_fen": item.get("candidate_fen") or "",
        "candidate_placement": item.get("candidate_placement") or "",
        "confidence": item.get("confidence"),
        "reason": item.get("reason"),
        "fen_warnings": item.get("fen_warnings") or [],
        "recognizer_warnings": item.get("recognizer_warnings") or [],
        "review_crop_fen": item.get("review_crop_fen") or "",
        "review_crop_placement": item.get("review_crop_placement") or "",
        "candidate_matches_review_crop": item.get("candidate_matches_review_crop"),
        "policy": "review_only_no_epub_mutation",
    }
    return {
        "model": str(model or DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL),
        "instructions": (
            "You are a conservative chess FEN label-assist reviewer for KindleMaster. "
            "Use only the provided board crop and deterministic evidence. Return JSON only. "
            "Do not invent pieces, do not assume side-to-move unless marker/caption evidence is explicit, "
            "and do not support a candidate when any occupied square is ambiguous. If you disagree with "
            "the candidate, return square_diffs. Never output verified, accepted, accepted_for_corpus, "
            "label_status, verified_by, or verified_at. Your output is review evidence only; it must not "
            f"mutate EPUB output or corpus labels and must include policy_acknowledgement='{POLICY_ACKNOWLEDGEMENT}'."
        ),
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(review_context, ensure_ascii=False),
                    },
                    {
                        "type": "input_image",
                        "image_url": image_url,
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "kindlemaster_chess_fen_label_assist",
                "strict": True,
                "schema": _openai_label_assist_schema(),
            }
        },
    }


def _openai_label_assist_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "review_opinion": {"type": "string", "enum": ["supports_candidate", "flags_candidate", "uncertain", "cannot_verify"]},
            "candidate_fen": {"type": "string"},
            "suggested_fen": {"type": "string"},
            "requires_review": {"type": "boolean"},
            "ambiguous_squares": {"type": "array", "items": {"type": "string"}},
            "square_diffs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "square": {"type": "string"},
                        "candidate_piece": {"type": "string"},
                        "observed_piece": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["square", "candidate_piece", "observed_piece", "confidence", "reason"],
                },
            },
            "side_to_move": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": {"type": "string", "enum": ["w", "b", "unknown"]},
                    "evidence": {"type": "string", "enum": ["marker", "caption", "inferred", "none"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["value", "evidence", "confidence"],
            },
            "issues": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "cannot_verify_reason": {"type": "string"},
            "evidence_level": {"type": "string", "enum": ["clear", "ambiguous", "insufficient_crop", "missing_crop"]},
            "crop_quality_notes": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
            "policy_acknowledgement": {"type": "string", "enum": [POLICY_ACKNOWLEDGEMENT]},
        },
        "required": [
            "id",
            "review_opinion",
            "candidate_fen",
            "suggested_fen",
            "requires_review",
            "ambiguous_squares",
            "square_diffs",
            "side_to_move",
            "issues",
            "confidence",
            "cannot_verify_reason",
            "evidence_level",
            "crop_quality_notes",
            "notes",
            "policy_acknowledgement",
        ],
    }


def _review_prompt(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Chess FEN Review Queue",
            "",
            "Goal: review unresolved chess-board crops and return evidence only.",
            "",
            "Policy:",
            "- Do not mutate EPUB output directly.",
            "- Support a candidate for human review only when the crop unambiguously supports every occupied square.",
            "- If uncertain, return `requires_review=true` and explain the ambiguous squares.",
            "- `review_opinion=supports_candidate` is only a review opinion; it is never a verified label or permission to publish.",
            "- Return side-to-move as `unknown` unless marker/caption evidence proves otherwise.",
            "- Include `square_diffs` when the observed crop disagrees with the candidate FEN.",
            f"- Always include `policy_acknowledgement`: `{POLICY_ACKNOWLEDGEMENT}`.",
            "",
            "Input files:",
            f"- `queue.jsonl`: {summary.get('exported_count', 0)} prioritized cases.",
            "- `crops/`: matching board crops.",
            f"- `openai_label_assist_requests.jsonl`: {summary.get('openai_request_count', 0)} optional OpenAI Responses API request bodies.",
            f"- `manual_verification_draft.jsonl`: {summary.get('manual_verification_draft_count', 0)} rows to fill after checking crops.",
            "- `manual_review_sheet.html`: browser-friendly crop contact sheet for manual labeling.",
            f"- `label_aids_command`: `{summary.get('label_aids_command', '')}`",
            f"- `label_promote_command`: `{summary.get('label_promote_command', '')}`",
            f"- `template_build_command`: `{summary.get('template_build_command', '')}`",
            f"- `profile_eval_command`: `{summary.get('profile_eval_command', '')}`",
            "",
            "If a row contains `review_crop_*` fields, treat them as the",
            "deterministic reading of the actual exported crop. If",
            "`candidate_matches_review_crop=false`, do not approve the",
            "candidate FEN without manually correcting it against the crop.",
            "",
            "Expected JSONL response per item:",
            '```json',
            '{"id":"...","review_opinion":"uncertain","candidate_fen":"","suggested_fen":"","requires_review":true,"ambiguous_squares":["e4"],"square_diffs":[],"side_to_move":{"value":"unknown","evidence":"none","confidence":0},"issues":[],"confidence":0.2,"cannot_verify_reason":"","evidence_level":"ambiguous","crop_quality_notes":[],"notes":"...","policy_acknowledgement":"review_only_no_corpus_promotion"}',
            '```',
            "",
            "Promotion rule: AI suggestions may only prefill a manual draft. A human must visually verify the crop before any corpus label or runtime publication.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export unresolved chess FEN cases from a smoke report.")
    parser.add_argument("--smoke-report", default="reports/smoke/smoke_full.json")
    parser.add_argument("--output-dir", default="reports/chess_fen/review_queue/latest")
    parser.add_argument("--max-items", type=int, default=64)
    parser.add_argument("--template-dir", default="")
    parser.add_argument("--min-confidence", type=float, default=0.70)
    parser.add_argument(
        "--crop-source-dir",
        action="append",
        default=[],
        help="Additional directory to search for existing crop PNGs; can be repeated.",
    )
    parser.add_argument("--openai-model", default=DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL)
    parser.add_argument("--openai-max-image-bytes", type=int, default=DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES)
    args = parser.parse_args()
    result = export_chess_fen_review_queue(
        args.smoke_report,
        output_dir=args.output_dir,
        max_items=args.max_items,
        template_dir=args.template_dir or None,
        min_confidence=args.min_confidence,
        crop_source_dirs=args.crop_source_dir,
        openai_model=args.openai_model,
        openai_max_image_bytes=args.openai_max_image_bytes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
