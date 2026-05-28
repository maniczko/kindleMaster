from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.export_chess_fen_review_queue import (
    DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL,
    DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES,
    _image_data_url,
    _openai_label_assist_body,
)

FILES = "abcdefgh"
RANKS = "87654321"


def build_chess_fen_label_aids(
    candidate_labels_path: str | Path,
    *,
    output_dir: str | Path = "reports/chess_fen/label_aids/latest",
    max_items: int = 64,
    openai_model: str = DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL,
    openai_max_image_bytes: int = DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES,
) -> dict[str, Any]:
    labels_path = Path(candidate_labels_path)
    rows = _read_jsonl(labels_path)
    selected = rows[: max(0, int(max_items))]
    target = Path(output_dir)
    aid_dir = target / "aids"
    aid_dir.mkdir(parents=True, exist_ok=True)

    aid_records: list[dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        crop_path = _resolve_crop_path(row.get("crop_path"), labels_path=labels_path)
        if crop_path is None or not crop_path.exists():
            aid_records.append(
                {
                    "id": str(row.get("id") or f"row_{index}"),
                    "crop_path": str(row.get("crop_path") or ""),
                    "status": "missing_crop",
                    "fen": "",
                }
            )
            continue
        image = ImageOps.autocontrast(Image.open(crop_path).convert("RGB"))
        aid_name = f"{index:03d}_{_safe_stem(str(row.get('id') or crop_path.stem))}_grid.png"
        aid_path = aid_dir / aid_name
        _draw_grid_aid(image).save(aid_path, format="PNG", optimize=True)
        aid_records.append(
            {
                "id": str(row.get("id") or f"row_{index}"),
                "crop_path": str(crop_path),
                "aid_path": str(aid_path),
                "page": row.get("page"),
                "diagram_index": row.get("diagram_index"),
                "status": "needs_manual_fen",
                "candidate_fen": str(row.get("candidate_fen") or ""),
                "candidate_placement": str(row.get("candidate_placement") or ""),
                "confidence": row.get("confidence") or row.get("candidate_confidence"),
                "reason": str(row.get("reason") or row.get("label_status") or "needs_manual_fen"),
                "fen_warnings": row.get("fen_warnings") or [],
                "recognizer_warnings": row.get("warnings") or row.get("recognizer_warnings") or [],
                "fen": "",
            }
        )

    contact_sheet_path = target / "contact_sheet.png"
    _write_contact_sheet(aid_records, contact_sheet_path)
    template_path = target / "manual_label_template.jsonl"
    _write_jsonl(template_path, [_manual_template_row(record) for record in aid_records])
    openai_requests = _build_openai_label_aid_requests(
        aid_records,
        model=openai_model,
        max_image_bytes=openai_max_image_bytes,
    )
    openai_requests_path = target / "openai_label_assist_requests.jsonl"
    _write_jsonl(openai_requests_path, openai_requests)
    readme_path = target / "README.md"
    readme_path.write_text(_readme(labels_path, template_path, contact_sheet_path, openai_requests_path), encoding="utf-8")

    summary = {
        "status": "ok",
        "accepted_for_corpus": False,
        "candidate_labels": str(labels_path),
        "output_dir": str(target),
        "row_count": len(rows),
        "aid_count": sum(1 for record in aid_records if record.get("aid_path")),
        "missing_crop_count": sum(1 for record in aid_records if record.get("status") == "missing_crop"),
        "contact_sheet": str(contact_sheet_path),
        "manual_label_template": str(template_path),
        "openai_label_assist_requests": str(openai_requests_path),
        "openai_request_count": len(openai_requests),
        "readme": str(readme_path),
        "policy": "review_only_no_fen_generation",
        "openai_policy": "label_assist_review_only_no_corpus_promotion",
        "aids": aid_records,
    }
    (target / "label_aids_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _draw_grid_aid(image: Image.Image) -> Image.Image:
    size = max(image.width, image.height)
    board = Image.new("RGB", (size, size), "white")
    board.paste(image.resize((size, size), Image.Resampling.LANCZOS), (0, 0))
    margin = max(28, size // 12)
    canvas = Image.new("RGB", (size + margin, size + margin), "white")
    canvas.paste(board, (margin, 0))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    cell = size / 8.0

    for index in range(9):
        x = margin + int(round(index * cell))
        y = int(round(index * cell))
        draw.line((x, 0, x, size), fill=(220, 40, 40), width=1)
        draw.line((margin, y, margin + size, y), fill=(220, 40, 40), width=1)
    for col, file_name in enumerate(FILES):
        x = margin + int(round((col + 0.5) * cell)) - 3
        draw.text((x, size + 6), file_name, fill=(0, 0, 0), font=font)
    for row, rank in enumerate(RANKS):
        y = int(round((row + 0.5) * cell)) - 5
        draw.text((6, y), rank, fill=(0, 0, 0), font=font)
    return canvas


def _write_contact_sheet(records: list[dict[str, Any]], output_path: Path) -> None:
    aid_paths = [Path(str(record.get("aid_path"))) for record in records if record.get("aid_path")]
    if not aid_paths:
        Image.new("RGB", (640, 160), "white").save(output_path, format="PNG", optimize=True)
        return
    thumb_w = 220
    thumb_h = 260
    columns = min(4, max(1, len(aid_paths)))
    rows = int(math.ceil(len(aid_paths) / columns))
    sheet = Image.new("RGB", (columns * thumb_w, rows * thumb_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (record, aid_path) in enumerate((record, Path(str(record.get("aid_path")))) for record in records if record.get("aid_path")):
        image = Image.open(aid_path).convert("RGB")
        image.thumbnail((thumb_w - 20, thumb_h - 48), Image.Resampling.LANCZOS)
        col = index % columns
        row = index // columns
        x = col * thumb_w + 10
        y = row * thumb_h + 10
        sheet.paste(image, (x, y))
        draw.text((x, y + image.height + 6), str(record.get("id") or ""), fill=(0, 0, 0), font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG", optimize=True)


def _manual_template_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id", ""),
        "crop_path": record.get("crop_path", ""),
        "aid_path": record.get("aid_path", ""),
        "page": record.get("page"),
        "diagram_index": record.get("diagram_index"),
        "fen": "",
        "verified_by": "",
        "verified_at": "",
        "notes": "Fill FEN manually from aid_path. This template is not accepted for corpus proof.",
    }


def _build_openai_label_aid_requests(
    records: list[dict[str, Any]],
    *,
    model: str,
    max_image_bytes: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for record in records:
        aid_path = Path(str(record.get("aid_path") or ""))
        if not aid_path.exists():
            continue
        try:
            image_url = _image_data_url(aid_path, max_bytes=max_image_bytes)
        except OSError:
            continue
        body = _openai_label_assist_body(record, image_url=image_url, model=model)
        requests.append(
            {
                "custom_id": f"kindlemaster_chess_fen_label_aid:{record.get('id')}",
                "method": "POST",
                "url": "/v1/responses",
                "body": body,
                "review_policy": "label_assist_review_only_no_corpus_promotion",
                "accepted_for_corpus": False,
            }
        )
    return requests


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _resolve_crop_path(value: Any, *, labels_path: Path) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or path.exists():
        return path
    candidate = labels_path.parent / path
    if candidate.exists():
        return candidate
    return path


def _safe_stem(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return sanitized.strip("_") or "crop"


def _readme(labels_path: Path, template_path: Path, contact_sheet_path: Path, openai_requests_path: Path) -> str:
    return "\n".join(
        [
            "# Chess FEN Label Aids",
            "",
            "Policy: review-only. These aids never create accepted FEN.",
            "",
            f"- Source candidate labels: `{labels_path}`",
            f"- Contact sheet: `{contact_sheet_path}`",
            f"- Manual label template: `{template_path}`",
            f"- Optional OpenAI label-assist requests: `{openai_requests_path}`",
            "",
            "Fill `fen`, `verified_by`, `verified_at`, and `notes` manually, then run:",
            "",
            "```powershell",
            "python scripts/validate_chess_fen_labels.py <verified_labels.jsonl>",
            "python scripts/build_chess_piece_templates.py <verified_labels.jsonl> --output-dir <template_profile>",
            "python scripts/evaluate_chess_fen_corpus.py --min-profile-count 2 --min-seed-label-count 20",
            "```",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build review-only chess FEN label aids from candidate crop labels.")
    parser.add_argument("candidate_labels")
    parser.add_argument("--output-dir", default="reports/chess_fen/label_aids/latest")
    parser.add_argument("--max-items", type=int, default=64)
    parser.add_argument("--openai-model", default=DEFAULT_OPENAI_CHESS_FEN_REVIEW_MODEL)
    parser.add_argument("--openai-max-image-bytes", type=int, default=DEFAULT_OPENAI_REVIEW_MAX_IMAGE_BYTES)
    args = parser.parse_args()

    payload = build_chess_fen_label_aids(
        args.candidate_labels,
        output_dir=args.output_dir,
        max_items=args.max_items,
        openai_model=args.openai_model,
        openai_max_image_bytes=args.openai_max_image_bytes,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
