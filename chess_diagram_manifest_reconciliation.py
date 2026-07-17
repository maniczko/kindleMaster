from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image

from chess_diagram_fingerprint import build_diagram_fingerprint, normalized_bbox


DRAFT_SCHEMA = "kindlemaster.chess.diagram_reconciliation_draft.v1"
REPORT_SCHEMA = "kindlemaster.chess.diagram_reconciliation_report.v1"
REVIEW_ROW_SCHEMA = "kindlemaster.chess.diagram_reconciliation_review.row.v1"
RESOLVED = {"exact_fingerprint", "page_bbox_one_to_one"}
CONTAINMENT_THRESHOLD = 0.80


@dataclass(frozen=True)
class _Record:
    key: str
    row: dict[str, Any]
    source_id: str
    aliases: tuple[str, ...]
    page: int
    bbox: tuple[float, float, float, float] | None
    fingerprint: str
    fingerprint_components: dict[str, Any]


def reconcile_diagram_manifest_files(
    *,
    detected_manifest: str | Path,
    intake_manifest: str | Path,
    output_dir: str | Path,
    marker_labels: str | Path | None = None,
    source_pdf: str | Path | None = None,
    source_profile: str,
    bbox_iou_threshold: float = 0.90,
) -> dict[str, Any]:
    detected_path = Path(detected_manifest).resolve()
    intake_path = Path(intake_manifest).resolve()
    detected_payload = _load_json(detected_path)
    intake_payload = _load_json(intake_path)
    normalized_profile = _validate_source_profiles(
        source_profile,
        detected_payload=detected_payload,
        intake_payload=intake_payload,
    )
    detected_rows = _diagram_rows(detected_payload, detected_path)
    intake_rows = _diagram_rows(intake_payload, intake_path)
    labels = _load_jsonl(Path(marker_labels).resolve()) if marker_labels else []
    explicit_pdf = Path(source_pdf).resolve() if source_pdf else None
    detected_pdf = _source_pdf(detected_payload, detected_path, explicit_pdf)
    intake_pdf = _source_pdf(intake_payload, intake_path, explicit_pdf)
    detected_sha = _source_sha(detected_payload, detected_rows, detected_pdf)
    intake_sha = _source_sha(intake_payload, intake_rows, intake_pdf)
    if detected_sha != intake_sha:
        raise ValueError("source_sha256_mismatch")

    result = reconcile_diagram_records(
        detected_rows=detected_rows,
        intake_rows=intake_rows,
        marker_labels=labels,
        source_document_sha256=detected_sha,
        source_profile=normalized_profile,
        bbox_iou_threshold=bbox_iou_threshold,
        detected_base_dir=detected_path.parent,
        intake_base_dir=intake_path.parent,
        source_pdf=detected_pdf or intake_pdf,
    )
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "draft_manifest": out / "canonical_expected_diagram_manifest.draft.json",
        "review_queue": out / "diagram_reconciliation_review.jsonl",
        "report_json": out / "diagram_reconciliation_report.json",
        "report_markdown": out / "diagram_reconciliation_report.md",
    }
    artifacts["draft_manifest"].write_text(
        json.dumps(result["draft"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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


def reconcile_diagram_records(
    *,
    detected_rows: Iterable[Mapping[str, Any]],
    intake_rows: Iterable[Mapping[str, Any]],
    source_document_sha256: str,
    source_profile: str,
    marker_labels: Iterable[Mapping[str, Any]] = (),
    bbox_iou_threshold: float = 0.90,
    detected_base_dir: str | Path | None = None,
    intake_base_dir: str | Path | None = None,
    source_pdf: str | Path | None = None,
) -> dict[str, Any]:
    source_sha = _normalize_sha(source_document_sha256)
    normalized_profile = _normalize_profile(source_profile)
    threshold = float(bbox_iou_threshold)
    if not 0.0 < threshold <= 1.0:
        raise ValueError("bbox_iou_threshold_out_of_range")
    detected_source = [dict(row) for row in detected_rows if isinstance(row, Mapping)]
    intake_source = [dict(row) for row in intake_rows if isinstance(row, Mapping)]
    if not detected_source or not intake_source:
        raise ValueError("diagram_records_missing")
    _validate_row_sources(detected_source, source_sha, "detected")
    _validate_row_sources(intake_source, source_sha, "intake")
    page_sizes = _page_sizes(source_pdf)
    detected = _prepare(
        detected_source,
        "detected",
        source_sha,
        Path(detected_base_dir).resolve() if detected_base_dir else None,
        page_sizes,
    )
    intake = _prepare(
        intake_source,
        "intake",
        source_sha,
        Path(intake_base_dir).resolve() if intake_base_dir else None,
        page_sizes,
    )
    labels_by_id, orphan_labels = _labels_by_id(marker_labels, intake)
    statuses: dict[str, str] = {}
    matches: dict[str, dict[str, Any]] = {}
    edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
    reverse_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
    relation_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
    reverse_relation_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)

    duplicate_keys = _duplicate_keys(intake) | _duplicate_keys(detected)
    for key in duplicate_keys:
        statuses[key] = "duplicate_identity"

    intake_fp = _unique_fingerprints(intake, duplicate_keys)
    detected_fp = _unique_fingerprints(detected, duplicate_keys)
    for fingerprint in sorted(set(intake_fp) & set(detected_fp)):
        expected = intake_fp[fingerprint]
        actual = detected_fp[fingerprint]
        if expected.page == actual.page:
            _match(expected, actual, "exact_fingerprint", 1.0, statuses, matches)

    free_intake = [row for row in intake if row.key not in statuses]
    free_detected = [row for row in detected if row.key not in statuses]
    for expected in free_intake:
        if expected.bbox is None:
            continue
        for actual in free_detected:
            if actual.page != expected.page or actual.bbox is None:
                continue
            score = _iou(expected.bbox, actual.bbox)
            if score >= threshold:
                edges[expected.key].append((actual.key, score))
                reverse_edges[actual.key].append((expected.key, score))

    detected_by_key = {row.key: row for row in detected}
    for expected in free_intake:
        candidates = edges.get(expected.key, [])
        if expected.key in statuses or len(candidates) != 1:
            continue
        actual_key, score = candidates[0]
        if actual_key not in statuses and len(reverse_edges.get(actual_key, [])) == 1:
            _match(
                expected,
                detected_by_key[actual_key],
                "page_bbox_one_to_one",
                score,
                statuses,
                matches,
            )

    for expected in intake:
        if expected.key in statuses:
            continue
        candidates = edges.get(expected.key, [])
        if candidates:
            reverse_degrees = [len(reverse_edges[key]) for key, _ in candidates]
            status = (
                "ambiguous_many_to_many"
                if len(candidates) > 1 and any(value > 1 for value in reverse_degrees)
                else "split_candidate"
                if len(candidates) > 1
                else "merge_candidate"
            )
            statuses[expected.key] = status
            for actual_key, _ in candidates:
                statuses.setdefault(actual_key, status)
            continue
        for actual in detected:
            if actual.key in statuses or actual.page != expected.page:
                continue
            if expected.bbox is None or actual.bbox is None:
                continue
            expected_coverage, actual_coverage = _overlap_coverages(
                expected.bbox,
                actual.bbox,
            )
            score = max(expected_coverage, actual_coverage)
            if score >= CONTAINMENT_THRESHOLD:
                relation_edges[expected.key].append((actual.key, score))
                reverse_relation_edges[actual.key].append((expected.key, score))

    for expected in intake:
        if expected.key in statuses:
            continue
        candidates = relation_edges.get(expected.key, [])
        reverse_degrees = [len(reverse_relation_edges[key]) for key, _ in candidates]
        if candidates:
            status = (
                "ambiguous_many_to_many"
                if len(candidates) > 1 and any(value > 1 for value in reverse_degrees)
                else "split_candidate"
                if len(candidates) > 1
                else "merge_candidate"
                if any(value > 1 for value in reverse_degrees)
                else "low_iou_containment_conflict"
            )
            statuses[expected.key] = status
            for actual_key, _ in candidates:
                statuses.setdefault(actual_key, status)

    for expected in intake:
        if expected.key in statuses:
            continue
        alias_matches = _alias_matches(expected, detected, statuses)
        if alias_matches:
            statuses[expected.key] = "id_geometry_conflict"
            for actual, _ in alias_matches:
                statuses.setdefault(actual.key, "id_geometry_conflict")
        else:
            statuses[expected.key] = "missing_detected"

    for actual in detected:
        if actual.key in statuses:
            continue
        incoming = reverse_edges.get(actual.key, [])
        if incoming:
            degrees = [len(edges[key]) for key, _ in incoming]
            statuses[actual.key] = (
                "ambiguous_many_to_many"
                if len(incoming) > 1 and any(value > 1 for value in degrees)
                else "merge_candidate"
                if len(incoming) > 1
                else "split_candidate"
            )
        else:
            statuses[actual.key] = "detector_only"

    canonical: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    marker_complete = 0
    for expected in intake:
        status = statuses[expected.key]
        if status in RESOLVED:
            match = matches[expected.key]
            actual = detected_by_key[match["detected_key"]]
            label = labels_by_id.get(expected.source_id)
            row = _canonical_row(expected, actual, match, label)
            canonical.append(row)
            if row["marker_evidence_complete"]:
                marker_complete += 1
            else:
                review.append(_marker_review(expected, actual, label))
        else:
            review.append(
                _identity_review(
                    expected,
                    status,
                    edges.get(expected.key, []) or relation_edges.get(expected.key, []),
                    detected_by_key,
                    _alias_matches(expected, detected, {}),
                )
            )

    matched_detected = {match["detected_key"] for match in matches.values()}
    represented_detected = {
        candidate["detected_key"]
        for row in review
        for candidate in row.get("candidates", [])
        if isinstance(candidate, Mapping) and candidate.get("detected_key")
    }
    for actual in detected:
        if (
            actual.key not in matched_detected
            and actual.key not in represented_detected
            and statuses[actual.key] == "detector_only"
        ):
            review.append(_detector_review(actual))
    for label in orphan_labels:
        review.append(
            {
                "schema": REVIEW_ROW_SCHEMA,
                "priority": "P1",
                "review_kind": "marker_label_orphan",
                "status": "marker_label_orphan",
                "record_side": "marker_label",
                "source_id": str(label.get("diagram_id") or ""),
                "page": _positive_int(label.get("page")),
                "candidates": [],
            }
        )

    intake_counts = Counter(statuses[row.key] for row in intake)
    detected_counts = Counter(statuses[row.key] for row in detected)
    unresolved_intake = sum(count for status, count in intake_counts.items() if status not in RESOLVED)
    unresolved_detected = sum(
        count for status, count in detected_counts.items() if status not in RESOLVED
    )
    blockers = []
    if unresolved_intake:
        blockers.append("diagram_identity_review_required")
    if marker_complete < len(canonical):
        blockers.append("marker_evidence_incomplete")
    blockers.append("verified_hard_negative_taxonomy_missing")
    report = {
        "schema": REPORT_SCHEMA,
        "status": "needs_review" if blockers else "passed",
        "source_profile": normalized_profile,
        "source_verified": True,
        "bbox_iou_threshold": round(threshold, 6),
        "containment_threshold": CONTAINMENT_THRESHOLD,
        "counts": {
            "intake": len(intake),
            "detected": len(detected),
            "canonical_resolved": len(canonical),
            "identity_review_intake": unresolved_intake,
            "identity_review_detected": unresolved_detected,
            "review_queue": len(review),
            "marker_labels": len(labels_by_id) + len(orphan_labels),
            "marker_labels_bound": len(labels_by_id),
            "marker_labels_orphan": len(orphan_labels),
            "marker_evidence_complete": marker_complete,
        },
        "intake_status_counts": dict(sorted(intake_counts.items())),
        "detected_status_counts": dict(sorted(detected_counts.items())),
        "required_hard_negative_kinds_missing": [
            "arrows",
            "borders",
            "captions",
            "coordinates",
            "letters",
            "neighboring_diagrams",
        ],
        "blockers": blockers,
        "acceptance_ready": not blockers,
    }
    draft = {
        "schema": DRAFT_SCHEMA,
        "status": report["status"],
        "source_profile": normalized_profile,
        "source": {
            "kind": "fixed_edition_pdf",
            "sha256": source_sha,
            "copyright_content_committed": False,
        },
        "verification": {
            "status": "draft",
            "acceptance_ready": report["acceptance_ready"],
            "blockers": blockers,
        },
        "reconciliation": {
            "bbox_iou_threshold": round(threshold, 6),
            "containment_threshold": CONTAINMENT_THRESHOLD,
            "intake_status_counts": report["intake_status_counts"],
            "detected_status_counts": report["detected_status_counts"],
        },
        "diagrams": canonical,
        "hard_negatives": [],
    }
    return {
        "draft": draft,
        "review_queue": sorted(
            review,
            key=lambda row: (
                str(row.get("priority") or "P9"),
                int(row.get("page") or 0),
                str(row.get("source_id") or ""),
            ),
        ),
        "report": report,
    }


def _match(
    expected: _Record,
    actual: _Record,
    status: str,
    score: float,
    statuses: dict[str, str],
    matches: dict[str, dict[str, Any]],
) -> None:
    statuses[expected.key] = status
    statuses[actual.key] = status
    matches[expected.key] = {
        "detected_key": actual.key,
        "method": status,
        "iou": round(float(score), 6),
    }


def _prepare(
    rows: Sequence[dict[str, Any]],
    side: str,
    source_sha: str,
    base_dir: Path | None,
    page_sizes: Mapping[int, tuple[float, float]],
) -> list[_Record]:
    prepared = []
    for index, row in enumerate(rows):
        source_id = str(row.get("diagram_id") or row.get("id") or f"row-{index + 1}").strip()
        aliases = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (row.get("diagram_id"), row.get("id"), row.get("legacy_diagram_id"))
                if str(value or "").strip()
            )
        )
        page = _positive_int(row.get("page") or row.get("page_number")) or 0
        bbox = _record_bbox(row, page, page_sizes)
        components = dict(row.get("diagram_fingerprint_components") or {})
        fingerprint = str(
            row.get("diagram_fingerprint") or components.get("diagram_fingerprint") or ""
        ).strip()
        if not fingerprint and bbox is not None and page > 0:
            crop = _crop_path(row, base_dir)
            if crop is not None:
                try:
                    with Image.open(crop) as image:
                        components = build_diagram_fingerprint(
                            source_sha256=source_sha,
                            page=page,
                            normalized_bbox_xyxy=bbox,
                            board_crop=image,
                        )
                    fingerprint = str(components["diagram_fingerprint"])
                except (OSError, ValueError):
                    fingerprint = ""
        prepared.append(
            _Record(
                key=f"{side}:{index}:{source_id}",
                row=row,
                source_id=source_id,
                aliases=aliases,
                page=page,
                bbox=bbox,
                fingerprint=fingerprint,
                fingerprint_components=components,
            )
        )
    return prepared


def _duplicate_keys(records: Sequence[_Record]) -> set[str]:
    groups: dict[tuple[str, str], list[_Record]] = defaultdict(list)
    for record in records:
        if record.source_id:
            groups[("id", record.source_id)].append(record)
        if record.fingerprint:
            groups[("fingerprint", record.fingerprint)].append(record)
    return {
        row.key
        for group in groups.values()
        if len(group) > 1
        for row in group
    }


def _unique_fingerprints(records: Sequence[_Record], blocked: set[str]) -> dict[str, _Record]:
    return {
        row.fingerprint: row
        for row in records
        if row.fingerprint and row.key not in blocked
    }


def _alias_matches(
    expected: _Record,
    detected: Sequence[_Record],
    statuses: Mapping[str, str],
) -> list[tuple[_Record, float | None]]:
    aliases = set(expected.aliases)
    matches = []
    for actual in detected:
        if actual.key in statuses or aliases.isdisjoint(actual.aliases):
            continue
        score = (
            _iou(expected.bbox, actual.bbox)
            if expected.bbox is not None and actual.bbox is not None and expected.page == actual.page
            else None
        )
        matches.append((actual, score))
    return matches


def _canonical_row(
    expected: _Record,
    actual: _Record,
    match: Mapping[str, Any],
    marker_label: Mapping[str, Any] | None,
) -> dict[str, Any]:
    evidence = _marker_evidence(marker_label)
    complete = bool(
        evidence.get("human_verified")
        and evidence.get("manual_visible_marker") in {"outline_triangle", "filled_triangle"}
        and evidence.get("marker_bbox_verified") is True
    )
    return {
        "schema": "kindlemaster.chess.diagram_reconciliation_draft.row.v1",
        "diagram_fingerprint": actual.fingerprint,
        "diagram_fingerprint_components": actual.fingerprint_components,
        "diagram_id": actual.source_id,
        "legacy_intake_diagram_id": expected.source_id,
        "page": actual.page,
        "normalized_bbox_xyxy": _bbox_list(actual.bbox),
        "chapter_id": str(expected.row.get("chapter_id") or ""),
        "split": str(expected.row.get("split") or ""),
        "allowed_for_tuning": expected.row.get("allowed_for_tuning"),
        "identity": {
            "status": str(match.get("method") or ""),
            "iou": match.get("iou"),
            "source_bound": True,
        },
        "marker_evidence": evidence,
        "marker_evidence_complete": complete,
        "acceptance_label_status": "verified" if complete else "review_required",
    }


def _identity_review(
    expected: _Record,
    status: str,
    candidates: Sequence[tuple[str, float]],
    detected_by_key: Mapping[str, _Record],
    alias_candidates: Sequence[tuple[_Record, float | None]],
) -> dict[str, Any]:
    rows = [
        _candidate(detected_by_key[key], score)
        for key, score in sorted(candidates, key=lambda item: (-item[1], item[0]))
    ]
    seen = {row["detected_key"] for row in rows}
    rows.extend(
        _candidate(actual, score)
        for actual, score in alias_candidates
        if actual.key not in seen
    )
    return {
        "schema": REVIEW_ROW_SCHEMA,
        "priority": "P0" if status in {"duplicate_identity", "ambiguous_many_to_many", "split_candidate", "merge_candidate", "id_geometry_conflict"} else "P1",
        "review_kind": "diagram_identity",
        "status": status,
        "record_side": "intake",
        "source_id": expected.source_id,
        "page": expected.page,
        "diagram_fingerprint": expected.fingerprint,
        "normalized_bbox_xyxy": _bbox_list(expected.bbox),
        "board_crop_path": _crop_reference(expected.row),
        "candidates": rows,
    }


def _detector_review(actual: _Record) -> dict[str, Any]:
    return {
        "schema": REVIEW_ROW_SCHEMA,
        "priority": "P1",
        "review_kind": "diagram_identity",
        "status": "detector_only",
        "record_side": "detected",
        "source_id": actual.source_id,
        "page": actual.page,
        "diagram_fingerprint": actual.fingerprint,
        "normalized_bbox_xyxy": _bbox_list(actual.bbox),
        "board_crop_path": _crop_reference(actual.row),
        "candidates": [],
    }


def _marker_review(
    expected: _Record,
    actual: _Record,
    marker_label: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": REVIEW_ROW_SCHEMA,
        "priority": "P2",
        "review_kind": "marker_evidence",
        "status": "marker_evidence_incomplete",
        "record_side": "canonical",
        "source_id": actual.source_id,
        "legacy_intake_diagram_id": expected.source_id,
        "page": actual.page,
        "diagram_fingerprint": actual.fingerprint,
        "normalized_bbox_xyxy": _bbox_list(actual.bbox),
        "board_crop_path": _crop_reference(actual.row),
        "marker_evidence": _marker_evidence(marker_label),
        "candidates": [],
    }


def _candidate(actual: _Record, score: float | None) -> dict[str, Any]:
    return {
        "detected_key": actual.key,
        "diagram_id": actual.source_id,
        "diagram_fingerprint": actual.fingerprint,
        "page": actual.page,
        "normalized_bbox_xyxy": _bbox_list(actual.bbox),
        "iou": round(float(score), 6) if score is not None else None,
        "board_crop_path": _crop_reference(actual.row),
    }


def _labels_by_id(
    rows: Iterable[Mapping[str, Any]],
    intake: Sequence[_Record],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    intake_ids = {row.source_id for row in intake}
    labels: dict[str, dict[str, Any]] = {}
    orphans = []
    for index, source in enumerate(rows, start=1):
        row = dict(source)
        diagram_id = str(row.get("diagram_id") or "").strip()
        if not diagram_id:
            raise ValueError(f"marker_label[{index}]:diagram_id_missing")
        if diagram_id in labels:
            raise ValueError(f"marker_label[{index}]:diagram_id_duplicate")
        if row.get("label_status") == "verified" and row.get("human_verified") is not True:
            raise ValueError(f"marker_label[{index}]:verified_label_requires_human_verified")
        if diagram_id in intake_ids:
            labels[diagram_id] = row
        else:
            orphans.append(row)
    return labels, orphans


def _marker_evidence(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        return {"label_status": "missing", "human_verified": False, "marker_bbox_verified": False}
    bbox = row.get("manual_marker_bbox")
    return {
        "label_status": str(row.get("label_status") or ""),
        "human_verified": row.get("human_verified") is True,
        "manual_visible_marker": str(row.get("manual_visible_marker") or ""),
        "manual_side_to_move": str(row.get("manual_side_to_move") or ""),
        "manual_marker_bbox": bbox if _valid_bbox(bbox) else None,
        "marker_bbox_verified": row.get("marker_bbox_verified") is True,
        "verification_source": str(row.get("verification_source") or ""),
        "verified_by": str(row.get("verified_by") or ""),
        "verified_at": str(row.get("verified_at") or ""),
        "binding": "source_bound_intake_diagram_id",
    }


def _diagram_rows(payload: Mapping[str, Any], manifest_path: Path) -> list[dict[str, Any]]:
    for key in ("diagrams", "records", "items", "expected_diagrams"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, Mapping)]
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), Mapping) else {}
    member = str(artifacts.get("review_rows") or "").strip()
    if member:
        return _load_jsonl(_safe_member(manifest_path.parent, member))
    raise ValueError("diagram_records_missing")


def _source_pdf(payload: Mapping[str, Any], manifest_path: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise ValueError("source_pdf_not_found")
        return explicit
    value = str(payload.get("source_pdf") or "").strip()
    if not value:
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else manifest_path.parent / path
    return candidate.resolve() if candidate.is_file() else None


def _source_sha(
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    source_pdf: Path | None,
) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
    values = [payload.get("source_document_sha256"), payload.get("source_sha256"), source.get("sha256")]
    for row in rows:
        values.append(row.get("source_document_sha256"))
        components = row.get("diagram_fingerprint_components")
        if isinstance(components, Mapping):
            values.append(components.get("source_document_sha256"))
    hashes = {_normalize_sha(value) for value in values if str(value or "").strip()}
    if source_pdf is not None:
        hashes.add(_file_sha(source_pdf))
    if len(hashes) != 1:
        raise ValueError("source_sha256_missing_or_conflicting")
    return next(iter(hashes))


def _validate_source_profiles(
    expected: str,
    *,
    detected_payload: Mapping[str, Any],
    intake_payload: Mapping[str, Any],
) -> str:
    normalized = _normalize_profile(expected)
    claims = [
        *_manifest_profile_claims(detected_payload),
        *_manifest_profile_claims(intake_payload),
    ]
    if any(claim != normalized for claim in claims):
        raise ValueError("source_profile_mismatch")
    return normalized


def _manifest_profile_claims(payload: Mapping[str, Any]) -> list[str]:
    source = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
    values = [payload.get("source_profile"), source.get("profile")]
    return [_normalize_profile(value) for value in values if str(value or "").strip()]


def _validate_row_sources(rows: Sequence[Mapping[str, Any]], source_sha: str, label: str) -> None:
    for index, row in enumerate(rows):
        components = row.get("diagram_fingerprint_components")
        component_sha = components.get("source_document_sha256") if isinstance(components, Mapping) else ""
        value = str(row.get("source_document_sha256") or component_sha or "").strip()
        if value and _normalize_sha(value) != source_sha:
            raise ValueError(f"{label}_row[{index}]:source_sha256_mismatch")


def _page_sizes(source_pdf: str | Path | None) -> dict[int, tuple[float, float]]:
    if source_pdf is None:
        return {}
    import fitz

    with fitz.open(Path(source_pdf)) as document:
        return {
            index: (float(page.rect.width), float(page.rect.height))
            for index, page in enumerate(document, start=1)
        }


def _record_bbox(
    row: Mapping[str, Any],
    page: int,
    page_sizes: Mapping[int, tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    value = row.get("normalized_bbox_xyxy")
    components = row.get("diagram_fingerprint_components")
    if not isinstance(value, (list, tuple)) and isinstance(components, Mapping):
        value = components.get("normalized_bbox_xyxy")
    if isinstance(value, (list, tuple)):
        return _normalized_values(value)
    raw = row.get("bbox_xyxy")
    if isinstance(raw, (list, tuple)) and page in page_sizes:
        return tuple(normalized_bbox(raw, page_sizes[page]))
    return None


def _crop_path(row: Mapping[str, Any], base_dir: Path | None) -> Path | None:
    for key in ("board_crop_path", "source_crop", "image_path", "legacy_source_crop"):
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        path = Path(value)
        candidates = [path] if path.is_absolute() else ([base_dir / path] if base_dir else [])
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    return None


def _crop_reference(row: Mapping[str, Any]) -> str:
    for key in ("board_crop_path", "source_crop", "image_path", "image_href"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _safe_member(root: Path, member: str) -> Path:
    candidate = (root / member).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("artifact_path_outside_manifest_root")
    if not candidate.is_file():
        raise ValueError("artifact_file_not_found")
    return candidate


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _overlap_coverages(
    expected: Sequence[float],
    actual: Sequence[float],
) -> tuple[float, float]:
    intersection_width = max(0.0, min(expected[2], actual[2]) - max(expected[0], actual[0]))
    intersection_height = max(0.0, min(expected[3], actual[3]) - max(expected[1], actual[1]))
    intersection = intersection_width * intersection_height
    expected_area = max(0.0, expected[2] - expected[0]) * max(0.0, expected[3] - expected[1])
    actual_area = max(0.0, actual[2] - actual[0]) * max(0.0, actual[3] - actual[1])
    return (
        intersection / expected_area if expected_area > 0 else 0.0,
        intersection / actual_area if actual_area > 0 else 0.0,
    )


def _normalized_values(value: Sequence[float]) -> tuple[float, float, float, float]:
    if len(value) < 4:
        raise ValueError("normalized_bbox_invalid")
    x0, y0, x1, y1 = (float(item) for item in value[:4])
    if not 0.0 <= x0 < x1 <= 1.0 or not 0.0 <= y0 < y1 <= 1.0:
        raise ValueError("normalized_bbox_invalid")
    return x0, y0, x1, y1


def _bbox_list(value: Sequence[float] | None) -> list[float] | None:
    return [round(float(item), 6) for item in value] if value is not None else None


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


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("manifest_file_not_found")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("manifest_must_be_object")
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
            raise ValueError(f"jsonl_row[{index}]_must_be_object")
        rows.append(dict(payload))
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")


def _report_markdown(report: Mapping[str, Any]) -> str:
    counts = report.get("counts") if isinstance(report.get("counts"), Mapping) else {}
    statuses = report.get("intake_status_counts") if isinstance(report.get("intake_status_counts"), Mapping) else {}
    lines = [
        "# Diagram Manifest Reconciliation",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Source verified: `{str(bool(report.get('source_verified'))).lower()}`",
        f"- Intake records: `{counts.get('intake', 0)}`",
        f"- Detected records: `{counts.get('detected', 0)}`",
        f"- Canonical resolved: `{counts.get('canonical_resolved', 0)}`",
        f"- Identity review: `{counts.get('identity_review_intake', 0)}`",
        f"- Review queue: `{counts.get('review_queue', 0)}`",
        f"- Marker labels bound: `{counts.get('marker_labels_bound', 0)}`",
        f"- Marker evidence complete: `{counts.get('marker_evidence_complete', 0)}`",
        "",
        "## Intake Statuses",
        "",
        *(f"- `{key}`: `{value}`" for key, value in sorted(statuses.items())),
        "",
        "## Blockers",
        "",
        *(f"- `{value}`" for value in report.get("blockers", [])),
    ]
    return "\n".join(lines).rstrip() + "\n"
