from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from chess_fen_review_ui import render_fen_manual_review_html


_PIECES = frozenset({"", "K", "Q", "R", "B", "N", "P", "k", "q", "r", "b", "n", "p"})
_SAFE_FILENAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


class FenReviewBuildError(RuntimeError):
    pass


def build_conversion_fen_review(
    *,
    artifact_id: str,
    diagrams_path: str | Path,
) -> dict[str, Any]:
    """Build the persistent 8x8 review bundle from conversion diagnostics."""
    diagrams_file = Path(diagrams_path).resolve()
    if diagrams_file.name != "chess_diagrams.json" or diagrams_file.parent.name != "report":
        raise FenReviewBuildError("chess_diagrams.json must use the canonical artifact layout")
    root = diagrams_file.parent.parent
    if not diagrams_file.is_file():
        raise FenReviewBuildError("chess_diagrams.json is missing")

    payload = _read_json(diagrams_file)
    records = payload.get("records") if isinstance(payload, Mapping) else None
    if not isinstance(records, list) or not records:
        raise FenReviewBuildError("chess_diagrams.json does not contain diagram records")

    review_dir = root / "review"
    assets_dir = review_dir / "fen_manual_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    source_file = _canonical_input_file(root)
    source_digest = _sha256_file(source_file or diagrams_file)
    source_binding = "source_pdf_sha256" if source_file is not None else "artifact_report_sha256"
    report_digest = _sha256_file(diagrams_file)

    rows: list[dict[str, Any]] = []
    missing_boards: list[str] = []
    seen_diagram_ids: set[str] = set()
    for index, raw_record in enumerate(records, start=1):
        if not isinstance(raw_record, Mapping):
            continue
        record = dict(raw_record)
        diagram_id = str(record.get("diagram_id") or record.get("id") or f"diagram-{index}").strip()
        if diagram_id in seen_diagram_ids:
            raise FenReviewBuildError(f"duplicate diagram id: {diagram_id}")
        seen_diagram_ids.add(diagram_id)
        board_source = _resolve_source_asset(root, record.get("board_crop_path"))
        if board_source is None:
            missing_boards.append(diagram_id)
            continue

        board = _materialize_asset(board_source, assets_dir, index, "board")
        marker_source = _first_source_asset(
            root,
            record,
            "side_marker_review_crop_path",
            "side_marker_crop_path",
        )
        marker_search_source = _first_source_asset(
            root,
            record,
            "marker_search_zone_preview_path",
            "side_marker_search_crop_path",
        ) or marker_source
        context_source = _first_source_asset(
            root,
            record,
            "debug_context_crop_path",
            "debug_overlay_path",
        )
        marker = _materialize_optional_asset(marker_source, assets_dir, index, "marker")
        marker_search = _materialize_optional_asset(
            marker_search_source,
            assets_dir,
            index,
            "marker-search",
        )
        context = _materialize_optional_asset(context_source, assets_dir, index, "context")

        runtime = record.get("model_runtime") if isinstance(record.get("model_runtime"), Mapping) else {}
        squares = runtime.get("squares") if isinstance(runtime, Mapping) else []
        square_labels = _square_labels(squares, fallback_placement=str(runtime.get("placement") or ""))
        blockers = _unique_strings(
            [
                *_as_values(record.get("recognition_blockers")),
                *_as_values(record.get("full_fen_blockers")),
                *_as_values(runtime.get("blockers")),
                runtime.get("owning_blocker"),
                record.get("reason"),
            ]
        )
        side = str(record.get("side_to_move") or "").strip().lower()
        side = side if side in {"w", "b"} else ""
        candidate = str(
            runtime.get("validation_fen")
            or record.get("full_fen")
            or record.get("fen_candidate")
            or ""
        ).strip()
        board_sha = _sha256_file(board[0])
        fingerprint = str(record.get("diagram_fingerprint") or "").strip() or _stable_fingerprint(
            source_digest,
            diagram_id,
            record.get("page_number") or record.get("page") or record.get("page_index") or 0,
        )
        model_conflict = "model_template_conflict" in blockers
        row = {
            "schema": "kindlemaster.fen_manual_review.row.v4",
            "review_contract": "source_bound_piece_grid_v2",
            "artifact_id": str(artifact_id),
            "diagram_id": diagram_id,
            "diagram_fingerprint": fingerprint,
            "source_document_sha256": source_digest,
            "source_artifact_sha256": report_digest,
            "source_binding": source_binding,
            "source_pdf": source_file.name if source_file is not None else "",
            "page": _positive_int(record.get("page_number") or record.get("page") or 0),
            "reading_order": _positive_int(record.get("source_order") or index),
            "bbox": record.get("board_bbox") or record.get("bbox") or [0, 0, 0, 0],
            "caption": str(record.get("caption") or f"Diagram {index}"),
            "crop_path": str(board[0]),
            "crop_rel_path": board[1],
            "crop_sha256": board_sha,
            "board_crop_path": board[1],
            "board_crop_rel_path": board[1],
            "board_crop_sha256": board_sha,
            "source_crop_path": str(board_source),
            "context_crop_path": str(context[0]) if context else "",
            "context_crop_rel_path": context[1] if context else "",
            "context_crop_sha256": _sha256_file(context[0]) if context else "",
            "marker_crop_path": str(marker[0]) if marker else "",
            "marker_crop_rel_path": marker[1] if marker else "",
            "marker_crop_sha256": _sha256_file(marker[0]) if marker else "",
            "marker_search_crop_path": str(marker_search[0]) if marker_search else "",
            "marker_search_crop_rel_path": marker_search[1] if marker_search else "",
            "marker_search_crop_sha256": _sha256_file(marker_search[0]) if marker_search else "",
            "marker_review_crop_kind": str(record.get("side_marker_review_crop_kind") or "detected_marker_bbox"),
            "detected_marker_symbol": str(record.get("side_marker_symbol") or ""),
            "detected_marker_status": str(record.get("side_marker_status") or ""),
            "board_crop_quality": str(record.get("board_crop_quality") or ""),
            "marker_crop_quality": str(record.get("marker_crop_quality") or ""),
            "marker_crop_fail_reason": record.get("marker_crop_fail_reason") or [],
            "current_fen": "",
            "fen_candidate": candidate,
            "candidate_source": "portable_rbf_svm" if runtime else "conversion_candidate",
            "legacy_verified_fen": "",
            "model_conflict": model_conflict,
            "review_priority": _review_priority(blockers, side=side, model_conflict=model_conflict),
            "manual_fen": "",
            "square_labels": square_labels,
            "piece_labels_verified": False,
            "fen_human_verified": False,
            "piece_labels_source": "model_candidate_draft" if any(square_labels) else "unlabeled",
            "side_to_move": side,
            "manual_side_to_move": "",
            "manual_side_evidence": "",
            "manual_visible_marker": "",
            "board_crop_label": "",
            "marker_crop_label": "",
            "confidence": float(runtime.get("confidence") or record.get("fen_confidence") or 0.0),
            "validation_status": "FEN_REVIEW_REQUIRED",
            "review_reason": str(record.get("reason") or runtime.get("owning_blocker") or "piece_labels_required"),
            "review_blockers": blockers,
            "manual_label": "needs_piece_labels",
            "label_status": "needs_piece_labels",
            "human_verified": False,
            "verified_by": "",
            "verified_at": "",
            "verification_source": "",
            "label_provenance": "",
            "notes": "",
            "policy": "human_labels_train_and_evaluate_only_no_direct_fen_publication",
            "review_index": index,
            "manual_placement": _cells_to_placement(square_labels),
        }
        rows.append(row)

    if missing_boards:
        raise FenReviewBuildError(
            f"missing board assets for {len(missing_boards)} diagram(s): {', '.join(missing_boards[:5])}"
        )
    if len(rows) != len(records):
        raise FenReviewBuildError("not all diagram records could be converted into review rows")

    draft_path = review_dir / "fen_manual_draft.jsonl"
    html_path = review_dir / "fen_manual_review.html"
    _write_jsonl(draft_path, rows)
    _atomic_write_text(
        html_path,
        render_fen_manual_review_html(rows, source_identity=rows[0], artifact_id=str(artifact_id)),
    )
    return {
        "status": "ok",
        "artifact_id": str(artifact_id),
        "diagram_count": len(rows),
        "source_document_sha256": source_digest,
        "source_binding": source_binding,
        "review_html": str(html_path),
        "draft_jsonl": str(draft_path),
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FenReviewBuildError(f"cannot read {path.name}: {error}") from error


def _canonical_input_file(root: Path) -> Path | None:
    input_dir = _named_child(root, "input", directory_only=True)
    if input_dir is None:
        return None
    try:
        files = [Path(entry.path) for entry in os.scandir(input_dir) if entry.is_file(follow_symlinks=False)]
    except OSError:
        return None
    return files[0] if len(files) == 1 else None


def _resolve_source_asset(root: Path, value: object) -> Path | None:
    relative = str(value or "").replace("\\", "/").strip().lstrip("/")
    parts = relative.split("/")
    if len(parts) != 4 or parts[:3] != ["review", "chess_fen", "two_crop"]:
        return None
    filename = parts[3]
    if not filename or filename in {".", ".."} or any(char not in _SAFE_FILENAME_CHARS for char in filename):
        return None
    review_dir = _named_child(root, "review", directory_only=True)
    chess_fen_dir = _named_child(review_dir, "chess_fen", directory_only=True) if review_dir else None
    two_crop_dir = _named_child(chess_fen_dir, "two_crop", directory_only=True) if chess_fen_dir else None
    return _named_child(two_crop_dir, filename) if two_crop_dir else None


def _named_child(directory: Path, name: str, *, directory_only: bool = False) -> Path | None:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name != name:
                    continue
                if directory_only and not entry.is_dir(follow_symlinks=False):
                    return None
                if not directory_only and not entry.is_file(follow_symlinks=False):
                    return None
                return Path(entry.path)
    except OSError:
        return None
    return None


def _first_source_asset(root: Path, record: Mapping[str, Any], *keys: str) -> Path | None:
    for key in keys:
        candidate = _resolve_source_asset(root, record.get(key))
        if candidate is not None:
            return candidate
    return None


def _materialize_optional_asset(
    source: Path | None,
    assets_dir: Path,
    asset_index: int,
    kind: str,
) -> tuple[Path, str] | None:
    return _materialize_asset(source, assets_dir, asset_index, kind) if source is not None else None


def _materialize_asset(source: Path, assets_dir: Path, asset_index: int, kind: str) -> tuple[Path, str]:
    digest = _sha256_file(source)
    if kind not in {"board", "context", "marker", "marker-search"}:
        raise FenReviewBuildError("unsupported review asset kind")
    suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    target = assets_dir / f"diagram-{max(1, int(asset_index)):04d}_{kind}_{digest[:12]}{suffix}"
    if target.is_file() and _sha256_file(target) != digest:
        raise FenReviewBuildError(f"existing review asset has invalid content: {target.name}")
    if not target.is_file():
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    return target, f"fen_manual_assets/{target.name}"


def _square_labels(squares: object, *, fallback_placement: str) -> list[str]:
    if isinstance(squares, Sequence) and not isinstance(squares, (str, bytes)) and len(squares) == 64:
        labels = []
        for square in squares:
            piece = str(square.get("piece") or "") if isinstance(square, Mapping) else ""
            labels.append(piece if piece in _PIECES else "")
        return labels
    try:
        return _placement_to_cells(fallback_placement)
    except ValueError:
        return [""] * 64


def _placement_to_cells(placement: str) -> list[str]:
    cells: list[str] = []
    ranks = str(placement or "").split("/")
    if len(ranks) != 8:
        raise ValueError("invalid placement")
    for rank in ranks:
        for token in rank:
            if token.isdigit():
                cells.extend([""] * int(token))
            elif token in _PIECES and token:
                cells.append(token)
            else:
                raise ValueError("invalid placement")
    if len(cells) != 64:
        raise ValueError("invalid placement")
    return cells


def _cells_to_placement(cells: Sequence[str]) -> str:
    ranks: list[str] = []
    for offset in range(0, 64, 8):
        empty = 0
        parts: list[str] = []
        for piece in cells[offset : offset + 8]:
            if not piece:
                empty += 1
                continue
            if empty:
                parts.append(str(empty))
                empty = 0
            parts.append(str(piece))
        if empty:
            parts.append(str(empty))
        ranks.append("".join(parts) or "8")
    return "/".join(ranks)


def _review_priority(blockers: Sequence[str], *, side: str, model_conflict: bool) -> int:
    joined = " ".join(blockers)
    if model_conflict or "king_count_invalid" in joined or "pawn_on_back_rank" in joined:
        return 0
    if not side or "side_to_move_unknown" in joined:
        return 10
    return 20


def _stable_fingerprint(source_digest: str, diagram_id: str, page: object) -> str:
    material = f"{source_digest}:{diagram_id}:{_positive_int(page)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _positive_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _unique_strings(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _as_values(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [] if value is None or value == "" else [value]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    content = "".join(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    _atomic_write_text(path, content)


def _atomic_write_text(path: Path, content: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temp, path)
