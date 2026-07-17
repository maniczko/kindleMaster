from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any


SESSION_SCHEMA = "kindlemaster.chess.evidence_review.session.v1"
ITEM_SCHEMA = "kindlemaster.chess.evidence_review.item.v1"
VISIBLE_MARKERS = {"outline_triangle", "filled_triangle"}
TERMINAL_STATUSES = {"verified_visible", "verified_absence", "unclear", "excluded"}
ALLOWED_STATUSES = {"open", *TERMINAL_STATUSES}
ALLOWED_MARKERS = {"", *VISIBLE_MARKERS, "none_confirmed", "unclear", "multiple", "unavailable"}


class EvidenceReviewStoreError(ValueError):
    pass


def prepare_evidence_review_queue(
    coverage_rows: Iterable[Mapping[str, Any]],
    *,
    artifact_id: str,
    source_document_sha256: str,
    source_profile: str,
) -> dict[str, Any]:
    normalized_artifact = _artifact_id(artifact_id)
    source_sha = _source_sha(source_document_sha256)
    profile = _source_profile(source_profile)
    rows: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for index, source in enumerate(coverage_rows):
        row = _seed_row(
            source,
            artifact_id=normalized_artifact,
            source_sha=source_sha,
            source_profile=profile,
            queue_index=index + 1,
        )
        fingerprint = row["canonical_diagram_fingerprint"]
        if fingerprint in fingerprints:
            raise EvidenceReviewStoreError("Duplikat fingerprintu diagramu w kolejce.")
        fingerprints.add(fingerprint)
        rows.append(row)
    if not rows:
        raise EvidenceReviewStoreError("Kolejka dowodow jest pusta.")
    rows.sort(key=lambda row: (int(row["page"]), str(row["canonical_diagram_id"])))
    for index, row in enumerate(rows, start=1):
        row["queue_index"] = index
    return {
        "schema": SESSION_SCHEMA,
        "status": "active",
        "artifact_id": normalized_artifact,
        "source_document_sha256": source_sha,
        "source_profile": profile,
        "rows": rows,
        "summary": summarize_evidence_review(rows),
    }


def prepare_evidence_review_submission(
    existing: Mapping[str, Any],
    submitted: Mapping[str, Any],
    *,
    expected_revision: int,
) -> dict[str, Any]:
    row = dict(existing)
    fingerprint = _fingerprint(row.get("canonical_diagram_fingerprint"))
    if _fingerprint(submitted.get("canonical_diagram_fingerprint")) != fingerprint:
        raise EvidenceReviewStoreError("Fingerprint zapisu nie zgadza sie z rekordem kolejki.")
    current_revision = _non_negative_int(row.get("revision"), "revision")
    if int(expected_revision) != current_revision:
        raise EvidenceReviewStoreError("Rekord zostal zmieniony w innej sesji. Odswiez dane.")

    status = str(submitted.get("label_status") or "open").strip().lower()
    marker_shape = str(submitted.get("marker_shape") or "").strip().lower()
    side = str(submitted.get("side_to_move") or "").strip().lower()
    if status not in ALLOWED_STATUSES:
        raise EvidenceReviewStoreError("Nieprawidlowy status etykiety markera.")
    if marker_shape not in ALLOWED_MARKERS:
        raise EvidenceReviewStoreError("Nieprawidlowy typ markera.")
    if side not in {"", "w", "b"}:
        raise EvidenceReviewStoreError("Nieprawidlowa strona ruchu.")
    bbox = _optional_normalized_bbox(submitted.get("marker_bbox"))
    crop_complete = submitted.get("crop_complete") is True
    asset_kind = str(row.get("asset_kind") or "unavailable")

    if status == "verified_visible":
        if marker_shape not in VISIBLE_MARKERS or bbox is None or asset_kind == "unavailable":
            raise EvidenceReviewStoreError("Widoczny marker wymaga poprawnego bbox na dostepnym cropie.")
        expected_side = "w" if marker_shape == "outline_triangle" else "b"
        if side != expected_side:
            raise EvidenceReviewStoreError("Ksztalt markera nie zgadza sie ze strona ruchu.")
    if status == "verified_absence" and (marker_shape != "none_confirmed" or not crop_complete):
        raise EvidenceReviewStoreError("Brak markera wymaga potwierdzenia kompletnego cropa.")
    if status == "unclear" and marker_shape not in {"unclear", "multiple", "unavailable"}:
        raise EvidenceReviewStoreError("Status niejednoznaczny wymaga przyczyny unclear/multiple/unavailable.")

    reviewer = str(submitted.get("verified_by") or "").strip()[:160]
    if status in TERMINAL_STATUSES and not reviewer:
        raise EvidenceReviewStoreError("Podaj identyfikator osoby weryfikujacej.")
    saved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    row.update(
        {
            "label_status": status,
            "human_verified": status in TERMINAL_STATUSES,
            "marker_shape": marker_shape,
            "side_to_move": side,
            "marker_bbox": bbox,
            "marker_bbox_space": "review_asset_normalized" if bbox else "",
            "marker_bbox_verified": status == "verified_visible" and bbox is not None,
            "crop_complete": crop_complete,
            "verified_by": reviewer if status in TERMINAL_STATUSES else "",
            "verified_at": saved_at if status in TERMINAL_STATUSES else "",
            "verification_source": "human_visual" if status in TERMINAL_STATUSES else "",
            "notes": str(submitted.get("notes") or "").strip()[:4000],
            "revision": current_revision,
            "saved_at": saved_at,
        }
    )
    return row


def summarize_evidence_review(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("label_status") or "open") for row in rows)
    return {
        "total": len(rows),
        "open": counts["open"],
        "verified_visible": counts["verified_visible"],
        "verified_absence": counts["verified_absence"],
        "unclear": counts["unclear"],
        "excluded": counts["excluded"],
        "complete_marker_evidence": counts["verified_visible"] + counts["verified_absence"],
    }


def export_marker_labels(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    exported = []
    for row in rows:
        status = str(row.get("label_status") or "open")
        if status not in TERMINAL_STATUSES:
            continue
        marker_shape = str(row.get("marker_shape") or "")
        crop_label = (
            "clear"
            if status == "verified_visible"
            else "complete_no_marker"
            if status == "verified_absence"
            else "unreadable"
        )
        exported.append(
            {
                "schema": "kindlemaster.chess.marker_review_label.v2",
                "diagram_id": str(row.get("legacy_intake_diagram_id") or ""),
                "canonical_diagram_fingerprint": str(row.get("canonical_diagram_fingerprint") or ""),
                "source_document_sha256": str(row.get("source_document_sha256") or ""),
                "source_profile": str(row.get("source_profile") or ""),
                "page": int(row.get("page") or 0),
                "label_status": "verified",
                "human_verified": True,
                "verification_source": "human_visual",
                "manual_visible_marker": marker_shape,
                "manual_side_to_move": str(row.get("side_to_move") or ""),
                "manual_marker_bbox": row.get("marker_bbox") or "",
                "marker_bbox_space": str(row.get("marker_bbox_space") or ""),
                "marker_bbox_verified": row.get("marker_bbox_verified") is True,
                "marker_crop_label": crop_label,
                "verified_by": str(row.get("verified_by") or ""),
                "verified_at": str(row.get("verified_at") or ""),
                "manual_notes": str(row.get("notes") or ""),
            }
        )
    return exported


def _seed_row(
    source: Mapping[str, Any],
    *,
    artifact_id: str,
    source_sha: str,
    source_profile: str,
    queue_index: int,
) -> dict[str, Any]:
    if str(source.get("source_profile") or "").strip().lower() != source_profile:
        raise EvidenceReviewStoreError("Profil zrodla rekordu nie zgadza sie z kolejka.")
    fingerprint = _fingerprint(source.get("canonical_diagram_fingerprint"))
    page = _positive_int(source.get("page"), "page")
    canonical_bbox = _normalized_bbox(source.get("normalized_bbox_xyxy"))
    fen = dict(source.get("fen_evidence") or {})
    marker = dict(source.get("marker_evidence") or {})
    marker_asset = _relative_asset(marker.get("marker_crop_rel_path"))
    board_asset = _relative_asset(fen.get("crop_rel_path"))
    asset_rel_path = marker_asset or board_asset
    asset_kind = "marker_crop" if marker_asset else "board_crop" if board_asset else "unavailable"
    marker_shape = str(marker.get("manual_visible_marker") or "").strip().lower()
    if marker_shape not in ALLOWED_MARKERS:
        marker_shape = ""
    side = str(marker.get("manual_side_to_move") or "").strip().lower()
    if side not in {"w", "b"}:
        side = ""
    return {
        "schema": ITEM_SCHEMA,
        "artifact_id": artifact_id,
        "source_document_sha256": source_sha,
        "source_profile": source_profile,
        "canonical_diagram_fingerprint": fingerprint,
        "canonical_diagram_id": str(source.get("canonical_diagram_id") or "").strip(),
        "legacy_intake_diagram_id": str(source.get("legacy_intake_diagram_id") or "").strip(),
        "page": page,
        "queue_index": queue_index,
        "normalized_bbox_xyxy": canonical_bbox,
        "identity_status": str(source.get("identity_status") or ""),
        "fen_status": str(fen.get("status") or ""),
        "asset_kind": asset_kind,
        "asset_rel_path": asset_rel_path,
        "suggested_marker_shape": marker_shape,
        "suggested_side_to_move": side,
        "suggested_bbox": _optional_bbox(marker.get("review_suggestion_bbox")),
        "label_status": "open",
        "human_verified": False,
        "marker_shape": marker_shape,
        "side_to_move": side,
        "marker_bbox": None,
        "marker_bbox_space": "",
        "marker_bbox_verified": False,
        "crop_complete": False,
        "verified_by": "",
        "verified_at": "",
        "verification_source": "",
        "notes": "",
        "revision": 0,
        "blockers": [str(value) for value in source.get("blockers") or []],
    }


def _relative_asset(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute() or ":" in raw or ".." in path.parts:
        return ""
    return str(path)


def _fingerprint(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"dfp_[0-9a-f]{32}", normalized):
        raise EvidenceReviewStoreError("Nieprawidlowy kanoniczny fingerprint diagramu.")
    return normalized


def _source_sha(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise EvidenceReviewStoreError("Nieprawidlowy SHA dokumentu zrodlowego.")
    return normalized


def _source_profile(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", normalized):
        raise EvidenceReviewStoreError("Nieprawidlowy profil zrodla.")
    return normalized


def _artifact_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 200:
        raise EvidenceReviewStoreError("Nieprawidlowy identyfikator artefaktu.")
    return normalized


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise EvidenceReviewStoreError(f"Nieprawidlowe pole {label}.") from error
    if parsed <= 0:
        raise EvidenceReviewStoreError(f"Nieprawidlowe pole {label}.")
    return parsed


def _non_negative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise EvidenceReviewStoreError(f"Nieprawidlowe pole {label}.") from error
    if parsed < 0:
        raise EvidenceReviewStoreError(f"Nieprawidlowe pole {label}.")
    return parsed


def _normalized_bbox(value: Any) -> list[float]:
    bbox = _optional_normalized_bbox(value)
    if bbox is None:
        raise EvidenceReviewStoreError("Brak poprawnego bbox diagramu.")
    return bbox


def _optional_normalized_bbox(value: Any) -> list[float] | None:
    bbox = _optional_bbox(value)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    if not 0.0 <= x0 < x1 <= 1.0 or not 0.0 <= y0 < y1 <= 1.0:
        raise EvidenceReviewStoreError("Bbox markera musi byc znormalizowany do zakresu 0..1.")
    return bbox


def _optional_bbox(value: Any) -> list[float] | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise EvidenceReviewStoreError("Nieprawidlowy bbox.")
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError) as error:
        raise EvidenceReviewStoreError("Nieprawidlowy bbox.") from error
    if x1 <= x0 or y1 <= y0:
        raise EvidenceReviewStoreError("Nieprawidlowy bbox.")
    return [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)]
