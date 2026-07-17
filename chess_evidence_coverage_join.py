from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from chess_diagram_fingerprint import normalized_bbox, source_document_sha256


REPORT_SCHEMA = "kindlemaster.chess.evidence_coverage_report.v1"
COVERAGE_ROW_SCHEMA = "kindlemaster.chess.evidence_coverage.row.v1"
QUEUE_ROW_SCHEMA = "kindlemaster.chess.evidence_review_queue.row.v1"
RESOLVED_IDENTITY_STATUSES = {"exact_fingerprint", "page_bbox_one_to_one"}
REQUIRED_HARD_NEGATIVE_KINDS = {
    "arrows",
    "borders",
    "captions",
    "coordinates",
    "letters",
    "neighboring_diagrams",
}
VISIBLE_MARKERS = {"outline_triangle", "filled_triangle"}
REVIEW_ONLY_MARKERS = {"unclear", "multiple", "unavailable"}
UNBOUND_MARKER_STATUSES = {"orphan_legacy_id", "page_mismatch"}


def join_chess_evidence_files(
    *,
    reconciliation_draft: str | Path,
    fen_labels: str | Path,
    fen_review_rows: str | Path,
    marker_labels: str | Path,
    source_pdf: str | Path,
    output_dir: str | Path,
    source_profile: str,
    bbox_iou_threshold: float = 0.90,
) -> dict[str, Any]:
    draft_path = Path(reconciliation_draft).resolve()
    fen_path = Path(fen_labels).resolve()
    review_path = Path(fen_review_rows).resolve()
    marker_path = Path(marker_labels).resolve()
    pdf_path = Path(source_pdf).resolve()
    draft = _load_json(draft_path)
    if not pdf_path.is_file():
        raise ValueError("source_pdf_not_found")

    source = draft.get("source") if isinstance(draft.get("source"), Mapping) else {}
    source_sha = _normalize_sha(source.get("sha256"))
    normalized_profile = _normalize_profile(source_profile)
    if _normalize_profile(draft.get("source_profile")) != normalized_profile:
        raise ValueError("source_profile_mismatch")
    if source_document_sha256(pdf_path) != source_sha:
        raise ValueError("source_pdf_sha256_mismatch")

    import fitz

    with fitz.open(pdf_path) as document:
        page_sizes = {
            index: (float(page.rect.width), float(page.rect.height))
            for index, page in enumerate(document, start=1)
        }
    result = join_chess_evidence_records(
        canonical_rows=_mapping_rows(draft.get("diagrams")),
        fen_labels=_load_jsonl(fen_path),
        fen_review_rows=_load_jsonl(review_path),
        marker_labels=_load_jsonl(marker_path),
        source_document_sha256=source_sha,
        source_profile=normalized_profile,
        page_sizes=page_sizes,
        bbox_iou_threshold=bbox_iou_threshold,
        reconciliation=dict(draft.get("reconciliation") or {}),
        verification=dict(draft.get("verification") or {}),
        hard_negatives=_mapping_rows(draft.get("hard_negatives")),
    )

    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "coverage": out / "chess_evidence_coverage.private.jsonl",
        "review_queue": out / "chess_evidence_review.private.jsonl",
        "report_json": out / "chess_evidence_coverage_report.json",
        "report_markdown": out / "chess_evidence_coverage_report.md",
    }
    _write_jsonl(artifacts["coverage"], result["coverage"])
    _write_jsonl(artifacts["review_queue"], result["review_queue"])
    artifacts["report_json"].write_text(
        json.dumps(result["report"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifacts["report_markdown"].write_text(
        _report_markdown(result["report"]),
        encoding="utf-8",
    )
    return {
        **result["report"],
        "artifacts": {key: str(value) for key, value in artifacts.items()},
    }


def join_chess_evidence_records(
    *,
    canonical_rows: Iterable[Mapping[str, Any]],
    fen_labels: Iterable[Mapping[str, Any]],
    fen_review_rows: Iterable[Mapping[str, Any]],
    marker_labels: Iterable[Mapping[str, Any]],
    source_document_sha256: str,
    source_profile: str,
    page_sizes: Mapping[int, Sequence[float]],
    bbox_iou_threshold: float = 0.90,
    reconciliation: Mapping[str, Any] | None = None,
    verification: Mapping[str, Any] | None = None,
    hard_negatives: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    source_sha = _normalize_sha(source_document_sha256)
    normalized_profile = _normalize_profile(source_profile)
    threshold = float(bbox_iou_threshold)
    if not 0.0 < threshold <= 1.0:
        raise ValueError("bbox_iou_threshold_out_of_range")

    canonical = [dict(row) for row in canonical_rows if isinstance(row, Mapping)]
    fen = [dict(row) for row in fen_labels if isinstance(row, Mapping)]
    review = [dict(row) for row in fen_review_rows if isinstance(row, Mapping)]
    markers = [dict(row) for row in marker_labels if isinstance(row, Mapping)]
    if not canonical or not review:
        raise ValueError("evidence_rows_missing")

    canonical_by_fp = _unique_map(canonical, _canonical_fingerprint, "canonical_fingerprint")
    canonical_by_legacy = _unique_map(canonical, _legacy_id, "canonical_legacy_id")
    fen_by_fp = _unique_map(fen, _fen_fingerprint, "fen_fingerprint")
    review_by_fp = _unique_map(review, _fen_fingerprint, "fen_review_fingerprint")
    marker_by_id = _unique_map(markers, _marker_id, "marker_diagram_id")
    _validate_source_rows(fen, source_sha, "fen_label")
    _validate_source_rows(review, source_sha, "fen_review")
    _validate_fen_labels(fen)
    _validate_marker_labels(markers)

    for fingerprint, row in fen_by_fp.items():
        seed = review_by_fp.get(fingerprint)
        if seed is None:
            raise ValueError("fen_label_review_row_missing")
        if str(seed.get("diagram_id") or "").strip() != str(row.get("diagram_id") or "").strip():
            raise ValueError("fen_label_review_identity_mismatch")

    review_boxes = {
        fingerprint: _review_normalized_bbox(row, page_sizes)
        for fingerprint, row in review_by_fp.items()
    }
    geometry_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
    reverse_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for review_fp, row in review_by_fp.items():
        page = _positive_int(row.get("page")) or 0
        bbox = review_boxes.get(review_fp)
        if page <= 0 or bbox is None:
            continue
        for canonical_fp, candidate in canonical_by_fp.items():
            if (_positive_int(candidate.get("page")) or 0) != page:
                continue
            candidate_bbox = _normalized_values(candidate.get("normalized_bbox_xyxy"))
            score = _iou(bbox, candidate_bbox)
            if score >= threshold:
                geometry_edges[review_fp].append((canonical_fp, score))
                reverse_edges[canonical_fp].append((review_fp, score))

    review_bindings: dict[str, tuple[str, float]] = {}
    review_statuses: dict[str, str] = {}
    for review_fp, row in review_by_fp.items():
        candidates = geometry_edges.get(review_fp, [])
        if len(candidates) == 1 and len(reverse_edges.get(candidates[0][0], [])) == 1:
            canonical_fp, score = candidates[0]
            review_bindings[review_fp] = (canonical_fp, score)
            label_status = str(row.get("label_status") or "").strip().lower()
            review_statuses[review_fp] = (
                "bound_verified_fen"
                if review_fp in fen_by_fp
                else "bound_excluded_review"
                if label_status in {"rejected", "unreadable"}
                else "bound_review_without_canonical_fen"
            )
        elif not candidates:
            review_statuses[review_fp] = "no_canonical_geometry_match"
        else:
            review_statuses[review_fp] = "ambiguous_canonical_geometry"

    fen_statuses = {
        fingerprint: (
            "bound_page_bbox_one_to_one"
            if fingerprint in review_bindings
            else review_statuses.get(fingerprint, "review_row_missing")
        )
        for fingerprint in fen_by_fp
    }
    marker_statuses: dict[str, str] = {}
    for diagram_id, row in marker_by_id.items():
        if diagram_id not in canonical_by_legacy:
            marker_statuses[diagram_id] = "orphan_legacy_id"
            continue
        canonical_row = canonical_by_legacy[diagram_id]
        if (_positive_int(row.get("page")) or 0) != (
            _positive_int(canonical_row.get("page")) or 0
        ):
            marker_statuses[diagram_id] = "page_mismatch"
            continue
        marker_statuses[diagram_id] = _standalone_marker_status(row)

    canonical_to_review = {
        canonical_fp: review_fp
        for review_fp, (canonical_fp, _score) in review_bindings.items()
    }
    coverage: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    fully_evidenced = 0
    marker_complete = 0
    canonical_statuses: Counter[str] = Counter()
    for canonical_fp, row in canonical_by_fp.items():
        legacy_id = _legacy_id(row)
        review_fp = canonical_to_review.get(canonical_fp, "")
        fen_row = fen_by_fp.get(review_fp)
        fen_seed = review_by_fp.get(review_fp)
        historical_marker = (
            marker_by_id.get(legacy_id)
            if marker_statuses.get(legacy_id) not in UNBOUND_MARKER_STATUSES
            else None
        )
        fen_status = _canonical_fen_status(canonical_fp, review_fp, fen_row, fen_seed, reverse_edges)
        marker_evidence = _combined_marker_evidence(historical_marker, fen_row)
        blockers = _coverage_blockers(fen_status, marker_evidence)
        overall_status = "fully_evidenced" if not blockers else "partial_evidence"
        if overall_status == "fully_evidenced":
            fully_evidenced += 1
        if marker_evidence["complete"]:
            marker_complete += 1
        canonical_statuses[overall_status] += 1
        match_score = review_bindings.get(review_fp, ("", None))[1] if review_fp else None
        coverage_row = {
            "schema": COVERAGE_ROW_SCHEMA,
            "source_profile": normalized_profile,
            "canonical_diagram_fingerprint": canonical_fp,
            "canonical_diagram_id": str(row.get("diagram_id") or ""),
            "legacy_intake_diagram_id": legacy_id,
            "page": _positive_int(row.get("page")) or 0,
            "normalized_bbox_xyxy": _bbox_list(row.get("normalized_bbox_xyxy")),
            "identity_status": str((row.get("identity") or {}).get("status") or ""),
            "fen_evidence": {
                "status": fen_status,
                "review_fingerprint": review_fp,
                "diagram_id": str((fen_row or fen_seed or {}).get("diagram_id") or ""),
                "human_verified": bool((fen_row or {}).get("human_verified") is True),
                "verification_source": str((fen_row or {}).get("verification_source") or ""),
                "geometry_iou": round(float(match_score), 6) if match_score is not None else None,
                "crop_rel_path": str((fen_row or fen_seed or {}).get("crop_rel_path") or ""),
            },
            "marker_evidence": marker_evidence,
            "evidence_status": overall_status,
            "blockers": blockers,
        }
        coverage.append(coverage_row)
        if blockers:
            review_queue.append(_canonical_queue_row(coverage_row))

    for fingerprint, status in fen_statuses.items():
        if status != "bound_page_bbox_one_to_one":
            row = fen_by_fp[fingerprint]
            review_queue.append(
                {
                    "schema": QUEUE_ROW_SCHEMA,
                    "priority": "P0",
                    "review_kind": "fen_identity",
                    "record_side": "fen_label",
                    "status": status,
                    "source_id": str(row.get("diagram_id") or ""),
                    "page": _positive_int(row.get("page")) or 0,
                    "fen_review_fingerprint": fingerprint,
                    "blockers": ["fen_geometry_binding_required"],
                }
            )
    for diagram_id, status in marker_statuses.items():
        if status in UNBOUND_MARKER_STATUSES:
            row = marker_by_id[diagram_id]
            review_queue.append(
                {
                    "schema": QUEUE_ROW_SCHEMA,
                    "priority": "P1",
                    "review_kind": "marker_identity",
                    "record_side": "marker_label",
                    "status": status,
                    "source_id": diagram_id,
                    "page": _positive_int(row.get("page")) or 0,
                    "blockers": [
                        "marker_page_binding_required"
                        if status == "page_mismatch"
                        else "marker_legacy_binding_required"
                    ],
                }
            )

    reconciliation_payload = dict(reconciliation or {})
    intake_status_counts = dict(reconciliation_payload.get("intake_status_counts") or {})
    identity_unresolved = sum(
        int(count or 0)
        for status, count in intake_status_counts.items()
        if status not in RESOLVED_IDENTITY_STATUSES
    )
    verified_negative_kinds = {
        str(row.get("kind") or "")
        for row in hard_negatives
        if isinstance(row, Mapping) and row.get("label_status") == "verified"
    }
    missing_negative_kinds = sorted(REQUIRED_HARD_NEGATIVE_KINDS - verified_negative_kinds)
    blockers = []
    if identity_unresolved:
        blockers.append("diagram_identity_review_required")
    if any(status != "bound_page_bbox_one_to_one" for status in fen_statuses.values()):
        blockers.append("fen_evidence_binding_incomplete")
    if any(status in UNBOUND_MARKER_STATUSES for status in marker_statuses.values()):
        blockers.append("marker_evidence_binding_incomplete")
    if marker_complete < len(canonical):
        blockers.append("marker_evidence_completion_incomplete")
    if missing_negative_kinds:
        blockers.append("verified_hard_negative_taxonomy_missing")

    report = {
        "schema": REPORT_SCHEMA,
        "status": "needs_review" if blockers else "passed",
        "source_profile": normalized_profile,
        "source_verified": True,
        "bbox_iou_threshold": round(threshold, 6),
        "counts": {
            "canonical_diagrams": len(canonical),
            "identity_review_intake": identity_unresolved,
            "fen_labels": len(fen),
            "fen_review_rows": len(review),
            "fen_labels_bound": sum(
                status == "bound_page_bbox_one_to_one" for status in fen_statuses.values()
            ),
            "fen_labels_unbound": sum(
                status != "bound_page_bbox_one_to_one" for status in fen_statuses.values()
            ),
            "marker_labels": len(markers),
            "marker_labels_bound": sum(
                status not in UNBOUND_MARKER_STATUSES for status in marker_statuses.values()
            ),
            "marker_labels_unbound": sum(
                status in UNBOUND_MARKER_STATUSES for status in marker_statuses.values()
            ),
            "marker_labels_orphan": sum(status == "orphan_legacy_id" for status in marker_statuses.values()),
            "canonical_marker_evidence_complete": marker_complete,
            "canonical_fully_evidenced": fully_evidenced,
            "action_queue": len(review_queue),
        },
        "canonical_status_counts": dict(sorted(canonical_statuses.items())),
        "fen_label_status_counts": dict(sorted(Counter(fen_statuses.values()).items())),
        "fen_review_status_counts": dict(sorted(Counter(review_statuses.values()).items())),
        "marker_label_status_counts": dict(sorted(Counter(marker_statuses.values()).items())),
        "required_hard_negative_kinds_missing": missing_negative_kinds,
        "inherited_verification_blockers": list((verification or {}).get("blockers") or []),
        "blockers": blockers,
        "acceptance_ready": not blockers,
    }
    return {
        "coverage": sorted(coverage, key=lambda item: (int(item["page"]), item["canonical_diagram_id"])),
        "review_queue": sorted(
            review_queue,
            key=lambda item: (
                str(item.get("priority") or "P9"),
                int(item.get("page") or 0),
                str(item.get("source_id") or ""),
            ),
        ),
        "report": report,
    }


def _canonical_fen_status(
    canonical_fp: str,
    review_fp: str,
    fen_row: Mapping[str, Any] | None,
    review_row: Mapping[str, Any] | None,
    reverse_edges: Mapping[str, Sequence[tuple[str, float]]],
) -> str:
    if review_fp and fen_row is not None:
        return "bound_human_verified"
    if review_fp and str((review_row or {}).get("label_status") or "") in {"rejected", "unreadable"}:
        return "human_excluded"
    if reverse_edges.get(canonical_fp):
        return "geometry_ambiguous"
    return "missing_verified_fen"


def _combined_marker_evidence(
    historical: Mapping[str, Any] | None,
    fen_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    sources = []
    if historical is not None:
        sources.append(("historical_marker_review", historical))
    if fen_row is not None and fen_row.get("human_verified") is True:
        sources.append(("fen_piece_grid_review", fen_row))
    if not sources:
        return {
            "status": "missing",
            "complete": False,
            "sources": [],
            "manual_visible_marker": "",
            "manual_side_to_move": "",
            "marker_bbox_verified": False,
        }

    shapes = {
        str(row.get("manual_visible_marker") or "").strip()
        for _name, row in sources
        if str(row.get("manual_visible_marker") or "").strip()
    }
    sides = {
        str(row.get("manual_side_to_move") or "").strip()
        for _name, row in sources
        if str(row.get("manual_side_to_move") or "").strip() in {"w", "b"}
    }
    semantic_conflict = len(shapes) > 1 or len(sides) > 1
    shape = next(iter(shapes), "")
    side = next(iter(sides), "")
    if shape == "outline_triangle" and side and side != "w":
        semantic_conflict = True
    if shape == "filled_triangle" and side and side != "b":
        semantic_conflict = True
    bbox_verified = any(
        row.get("marker_bbox_verified") is True and _valid_bbox(row.get("manual_marker_bbox"))
        for _name, row in sources
    )
    absence_complete = any(
        str(row.get("manual_visible_marker") or "") == "none_confirmed"
        and str(row.get("marker_crop_label") or "") == "complete_no_marker"
        for _name, row in sources
    )
    if semantic_conflict:
        status = "conflicting_human_evidence"
        complete = False
    elif shape in VISIBLE_MARKERS and bbox_verified:
        status = "complete_visible_marker"
        complete = True
    elif shape in VISIBLE_MARKERS:
        status = "human_shape_bbox_unverified"
        complete = False
    elif shape == "none_confirmed" and absence_complete:
        status = "complete_confirmed_absence"
        complete = True
    elif shape in REVIEW_ONLY_MARKERS:
        status = "human_review_only_marker"
        complete = False
    else:
        status = "human_marker_context_incomplete"
        complete = False
    return {
        "status": status,
        "complete": complete,
        "sources": [name for name, _row in sources],
        "manual_visible_marker": shape,
        "manual_side_to_move": side,
        "marker_bbox_verified": bbox_verified,
        "manual_marker_bbox": next(
            (
                row.get("manual_marker_bbox")
                for _name, row in sources
                if _valid_bbox(row.get("manual_marker_bbox"))
            ),
            None,
        ),
        "review_suggestion_bbox": next(
            (
                row.get("review_suggestion_bbox")
                for _name, row in sources
                if _valid_bbox(row.get("review_suggestion_bbox"))
            ),
            None,
        ),
        "marker_crop_rel_path": str((fen_row or {}).get("marker_crop_rel_path") or ""),
    }


def _coverage_blockers(fen_status: str, marker_evidence: Mapping[str, Any]) -> list[str]:
    blockers = []
    if fen_status == "human_excluded":
        blockers.append("fen_human_excluded")
    elif fen_status != "bound_human_verified":
        blockers.append("fen_evidence_missing_or_unresolved")
    marker_status = str(marker_evidence.get("status") or "missing")
    if marker_status == "missing":
        blockers.append("marker_evidence_missing")
    elif marker_status == "conflicting_human_evidence":
        blockers.append("marker_evidence_conflict")
    elif not marker_evidence.get("complete"):
        blockers.append(
            "marker_bbox_verification_required"
            if marker_status == "human_shape_bbox_unverified"
            else "marker_evidence_review_required"
        )
    return blockers


def _canonical_queue_row(row: Mapping[str, Any]) -> dict[str, Any]:
    marker = dict(row.get("marker_evidence") or {})
    fen = dict(row.get("fen_evidence") or {})
    return {
        "schema": QUEUE_ROW_SCHEMA,
        "priority": "P0" if "marker_evidence_conflict" in row.get("blockers", []) else "P1",
        "review_kind": "canonical_evidence",
        "record_side": "canonical",
        "status": str(row.get("evidence_status") or "partial_evidence"),
        "source_id": str(row.get("canonical_diagram_id") or ""),
        "legacy_intake_diagram_id": str(row.get("legacy_intake_diagram_id") or ""),
        "page": int(row.get("page") or 0),
        "canonical_diagram_fingerprint": str(row.get("canonical_diagram_fingerprint") or ""),
        "normalized_bbox_xyxy": row.get("normalized_bbox_xyxy"),
        "fen_evidence": fen,
        "marker_evidence": marker,
        "blockers": list(row.get("blockers") or []),
    }


def _standalone_marker_status(row: Mapping[str, Any]) -> str:
    if row.get("human_verified") is not True or str(row.get("label_status") or "") != "verified":
        return "invalid_human_provenance"
    shape = str(row.get("manual_visible_marker") or "")
    side = str(row.get("manual_side_to_move") or "")
    if (shape == "outline_triangle" and side != "w") or (shape == "filled_triangle" and side != "b"):
        return "semantic_conflict"
    if shape in VISIBLE_MARKERS and row.get("marker_bbox_verified") is True and _valid_bbox(
        row.get("manual_marker_bbox")
    ):
        return "bound_complete"
    if shape in VISIBLE_MARKERS:
        return "bound_bbox_unverified"
    if shape in REVIEW_ONLY_MARKERS:
        return "bound_review_only"
    return "bound_context_incomplete"


def _validate_fen_labels(rows: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows):
        if str(row.get("label_status") or "") != "verified":
            raise ValueError(f"fen_label[{index}]:not_verified")
        if row.get("human_verified") is not True or row.get("fen_human_verified") is not True:
            raise ValueError(f"fen_label[{index}]:human_verification_missing")
        if str(row.get("verification_source") or "") != "human_visual":
            raise ValueError(f"fen_label[{index}]:verification_source_invalid")


def _validate_marker_labels(rows: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows):
        if str(row.get("label_status") or "") != "verified":
            raise ValueError(f"marker_label[{index}]:not_verified")
        if row.get("human_verified") is not True:
            raise ValueError(f"marker_label[{index}]:human_verification_missing")
        if str(row.get("verification_source") or "") != "human_visual":
            raise ValueError(f"marker_label[{index}]:verification_source_invalid")


def _validate_source_rows(rows: Sequence[Mapping[str, Any]], source_sha: str, label: str) -> None:
    for index, row in enumerate(rows):
        value = row.get("source_document_sha256") or row.get("source_artifact_sha256")
        if _normalize_sha(value) != source_sha:
            raise ValueError(f"{label}[{index}]:source_sha256_mismatch")


def _review_normalized_bbox(
    row: Mapping[str, Any],
    page_sizes: Mapping[int, Sequence[float]],
) -> tuple[float, float, float, float] | None:
    value = row.get("normalized_bbox_xyxy")
    if isinstance(value, (list, tuple)):
        return _normalized_values(value)
    page = _positive_int(row.get("page")) or 0
    raw = row.get("bbox") or row.get("bbox_xyxy")
    if not isinstance(raw, (list, tuple)) or page not in page_sizes:
        return None
    return tuple(normalized_bbox(raw, page_sizes[page]))


def _unique_map(
    rows: Sequence[Mapping[str, Any]],
    key_fn: Any,
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(rows):
        key = str(key_fn(source) or "").strip()
        if not key:
            raise ValueError(f"{label}[{index}]:missing")
        if key in result:
            raise ValueError(f"{label}[{index}]:duplicate")
        result[key] = dict(source)
    return result


def _canonical_fingerprint(row: Mapping[str, Any]) -> str:
    value = str(row.get("diagram_fingerprint") or "").strip()
    if not re.fullmatch(r"dfp_[0-9a-f]{32}", value):
        raise ValueError("canonical_fingerprint_invalid")
    return value


def _fen_fingerprint(row: Mapping[str, Any]) -> str:
    value = str(row.get("diagram_fingerprint") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("fen_fingerprint_invalid")
    return value


def _legacy_id(row: Mapping[str, Any]) -> str:
    return str(row.get("legacy_intake_diagram_id") or "").strip()


def _marker_id(row: Mapping[str, Any]) -> str:
    return str(row.get("diagram_id") or "").strip()


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _normalized_values(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        raise ValueError("normalized_bbox_invalid")
    x0, y0, x1, y1 = (float(item) for item in value[:4])
    if not 0.0 <= x0 < x1 <= 1.0 or not 0.0 <= y0 < y1 <= 1.0:
        raise ValueError("normalized_bbox_invalid")
    return x0, y0, x1, y1


def _bbox_list(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    return [round(float(item), 6) for item in value[:4]]


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return False
    try:
        x0, y0, x1, y1 = (float(item) for item in value[:4])
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_sha(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("source_sha256_invalid")
    return normalized


def _normalize_profile(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", normalized):
        raise ValueError("source_profile_invalid")
    return normalized


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value or [] if isinstance(row, Mapping)]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("json_file_not_found")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("json_payload_must_be_object")
    return dict(payload)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError("jsonl_file_not_found")
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"jsonl_row[{index}]:must_be_object")
        rows.append(dict(payload))
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")


def _report_markdown(report: Mapping[str, Any]) -> str:
    counts = report.get("counts") if isinstance(report.get("counts"), Mapping) else {}
    lines = [
        "# Chess Evidence Coverage",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Source verified: `{str(bool(report.get('source_verified'))).lower()}`",
        f"- Canonical diagrams: `{counts.get('canonical_diagrams', 0)}`",
        f"- Identity review intake: `{counts.get('identity_review_intake', 0)}`",
        f"- FEN labels: `{counts.get('fen_labels', 0)}`",
        f"- FEN labels bound: `{counts.get('fen_labels_bound', 0)}`",
        f"- Marker labels: `{counts.get('marker_labels', 0)}`",
        f"- Marker labels bound: `{counts.get('marker_labels_bound', 0)}`",
        f"- Marker evidence complete: `{counts.get('canonical_marker_evidence_complete', 0)}`",
        f"- Fully evidenced: `{counts.get('canonical_fully_evidenced', 0)}`",
        f"- Action queue: `{counts.get('action_queue', 0)}`",
        "",
        "## Blockers",
        "",
        *(f"- `{value}`" for value in report.get("blockers", [])),
    ]
    return "\n".join(lines).rstrip() + "\n"
