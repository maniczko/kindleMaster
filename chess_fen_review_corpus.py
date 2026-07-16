from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from chess_fen_hardening import crop_sha256
from chess_position_recognizer import validate_fen
from chess_fen_review_contract import (
    CANONICAL_VERIFICATION_SOURCE,
    LEGACY_REVIEW_VERIFICATION_SOURCES,
    normalize_review_row_for_gold_contract,
)
from scripts.validate_chess_fen_labels import validate_chess_fen_labels
from supabase_fen_review import SupabaseFenReviewClient


CANONICAL_FEN_LABEL_SCHEMA = "kindlemaster.chess_fen_label.v2"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_ASSET_BYTES = 12 * 1024 * 1024


class FenReviewCorpusExportError(ValueError):
    pass


def export_fen_review_corpus(
    *,
    artifact_id: str,
    out_dir: str | Path,
    review_dir: str | Path | None = None,
    service_base_url: str = "",
    cloud_client: SupabaseFenReviewClient | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    artifact = str(artifact_id or "").strip()
    if not artifact:
        raise FenReviewCorpusExportError("artifact_id_missing")
    out = Path(out_dir)
    review_root = Path(review_dir).resolve() if review_dir else None
    review_out = out / "review"
    reports_out = out / "reports"
    assets_out = out / "assets"
    review_out.mkdir(parents=True, exist_ok=True)
    reports_out.mkdir(parents=True, exist_ok=True)
    assets_out.mkdir(parents=True, exist_ok=True)

    payload = _load_review_payload(
        artifact_id=artifact,
        service_base_url=service_base_url,
        cloud_client=cloud_client,
        opener=opener,
    )
    rows = [dict(row) for row in payload.get("rows") or [] if isinstance(row, Mapping)]
    summary = dict(payload.get("summary") or {})
    storage = str(payload.get("storage") or "unknown")
    source_digest = str(payload.get("source_document_sha256") or _first_source_digest(rows)).strip().lower()
    issues: list[dict[str, Any]] = []
    verified_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()

    if not _SHA256_PATTERN.fullmatch(source_digest):
        issues.append({"code": "source_document_sha256_invalid", "value": source_digest})
    if not rows:
        issues.append({"code": "review_rows_missing"})
    if int(summary.get("pending") or 0) > 0:
        issues.append({"code": "review_pending_rows", "count": int(summary.get("pending") or 0)})
    if int(summary.get("invalid") or 0) > 0:
        issues.append({"code": "review_invalid_rows", "count": int(summary.get("invalid") or 0)})

    for row in rows:
        fingerprint = str(row.get("diagram_fingerprint") or "").strip().lower()
        row_artifact = str(row.get("artifact_id") or artifact).strip()
        row_digest = str(
            row.get("source_document_sha256")
            or row.get("source_artifact_sha256")
            or ""
        ).strip().lower()
        if row_artifact != artifact:
            issues.append(_row_issue(row, "artifact_id_mismatch"))
            continue
        if row_digest != source_digest:
            issues.append(_row_issue(row, "source_document_sha256_mismatch"))
            continue
        if not _SHA256_PATTERN.fullmatch(fingerprint):
            issues.append(_row_issue(row, "diagram_fingerprint_invalid"))
            continue
        if fingerprint in seen_fingerprints:
            issues.append(_row_issue(row, "duplicate_diagram_fingerprint"))
            continue
        seen_fingerprints.add(fingerprint)
        status = str(row.get("label_status") or "").strip().lower()
        if status in {"rejected", "unreadable"}:
            excluded_rows.append(_excluded_audit_row(row))
            continue
        if status != "verified":
            issues.append(_row_issue(row, "review_row_not_terminal", status=status))
            continue
        try:
            crop_path = _resolve_crop_asset(
                row,
                artifact_id=artifact,
                out_dir=out,
                review_dir=review_root,
                service_base_url=service_base_url,
                opener=opener,
            )
            verified_rows.append(
                _canonical_label(
                    row,
                    artifact_id=artifact,
                    source_digest=source_digest,
                    crop_path=crop_path,
                )
            )
        except FenReviewCorpusExportError as exc:
            issues.append(_row_issue(row, str(exc)))

    expected_verified = int(summary.get("verified") or 0)
    expected_excluded = int(summary.get("excluded") or summary.get("closed") or 0)
    if expected_verified and expected_verified != len(verified_rows):
        issues.append(
            {
                "code": "verified_count_mismatch",
                "expected": expected_verified,
                "actual": len(verified_rows),
            }
        )
    if expected_excluded and expected_excluded != len(excluded_rows):
        issues.append(
            {
                "code": "excluded_count_mismatch",
                "expected": expected_excluded,
                "actual": len(excluded_rows),
            }
        )

    canonical_path = review_out / "fen_verified_labels.jsonl"
    pending_path = review_out / "fen_verified_labels.pending.jsonl"
    excluded_path = review_out / "fen_review_excluded.jsonl"
    validation_path = reports_out / "fen_verified_labels_validation.json"
    canonical_path.unlink(missing_ok=True)
    pending_path.unlink(missing_ok=True)
    _write_jsonl(excluded_path, excluded_rows)
    _write_jsonl(pending_path, verified_rows)

    validator = {
        "status": "not_run",
        "label_count": len(verified_rows),
        "valid_label_count": 0,
        "issue_count": 0,
        "issues": [],
    }
    if verified_rows and not issues:
        validator = validate_chess_fen_labels(pending_path, output_path=validation_path)
        if validator.get("status") == "passed":
            pending_path.replace(canonical_path)
            validator["labels_path"] = str(canonical_path)
        else:
            issues.append(
                {
                    "code": "canonical_validator_failed",
                    "issue_count": int(validator.get("issue_count") or 0),
                }
            )

    status = "passed" if canonical_path.is_file() and not issues else "failed"
    report = {
        "schema": "kindlemaster.fen_review_corpus_export.v1",
        "status": status,
        "artifact_id": artifact,
        "storage": storage,
        "saved_at": str(payload.get("saved_at") or ""),
        "source_document_sha256": source_digest,
        "review_row_count": len(rows),
        "verified_count": len(verified_rows),
        "excluded_count": len(excluded_rows),
        "issue_count": len(issues),
        "issues": issues,
        "validator": validator,
        "artifacts": {
            "labels": str(canonical_path) if canonical_path.is_file() else "",
            "pending_labels": str(pending_path) if pending_path.is_file() else "",
            "excluded": str(excluded_path),
            "validation": str(validation_path) if validation_path.is_file() else "",
            "assets": str(assets_out),
        },
        "policy": (
            "Only source-bound human-verified 64-square labels are exported. "
            "Excluded and invalid rows never enter the training JSONL."
        ),
    }
    report_path = reports_out / "fen_review_corpus_export.json"
    markdown_path = reports_out / "fen_review_corpus_export.md"
    report["artifacts"]["report_json"] = str(report_path)
    report["artifacts"]["report_markdown"] = str(markdown_path)
    _write_json(report_path, report)
    markdown_path.write_text(_report_markdown(report), encoding="utf-8")
    return report


def _load_review_payload(
    *,
    artifact_id: str,
    service_base_url: str,
    cloud_client: SupabaseFenReviewClient | None,
    opener: Callable[..., Any] | None,
) -> dict[str, Any]:
    base_url = str(service_base_url or "").strip().rstrip("/")
    if base_url:
        _validate_service_base_url(base_url)
        url = (
            f"{base_url}/convert/artifact/{urllib.parse.quote(artifact_id, safe='')}"
            "/chess_fen_review_progress"
        )
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        raw = _open_bytes(request, opener=opener, max_bytes=32 * 1024 * 1024)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FenReviewCorpusExportError("review_payload_invalid_json") from exc
        if not isinstance(payload, dict):
            raise FenReviewCorpusExportError("review_payload_invalid")
        return payload

    client = cloud_client or SupabaseFenReviewClient()
    if not client.available:
        raise FenReviewCorpusExportError("supabase_fen_review_unavailable")
    payload = client.load_review(artifact_id=artifact_id)
    if not isinstance(payload, Mapping):
        raise FenReviewCorpusExportError("supabase_fen_review_missing")
    return dict(payload)


def _canonical_label(
    row: Mapping[str, Any],
    *,
    artifact_id: str,
    source_digest: str,
    crop_path: Path,
) -> dict[str, Any]:
    normalized = normalize_review_row_for_gold_contract(row)
    row_artifact = str(normalized.get("artifact_id") or artifact_id).strip()
    if row_artifact != artifact_id:
        raise FenReviewCorpusExportError("artifact_id_mismatch")
    row_digest = str(
        normalized.get("source_document_sha256")
        or normalized.get("source_artifact_sha256")
        or ""
    ).strip().lower()
    if row_digest != source_digest:
        raise FenReviewCorpusExportError("source_document_sha256_mismatch")
    fingerprint = str(normalized.get("diagram_fingerprint") or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(fingerprint):
        raise FenReviewCorpusExportError("diagram_fingerprint_invalid")
    verification_source = str(row.get("verification_source") or "").strip().lower()
    if verification_source not in LEGACY_REVIEW_VERIFICATION_SOURCES:
        raise FenReviewCorpusExportError("verification_source_not_human_review")
    if row.get("piece_labels_verified") is not True:
        raise FenReviewCorpusExportError("piece_labels_not_verified")
    cells = row.get("square_labels")
    if not isinstance(cells, list) or len(cells) != 64:
        raise FenReviewCorpusExportError("square_labels_invalid")
    fen = str(row.get("manual_fen") or "").strip()
    valid_fen, warnings = validate_fen(fen)
    if not valid_fen or warnings:
        raise FenReviewCorpusExportError("manual_fen_invalid")
    manual_label = str(row.get("manual_label") or "").strip().lower()
    if manual_label not in {"correct_diagram", "cropped_diagram"}:
        raise FenReviewCorpusExportError("manual_label_not_trainable")
    manual_side = str(row.get("manual_side_to_move") or "").strip().lower()
    fen_side = fen.split()[1] if len(fen.split()) >= 2 else ""
    if manual_side not in {"w", "b"} or manual_side != fen_side:
        raise FenReviewCorpusExportError("manual_side_to_move_mismatch")
    verified_by = str(row.get("verified_by") or "").strip()
    verified_at = str(row.get("verified_at") or "").strip()
    if not verified_by:
        raise FenReviewCorpusExportError("verified_by_missing")
    if not verified_at:
        raise FenReviewCorpusExportError("verified_at_missing")
    if not crop_path.is_file():
        raise FenReviewCorpusExportError("crop_path_missing")
    actual_hash = crop_sha256(crop_path)
    declared_hash = str(
        row.get("crop_sha256")
        or row.get("board_crop_sha256")
        or ""
    ).strip().lower()
    if not _SHA256_PATTERN.fullmatch(declared_hash):
        raise FenReviewCorpusExportError("crop_sha256_missing")
    if actual_hash != declared_hash:
        raise FenReviewCorpusExportError("crop_sha256_mismatch")
    diagram_id = str(row.get("diagram_id") or "").strip()
    record_id = str(row.get("id") or diagram_id or fingerprint).strip()
    return {
        "schema": CANONICAL_FEN_LABEL_SCHEMA,
        "id": record_id,
        "artifact_id": artifact_id,
        "diagram_id": diagram_id,
        "diagram_fingerprint": fingerprint,
        "source_document_sha256": source_digest,
        "source_artifact_sha256": str(row.get("source_artifact_sha256") or ""),
        "source_binding": str(row.get("source_binding") or "source_pdf_sha256"),
        "page": int(row.get("page") or 0),
        "review_index": int(row.get("review_index") or 0),
        "fen": fen,
        "manual_fen": fen,
        "manual_label": manual_label,
        "label_status": "verified",
        "crop_path": str(crop_path.resolve()),
        "crop_rel_path": str(row.get("crop_rel_path") or row.get("board_crop_rel_path") or ""),
        "crop_sha256": actual_hash,
        "square_labels": list(cells),
        "piece_labels_verified": True,
        "manual_side_to_move": manual_side,
        "manual_side_evidence": str(row.get("manual_side_evidence") or ""),
        "manual_visible_marker": str(row.get("manual_visible_marker") or ""),
        "board_crop_label": str(row.get("board_crop_label") or ""),
        "marker_crop_label": str(row.get("marker_crop_label") or ""),
        "verified_by": verified_by,
        "verified_at": verified_at,
        "verification_source": CANONICAL_VERIFICATION_SOURCE,
        "human_verified": True,
        "fen_human_verified": True,
        "square_diff_ack": True,
        "label_provenance": "human_visual_source_bound_piece_grid_review",
        "notes": str(row.get("notes") or ""),
    }


def _resolve_crop_asset(
    row: Mapping[str, Any],
    *,
    artifact_id: str,
    out_dir: Path,
    review_dir: Path | None,
    service_base_url: str,
    opener: Callable[..., Any] | None,
) -> Path:
    rel_path = _safe_relative_asset_path(
        str(row.get("crop_rel_path") or row.get("board_crop_rel_path") or "")
    )
    if review_dir is not None:
        candidates = [(review_dir / Path(*rel_path.parts)).resolve()]
        if review_dir.name == "fen_manual_assets" and rel_path.parts[0] == "fen_manual_assets":
            candidates.append((review_dir / Path(*rel_path.parts[1:])).resolve())
        for candidate in candidates:
            if review_dir not in candidate.parents and candidate != review_dir:
                continue
            if candidate.is_file():
                return candidate
        raise FenReviewCorpusExportError("crop_asset_missing_local")

    base_url = str(service_base_url or "").strip().rstrip("/")
    if not base_url:
        raise FenReviewCorpusExportError("crop_asset_source_missing")
    _validate_service_base_url(base_url)
    asset_parts = rel_path.parts[1:] if rel_path.parts[0] == "fen_manual_assets" else rel_path.parts
    quoted_asset = "/".join(urllib.parse.quote(part, safe="") for part in asset_parts)
    url = (
        f"{base_url}/convert/artifact/{urllib.parse.quote(artifact_id, safe='')}"
        f"/fen_manual_assets/{quoted_asset}"
    )
    target = out_dir / "assets" / Path(*rel_path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"Accept": "image/png,image/*;q=0.9"})
    target.write_bytes(_open_bytes(request, opener=opener, max_bytes=_MAX_ASSET_BYTES))
    return target.resolve()


def _safe_relative_asset_path(value: str) -> PurePosixPath:
    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise FenReviewCorpusExportError("crop_rel_path_invalid")
    if path.parts[0] != "fen_manual_assets":
        raise FenReviewCorpusExportError("crop_rel_path_outside_review_assets")
    return path


def _open_bytes(
    request: urllib.request.Request,
    *,
    opener: Callable[..., Any] | None,
    max_bytes: int,
) -> bytes:
    resolved_opener = opener or urllib.request.urlopen
    try:
        response = resolved_opener(request, timeout=60)
        with response:
            data = response.read(max_bytes + 1)
    except Exception as exc:
        raise FenReviewCorpusExportError("remote_asset_or_review_fetch_failed") from exc
    if len(data) > max_bytes:
        raise FenReviewCorpusExportError("remote_payload_too_large")
    return data


def _validate_service_base_url(value: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FenReviewCorpusExportError("service_base_url_invalid")


def _first_source_digest(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        value = str(
            row.get("source_document_sha256")
            or row.get("source_artifact_sha256")
            or ""
        ).strip()
        if value:
            return value
    return ""


def _excluded_audit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": str(row.get("artifact_id") or ""),
        "diagram_id": str(row.get("diagram_id") or ""),
        "diagram_fingerprint": str(row.get("diagram_fingerprint") or ""),
        "label_status": str(row.get("label_status") or ""),
        "verified_by": str(row.get("verified_by") or ""),
        "verified_at": str(row.get("verified_at") or ""),
        "notes": str(row.get("notes") or ""),
    }


def _row_issue(row: Mapping[str, Any], code: str, **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "diagram_id": str(row.get("diagram_id") or ""),
        "diagram_fingerprint": str(row.get("diagram_fingerprint") or ""),
        **extra,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _report_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# FEN review corpus export",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Artifact: `{report.get('artifact_id')}`",
            f"- Storage: `{report.get('storage')}`",
            f"- Review rows: `{report.get('review_row_count')}`",
            f"- Verified labels: `{report.get('verified_count')}`",
            f"- Excluded rows: `{report.get('excluded_count')}`",
            f"- Issues: `{report.get('issue_count')}`",
            f"- Validator: `{(report.get('validator') or {}).get('status')}`",
            "",
            "Only the verified JSONL is eligible for dataset generation. Excluded rows remain audit evidence.",
            "",
        ]
    )
