from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NEGATIVE_REASON_CHOICES = ("not_chess_diagram", "decorative_grid", "table", "text_only")
DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/negative_sample_intake")
DEFAULT_TARGET_DATASET = Path("reference_inputs/chess_fen/audit_2026_06")


def normalize_negative_sample_profile_id(value: str | Path) -> str:
    stem = Path(str(value)).stem if str(value).lower().endswith(".pdf") else str(value)
    normalized = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return normalized or "negative_samples"


def prepare_chess_negative_sample_intake(
    *,
    profile_id: str,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    source_pdf: str | Path = "",
    source_crops_dir: str | Path = "",
    target_dataset_dir: str | Path = DEFAULT_TARGET_DATASET,
    count: int = 8,
    default_reason: str = "not_chess_diagram",
    extract_from_pdf: bool = False,
) -> dict[str, Any]:
    if default_reason not in NEGATIVE_REASON_CHOICES:
        raise ValueError(f"default_reason must be one of: {', '.join(NEGATIVE_REASON_CHOICES)}")
    profile = normalize_negative_sample_profile_id(profile_id or source_pdf or "negative_samples")
    target = Path(output_dir) / profile
    target.mkdir(parents=True, exist_ok=True)
    template_path = target / "negative_samples_template.jsonl"
    review_path = target / "candidate_negative_samples_review.jsonl"
    summary_path = target / "negative_sample_intake_summary.json"
    readme_path = target / "README.md"
    candidate_crops_dir = target / "candidate_crops"
    extraction_warnings: list[str] = []
    source_crops = _copy_candidate_crops(Path(source_crops_dir), candidate_crops_dir, limit=max(1, int(count))) if str(source_crops_dir).strip() else []
    candidate_source = "source_crops_dir" if source_crops else "blank_template"
    if not source_crops and extract_from_pdf and str(source_pdf).strip():
        source_crops, extraction_warnings = _extract_pdf_region_candidate_crops(
            Path(source_pdf),
            candidate_crops_dir,
            limit=max(1, int(count)),
        )
        if source_crops:
            candidate_source = "source_pdf_regions"
    if source_crops:
        rows = [
            _negative_candidate_row(profile, index, candidate, source_pdf=source_pdf, reason=default_reason)
            for index, candidate in enumerate(source_crops, start=1)
        ]
    else:
        rows = [_negative_template_row(profile, index, source_pdf=source_pdf, reason=default_reason) for index in range(1, max(1, int(count)) + 1)]
    _write_jsonl(template_path, rows)
    _write_jsonl(review_path, rows)
    target_negative_samples = Path(target_dataset_dir) / "labels" / "negative_samples.jsonl"
    payload = {
        "status": "review_required",
        "accepted_for_release_proof": False,
        "profile_id": profile,
        "source_pdf": str(source_pdf),
        "source_crops_dir": str(source_crops_dir),
        "row_count": len(rows),
        "candidate_source": candidate_source,
        "extract_from_pdf": bool(extract_from_pdf),
        "extraction_warnings": extraction_warnings,
        "candidate_counts": {
            "rows": len(rows),
            "with_candidate_crop_path": sum(1 for row in rows if str(row.get("candidate_crop_path") or "").strip()),
            "with_canonical_crop_path": sum(1 for row in rows if str(row.get("crop_path") or "").strip()),
        },
        "template": str(template_path),
        "candidate_review": str(review_path),
        "readme": str(readme_path),
        "target_negative_samples": str(target_negative_samples),
        "target_dataset_dir": str(target_dataset_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": "review_only_no_negative_sample_promotion_without_human_crop_evidence",
        "next_steps": [
            "Review candidate_crop_path images and reject any actual chess diagram.",
            "Copy confirmed non-diagram crops into the target audit dataset crops directory.",
            "Fill crop_path, page, reason, human_verified=true, verified_by, and verified_at for each row.",
            "Run scripts/apply_chess_audit_dataset_intake.py as dry-run, then rerun it with --apply only after reviewing the summary.",
            "Run scripts/validate_chess_audit_dataset.py and scripts/audit_chess_pipeline_breakdown.py.",
        ],
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    readme_path.write_text(_readme(payload), encoding="utf-8")
    payload["summary_path"] = str(summary_path)
    return payload


def _negative_template_row(profile: str, index: int, *, source_pdf: str | Path, reason: str) -> dict[str, Any]:
    return {
        "id": f"{profile}_negative_{index:03d}",
        "source_pdf": str(source_pdf),
        "page": 0,
        "reason": reason,
        "crop_path": "",
        "candidate_crop_path": "",
        "candidate_source_path": "",
        "human_verified": False,
        "verified_by": "",
        "verified_at": "",
        "accepted_for_release_proof": False,
        "notes": "Fill only after human confirms this crop is not a chess diagram/FEN target.",
    }


def _negative_candidate_row(
    profile: str,
    index: int,
    candidate: dict[str, str],
    *,
    source_pdf: str | Path,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": f"{profile}_negative_{index:03d}",
        "source_pdf": str(source_pdf),
        "page": int(candidate["page"]) if str(candidate.get("page") or "").isdigit() else 0,
        "reason": reason,
        "crop_path": "",
        "candidate_crop_path": candidate["candidate_crop_path"],
        "candidate_source_path": candidate["source_path"],
        "candidate_region": candidate.get("candidate_region", ""),
        "human_verified": False,
        "verified_by": "",
        "verified_at": "",
        "accepted_for_release_proof": False,
        "notes": "Candidate crop is review evidence only. If it is truly non-diagram, copy it into the audit dataset crops/ dir and fill crop_path manually.",
    }


def _copy_candidate_crops(source_dir: Path, target_dir: Path, *, limit: int) -> list[dict[str, str]]:
    if not source_dir.exists() or not source_dir.is_dir():
        return []
    image_paths = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    for path in image_paths[:limit]:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.name).strip("_") or f"candidate_{len(copied)+1}{path.suffix.lower()}"
        destination = target_dir / safe_name
        shutil.copy2(path, destination)
        copied.append({"source_path": str(path), "candidate_crop_path": str(destination)})
    return copied


def _extract_pdf_region_candidate_crops(source_pdf: Path, target_dir: Path, *, limit: int) -> tuple[list[dict[str, str]], list[str]]:
    if not source_pdf.exists() or not source_pdf.is_file():
        return [], ["source_pdf_missing"]
    try:
        import fitz  # type: ignore
    except Exception:
        return [], ["pymupdf_unavailable"]

    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    warnings: list[str] = []
    try:
        document = fitz.open(source_pdf)
    except Exception:
        return [], ["source_pdf_open_failed"]
    try:
        for page_index in range(len(document)):
            if len(copied) >= limit:
                break
            page = document[page_index]
            for role, clip in _negative_candidate_regions(page.rect):
                if len(copied) >= limit:
                    break
                destination = target_dir / f"page_{page_index + 1:04d}_{role}.png"
                try:
                    pixmap = page.get_pixmap(clip=clip, matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    pixmap.save(destination)
                except Exception:
                    warnings.append(f"pdf_region_render_failed:{page_index + 1}:{role}")
                    continue
                copied.append(
                    {
                        "source_path": str(source_pdf),
                        "candidate_crop_path": str(destination),
                        "page": str(page_index + 1),
                        "candidate_region": role,
                    }
                )
    finally:
        document.close()
    if not copied and not warnings:
        warnings.append("no_pdf_regions_extracted")
    return copied, warnings


def _negative_candidate_regions(rect: Any) -> list[tuple[str, Any]]:
    width = float(rect.width)
    height = float(rect.height)
    margin_x = width * 0.08
    top_h = height * 0.16
    bottom_h = height * 0.16
    left_w = width * 0.18
    return [
        ("top_text_band", rect.__class__(margin_x, 0, width - margin_x, top_h)),
        ("bottom_text_band", rect.__class__(margin_x, height - bottom_h, width - margin_x, height)),
        ("left_margin_band", rect.__class__(0, height * 0.2, left_w, height * 0.8)),
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _readme(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Chess Negative Sample Intake: {payload['profile_id']}",
            "",
            "Status: review_required until every row has real crop evidence and human verification.",
            "",
            "This package is intake-only. It must not be treated as release proof until completed rows are copied into the audit dataset and validation passes.",
            "",
            "## Files",
            "",
            f"- Template: `{payload['template']}`",
            f"- Candidate review JSONL: `{payload['candidate_review']}`",
            f"- Target audit dataset: `{payload['target_dataset_dir']}`",
            f"- Target negative samples: `{payload['target_negative_samples']}`",
            f"- Source crops dir: `{payload.get('source_crops_dir') or ''}`",
            "",
            "## Candidate Policy",
            "",
            "- `candidate_crop_path` is evidence only and is stored under this intake package.",
            "- PDF-extracted candidate crops are deterministic review candidates, not verified negative samples.",
            "- `crop_path` intentionally remains empty until a human copies a confirmed non-diagram crop into the audit dataset.",
            "- Do not use chess-board overlays or true diagrams as negative samples.",
            "",
            "## Required Manual Fields",
            "",
            "- `crop_path`: relative path inside the audit dataset, usually under `crops/`",
            "- `page`: source page number",
            "- `reason`: one of `not_chess_diagram`, `decorative_grid`, `table`, `text_only`",
            "- `human_verified`: `true` only after visual review",
            "- `verified_by`: reviewer identity",
            "- `verified_at`: `YYYY-MM-DD`",
            "",
            "## Apply Completed Rows",
            "",
            "Run a dry-run first. The importer only accepts `human_verified=true` rows with canonical `crop_path` inside the audit dataset and reviewer evidence.",
            "",
            "```powershell",
            f"python scripts/apply_chess_audit_dataset_intake.py --target-dataset-dir {payload['target_dataset_dir']} --negative-draft {payload['template']} --output-summary reports/chess_audit/latest_intake_apply_dry_run.json",
            "```",
            "",
            "After reviewing the dry-run summary, apply completed rows:",
            "",
            "```powershell",
            f"python scripts/apply_chess_audit_dataset_intake.py --target-dataset-dir {payload['target_dataset_dir']} --negative-draft {payload['template']} --output-summary reports/chess_audit/latest_intake_apply.json --apply",
            "```",
            "",
            "## Validation",
            "",
            "The fresh template should fail validation until those fields are manually filled:",
            "",
            "```powershell",
            f"python scripts/validate_chess_audit_dataset.py {Path(payload['target_dataset_dir']) / 'manifest.json'}",
            "```",
            "",
            "After filling real rows:",
            "",
            "```powershell",
            f"python scripts/validate_chess_audit_dataset.py {Path(payload['target_dataset_dir']) / 'manifest.json'} --output reports/chess_audit/latest_dataset_validation.json",
            f"python scripts/audit_chess_pipeline_breakdown.py {Path(payload['target_dataset_dir']) / 'manifest.json'} --output reports/chess_audit/latest",
            "```",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a review-only intake package for chess audit negative samples.")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--source-pdf", default="")
    parser.add_argument("--source-crops-dir", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target-dataset-dir", default=str(DEFAULT_TARGET_DATASET))
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--default-reason", choices=NEGATIVE_REASON_CHOICES, default="not_chess_diagram")
    parser.add_argument("--extract-from-pdf", action="store_true", help="Create review-only candidate crops from deterministic PDF page regions.")
    args = parser.parse_args()
    payload = prepare_chess_negative_sample_intake(
        profile_id=args.profile_id,
        output_dir=args.output_dir,
        source_pdf=args.source_pdf,
        source_crops_dir=args.source_crops_dir,
        target_dataset_dir=args.target_dataset_dir,
        count=args.count,
        default_reason=args.default_reason,
        extract_from_pdf=args.extract_from_pdf,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
