from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_position_recognizer import load_piece_templates, recognize_chess_position_from_image, validate_fen

DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES = 1_500_000


def export_chess_fen_review_queue(
    smoke_report: str | Path = "reports/smoke/smoke_full.json",
    *,
    output_dir: str | Path = "reports/chess_fen/review_queue/latest",
    max_items: int = 64,
    template_dir: str | Path | None = None,
    min_confidence: float = 0.70,
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
    chess_fen = (case.get("quality_report") or {}).get("chess_fen") or {}
    records = list(chess_fen.get("records") or [])
    epub_path = Path(str(case.get("output_epub") or ""))

    review_records = [_review_item(record) for record in records if record.get("requires_review")]
    review_records.sort(key=_review_sort_key)
    selected = review_records[: max(0, int(max_items))]

    target = Path(output_dir)
    crops_dir = target / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    _copy_review_crops(epub_path, selected, crops_dir)
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

    summary = {
        "status": "ok",
        "source_report": str(report_path),
        "source_epub": str(epub_path),
        "diagram_count": int(chess_fen.get("diagram_count") or len(records)),
        "fen_count": int(chess_fen.get("fen_count") or 0),
        "manual_review_count": len(review_records),
        "exported_count": len(selected),
        "reason_counts": _count_reasons(review_records),
        "openai_policy": "label_assist_review_only_no_epub_mutation",
        "openai_request_count": len(openai_requests),
        "openai_requests_path": str(target / "openai_label_assist_requests.jsonl"),
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
    (target / "openai_review_prompt.md").write_text(_review_prompt(summary), encoding="utf-8")
    return summary


def _select_chess_case(payload: dict[str, Any]) -> dict[str, Any]:
    cases = list(payload.get("cases") or [])
    for case in cases:
        chess_fen = (case.get("quality_report") or {}).get("chess_fen") or {}
        if chess_fen.get("diagram_count"):
            return case
    if cases:
        return cases[0]
    raise ValueError("Smoke report does not contain cases.")


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


def _copy_review_crops(epub_path: Path, selected: list[dict[str, Any]], crops_dir: Path) -> None:
    if not epub_path.exists():
        return
    wanted = {str(item.get("filename") or "") for item in selected if item.get("filename")}
    if not wanted:
        return
    with zipfile.ZipFile(epub_path) as archive:
        by_name = {Path(name).name: name for name in archive.namelist()}
        for filename in wanted:
            source_name = by_name.get(filename)
            if not source_name:
                continue
            with archive.open(source_name) as src, (crops_dir / filename).open("wb") as dst:
                shutil.copyfileobj(src, dst)


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
            "Do not invent pieces, do not assume side-to-move unless evidence is explicit, "
            "and do not approve a FEN when any occupied square is ambiguous. "
            "Your output is review evidence only; it must not mutate EPUB output or corpus labels."
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
            "approved": {"type": "boolean"},
            "corrected_fen": {"type": "string"},
            "requires_review": {"type": "boolean"},
            "ambiguous_squares": {"type": "array", "items": {"type": "string"}},
            "issues": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "notes": {"type": "string"},
        },
        "required": [
            "id",
            "approved",
            "corrected_fen",
            "requires_review",
            "ambiguous_squares",
            "issues",
            "confidence",
            "notes",
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
            "- Accept a FEN only when the crop unambiguously supports every occupied square.",
            "- If uncertain, return `requires_review=true` and explain the ambiguous squares.",
            "- Preserve side-to-move as `w` unless caption evidence proves otherwise.",
            "",
            "Input files:",
            f"- `queue.jsonl`: {summary.get('exported_count', 0)} prioritized cases.",
            "- `crops/`: matching board crops.",
            f"- `openai_label_assist_requests.jsonl`: {summary.get('openai_request_count', 0)} optional OpenAI Responses API request bodies.",
            "",
            "If a row contains `review_crop_*` fields, treat them as the",
            "deterministic reading of the actual exported crop. If",
            "`candidate_matches_review_crop=false`, do not approve the",
            "candidate FEN without manually correcting it against the crop.",
            "",
            "Expected JSONL response per item:",
            '```json',
            '{"id":"...","approved":false,"corrected_fen":"","requires_review":true,"ambiguous_squares":["e4"],"notes":"..."}',
            '```',
            "",
            "Promotion rule: approved/corrected items must be added to the canonical label JSONL and pass deterministic eval before runtime publication.",
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
    parser.add_argument("--openai-model", default=DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL)
    parser.add_argument("--openai-max-image-bytes", type=int, default=DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES)
    args = parser.parse_args()
    result = export_chess_fen_review_queue(
        args.smoke_report,
        output_dir=args.output_dir,
        max_items=args.max_items,
        template_dir=args.template_dir or None,
        min_confidence=args.min_confidence,
        openai_model=args.openai_model,
        openai_max_image_bytes=args.openai_max_image_bytes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
