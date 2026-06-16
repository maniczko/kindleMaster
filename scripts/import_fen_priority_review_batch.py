from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

import fitz
import pytesseract
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import (
    detect_board_candidates_in_page_image,
    load_piece_templates,
    recognize_chess_position_from_image,
    validate_fen,
)
from converter import ConversionConfig
from premium_tools import find_tesseract_executable
from pymupdf_chess_extractor import (
    _clamp_bbox,
    _encode_scan_chess_diagram_crop,
    _page_image_data_for_scan_chess,
    _resize_image_to_long_edge,
)


DEFAULT_TARGET_SEED_LABELS = Path("reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl")
DEFAULT_TARGET_VERIFIED_LABELS = Path("reference_inputs/chess_fen/labels/fundamenty_verified_crop_labels.jsonl")
DEFAULT_TARGET_CROPS_DIR = Path("reference_inputs/chess_fen/crops/imported_priority_review")
DEFAULT_REPORTS_DIR = Path("reports/chess_fen/imported_priority_review")
DEFAULT_TEMPLATE_DIR = Path("reference_inputs/chess_fen/templates/fundamenty_merida_like")


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class CandidateRecord:
    raw_index: int
    reading_order: int
    bbox: tuple[int, int, int, int]
    crop_bytes: bytes
    crop_sha256: str
    recognized_fen: str
    recognized_placement: str
    confidence: float
    requires_review: bool
    above_text: str
    below_text: str


@dataclass
class MatchScore:
    score: float
    placement_accuracy: float
    caption_similarity: float
    caption_key_exact: bool
    exact_fen: bool
    reading_order_penalty: int


def import_fen_priority_review_batch(
    filled_jsonl_path: str | Path,
    *,
    source_pdf: str | Path,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
    target_seed_labels: str | Path = DEFAULT_TARGET_SEED_LABELS,
    target_verified_labels: str | Path = DEFAULT_TARGET_VERIFIED_LABELS,
    target_crops_dir: str | Path = DEFAULT_TARGET_CROPS_DIR,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
    reviewer: str = "codex-priority-review-import",
    verified_at: str | None = None,
    max_candidates_per_page: int = 12,
    min_grid_confidence: float = 0.45,
    enable_sliding_probe: bool = True,
    strip_ratio: float = 0.22,
    apply_changes: bool = False,
) -> dict[str, Any]:
    verified_date = str(verified_at or date.today().isoformat()).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", verified_date):
        raise ValueError("verified_at must be YYYY-MM-DD")

    filled_path = Path(filled_jsonl_path)
    pdf_path = Path(source_pdf)
    template_path = Path(template_dir)
    seed_target = Path(target_seed_labels)
    exact_target = Path(target_verified_labels)
    crops_target = Path(target_crops_dir)
    report_root = Path(reports_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    crops_target.mkdir(parents=True, exist_ok=True)

    _configure_tesseract()
    review_rows = _load_verified_review_rows(filled_path)
    piece_templates = load_piece_templates(template_path)
    config = ConversionConfig()

    page_rows = _group_rows_by_page(review_rows)
    page_results: list[dict[str, Any]] = []
    imported_seed_rows: list[dict[str, Any]] = []
    imported_exact_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []

    with fitz.open(pdf_path) as document:
        for page_number, rows in sorted(page_rows.items()):
            candidates = _build_page_candidates(
                document,
                page_number=page_number,
                piece_templates=piece_templates,
                config=config,
                max_candidates_per_page=max_candidates_per_page,
                min_grid_confidence=min_grid_confidence,
                enable_sliding_probe=enable_sliding_probe,
                strip_ratio=strip_ratio,
            )
            page_result = _match_page_rows(rows, candidates)
            accepted = page_result["accepted"]
            unresolved = page_result["unresolved"]
            page_results.append(
                {
                    "page": page_number,
                    "candidate_count": len(candidates),
                    "accepted_count": len(accepted),
                    "unresolved_count": len(unresolved),
                    "accepted": accepted,
                    "unresolved": unresolved,
                }
            )

            for accepted_match in accepted:
                candidate = candidates[accepted_match["candidate_index"]]
                diagram_id = str(accepted_match["diagram_id"])
                crop_filename = f"{pdf_path.stem}_priority_{diagram_id}.png"
                crop_path = crops_target / crop_filename
                crop_path.write_bytes(candidate.crop_bytes)

                manual_fen = str(accepted_match["manual_fen"]).strip()
                seed_row = {
                    "id": f"fundamenty_priority_{diagram_id}",
                    "source_pdf": str(pdf_path),
                    "page": int(page_number),
                    "diagram_index": int(accepted_match["diagram_ordinal"]),
                    "crop_path": str(crop_path),
                    "fen": manual_fen,
                    "verified_by": reviewer,
                    "verified_at": verified_date,
                    "label_status": "verified",
                    "notes": (
                        "Imported from verified priority review batch; "
                        f"match={accepted_match['match_reason']}; "
                        f"caption_key_exact={accepted_match['caption_key_exact']}; "
                        f"placement_accuracy={accepted_match['placement_accuracy']:.3f}"
                    ),
                }
                exact_row = {
                    "id": f"verified_priority_{diagram_id}",
                    "source_pdf": str(pdf_path),
                    "page": int(page_number),
                    "filename": crop_filename,
                    "sha256": candidate.crop_sha256,
                    "fen": manual_fen,
                    "verified_by": reviewer,
                    "verified_at": verified_date,
                    "source_crop_path": str(crop_path),
                    "notes": (
                        "Imported from verified priority review batch; "
                        f"match={accepted_match['match_reason']}; "
                        f"caption_key_exact={accepted_match['caption_key_exact']}; "
                        f"placement_accuracy={accepted_match['placement_accuracy']:.3f}"
                    ),
                }
                imported_seed_rows.append(seed_row)
                imported_exact_rows.append(exact_row)

            unresolved_rows.extend(unresolved)

    merged_seed_rows, seed_merge_summary = _merge_seed_rows(seed_target, imported_seed_rows)
    merged_exact_rows, exact_merge_summary = _merge_exact_rows(exact_target, imported_exact_rows)

    report_payload = {
        "status": "applied" if apply_changes else "dry_run",
        "filled_jsonl_path": str(filled_path),
        "source_pdf": str(pdf_path),
        "template_dir": str(template_path),
        "target_seed_labels": str(seed_target),
        "target_verified_labels": str(exact_target),
        "target_crops_dir": str(crops_target),
        "reviewer": reviewer,
        "verified_at": verified_date,
        "page_count": len(page_rows),
        "verified_row_count": len(review_rows),
        "accepted_row_count": len(imported_seed_rows),
        "unresolved_row_count": len(unresolved_rows),
        "seed_merge": seed_merge_summary,
        "exact_merge": exact_merge_summary,
        "pages": page_results,
        "unresolved_rows": unresolved_rows,
    }
    summary_path = report_root / f"{filled_path.stem}_import_summary.json"
    summary_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if apply_changes:
        _write_jsonl(seed_target, merged_seed_rows)
        _write_jsonl(exact_target, merged_exact_rows)

    report_payload["summary_path"] = str(summary_path)
    return report_payload


def _configure_tesseract() -> None:
    executable = find_tesseract_executable()
    if executable:
        pytesseract.pytesseract.tesseract_cmd = executable


def _load_verified_review_rows(path: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    verified: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("label_status") or "").strip().lower() != "verified":
            continue
        if str(row.get("manual_label") or "").strip().lower() != "correct_diagram":
            continue
        manual_fen = str(row.get("manual_fen") or "").strip()
        valid, _warnings = validate_fen(manual_fen)
        if not valid:
            continue
        verified.append(row)
    return verified


def _group_rows_by_page(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        page_number = int(row.get("page") or 0)
        if page_number <= 0:
            continue
        grouped.setdefault(page_number, []).append(row)
    return grouped


def _build_page_candidates(
    document: fitz.Document,
    *,
    page_number: int,
    piece_templates: dict[str, Any],
    config: ConversionConfig,
    max_candidates_per_page: int,
    min_grid_confidence: float,
    enable_sliding_probe: bool,
    strip_ratio: float,
) -> list[CandidateRecord]:
    image_data = _page_image_data_for_scan_chess(document, page_number - 1)
    page_image = Image.open(io.BytesIO(image_data)).convert("RGB")
    words = _ocr_page_words(page_image)
    candidates = detect_board_candidates_in_page_image(
        image_data,
        max_candidates=max_candidates_per_page,
        min_grid_confidence=min_grid_confidence,
        enable_sliding_probe=enable_sliding_probe,
    )
    prepared: list[tuple[tuple[int, int], CandidateRecord]] = []
    for raw_index, candidate in enumerate(candidates, start=1):
        bbox = _clamp_bbox(candidate.bbox, page_image.size)
        if not bbox:
            continue
        crop = _resize_image_to_long_edge(
            page_image.crop(bbox),
            int(getattr(config, "scanned_chess_diagram_long_edge", 360) or 360),
            resample=Image.Resampling.LANCZOS,
        )
        crop_bytes, _width, _height = _encode_scan_chess_diagram_crop(crop, config)
        recognition = recognize_chess_position_from_image(
            crop_bytes,
            piece_templates=piece_templates,
            min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.835) or 0.835),
        ).to_dict()
        above_text, below_text = _candidate_caption_texts(words, bbox=bbox, strip_ratio=strip_ratio, image_height=page_image.height)
        prepared.append(
            (
                (bbox[1], bbox[0]),
                CandidateRecord(
                    raw_index=raw_index,
                    reading_order=0,
                    bbox=bbox,
                    crop_bytes=crop_bytes,
                    crop_sha256=sha256(crop_bytes).hexdigest(),
                    recognized_fen=str(recognition.get("fen") or "").strip(),
                    recognized_placement=str(recognition.get("placement") or "").strip(),
                    confidence=float(recognition.get("confidence") or 0.0),
                    requires_review=bool(recognition.get("requires_review", True)),
                    above_text=above_text,
                    below_text=below_text,
                ),
            )
        )
    prepared.sort(key=lambda item: item[0])
    output: list[CandidateRecord] = []
    for reading_order, (_sort_key, record) in enumerate(prepared, start=1):
        record.reading_order = reading_order
        output.append(record)
    return output


@lru_cache(maxsize=128)
def _caption_key(text: str) -> str:
    numbers = re.findall(r"\d+", str(text or ""))
    return "-".join(numbers)


def _caption_similarity(caption: str, candidate: CandidateRecord) -> tuple[float, bool]:
    normalized_caption = _normalize_text(caption)
    if not normalized_caption:
        return 0.0, False
    key = _caption_key(caption)
    texts = [candidate.above_text, candidate.below_text]
    best_ratio = 0.0
    key_exact = False
    for text in texts:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            continue
        ratio = difflib.SequenceMatcher(None, normalized_caption, normalized_text).ratio()
        best_ratio = max(best_ratio, ratio)
        if key and key in normalized_text.replace(" ", "-"):
            key_exact = True
            best_ratio = max(best_ratio, 1.0)
    return best_ratio, key_exact


def _match_page_rows(rows: list[dict[str, Any]], candidates: list[CandidateRecord]) -> dict[str, list[dict[str, Any]]]:
    if not rows:
        return {"accepted": [], "unresolved": []}
    if not candidates:
        return {
            "accepted": [],
            "unresolved": [
                {
                    "diagram_id": str(row.get("diagram_id") or ""),
                    "page": int(row.get("page") or 0),
                    "reason": "no_candidates_detected",
                    "caption": str(row.get("caption") or ""),
                    "manual_fen": str(row.get("manual_fen") or ""),
                }
                for row in rows
            ],
        }

    sorted_rows = sorted(rows, key=lambda row: _diagram_ordinal(str(row.get("diagram_id") or "")))
    score_matrix: list[list[MatchScore]] = []
    for row in sorted_rows:
        manual_fen = str(row.get("manual_fen") or "").strip()
        manual_placement = manual_fen.split()[0] if manual_fen else ""
        row_scores: list[MatchScore] = []
        for candidate in candidates:
            caption_similarity, caption_key_exact = _caption_similarity(str(row.get("caption") or ""), candidate)
            exact_fen = bool(candidate.recognized_fen and candidate.recognized_fen == manual_fen)
            placement_accuracy = _placement_accuracy(manual_placement, candidate.recognized_placement)
            reading_order_penalty = abs(candidate.reading_order - _diagram_ordinal(str(row.get("diagram_id") or "")))
            score = (
                (3.0 if exact_fen else 0.0)
                + (2.0 * placement_accuracy)
                + (1.6 * caption_similarity)
                + (1.5 if caption_key_exact else 0.0)
                - (0.04 * reading_order_penalty)
            )
            row_scores.append(
                MatchScore(
                    score=score,
                    placement_accuracy=placement_accuracy,
                    caption_similarity=caption_similarity,
                    caption_key_exact=caption_key_exact,
                    exact_fen=exact_fen,
                    reading_order_penalty=reading_order_penalty,
                )
            )
        score_matrix.append(row_scores)

    assignment = _best_assignment(score_matrix)
    accepted: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row_index, candidate_index in enumerate(assignment):
        row = sorted_rows[row_index]
        row_scores = score_matrix[row_index]
        chosen = row_scores[candidate_index]
        alternatives = sorted(
            (score.score, index)
            for index, score in enumerate(row_scores)
            if index != candidate_index
        )
        second_best_score = alternatives[-1][0] if alternatives else None
        margin = chosen.score - second_best_score if second_best_score is not None else chosen.score
        decision = _accept_match(chosen, margin)
        record = {
            "diagram_id": str(row.get("diagram_id") or ""),
            "page": int(row.get("page") or 0),
            "caption": str(row.get("caption") or ""),
            "manual_fen": str(row.get("manual_fen") or ""),
            "diagram_ordinal": _diagram_ordinal(str(row.get("diagram_id") or "")),
            "candidate_index": candidate_index,
            "candidate_reading_order": candidates[candidate_index].reading_order,
            "candidate_raw_index": candidates[candidate_index].raw_index,
            "match_score": round(chosen.score, 4),
            "second_best_score": round(second_best_score, 4) if second_best_score is not None else None,
            "margin": round(margin, 4),
            "placement_accuracy": round(chosen.placement_accuracy, 4),
            "caption_similarity": round(chosen.caption_similarity, 4),
            "caption_key_exact": chosen.caption_key_exact,
            "exact_fen": chosen.exact_fen,
            "candidate_recognized_fen": candidates[candidate_index].recognized_fen,
            "candidate_bbox": list(candidates[candidate_index].bbox),
            "candidate_above_text": candidates[candidate_index].above_text,
            "candidate_below_text": candidates[candidate_index].below_text,
            "match_reason": decision["reason"],
        }
        if decision["accepted"]:
            accepted.append(record)
        else:
            record["reason"] = decision["reason"]
            unresolved.append(record)
    return {"accepted": accepted, "unresolved": unresolved}


def _accept_match(score: MatchScore, margin: float) -> dict[str, Any]:
    if score.exact_fen:
        return {"accepted": True, "reason": "exact_fen"}
    if score.placement_accuracy >= 0.96:
        return {"accepted": True, "reason": "high_placement_accuracy"}
    if score.placement_accuracy >= 0.93 and margin >= 0.18:
        return {"accepted": True, "reason": "near_exact_placement_accuracy"}
    if score.caption_key_exact and margin >= 0.25:
        return {"accepted": True, "reason": "caption_key_exact"}
    if score.placement_accuracy >= 0.86 and score.caption_similarity >= 0.55 and margin >= 0.20:
        return {"accepted": True, "reason": "caption_plus_placement"}
    return {"accepted": False, "reason": "match_uncertain"}


def _best_assignment(score_matrix: list[list[MatchScore]]) -> list[int]:
    row_count = len(score_matrix)
    candidate_count = len(score_matrix[0]) if score_matrix else 0
    if candidate_count < row_count:
        raise ValueError("candidate_count must be >= row_count")

    @lru_cache(maxsize=None)
    def solve(row_index: int, used_mask: int) -> tuple[float, tuple[int, ...]]:
        if row_index >= row_count:
            return 0.0, ()
        best_total = float("-inf")
        best_assignment: tuple[int, ...] = ()
        for candidate_index in range(candidate_count):
            if used_mask & (1 << candidate_index):
                continue
            current = score_matrix[row_index][candidate_index].score
            rest_total, rest_assignment = solve(row_index + 1, used_mask | (1 << candidate_index))
            total = current + rest_total
            if total > best_total:
                best_total = total
                best_assignment = (candidate_index, *rest_assignment)
        return best_total, best_assignment

    _total, assignment = solve(0, 0)
    return list(assignment)


def _diagram_ordinal(diagram_id: str) -> int:
    match = re.search(r"_d(\d+)$", str(diagram_id or ""))
    return int(match.group(1)) if match else 0


def _placement_accuracy(expected_placement: str, actual_placement: str) -> float:
    expected = _placement_to_cells(expected_placement)
    actual = _placement_to_cells(actual_placement)
    if expected is None or actual is None:
        return 0.0
    return sum(1 for left, right in zip(expected, actual) if left == right) / 64.0


def _placement_to_cells(placement: str) -> list[str] | None:
    rows = str(placement or "").split("/")
    if len(rows) != 8:
        return None
    cells: list[str] = []
    for rank in rows:
        width = 0
        for char in rank:
            if char.isdigit():
                value = int(char)
                cells.extend([""] * value)
                width += value
            else:
                cells.append(char)
                width += 1
        if width != 8:
            return None
    return cells if len(cells) == 64 else None


def _ocr_page_words(image: Image.Image) -> list[OcrWord]:
    resized = _resize_image_to_long_edge(image, 1800, resample=Image.Resampling.LANCZOS)
    scale_x = image.width / float(resized.width)
    scale_y = image.height / float(resized.height)
    data = pytesseract.image_to_data(resized, lang="eng", output_type=pytesseract.Output.DICT)
    words: list[OcrWord] = []
    for index, text in enumerate(data.get("text", [])):
        raw = str(text or "").strip()
        if not raw:
            continue
        words.append(
            OcrWord(
                text=raw,
                left=int(round(int(data["left"][index]) * scale_x)),
                top=int(round(int(data["top"][index]) * scale_y)),
                width=max(1, int(round(int(data["width"][index]) * scale_x))),
                height=max(1, int(round(int(data["height"][index]) * scale_y))),
            )
        )
    return words


def _candidate_caption_texts(
    words: list[OcrWord],
    *,
    bbox: tuple[int, int, int, int],
    strip_ratio: float,
    image_height: int,
) -> tuple[str, str]:
    x0, y0, x1, y1 = bbox
    strip_height = max(24, int(round((y1 - y0) * strip_ratio)))
    above = _collect_region_text(
        words,
        left=max(0, x0 - 20),
        top=max(0, y0 - strip_height),
        right=x1 + 20,
        bottom=min(image_height, y0 + 10),
    )
    below = _collect_region_text(
        words,
        left=max(0, x0 - 20),
        top=max(0, y1 - 10),
        right=x1 + 20,
        bottom=min(image_height, y1 + strip_height),
    )
    return above, below


def _collect_region_text(words: list[OcrWord], *, left: int, top: int, right: int, bottom: int) -> str:
    selected = [
        word
        for word in words
        if word.right >= left and word.left <= right and word.bottom >= top and word.top <= bottom
    ]
    selected.sort(key=lambda word: (word.top, word.left))
    return " ".join(word.text for word in selected)


def _normalize_text(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = lowered.replace("diagran", "diagram").replace("diagrarn", "diagram")
    lowered = lowered.replace("diagram", "diagram").replace("ex.", "ex")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _merge_seed_rows(existing_path: Path, imported_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    existing = _read_jsonl(existing_path) if existing_path.exists() else []
    by_id = {str(row.get("id") or ""): row for row in existing if str(row.get("id") or "")}
    added = 0
    replaced = 0
    for row in imported_rows:
        row_id = str(row.get("id") or "")
        if row_id in by_id:
            replaced += 1
        else:
            added += 1
        by_id[row_id] = row
    merged = [*existing]
    seen: set[str] = set()
    for index, row in enumerate(merged):
        row_id = str(row.get("id") or "")
        if row_id in by_id and row_id not in seen:
            merged[index] = by_id[row_id]
            seen.add(row_id)
    for row_id, row in by_id.items():
        if row_id and row_id not in seen:
            merged.append(row)
            seen.add(row_id)
    return merged, {
        "existing_count": len(existing),
        "imported_count": len(imported_rows),
        "merged_count": len(merged),
        "added_count": added,
        "replaced_count": replaced,
    }


def _merge_exact_rows(existing_path: Path, imported_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    existing = _read_jsonl(existing_path) if existing_path.exists() else []
    by_digest = {
        str(row.get("sha256") or "").lower(): row
        for row in existing
        if re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or "").lower())
    }
    added = 0
    replaced = 0
    for row in imported_rows:
        digest = str(row.get("sha256") or "").lower()
        if digest in by_digest:
            replaced += 1
        else:
            added += 1
        by_digest[digest] = row
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in existing:
        digest = str(row.get("sha256") or "").lower()
        if digest and digest in by_digest and digest not in seen:
            merged.append(by_digest[digest])
            seen.add(digest)
        elif not digest:
            merged.append(row)
    for digest, row in by_digest.items():
        if digest and digest not in seen:
            merged.append(row)
            seen.add(digest)
    return merged, {
        "existing_count": len(existing),
        "imported_count": len(imported_rows),
        "merged_count": len(merged),
        "added_count": added,
        "replaced_count": replaced,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Import verified FEN priority review rows into runtime and seed labels.")
    parser.add_argument("filled_jsonl")
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    parser.add_argument("--target-seed-labels", default=str(DEFAULT_TARGET_SEED_LABELS))
    parser.add_argument("--target-verified-labels", default=str(DEFAULT_TARGET_VERIFIED_LABELS))
    parser.add_argument("--target-crops-dir", default=str(DEFAULT_TARGET_CROPS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--reviewer", default="codex-priority-review-import")
    parser.add_argument("--verified-at", default="")
    parser.add_argument("--max-candidates-per-page", type=int, default=12)
    parser.add_argument("--min-grid-confidence", type=float, default=0.45)
    parser.add_argument("--sliding-probe", action="store_true")
    parser.add_argument("--strip-ratio", type=float, default=0.22)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = import_fen_priority_review_batch(
        args.filled_jsonl,
        source_pdf=args.source_pdf,
        template_dir=args.template_dir,
        target_seed_labels=args.target_seed_labels,
        target_verified_labels=args.target_verified_labels,
        target_crops_dir=args.target_crops_dir,
        reports_dir=args.reports_dir,
        reviewer=args.reviewer,
        verified_at=args.verified_at or None,
        max_candidates_per_page=args.max_candidates_per_page,
        min_grid_confidence=args.min_grid_confidence,
        enable_sliding_probe=args.sliding_probe,
        strip_ratio=args.strip_ratio,
        apply_changes=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["unresolved_row_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
