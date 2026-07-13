from __future__ import annotations

import html
import json
import re
import shutil
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

from chess_diagram_fingerprint import (
    build_diagram_fingerprint,
    normalized_bbox,
    source_document_sha256,
)
from chess_position_recognizer import validate_fen


INTAKE_MANIFEST_SCHEMA = "kindlemaster.chess.fen_gold_corpus_intake.v1"
REVIEW_ROW_SCHEMA = "kindlemaster.chess.fen_gold_review.row.v1"
IMPORT_REPORT_SCHEMA = "kindlemaster.chess.fen_gold_import_report.v1"
MARKER_POLICY = "manual_marker_labels_train_and_evaluate_only_no_direct_fen_publication"
REVIEW_STATUSES = {"needs_manual_fen", "verified", "rejected", "unreadable"}
MARKER_CLASSES = {"outline_triangle", "filled_triangle", "bad_crop", "multiple"}


def build_fen_gold_corpus_review(
    *,
    source_pdf: str | Path,
    job_output: str | Path,
    marker_labels: str | Path,
    output_dir: str | Path,
    source_profile: str = "yusupov-fundamentals",
    asset_roots: Sequence[str | Path] = (),
) -> dict[str, Any]:
    pdf_path = Path(source_pdf).resolve()
    job_root = Path(job_output).resolve()
    labels_path = Path(marker_labels).resolve()
    out = Path(output_dir).resolve()
    _require_file(pdf_path, "source_pdf")
    _require_file(labels_path, "marker_labels")
    if not job_root.is_dir():
        raise ValueError(f"job_output_not_found:{job_root}")

    source_sha = source_document_sha256(pdf_path)
    job_manifest_path, job_payload, diagrams = _load_job_diagrams(job_root)
    _validate_job_source(job_payload, source_sha)
    manual_labels = _load_jsonl(labels_path)
    labels_by_id, label_errors = _validate_marker_labels(manual_labels, diagrams)
    if label_errors:
        raise ValueError("marker_labels_invalid:" + ";".join(label_errors[:20]))

    roots = [job_root, *(Path(value).resolve() for value in asset_roots)]
    out.mkdir(parents=True, exist_ok=True)
    assets_dir = out / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    review_rows: list[dict[str, Any]] = []
    marker_rows: list[dict[str, Any]] = []
    missing_assets: list[dict[str, str]] = []
    copied_asset_hashes: dict[str, str] = {}
    with fitz.open(pdf_path) as document:
        for diagram in sorted(diagrams, key=_diagram_sort_key):
            diagram_id = _diagram_id(diagram)
            page = _positive_int(diagram.get("page") or diagram.get("page_number"))
            if page is None or page > len(document):
                raise ValueError(f"diagram_page_invalid:{diagram_id}:{page}")
            board_source = _resolve_asset(
                roots,
                diagram.get("board_crop_path")
                or diagram.get("source_crop")
                or diagram.get("image_path")
                or diagram.get("image_href"),
            )
            if board_source is None:
                missing_assets.append({"diagram_id": diagram_id, "kind": "board_crop"})
                continue

            bbox_xyxy = _diagram_bbox_xyxy(diagram)
            page_rect = document[page - 1].rect
            normalized = normalized_bbox(bbox_xyxy, (page_rect.width, page_rect.height))
            with Image.open(board_source) as board_image:
                fingerprint = build_diagram_fingerprint(
                    source_sha256=source_sha,
                    page=page,
                    normalized_bbox_xyxy=normalized,
                    board_crop=board_image,
                )
            fingerprint_id = str(fingerprint["diagram_fingerprint"])
            board_target = assets_dir / f"{fingerprint_id}_board{board_source.suffix.lower() or '.png'}"
            _copy_verified_asset(board_source, board_target, copied_asset_hashes)
            board_sha = _file_sha256(board_target)

            marker_label = labels_by_id.get(diagram_id)
            copied_marker_assets = _copy_marker_assets(
                marker_label,
                roots=roots,
                target_dir=assets_dir,
                fingerprint=fingerprint_id,
                copied_asset_hashes=copied_asset_hashes,
                missing_assets=missing_assets,
                diagram_id=diagram_id,
            )
            chapter_id, split = _split_for_page(page)
            marker_side = _manual_marker_side(marker_label)
            row = {
                "schema": REVIEW_ROW_SCHEMA,
                **fingerprint,
                "review_index": len(review_rows),
                "source_profile": source_profile,
                "diagram_id": diagram_id,
                "chapter_id": chapter_id,
                "split": split,
                "allowed_for_tuning": split != "holdout",
                "board_crop_path": board_target.relative_to(out).as_posix(),
                "board_crop_sha256": board_sha,
                "marker_assets": copied_marker_assets,
                "manual_visible_marker": str((marker_label or {}).get("manual_visible_marker") or ""),
                "manual_marker_side": marker_side,
                "marker_label_status": str((marker_label or {}).get("label_status") or "unlabeled"),
                "marker_human_verified": bool((marker_label or {}).get("human_verified")),
                "marker_verification_source": str((marker_label or {}).get("verification_source") or ""),
                "proposed_marker_bbox": _bbox_list((marker_label or {}).get("review_suggestion_bbox")),
                "manual_marker_bbox": _bbox_list((marker_label or {}).get("manual_marker_bbox")),
                "marker_bbox_verified": bool(_bbox_list((marker_label or {}).get("manual_marker_bbox"))),
                "candidate_fen": _candidate_fen(diagram),
                "candidate_fen_status": str(diagram.get("full_fen_status") or diagram.get("status") or ""),
                "candidate_confidence": _optional_float(
                    diagram.get("fen_confidence") or diagram.get("confidence")
                ),
                "manual_fen": "",
                "label_status": "needs_manual_fen",
                "human_verified": False,
                "verified_by": "",
                "verified_at": "",
                "review_notes": "",
                "policy": "review_only_no_fen_publication_until_validated_import",
            }
            review_rows.append(row)
            if marker_label is not None:
                marker_rows.append(
                    {
                        "schema": "kindlemaster.chess.marker_training_label.v1",
                        **fingerprint,
                        "source_profile": source_profile,
                        "diagram_id": diagram_id,
                        "chapter_id": chapter_id,
                        "split": split,
                        "allowed_for_tuning": split != "holdout",
                        "board_crop_path": row["board_crop_path"],
                        "board_crop_sha256": board_sha,
                        "marker_assets": copied_marker_assets,
                        "manual_visible_marker": row["manual_visible_marker"],
                        "manual_side_to_move": marker_side,
                        "manual_marker_bbox": row["manual_marker_bbox"],
                        "proposed_marker_bbox": row["proposed_marker_bbox"],
                        "marker_bbox_verified": row["marker_bbox_verified"],
                        "label_status": "verified",
                        "human_verified": True,
                        "verification_source": row["marker_verification_source"],
                        "verified_by": str(marker_label.get("verified_by") or "manual-review-export"),
                        "verified_at": str(marker_label.get("verified_at") or ""),
                        "policy": MARKER_POLICY,
                    }
                )

    if not review_rows:
        raise ValueError("review_rows_empty")
    fingerprint_count = len({row["diagram_fingerprint"] for row in review_rows})
    if fingerprint_count != len(review_rows):
        raise ValueError("diagram_fingerprint_duplicate")

    review_path = out / "full_fen_review.jsonl"
    marker_path = out / "marker_training_labels.jsonl"
    html_path = out / "full_fen_review.html"
    summary_path = out / "summary.json"
    manifest_path = out / "intake_manifest.json"
    _write_jsonl(review_path, review_rows)
    _write_jsonl(marker_path, marker_rows)
    html_path.write_text(_review_html(review_rows, source_sha=source_sha), encoding="utf-8")

    marker_counts = Counter(row["manual_visible_marker"] for row in marker_rows)
    side_counts = Counter(row["manual_side_to_move"] or "unknown" for row in marker_rows)
    split_counts = Counter(row["split"] for row in review_rows)
    missing_board_assets = [item for item in missing_assets if item["kind"] == "board_crop"]
    missing_optional_assets = [item for item in missing_assets if item["kind"] != "board_crop"]
    status = "ready_for_human_review" if not missing_board_assets else "failed_missing_board_assets"
    manifest = {
        "schema": INTAKE_MANIFEST_SCHEMA,
        "manifest_version": datetime.now(timezone.utc).strftime("%Y-%m-%d.%H%M%S"),
        "source_profile": source_profile,
        "source": {
            "kind": "fixed_edition_pdf",
            "sha256": source_sha,
            "filename": pdf_path.name,
            "copyright_content_committed": False,
        },
        "job_evidence": {
            "path": str(job_manifest_path),
            "candidate_count": len(diagrams),
        },
        "split_policy": {
            "method": "eight_page_bands_fixed_cycle",
            "page_and_group_isolated": True,
            "holdout_tuning_forbidden": True,
        },
        "artifacts": {
            "review_rows": review_path.name,
            "marker_training_labels": marker_path.name,
            "review_html": html_path.name,
            "assets_dir": assets_dir.name,
        },
        "counts": {
            "job_candidate_count": len(diagrams),
            "review_row_count": len(review_rows),
            "manual_marker_label_count": len(marker_rows),
            "remaining_human_review_count": len(review_rows),
            "missing_asset_count": len(missing_assets),
            "missing_board_asset_count": len(missing_board_assets),
            "missing_optional_asset_count": len(missing_optional_assets),
            "marker_classes": dict(sorted(marker_counts.items())),
            "marker_sides": dict(sorted(side_counts.items())),
            "splits": dict(sorted(split_counts.items())),
        },
        "policy": {
            "marker_labels": MARKER_POLICY,
            "full_fen": "review_only_until_source_bound_import_passes",
            "model_generated_labels_forbidden": True,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "status": status,
        "schema": "kindlemaster.chess.fen_gold_corpus_build_report.v1",
        "source_document_sha256": source_sha,
        "source_profile": source_profile,
        "candidate_count": len(diagrams),
        "review_row_count": len(review_rows),
        "manual_marker_label_count": len(marker_rows),
        "remaining_human_review_count": len(review_rows),
        "missing_assets": missing_assets,
        "missing_board_asset_count": len(missing_board_assets),
        "missing_optional_asset_count": len(missing_optional_assets),
        "marker_class_counts": dict(sorted(marker_counts.items())),
        "marker_side_counts": dict(sorted(side_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "manifest_path": str(manifest_path),
        "review_jsonl_path": str(review_path),
        "review_html_path": str(html_path),
        "marker_labels_path": str(marker_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = out / "summary.md"
    markdown_path.write_text(_build_summary_markdown(summary), encoding="utf-8")
    zip_path = out / "full_fen_review_package.zip"
    _write_review_zip(out, zip_path)
    summary["summary_path"] = str(summary_path)
    summary["summary_markdown_path"] = str(markdown_path)
    summary["review_zip_path"] = str(zip_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def validate_fen_gold_corpus_labels(
    *,
    source_pdf: str | Path,
    intake_manifest: str | Path,
    filled_labels: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    pdf_path = Path(source_pdf).resolve()
    manifest_path = Path(intake_manifest).resolve()
    filled_path = Path(filled_labels).resolve()
    out = Path(output_dir).resolve()
    _require_file(pdf_path, "source_pdf")
    _require_file(manifest_path, "intake_manifest")
    _require_file(filled_path, "filled_labels")
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != INTAKE_MANIFEST_SCHEMA:
        raise ValueError("intake_manifest_schema_invalid")
    expected_sha = str((manifest.get("source") or {}).get("sha256") or "")
    actual_sha = source_document_sha256(pdf_path)
    if actual_sha != expected_sha:
        raise ValueError("source_sha256_mismatch")
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    expected_path = _resolve_package_member(
        manifest_path.parent,
        artifacts.get("review_rows") or "full_fen_review.jsonl",
    )
    expected_rows = _load_jsonl(expected_path)
    filled_rows = _load_jsonl(filled_path)
    expected = {str(row.get("diagram_fingerprint") or ""): row for row in expected_rows}
    if not expected or "" in expected:
        raise ValueError("expected_diagram_fingerprint_missing")
    if len(expected) != len(expected_rows):
        raise ValueError("expected_diagram_fingerprint_duplicate")
    if any(str(row.get("source_document_sha256") or "") != actual_sha for row in expected_rows):
        raise ValueError("expected_row_source_sha256_mismatch")
    errors: list[str] = []
    seen: set[str] = set()
    verified: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for index, row in enumerate(filled_rows, start=1):
        prefix = f"row[{index}]"
        fingerprint = str(row.get("diagram_fingerprint") or "")
        if fingerprint in seen:
            errors.append(f"{prefix}:diagram_fingerprint_duplicate")
            continue
        seen.add(fingerprint)
        expected_row = expected.get(fingerprint)
        if expected_row is None:
            errors.append(f"{prefix}:diagram_fingerprint_unknown")
            continue
        if str(row.get("source_document_sha256") or "") != actual_sha:
            errors.append(f"{prefix}:source_sha256_mismatch")
        if str(row.get("board_crop_sha256") or "") != str(expected_row.get("board_crop_sha256") or ""):
            errors.append(f"{prefix}:board_crop_sha256_mismatch")
        status = str(row.get("label_status") or "")
        status_counts[status or "missing"] += 1
        if status not in REVIEW_STATUSES:
            errors.append(f"{prefix}:label_status_invalid")
            continue
        if status == "needs_manual_fen":
            continue
        if row.get("human_verified") is not True:
            errors.append(f"{prefix}:human_verified_required")
        if not str(row.get("verified_by") or "").strip():
            errors.append(f"{prefix}:verified_by_missing")
        if not _valid_timestamp(row.get("verified_at")):
            errors.append(f"{prefix}:verified_at_invalid")
        manual_fen = str(row.get("manual_fen") or "").strip()
        if status == "verified":
            valid, warnings = validate_fen(manual_fen)
            if not valid:
                errors.append(f"{prefix}:manual_fen_invalid:{','.join(warnings)}")
                continue
            expected_side = str(expected_row.get("manual_marker_side") or "")
            actual_side = manual_fen.split()[1] if len(manual_fen.split()) == 6 else ""
            if expected_side in {"w", "b"} and actual_side != expected_side:
                errors.append(f"{prefix}:marker_side_conflict")
                continue
            verified.append(
                {
                    "schema": "kindlemaster.chess.fen_gold_label.v1",
                    "source_document_sha256": actual_sha,
                    "source_profile": str(expected_row.get("source_profile") or ""),
                    "diagram_fingerprint": fingerprint,
                    "diagram_id": str(expected_row.get("diagram_id") or ""),
                    "page": expected_row.get("page"),
                    "chapter_id": str(expected_row.get("chapter_id") or ""),
                    "split": str(expected_row.get("split") or ""),
                    "allowed_for_tuning": bool(expected_row.get("allowed_for_tuning")),
                    "normalized_bbox_xyxy": expected_row.get("normalized_bbox_xyxy"),
                    "board_perceptual_hash": expected_row.get("board_perceptual_hash"),
                    "board_crop_path": expected_row.get("board_crop_path"),
                    "board_crop_sha256": expected_row.get("board_crop_sha256"),
                    "fen": manual_fen,
                    "manual_fen": manual_fen,
                    "label_status": "verified",
                    "label_source": "manual_fen",
                    "human_verified": True,
                    "verified_by": str(row.get("verified_by")),
                    "verified_at": str(row.get("verified_at")),
                    "review_notes": str(row.get("review_notes") or ""),
                }
            )
        elif manual_fen:
            errors.append(f"{prefix}:non_verified_status_must_not_include_fen")

    missing = sorted(set(expected) - seen)
    if missing:
        errors.append(f"expected_rows_missing:{len(missing)}")
    pending = status_counts["needs_manual_fen"] + len(missing)
    status = "failed" if errors else "needs_review" if pending else "passed"
    out.mkdir(parents=True, exist_ok=True)
    labels_output = out / "verified_full_fen_labels.jsonl"
    if status == "passed":
        _write_jsonl(labels_output, verified)
    report = {
        "schema": IMPORT_REPORT_SCHEMA,
        "status": status,
        "source_document_sha256": actual_sha,
        "expected_row_count": len(expected_rows),
        "filled_row_count": len(filled_rows),
        "verified_row_count": len(verified),
        "pending_row_count": pending,
        "status_counts": dict(sorted(status_counts.items())),
        "error_count": len(errors),
        "errors": errors,
        "verified_labels_path": str(labels_output) if status == "passed" else "",
    }
    report_path = out / "fen_gold_import_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = out / "fen_gold_import_report.md"
    markdown_path.write_text(_import_summary_markdown(report), encoding="utf-8")
    report["report_path"] = str(report_path)
    report["report_markdown_path"] = str(markdown_path)
    return report


def _load_job_diagrams(root: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    candidates = (root / "chess_diagrams.json", root / "diagrams" / "diagrams.json")
    for path in candidates:
        if not path.is_file():
            continue
        payload = _load_json(path)
        for key in ("diagrams", "records", "items"):
            rows = payload.get(key)
            if isinstance(rows, list):
                diagrams = [dict(row) for row in rows if isinstance(row, Mapping)]
                if diagrams:
                    return path, payload, diagrams
    raise ValueError(f"job_diagrams_not_found:{root}")


def _validate_job_source(payload: Mapping[str, Any], source_sha: str) -> None:
    source_path = str(payload.get("source_pdf") or "").strip()
    if not source_path:
        raise ValueError("job_source_pdf_missing")
    if not Path(source_path).is_file():
        raise ValueError("job_source_pdf_unavailable")
    if source_document_sha256(source_path) != source_sha:
        raise ValueError("job_source_pdf_sha256_mismatch")


def _validate_marker_labels(
    rows: Iterable[Mapping[str, Any]],
    diagrams: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    diagram_ids = {_diagram_id(row) for row in diagrams}
    labels: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for index, source in enumerate(rows, start=1):
        row = dict(source)
        diagram_id = str(row.get("diagram_id") or "").strip()
        prefix = f"marker[{index}]"
        if not diagram_id:
            errors.append(f"{prefix}:diagram_id_missing")
            continue
        if diagram_id in labels:
            errors.append(f"{prefix}:diagram_id_duplicate")
            continue
        if diagram_id not in diagram_ids:
            errors.append(f"{prefix}:diagram_id_not_in_job")
        marker_class = str(row.get("manual_visible_marker") or "")
        if marker_class not in MARKER_CLASSES:
            errors.append(f"{prefix}:manual_visible_marker_invalid")
        if row.get("label_status") != "verified" or row.get("human_verified") is not True:
            errors.append(f"{prefix}:human_verified_label_required")
        side = str(row.get("manual_side_to_move") or "")
        expected_side = "w" if marker_class == "outline_triangle" else "b" if marker_class == "filled_triangle" else ""
        if side != expected_side:
            errors.append(f"{prefix}:marker_side_inconsistent")
        labels[diagram_id] = row
    return labels, errors


def _copy_marker_assets(
    row: Mapping[str, Any] | None,
    *,
    roots: Sequence[Path],
    target_dir: Path,
    fingerprint: str,
    copied_asset_hashes: dict[str, str],
    missing_assets: list[dict[str, str]],
    diagram_id: str,
) -> dict[str, str]:
    if row is None:
        return {}
    fields = {
        "marker": "side_marker_crop_path",
        "search": "side_marker_search_crop_path",
        "review": "side_marker_review_crop_path",
        "overlay": "debug_overlay_path",
    }
    copied: dict[str, str] = {}
    for kind, field in fields.items():
        raw_path = row.get(field)
        if not str(raw_path or "").strip():
            continue
        source = _resolve_asset(roots, raw_path)
        if source is None:
            missing_assets.append({"diagram_id": diagram_id, "kind": kind})
            continue
        target = target_dir / f"{fingerprint}_{kind}{source.suffix.lower() or '.png'}"
        _copy_verified_asset(source, target, copied_asset_hashes)
        copied[kind] = target.relative_to(target_dir.parent).as_posix()
    return copied


def _resolve_asset(roots: Sequence[Path], value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute() and candidate.is_file():
        resolved = candidate.resolve()
        if any(resolved.is_relative_to(root.resolve()) for root in roots):
            return resolved
        return None
    normalized = raw.replace("\\", "/").lstrip("/")
    for root in roots:
        resolved_root = root.resolve()
        direct = (resolved_root / Path(normalized)).resolve()
        if direct.is_relative_to(resolved_root) and direct.is_file():
            return direct
        basename = root / Path(normalized).name
        if basename.is_file():
            return basename.resolve()
    return None


def _resolve_package_member(root: Path, value: Any) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("package_member_missing")
    resolved_root = root.resolve()
    candidate = (resolved_root / Path(raw)).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("package_member_outside_root")
    _require_file(candidate, "package_member")
    return candidate


def _copy_verified_asset(source: Path, target: Path, seen: dict[str, str]) -> None:
    digest = _file_sha256(source)
    prior = seen.get(target.name)
    if prior and prior != digest:
        raise ValueError(f"asset_name_collision:{target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or _file_sha256(target) != digest:
        shutil.copy2(source, target)
    seen[target.name] = digest


def _diagram_bbox_xyxy(row: Mapping[str, Any]) -> list[float]:
    direct = _bbox_list(row.get("bbox_xyxy"))
    if direct and direct[2] > direct[0] and direct[3] > direct[1]:
        return direct
    bbox = _bbox_list(row.get("bbox"))
    if bbox and bbox[2] > 0 and bbox[3] > 0:
        return [bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]]
    raise ValueError(f"diagram_bbox_invalid:{_diagram_id(row)}")


def _split_for_page(page: int) -> tuple[str, str]:
    group_index = max(0, (int(page) - 1) // 8)
    start = group_index * 8 + 1
    chapter_id = f"page-band-{start:03d}-{start + 7:03d}"
    split = ("train", "train", "train", "calibration", "holdout")[group_index % 5]
    return chapter_id, split


def _candidate_fen(row: Mapping[str, Any]) -> str:
    for key in ("full_fen", "fen", "fen_candidate"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _manual_marker_side(row: Mapping[str, Any] | None) -> str:
    if row is None:
        return ""
    side = str(row.get("manual_side_to_move") or "").strip().lower()
    return side if side in {"w", "b"} else ""


def _review_html(rows: Sequence[Mapping[str, Any]], *, source_sha: str) -> str:
    cards = "\n".join(_review_card(row) for row in rows)
    seed_json = json.dumps(list(rows), ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>KindleMaster - weryfikacja pelnego FEN</title>
<style>
:root{{--paper:#f4efe5;--panel:#fffdf7;--ink:#18211c;--muted:#697269;--line:#d7d0c1;--accent:#b4432f;--accent-dark:#7f2d20;--ok:#236a4a;--warn:#9a5b12;--radius:14px;--shadow:0 14px 36px rgba(43,38,29,.09)}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 8% 0,#fff9e9 0,transparent 31%),linear-gradient(135deg,#eee7d9,#f8f4eb 56%,#e7eee7);color:var(--ink);font-family:"Trebuchet MS",sans-serif;line-height:1.5}}
button,input,select,textarea{{font:inherit}} button,select,input,textarea{{min-height:44px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink)}} button{{padding:.65rem 1rem;font-weight:700;cursor:pointer}} button:hover{{border-color:var(--accent)}} :focus-visible{{outline:3px solid #e28b68;outline-offset:2px}}
.shell{{width:min(1500px,calc(100% - 32px));margin:0 auto;padding:28px 0 64px}} header{{display:grid;grid-template-columns:1fr auto;gap:24px;align-items:end;margin-bottom:20px}} h1,h2{{font-family:Georgia,serif;margin:0}} h1{{font-size:clamp(2rem,4vw,3.4rem);line-height:1.02;max-width:760px}} .eyebrow{{color:var(--accent-dark);font-weight:800;letter-spacing:.08em;text-transform:uppercase;font-size:.76rem}} .source{{max-width:420px;color:var(--muted);font-family:Consolas,monospace;font-size:.76rem;overflow-wrap:anywhere}}
.toolbar{{position:sticky;top:10px;z-index:5;display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) auto;gap:10px;padding:12px;background:rgba(255,253,247,.92);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);backdrop-filter:blur(12px)}} .metric strong{{display:block;font-family:Georgia,serif;font-size:1.45rem}} .metric span{{color:var(--muted);font-size:.78rem}} .actions{{display:flex;gap:8px;align-items:center}} .primary{{background:var(--accent);border-color:var(--accent);color:white}} .primary:hover{{background:var(--accent-dark)}}
.filters{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}} .filters input{{min-width:230px;padding:0 12px}} .filters select{{padding:0 10px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 7px 22px rgba(43,38,29,.06);overflow:hidden}} .card[hidden]{{display:none}} .card-head{{display:flex;justify-content:space-between;gap:12px;padding:13px 15px;border-bottom:1px solid var(--line)}} .card-head h2{{font-size:1.1rem}} .meta{{color:var(--muted);font-size:.78rem}} .badge{{align-self:start;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem;font-weight:800}} .media{{display:grid;grid-template-columns:1fr minmax(120px,.42fr);gap:8px;padding:12px;background:#e9e3d8}} figure{{margin:0;min-width:0}} img{{display:block;width:100%;height:260px;object-fit:contain;background:white;border-radius:9px}} .marker img{{height:126px}} figcaption{{padding-top:5px;color:var(--muted);font-size:.72rem}} .form{{display:grid;gap:10px;padding:14px}} .form label{{display:grid;gap:5px;font-size:.78rem;font-weight:800}} .form input,.form textarea,.form select{{width:100%;padding:9px 10px}} .form textarea{{min-height:70px;resize:vertical}} .candidate{{font-family:Consolas,monospace;font-size:.76rem;overflow-wrap:anywhere;color:var(--muted)}} .status-line{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .is-saved{{border-color:#76a78e;box-shadow:inset 4px 0 var(--ok)}}
.empty{{display:none;padding:40px;text-align:center;color:var(--muted)}}
@media(max-width:900px){{header{{grid-template-columns:1fr}}.toolbar{{grid-template-columns:repeat(2,1fr)}}.actions{{grid-column:1/-1}}.grid{{grid-template-columns:1fr}}}}
@media(max-width:560px){{.shell{{width:min(100% - 20px,1500px);padding-top:18px}}.toolbar{{position:static;grid-template-columns:1fr 1fr}}.media{{grid-template-columns:1fr}}img{{height:auto;max-height:320px}}.marker img{{height:150px}}.status-line{{grid-template-columns:1fr}}}}
</style></head><body><main class="shell">
<header><div><div class="eyebrow">Gold corpus / fixed edition</div><h1>Weryfikacja pelnego FEN</h1></div><div class="source">SHA256: {html.escape(source_sha)}</div></header>
<section class="toolbar" aria-label="Postep"><div class="metric"><strong id="total">{len(rows)}</strong><span>wszystkie</span></div><div class="metric"><strong id="verified">0</strong><span>zweryfikowane</span></div><div class="metric"><strong id="rejected">0</strong><span>odrzucone</span></div><div class="metric"><strong id="pending">{len(rows)}</strong><span>pozostalo</span></div><div class="actions"><button id="clear">Wyczysc filtry</button><button class="primary" id="export">Eksportuj JSONL</button></div></section>
<section class="filters" aria-label="Filtry"><input id="query" type="search" placeholder="Szukaj ID, strony lub FEN" aria-label="Szukaj"><select id="status" aria-label="Filtr statusu"><option value="">Wszystkie statusy</option><option value="needs_manual_fen">Do weryfikacji</option><option value="verified">Zweryfikowane</option><option value="rejected">Odrzucone</option><option value="unreadable">Nieczytelne</option></select><select id="split" aria-label="Filtr zbioru"><option value="">Wszystkie zbiory</option><option>train</option><option>calibration</option><option>holdout</option></select></section>
<section class="grid" id="grid">{cards}</section><p class="empty" id="empty">Brak rekordow spelniajacych filtry.</p>
</main><script id="seed" type="application/json">{seed_json}</script><script>
const seed=JSON.parse(document.getElementById('seed').textContent);const key='kindlemaster-fen-gold-{source_sha[:16]}';let state={{}};try{{state=JSON.parse(localStorage.getItem(key)||'{{}}')}}catch{{state={{}}}}
const cards=[...document.querySelectorAll('.card')];function rowFor(card){{const base=seed[Number(card.dataset.index)];const form=card.querySelector('form');return{{...base,manual_fen:form.manual_fen.value.trim(),label_status:form.label_status.value,human_verified:form.label_status.value!=='needs_manual_fen',verified_by:form.verified_by.value.trim(),verified_at:form.verified_at.value.trim(),review_notes:form.review_notes.value.trim()}}}}
function save(card){{const row=rowFor(card);state[row.diagram_fingerprint]=row;localStorage.setItem(key,JSON.stringify(state));card.classList.add('is-saved');refresh()}}function restore(card){{const row=state[card.dataset.fingerprint];if(!row)return;const f=card.querySelector('form');for(const n of ['manual_fen','label_status','verified_by','verified_at','review_notes'])if(row[n]!==undefined)f[n].value=row[n];card.classList.add('is-saved')}}
function refresh(){{let v=0,r=0,p=0;for(const card of cards){{const row=rowFor(card);if(row.label_status==='verified')v++;else if(row.label_status==='rejected'||row.label_status==='unreadable')r++;else p++}}document.getElementById('verified').textContent=v;document.getElementById('rejected').textContent=r;document.getElementById('pending').textContent=p;filter()}}
function filter(){{const q=document.getElementById('query').value.toLowerCase();const s=document.getElementById('status').value;const split=document.getElementById('split').value;let shown=0;for(const card of cards){{const row=rowFor(card);const text=(row.diagram_id+' '+row.page+' '+row.manual_fen+' '+row.candidate_fen).toLowerCase();const ok=(!q||text.includes(q))&&(!s||row.label_status===s)&&(!split||row.split===split);card.hidden=!ok;if(ok)shown++}}document.getElementById('empty').style.display=shown?'none':'block'}}
for(const card of cards){{restore(card);card.querySelector('form').addEventListener('input',()=>save(card));card.querySelector('form').addEventListener('change',()=>save(card))}}for(const id of ['query','status','split'])document.getElementById(id).addEventListener('input',filter);document.getElementById('clear').onclick=()=>{{document.getElementById('query').value='';document.getElementById('status').value='';document.getElementById('split').value='';filter()}};document.getElementById('export').onclick=()=>{{const lines=cards.map(c=>JSON.stringify(rowFor(c))).join('\\n')+'\\n';const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines],{{type:'application/x-ndjson'}}));a.download='fen_gold_review_filled.jsonl';a.click();URL.revokeObjectURL(a.href)}};refresh();
</script></body></html>"""


def _review_card(row: Mapping[str, Any]) -> str:
    index = int(row.get("review_index") or 0)
    fingerprint = html.escape(str(row.get("diagram_fingerprint") or ""), quote=True)
    board = html.escape(str(row.get("board_crop_path") or ""), quote=True)
    marker_assets = row.get("marker_assets") if isinstance(row.get("marker_assets"), Mapping) else {}
    marker = html.escape(str(marker_assets.get("search") or marker_assets.get("marker") or ""), quote=True)
    marker_html = (
        f'<figure class="marker"><img loading="lazy" src="{marker}" alt="Crop markera"><figcaption>Marker / strefa wyszukiwania</figcaption></figure>'
        if marker
        else '<figure class="marker"><div class="meta">Brak cropa markera</div></figure>'
    )
    candidate = html.escape(str(row.get("candidate_fen") or "brak"))
    return f"""<article class="card" data-index="{index}" data-fingerprint="{fingerprint}">
<div class="card-head"><div><h2>{html.escape(str(row.get('diagram_id') or 'diagram'))}</h2><div class="meta">strona {row.get('page')} / {html.escape(str(row.get('split') or ''))}</div></div><span class="badge">{html.escape(str(row.get('manual_visible_marker') or 'bez etykiety'))}</span></div>
<div class="media"><figure><img loading="lazy" src="{board}" alt="Plansza {html.escape(str(row.get('diagram_id') or ''))}"><figcaption>Zweryfikuj wszystkie 64 pola i orientacje</figcaption></figure>{marker_html}</div>
<form class="form"><div class="candidate">Kandydat: {candidate}</div><label>Pelny FEN<input name="manual_fen" autocomplete="off" placeholder="8/8/8/8/8/8/8/4K2k w - - 0 1"></label><div class="status-line"><label>Status<select name="label_status"><option value="needs_manual_fen">Do weryfikacji</option><option value="verified">Zweryfikowany</option><option value="rejected">False positive</option><option value="unreadable">Nieczytelny</option></select></label><label>Weryfikujacy<input name="verified_by" autocomplete="name" placeholder="Imie lub identyfikator"></label></div><label>Czas weryfikacji<input name="verified_at" placeholder="2026-07-13T12:00:00Z"></label><label>Uwagi<textarea name="review_notes" placeholder="Konflikt markera, zly crop, nieczytelne pole..."></textarea></label></form></article>"""


def _write_review_zip(root: Path, target: Path) -> None:
    readme = root / "README.txt"
    readme.write_text(
        "Open full_fen_review.html, verify every candidate, then use Eksportuj JSONL.\n"
        "Do not rename or edit source/fingerprint/crop hash fields.\n"
        "Run: python kindlemaster.py chess import-fen-gold-labels ...\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path != target:
                archive.write(path, path.relative_to(root).as_posix())


def _build_summary_markdown(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# FEN gold corpus intake",
            "",
            f"- status: `{summary.get('status')}`",
            f"- source SHA256: `{summary.get('source_document_sha256')}`",
            f"- candidates: {summary.get('candidate_count')}",
            f"- manual marker labels: {summary.get('manual_marker_label_count')}",
            f"- remaining full-FEN reviews: {summary.get('remaining_human_review_count')}",
            f"- missing board assets: {summary.get('missing_board_asset_count')}",
            f"- missing optional assets: {summary.get('missing_optional_asset_count')}",
            "",
            "Manual marker labels remain training/evaluation evidence. No FEN is published by this build step.",
            "",
        ]
    )


def _import_summary_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# FEN gold corpus import",
            "",
            f"- status: `{report.get('status')}`",
            f"- expected: {report.get('expected_row_count')}",
            f"- verified: {report.get('verified_row_count')}",
            f"- pending: {report.get('pending_row_count')}",
            f"- errors: {report.get('error_count')}",
            "",
        ]
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"json_object_required:{path}")
    return dict(payload)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"jsonl_invalid:{path}:{line_number}:{error.msg}") from error
        if not isinstance(row, Mapping):
            raise ValueError(f"jsonl_object_required:{path}:{line_number}")
        rows.append(dict(row))
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _diagram_id(row: Mapping[str, Any]) -> str:
    value = str(row.get("diagram_id") or row.get("id") or "").strip()
    if not value:
        raise ValueError("diagram_id_missing")
    return value


def _diagram_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    page = _positive_int(row.get("page") or row.get("page_number")) or 0
    order = _positive_int(row.get("visual_order_on_page") or row.get("source_order")) or 0
    return page, order, _diagram_id(row)


def _bbox_list(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return []
    try:
        return [round(float(item), 6) for item in value[:4]]
    except (TypeError, ValueError):
        return []


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_float(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _valid_timestamp(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label}_not_found:{path}")
