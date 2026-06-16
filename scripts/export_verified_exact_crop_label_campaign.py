from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen


DEFAULT_TARGET_VERIFIED_LABELS = Path("reference_inputs/chess_fen/labels/fundamenty_verified_crop_labels.jsonl")
DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/exact_label_campaign/latest")

THRESHOLD_ONLY_WARNINGS = {
    frozenset({"piece_template_confidence_below_threshold", "side_to_move_inferred"}),
    frozenset({"piece_template_confidence_below_threshold", "side_to_move_marker_detected"}),
    frozenset(
        {
            "piece_template_confidence_below_threshold",
            "side_to_move_inferred",
            "side_to_move_marker_detected",
        }
    ),
}


def export_verified_exact_crop_label_campaign(
    report_json: str | Path,
    *,
    source_pdf: str | Path,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    target_verified_labels: str | Path = DEFAULT_TARGET_VERIFIED_LABELS,
) -> dict[str, Any]:
    report_path = Path(report_json)
    source_pdf_path = Path(source_pdf)
    target = Path(output_dir)
    crops_dir = target / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    epub_path = Path(str(report.get("output_path") or "").strip())
    if not epub_path.is_file():
        raise FileNotFoundError(f"Expected output EPUB at {epub_path}")

    records = list((((report.get("quality_report") or {}).get("chess_fen") or {}).get("records") or []))
    review_records = [record for record in records if bool(record.get("requires_review"))]

    existing_labels = _read_jsonl(Path(target_verified_labels))
    labels_by_sha = {
        str(row.get("sha256") or "").strip().lower(): row
        for row in existing_labels
        if _looks_like_sha256(row.get("sha256"))
    }
    labels_by_page_filename: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in existing_labels:
        key = (int(row.get("page") or 0), str(row.get("filename") or "").strip())
        if key[0] <= 0 or not key[1]:
            continue
        labels_by_page_filename.setdefault(key, []).append(row)

    exported_rows: list[dict[str, Any]] = []
    missing_crop_details: list[dict[str, Any]] = []

    with zipfile.ZipFile(epub_path) as archive:
        epub_names = {Path(name).name: name for name in archive.namelist()}
        for record in review_records:
            filename = str(record.get("filename") or "").strip()
            page = int(record.get("page") or 0)
            if not filename:
                missing_crop_details.append(
                    {
                        "page": page,
                        "filename": "",
                        "reason": "filename_missing",
                    }
                )
                continue
            archive_name = epub_names.get(filename)
            if not archive_name:
                missing_crop_details.append(
                    {
                        "page": page,
                        "filename": filename,
                        "reason": "crop_missing_in_epub",
                    }
                )
                continue

            crop_bytes = archive.read(archive_name)
            crop_sha256 = hashlib.sha256(crop_bytes).hexdigest()
            crop_path = crops_dir / filename
            crop_path.write_bytes(crop_bytes)

            exact_status, existing_row = _exact_label_status(
                crop_sha256=crop_sha256,
                page=page,
                filename=filename,
                labels_by_sha=labels_by_sha,
                labels_by_page_filename=labels_by_page_filename,
            )
            candidate_fen = _candidate_fen_from_record(record)
            warnings = [str(item) for item in (record.get("warnings") or [])]
            threshold_only = _is_threshold_only(warnings)
            confidence = float(record.get("confidence") or 0.0)
            existing_exact_fen = str((existing_row or {}).get("fen") or "").strip()
            exported_rows.append(
                {
                    "id": f"exact_p{page:03d}_{Path(filename).stem}",
                    "source_pdf": str(source_pdf_path),
                    "page": page,
                    "filename": filename,
                    "diagram_index": _diagram_index_from_filename(filename),
                    "crop_path": str(crop_path),
                    "crop_sha256": crop_sha256,
                    "existing_exact_fen": existing_exact_fen,
                    "existing_exact_sha256": str((existing_row or {}).get("sha256") or "").strip().lower(),
                    "stale_exact_label": exact_status == "stale_exact_label",
                    "candidate_fen": candidate_fen,
                    "candidate_placement": str(record.get("placement") or "").strip(),
                    "candidate_confidence": confidence,
                    "candidate_warnings": warnings,
                    "candidate_requires_review": bool(record.get("requires_review", True)),
                    "candidate_method": str(record.get("method") or "").strip(),
                    "candidate_side_to_move": str(record.get("side_to_move") or "").strip() or "w",
                    "threshold_only_candidate": threshold_only,
                    "near_threshold_candidate": confidence >= 0.80,
                    "exact_label_status": exact_status,
                    "human_verified": False,
                    "human_rejected": False,
                    "fen": "",
                    "verified_by": "",
                    "verified_at": "",
                    "notes": _draft_notes(exact_status, existing_exact_fen=existing_exact_fen),
                }
            )

    exported_rows.sort(key=_draft_sort_key)
    draft_path = target / "exact_label_draft.jsonl"
    summary_path = target / "campaign_summary.json"
    review_sheet_path = target / "review_sheet.html"
    _write_jsonl(draft_path, exported_rows)

    status_counts = Counter(str(row.get("exact_label_status") or "unknown") for row in exported_rows)
    priority_counts = _review_priority_counts(exported_rows)
    summary = {
        "status": "ok",
        "report_json": str(report_path),
        "source_pdf": str(source_pdf_path),
        "source_epub": str(epub_path),
        "target_verified_labels": str(Path(target_verified_labels)),
        "output_dir": str(target),
        "review_count": len(review_records),
        "exact_label_draft_count": len(exported_rows),
        "already_covered_count": status_counts.get("already_covered", 0),
        "stale_exact_label_count": status_counts.get("stale_exact_label", 0),
        "new_exact_label_candidate_count": status_counts.get("new_exact_label_candidate", 0),
        "missing_crop_count": len(missing_crop_details),
        "review_priority_counts": priority_counts,
        "status_counts": dict(sorted(status_counts.items())),
        "missing_crop_details": missing_crop_details,
        "exact_label_draft_path": str(draft_path),
        "review_sheet_path": str(review_sheet_path),
        "label_aids_command": f"python scripts/build_chess_fen_label_aids.py {_ps_path(draft_path)} --output-dir {_ps_path(target / 'label_aids')}",
        "apply_exact_labels_command": (
            f"python scripts/apply_verified_exact_crop_labels.py {_ps_path(draft_path)} "
            f"--target-labels {_ps_path(Path(target_verified_labels))} "
            f"--target-crops-dir {_ps_path(Path('reference_inputs/chess_fen/crops/imported_exact_review'))}"
        ),
        "rerun_convert_command": (
            f"python kindlemaster.py convert {_ps_path(source_pdf_path)} "
            f"--output {_ps_path(Path('output/fundamenty_exact_label_campaign.epub'))} "
            f"--report-json {_ps_path(Path('reports/chess_fen/fundamenty_exact_label_campaign.json'))}"
        ),
        "queue": exported_rows,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    review_sheet_path.write_text(_review_sheet_html(summary, exported_rows), encoding="utf-8")
    return summary


def _exact_label_status(
    *,
    crop_sha256: str,
    page: int,
    filename: str,
    labels_by_sha: dict[str, dict[str, Any]],
    labels_by_page_filename: dict[tuple[int, str], list[dict[str, Any]]],
) -> tuple[str, dict[str, Any] | None]:
    digest_match = labels_by_sha.get(crop_sha256)
    if digest_match is not None:
        return "already_covered", digest_match
    page_filename_rows = labels_by_page_filename.get((page, filename), [])
    if page_filename_rows:
        return "stale_exact_label", _select_latest_exact_label(page_filename_rows)
    return "new_exact_label_candidate", None


def _select_latest_exact_label(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        rows,
        key=lambda row: (
            str(row.get("verified_at") or ""),
            str(row.get("verified_by") or ""),
            str(row.get("sha256") or ""),
        ),
    )


def _candidate_fen_from_record(record: dict[str, Any]) -> str:
    explicit = str(record.get("fen") or "").strip()
    if explicit:
        return explicit
    placement = str(record.get("placement") or "").strip()
    if not placement:
        return ""
    side_to_move = str(record.get("side_to_move") or "w").strip().lower()
    side_to_move = side_to_move if side_to_move in {"w", "b"} else "w"
    candidate = f"{placement} {side_to_move} - - 0 1"
    is_valid, _warnings = validate_fen(candidate)
    return candidate if is_valid else ""


def _diagram_index_from_filename(filename: str) -> int | str:
    stem = Path(filename).stem
    suffix = stem.rsplit("_", 1)[-1] if "_" in stem else ""
    if suffix.isdigit():
        return int(suffix)
    return ""


def _looks_like_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _draft_notes(exact_status: str, *, existing_exact_fen: str) -> str:
    if exact_status == "stale_exact_label" and existing_exact_fen:
        return "Existing exact label found for the same page+filename but current crop hash changed. Re-verify and copy the checked FEN into fen."
    if exact_status == "already_covered":
        return "Current crop hash already exists in exact labels. This review row should be investigated before promotion."
    return "Review-only draft. Check the crop, then fill fen/verified_by/verified_at before applying exact labels."


def _is_threshold_only(warnings: list[str]) -> bool:
    warning_set = frozenset(str(item) for item in warnings if str(item).strip())
    return warning_set in THRESHOLD_ONLY_WARNINGS


def _draft_sort_key(row: dict[str, Any]) -> tuple[int, float, int, str]:
    if row.get("stale_exact_label"):
        priority = 0
    elif row.get("threshold_only_candidate") or row.get("near_threshold_candidate"):
        priority = 1
    else:
        priority = 2
    return (
        priority,
        -float(row.get("candidate_confidence") or 0.0),
        int(row.get("page") or 0),
        str(row.get("filename") or ""),
    )


def _review_priority_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "stale_exact_label": 0,
        "threshold_only_or_near_threshold": 0,
        "other_review_cases": 0,
    }
    for row in rows:
        if row.get("stale_exact_label"):
            counts["stale_exact_label"] += 1
        elif row.get("threshold_only_candidate") or row.get("near_threshold_candidate"):
            counts["threshold_only_or_near_threshold"] += 1
        else:
            counts["other_review_cases"] += 1
    return counts


def _review_sheet_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_review_card(row) for row in rows)
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            "<title>KindleMaster Exact Label Campaign</title>",
            "<style>",
            "body{margin:0;background:#f6efe5;color:#1d241c;font-family:Georgia,'Times New Roman',serif;}",
            "main{max-width:1240px;margin:0 auto;padding:32px 20px 56px;}",
            "h1{font-size:34px;margin:0 0 8px;} .meta{color:#5f675d;margin:0 0 24px;}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;}",
            ".card{background:#fffaf2;border:1px solid #dfcdb7;border-radius:18px;padding:14px;box-shadow:0 18px 50px rgba(47,38,24,.10);}",
            ".card img{width:100%;aspect-ratio:1/1;object-fit:contain;background:#efe7d8;border-radius:12px;border:1px solid #ead8c2;}",
            ".tag{display:inline-block;margin:10px 8px 8px 0;padding:4px 9px;border-radius:999px;font-weight:700;font-size:12px;}",
            ".tag.stale{background:#ffe7e1;color:#a8361e}.tag.near{background:#fff1d6;color:#9a4b00}.tag.new{background:#e8f5e9;color:#116a31}",
            "dl{display:grid;grid-template-columns:112px 1fr;gap:6px 10px;margin:10px 0 0;font-size:13px;}dt{font-weight:700;color:#5f675d;}dd{margin:0;word-break:break-word;}",
            "code{font-family:'Cascadia Mono','Courier New',monospace;font-size:12px;color:#20251f;background:#f1eadf;padding:2px 4px;border-radius:5px;}",
            "textarea{width:100%;box-sizing:border-box;min-height:54px;margin-top:10px;border:1px solid #d8c5ae;border-radius:10px;background:#fffdf8;padding:8px;font-family:'Cascadia Mono','Courier New',monospace;font-size:12px;}",
            "</style>",
            "</head>",
            "<body><main>",
            "<h1>Exact-Crop Label Campaign</h1>",
            (
                f"<p class=\"meta\">{_html(summary.get('review_count'))} review crops, "
                f"{_html(summary.get('stale_exact_label_count'))} stale exact-label overlaps, "
                f"{_html(summary.get('new_exact_label_candidate_count'))} new exact-label candidates.</p>"
            ),
            "<section class=\"grid\">",
            cards,
            "</section>",
            "</main></body></html>",
        ]
    )


def _review_card(row: dict[str, Any]) -> str:
    crop = str(row.get("crop_path") or "")
    if row.get("stale_exact_label"):
        tag = "<span class=\"tag stale\">stale exact label</span>"
    elif row.get("threshold_only_candidate") or row.get("near_threshold_candidate"):
        tag = "<span class=\"tag near\">near threshold</span>"
    else:
        tag = "<span class=\"tag new\">new exact label</span>"
    return "\n".join(
        [
            "<article class=\"card\">",
            f"<img src=\"{_attr(crop)}\" alt=\"{_attr(row.get('id'))}\">" if crop else "",
            tag,
            "<dl>",
            f"<dt>ID</dt><dd><code>{_html(row.get('id'))}</code></dd>",
            f"<dt>Page</dt><dd>{_html(row.get('page'))}</dd>",
            f"<dt>Filename</dt><dd><code>{_html(row.get('filename'))}</code></dd>",
            f"<dt>SHA256</dt><dd><code>{_html(row.get('crop_sha256'))}</code></dd>",
            f"<dt>Existing</dt><dd><code>{_html(row.get('existing_exact_fen') or '-')}</code></dd>",
            f"<dt>Candidate</dt><dd><code>{_html(row.get('candidate_fen') or '-')}</code></dd>",
            f"<dt>Confidence</dt><dd>{_html(row.get('candidate_confidence'))}</dd>",
            f"<dt>Warnings</dt><dd>{_html(', '.join(str(item) for item in row.get('candidate_warnings') or []) or '-')}</dd>",
            "</dl>",
            f"<textarea aria-label=\"Verified FEN for {_attr(row.get('id'))}\" placeholder=\"Paste verified FEN here after checking the crop\">{_html(row.get('existing_exact_fen') or row.get('candidate_fen') or '')}</textarea>",
            "</article>",
        ]
    )


def _html(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _attr(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


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


def _ps_path(path: Path) -> str:
    value = str(path)
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a review-only exact-crop label campaign from a chess FEN report.")
    parser.add_argument("report_json")
    parser.add_argument("--source-pdf", required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target-verified-labels", default=str(DEFAULT_TARGET_VERIFIED_LABELS))
    args = parser.parse_args()

    result = export_verified_exact_crop_label_campaign(
        args.report_json,
        source_pdf=args.source_pdf,
        output_dir=args.output_dir,
        target_verified_labels=args.target_verified_labels,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
