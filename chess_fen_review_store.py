from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chess_fen_review_contract import normalize_review_row_for_gold_contract


FEN_REVIEW_DRAFT_FILENAME = "fen_manual_draft.jsonl"
FEN_REVIEW_PROGRESS_FILENAME = "fen_piece_grid_progress.jsonl"
FEN_REVIEW_PROGRESS_BACKUP_FILENAME = "fen_piece_grid_progress.previous.jsonl"
FEN_REVIEW_PROGRESS_META_FILENAME = "fen_piece_grid_progress.meta.json"

_PIECES = frozenset({"", "K", "Q", "R", "B", "N", "P", "k", "q", "r", "b", "n", "p"})
_LABEL_STATUSES = frozenset(
    {"needs_piece_labels", "verified", "placement_verified", "rejected", "unreadable"}
)
_BOARD_CROP_LABELS = frozenset({"", "correct", "cropped", "wrong", "unreadable"})
_MARKER_CROP_LABELS = frozenset({"", "clear", "complete_no_marker", "cropped", "wrong", "unreadable"})
_VISIBLE_MARKERS = frozenset(
    {"", "outline_triangle", "filled_triangle", "none_confirmed", "unclear", "multiple", "unavailable"}
)
_SIDES = frozenset({"", "w", "b"})
_SIDE_EVIDENCE = frozenset({"", "marker", "caption", "verified_source", "unknown"})
_MAX_REVIEW_ROWS = 2000
_MAX_NOTES_LENGTH = 4000
_MAX_REVIEWER_LENGTH = 200


class FenReviewStoreError(ValueError):
    pass


class FenReviewConflictError(FenReviewStoreError):
    pass


class FenReviewSessionClosedError(FenReviewStoreError):
    pass


class FenReviewOwnershipError(FenReviewStoreError):
    pass


def load_fen_review_progress(
    review_dir: str | Path,
    *,
    persisted_rows: Sequence[Mapping[str, Any]] | None = None,
    persisted_saved_at: str = "",
    storage: str = "",
) -> dict[str, Any]:
    review_path = Path(review_dir)
    seed_rows = _read_jsonl(review_path / FEN_REVIEW_DRAFT_FILENAME)
    if persisted_rows is not None:
        progress_rows = [dict(row) for row in persisted_rows]
        rows = _merge_rows(seed_rows, progress_rows) if progress_rows else seed_rows
        saved_at = persisted_saved_at
        resolved_storage = storage or "database"
    else:
        progress_path = review_path / FEN_REVIEW_PROGRESS_FILENAME
        progress_rows = _read_jsonl(progress_path) if progress_path.is_file() else []
        rows = _merge_rows(seed_rows, progress_rows) if progress_rows else seed_rows
        saved_at = _progress_saved_at(review_path)
        resolved_storage = storage or ("server" if progress_path.is_file() else "seed")
    summary = summarize_fen_review_rows(rows)
    return {
        "schema": "kindlemaster.fen_review_progress.v1",
        "status": "ok",
        "rows": rows,
        "summary": summary,
        "saved_at": saved_at,
        "storage": resolved_storage,
    }


def prepare_fen_review_progress(
    review_dir: str | Path,
    submitted_rows: Sequence[Mapping[str, Any]],
    *,
    artifact_id: str,
    source_digest: str = "",
    existing_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    review_path = Path(review_dir)
    seed_rows = _read_jsonl(review_path / FEN_REVIEW_DRAFT_FILENAME)
    if not seed_rows:
        raise FenReviewStoreError("Brak źródłowego pliku fen_manual_draft.jsonl.")
    if not isinstance(submitted_rows, Sequence) or isinstance(submitted_rows, (str, bytes)):
        raise FenReviewStoreError("Pole rows musi być listą rekordów.")
    if len(submitted_rows) > _MAX_REVIEW_ROWS:
        raise FenReviewStoreError(f"Przekroczono limit {_MAX_REVIEW_ROWS} rekordów.")

    expected_artifact = str(seed_rows[0].get("artifact_id") or artifact_id or "").strip()
    if expected_artifact and str(artifact_id or "").strip() != expected_artifact:
        raise FenReviewStoreError("Identyfikator artefaktu nie zgadza się ze źródłowym raportem.")
    expected_digest = _source_digest(seed_rows)
    if source_digest and expected_digest and str(source_digest).strip() != expected_digest:
        raise FenReviewStoreError("SHA źródła nie zgadza się ze źródłowym raportem.")

    if existing_rows is None:
        existing_path = review_path / FEN_REVIEW_PROGRESS_FILENAME
        resolved_existing_rows = _read_jsonl(existing_path) if existing_path.is_file() else []
    else:
        resolved_existing_rows = [dict(row) for row in existing_rows]
    base_rows = _merge_rows(seed_rows, resolved_existing_rows) if resolved_existing_rows else seed_rows
    submitted_by_fingerprint: dict[str, Mapping[str, Any]] = {}
    known_fingerprints = {_fingerprint(row) for row in seed_rows}
    for raw_row in submitted_rows:
        if not isinstance(raw_row, Mapping):
            raise FenReviewStoreError("Każdy rekord postępu musi być obiektem JSON.")
        fingerprint = _fingerprint(raw_row)
        if not fingerprint or fingerprint not in known_fingerprints:
            raise FenReviewStoreError("Rekord nie jest powiązany ze źródłowym cropem.")
        if fingerprint in submitted_by_fingerprint:
            raise FenReviewStoreError("Ten sam diagram występuje w zapisie więcej niż raz.")
        submitted_by_fingerprint[fingerprint] = raw_row

    normalized_rows = []
    for base_row in base_rows:
        fingerprint = _fingerprint(base_row)
        submitted = submitted_by_fingerprint.get(fingerprint)
        normalized_rows.append(_normalize_progress_row(base_row, submitted) if submitted else dict(base_row))

    saved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    summary = summarize_fen_review_rows(normalized_rows)
    return {
        "schema": "kindlemaster.fen_review_progress.v1",
        "status": "prepared",
        "artifact_id": expected_artifact or artifact_id,
        "source_document_sha256": expected_digest,
        "saved_at": saved_at,
        "submitted_count": len(submitted_by_fingerprint),
        "summary": summary,
        "rows": normalized_rows,
    }


def persist_fen_review_progress_snapshot(
    review_dir: str | Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    artifact_id: str,
    source_digest: str,
    saved_at: str = "",
    submitted_count: int | None = None,
) -> dict[str, Any]:
    review_path = Path(review_dir)
    review_path.mkdir(parents=True, exist_ok=True)
    existing_path = review_path / FEN_REVIEW_PROGRESS_FILENAME
    if existing_path.is_file():
        shutil.copy2(existing_path, review_path / FEN_REVIEW_PROGRESS_BACKUP_FILENAME)
    normalized_rows = [dict(row) for row in rows]
    _atomic_write_jsonl(existing_path, normalized_rows)
    resolved_saved_at = saved_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    resolved_submitted_count = len(normalized_rows) if submitted_count is None else submitted_count
    summary = summarize_fen_review_rows(normalized_rows)
    _atomic_write_json(
        review_path / FEN_REVIEW_PROGRESS_META_FILENAME,
        {
            "schema": "kindlemaster.fen_review_progress.meta.v1",
            "artifact_id": artifact_id,
            "source_document_sha256": source_digest,
            "saved_at": resolved_saved_at,
            "submitted_count": resolved_submitted_count,
            "summary": summary,
            "policy": (
                "This file is a server cache and export snapshot. Supabase is the primary store when configured. "
                "Human labels remain training and evaluation evidence; this save never accepts FEN for publication."
            ),
        },
    )
    return {
        "schema": "kindlemaster.fen_review_progress.v1",
        "status": "saved",
        "saved_at": resolved_saved_at,
        "submitted_count": resolved_submitted_count,
        "summary": summary,
        "storage": "server",
    }


def save_fen_review_progress(
    review_dir: str | Path,
    submitted_rows: Sequence[Mapping[str, Any]],
    *,
    artifact_id: str,
    source_digest: str = "",
) -> dict[str, Any]:
    prepared = prepare_fen_review_progress(
        review_dir,
        submitted_rows,
        artifact_id=artifact_id,
        source_digest=source_digest,
    )
    return persist_fen_review_progress_snapshot(
        review_dir,
        prepared["rows"],
        artifact_id=str(prepared["artifact_id"]),
        source_digest=str(prepared["source_document_sha256"]),
        saved_at=str(prepared["saved_at"]),
        submitted_count=int(prepared["submitted_count"]),
    )


def summarize_fen_review_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    verified = 0
    placement_verified = 0
    excluded = 0
    pending = 0
    invalid = 0
    for row in rows:
        status = str(row.get("label_status") or "needs_piece_labels")
        errors = validate_fen_review_row(row)
        if status == "verified" and not errors:
            verified += 1
        elif status == "placement_verified" and not errors:
            placement_verified += 1
        elif status in {"rejected", "unreadable"} and not errors:
            excluded += 1
        elif status in {"verified", "placement_verified", "rejected", "unreadable"}:
            invalid += 1
        else:
            pending += 1
    return {
        "total": len(rows),
        "completed": verified + placement_verified + excluded,
        "verified": verified,
        "placement_verified": placement_verified,
        "excluded": excluded,
        # Backward-compatible alias used by older review pages.
        "closed": excluded,
        "pending": pending,
        "invalid": invalid,
    }


def validate_fen_review_row(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    status = str(row.get("label_status") or "needs_piece_labels")
    if status not in _LABEL_STATUSES:
        return ["Nieznany status etykiety."]
    if status in {"verified", "placement_verified"}:
        cells = row.get("square_labels")
        if not isinstance(cells, list) or len(cells) != 64 or any(str(piece) not in _PIECES for piece in cells):
            errors.append("Siatka musi zawierać 64 poprawne klasy pól.")
        else:
            if cells.count("K") != 1 or cells.count("k") != 1:
                errors.append("Plansza musi zawierać dokładnie jednego króla każdego koloru.")
            if any(piece in {"P", "p"} for piece in [*cells[:8], *cells[56:]]):
                errors.append("Pion nie może stać w pierwszym ani ósmym rzędzie.")
        if row.get("piece_labels_verified") is not True:
            errors.append("Brak potwierdzenia 64 pól.")
        if str(row.get("board_crop_label") or "") not in {"correct", "cropped"}:
            errors.append("Crop planszy nie jest oznaczony jako czytelny.")
        if status == "verified":
            if str(row.get("marker_crop_label") or "") not in {"clear", "complete_no_marker"}:
                errors.append("Dowód markera nie jest oznaczony jako czytelny.")
            if str(row.get("manual_side_to_move") or "") not in {"w", "b"}:
                errors.append("Brak potwierdzonej strony ruchu.")
            if str(row.get("manual_side_evidence") or "") not in {"marker", "caption", "verified_source"}:
                errors.append("Brak rozstrzygającego dowodu strony ruchu.")
        if not str(row.get("verified_by") or "").strip():
            errors.append("Brak identyfikatora osoby oznaczającej.")
    elif status in {"rejected", "unreadable"} and not str(row.get("verified_by") or "").strip():
        errors.append("Brak identyfikatora osoby oznaczającej.")
    return errors


def _normalize_progress_row(base_row: Mapping[str, Any], submitted: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(base_row)
    cells = submitted.get("square_labels")
    if not isinstance(cells, list) or len(cells) != 64:
        raise FenReviewStoreError("Siatka diagramu musi zawierać dokładnie 64 pola.")
    normalized_cells = [str(piece or "") for piece in cells]
    if any(piece not in _PIECES for piece in normalized_cells):
        raise FenReviewStoreError("Siatka diagramu zawiera nieznaną klasę figury.")

    status = _enum_value(submitted, "label_status", _LABEL_STATUSES, "needs_piece_labels")
    side = _enum_value(submitted, "manual_side_to_move", _SIDES, "")
    piece_labels_verified = submitted.get("piece_labels_verified") is True
    board_crop_label = _enum_value(submitted, "board_crop_label", _BOARD_CROP_LABELS, "")
    marker_crop_label = _enum_value(submitted, "marker_crop_label", _MARKER_CROP_LABELS, "")
    side_evidence = _enum_value(submitted, "manual_side_evidence", _SIDE_EVIDENCE, "")
    status_migration = _bounded_text(submitted.get("status_migration"), 120)
    if status == "verified" and piece_labels_verified and board_crop_label in {"correct", "cropped"}:
        has_full_fen_evidence = (
            side in {"w", "b"}
            and side_evidence in {"marker", "caption", "verified_source"}
            and marker_crop_label in {"clear", "complete_no_marker"}
        )
        if not has_full_fen_evidence:
            status = "placement_verified"
            status_migration = "verified_without_full_fen_to_placement_verified_v1"
    row.update(
        {
            "schema": "kindlemaster.fen_manual_review.row.v4",
            "review_contract": "source_bound_piece_grid_v2",
            "square_labels": normalized_cells,
            "piece_labels_verified": piece_labels_verified,
            "manual_side_to_move": side,
            "manual_side_evidence": side_evidence,
            "manual_visible_marker": _enum_value(submitted, "manual_visible_marker", _VISIBLE_MARKERS, ""),
            "board_crop_label": board_crop_label,
            "marker_crop_label": marker_crop_label,
            "label_status": status,
            "status_migration": status_migration,
            "verified_by": _bounded_text(submitted.get("verified_by"), _MAX_REVIEWER_LENGTH),
            "notes": _bounded_text(submitted.get("notes"), _MAX_NOTES_LENGTH),
        }
    )
    placement = _cells_to_placement(normalized_cells)
    terminal = status in {"verified", "placement_verified", "rejected", "unreadable"}
    row.update(
        {
            "manual_placement": placement,
            "manual_fen": f"{placement} {side} - - 0 1" if status == "verified" and side in {"w", "b"} else "",
            "fen_human_verified": status == "verified" and piece_labels_verified,
            "placement_human_verified": status in {"verified", "placement_verified"} and piece_labels_verified,
            "piece_labels_source": (
                "human_visual_64_square_grid" if piece_labels_verified else "model_candidate_draft"
            ),
            "manual_label": _manual_label(status, str(row.get("board_crop_label") or "")),
            "human_verified": terminal,
            "verified_at": (
                _bounded_text(submitted.get("verified_at"), 100)
                or str(base_row.get("verified_at") or "")
                or datetime.now(UTC).isoformat().replace("+00:00", "Z")
                if terminal
                else ""
            ),
            "verification_source": (
                "human_visual_64_square_grid"
                if status == "placement_verified"
                else "human_visual_piece_grid_and_marker"
                if terminal
                else ""
            ),
            "label_provenance": "human_visual_source_bound_piece_grid_review" if terminal else "",
        }
    )
    return normalize_review_row_for_gold_contract(row)


def _merge_rows(seed_rows: Sequence[Mapping[str, Any]], progress_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    progress_by_fingerprint = {_fingerprint(row): row for row in progress_rows if _fingerprint(row)}
    seed_digest = _source_digest(seed_rows)
    progress_digest = _source_digest(progress_rows)
    progress_by_diagram_id: dict[str, Mapping[str, Any]] = {}
    if seed_digest and seed_digest == progress_digest:
        progress_ids: dict[str, list[Mapping[str, Any]]] = {}
        seed_id_counts: dict[str, int] = {}
        for row in progress_rows:
            diagram_id = str(row.get("diagram_id") or "").strip()
            if diagram_id:
                progress_ids.setdefault(diagram_id, []).append(row)
        for row in seed_rows:
            diagram_id = str(row.get("diagram_id") or "").strip()
            if diagram_id:
                seed_id_counts[diagram_id] = seed_id_counts.get(diagram_id, 0) + 1
        progress_by_diagram_id = {
            diagram_id: candidates[0]
            for diagram_id, candidates in progress_ids.items()
            if len(candidates) == 1 and seed_id_counts.get(diagram_id) == 1
        }
    merged = []
    for seed in seed_rows:
        progress = progress_by_fingerprint.get(_fingerprint(seed))
        if progress is None:
            progress = progress_by_diagram_id.get(str(seed.get("diagram_id") or "").strip())
        if progress is None:
            merged.append(dict(seed))
            continue
        editable = {
            key: progress.get(key)
            for key in (
                "square_labels",
                "piece_labels_verified",
                "manual_side_to_move",
                "manual_side_evidence",
                "manual_visible_marker",
                "board_crop_label",
                "marker_crop_label",
                "label_status",
                "status_migration",
                "verified_by",
                "verified_at",
                "notes",
            )
        }
        merged.append(_normalize_progress_row(seed, editable))
    return merged


def _manual_label(status: str, board_crop_label: str) -> str:
    if status == "rejected":
        return "false_positive"
    if status == "unreadable":
        return "uncertain"
    if status not in {"verified", "placement_verified"}:
        return "needs_piece_labels"
    return "cropped_diagram" if board_crop_label == "cropped" else "correct_diagram"


def _cells_to_placement(cells: Sequence[str]) -> str:
    ranks = []
    for rank_index in range(8):
        empty = 0
        output = ""
        for piece in cells[rank_index * 8 : (rank_index + 1) * 8]:
            if not piece:
                empty += 1
                continue
            if empty:
                output += str(empty)
                empty = 0
            output += piece
        if empty:
            output += str(empty)
        ranks.append(output or "8")
    return "/".join(ranks)


def _enum_value(row: Mapping[str, Any], key: str, allowed: frozenset[str], default: str) -> str:
    value = str(row.get(key) or default)
    if value not in allowed:
        raise FenReviewStoreError(f"Nieznana wartość pola {key}.")
    return value


def _bounded_text(value: Any, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise FenReviewStoreError("Tekst w rekordzie postępu jest zbyt długi.")
    return text


def _fingerprint(row: Mapping[str, Any]) -> str:
    return str(row.get("diagram_fingerprint") or "").strip()


def _source_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    for row in rows:
        value = str(row.get("source_document_sha256") or row.get("source_artifact_sha256") or "").strip()
        if value:
            return value
    return ""


def _progress_saved_at(review_dir: Path) -> str:
    meta_path = review_dir / FEN_REVIEW_PROGRESS_META_FILENAME
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("saved_at"):
            return str(payload["saved_at"])
    progress_path = review_dir / FEN_REVIEW_PROGRESS_FILENAME
    if progress_path.is_file():
        return datetime.fromtimestamp(progress_path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
    return ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise FenReviewStoreError(f"Niepoprawny JSONL w wierszu {line_number}: {exc.msg}.") from exc
        if not isinstance(row, dict):
            raise FenReviewStoreError(f"Wiersz {line_number} nie jest obiektem JSON.")
        rows.append(row)
    return rows


def _atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
