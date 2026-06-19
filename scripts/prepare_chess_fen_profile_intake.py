from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.build_chess_fen_label_aids import build_chess_fen_label_aids
from scripts.extract_chess_diagram_crops import extract_chess_diagram_crops


def normalize_chess_fen_profile_id(value: str | Path) -> str:
    stem = Path(str(value)).stem if str(value).lower().endswith(".pdf") else str(value)
    normalized = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return normalized or "scanned_chess_profile"


def prepare_chess_fen_profile_intake(
    pdf_path: str | Path,
    *,
    profile_id: str = "",
    output_dir: str | Path = "reports/chess_fen/intake",
    pages: int = 48,
    dpi: int = 72,
    max_candidates_per_page: int = 4,
    min_grid_confidence: float = 0.50,
    min_seed_labels: int = 20,
    enable_sliding_probe: bool = False,
) -> dict[str, Any]:
    profile = normalize_chess_fen_profile_id(profile_id or pdf_path)
    profile_dir = Path(output_dir) / profile
    crops_dir = profile_dir / "crops"
    crop_manifest = extract_chess_diagram_crops(
        pdf_path,
        output_dir=crops_dir,
        pages=pages,
        dpi=dpi,
        max_candidates_per_page=max_candidates_per_page,
        min_grid_confidence=min_grid_confidence,
        enable_sliding_probe=enable_sliding_probe,
    )
    return prepare_chess_fen_profile_intake_from_crop_manifest(
        crop_manifest,
        profile_id=profile,
        output_dir=profile_dir,
        min_seed_labels=min_seed_labels,
    )


def prepare_chess_fen_profile_intake_from_crop_manifest(
    crop_manifest: Mapping[str, Any],
    *,
    profile_id: str,
    output_dir: str | Path,
    min_seed_labels: int = 20,
) -> dict[str, Any]:
    profile = normalize_chess_fen_profile_id(profile_id)
    profile_dir = Path(output_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    crops = [dict(item) for item in crop_manifest.get("crops", []) if isinstance(item, Mapping)]
    candidate_labels_path = profile_dir / "candidate_labels_review.jsonl"
    label_aids_dir = profile_dir / "label_aids"
    manifest_draft_path = profile_dir / "manifest_case_draft.json"
    readme_path = profile_dir / "README.md"
    verified_seed_target = Path("reference_inputs/chess_fen/labels") / f"{profile}_seed_positions.jsonl"
    template_target = Path("reference_inputs/chess_fen/templates") / profile

    candidate_rows = [_candidate_label_row(profile, item, index) for index, item in enumerate(crops, start=1)]
    _write_jsonl(candidate_labels_path, candidate_rows)
    label_aids_summary = build_chess_fen_label_aids(
        candidate_labels_path,
        output_dir=label_aids_dir,
        max_items=max(1, int(min_seed_labels)),
    )

    manifest_case_draft = {
        "id": profile,
        "document_class": "diagram_training_book",
        "input_type": "pdf",
        "language": "pl",
        "quick_smoke": False,
        "release_strict": False,
        "source": str(crop_manifest.get("source_pdf") or ""),
        "target": str(crop_manifest.get("source_pdf") or ""),
        "notes": "Draft only. Do not merge until candidate labels are manually verified and pass the FEN corpus gate.",
        "chess_fen_seed_labels": str(verified_seed_target),
        "chess_fen_template_profile": profile,
        "chess_fen_seed_exact_accuracy_min": 0.90,
    }
    manifest_draft_path.write_text(json.dumps(manifest_case_draft, ensure_ascii=False, indent=2), encoding="utf-8")
    readme_path.write_text(
        _readme(
            profile,
            candidate_labels_path,
            Path(str(label_aids_summary.get("manual_label_template") or label_aids_dir / "manual_label_template.jsonl")),
            Path(str(label_aids_summary.get("contact_sheet") or label_aids_dir / "contact_sheet.png")),
            verified_seed_target,
            template_target,
            manifest_draft_path,
        ),
        encoding="utf-8",
    )

    crop_count = len(candidate_rows)
    status = "review_required" if crop_count >= max(1, int(min_seed_labels)) else ("insufficient_crops" if crop_count else "no_crops")
    payload = {
        "status": status,
        "accepted_for_corpus": False,
        "profile_id": profile,
        "source_pdf": str(crop_manifest.get("source_pdf") or ""),
        "crop_manifest_path": str(crop_manifest.get("manifest_path") or ""),
        "candidate_label_count": crop_count,
        "required_verified_seed_count": max(1, int(min_seed_labels)),
        "candidate_labels_review": str(candidate_labels_path),
        "label_aids_dir": str(label_aids_dir),
        "contact_sheet": str(label_aids_summary.get("contact_sheet") or label_aids_dir / "contact_sheet.png"),
        "manual_label_template": str(label_aids_summary.get("manual_label_template") or label_aids_dir / "manual_label_template.jsonl"),
        "label_aids_summary": str(label_aids_dir / "label_aids_summary.json"),
        "verified_seed_labels_target": str(verified_seed_target),
        "template_profile_target": str(template_target),
        "manifest_case_draft": str(manifest_draft_path),
        "readme": str(readme_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "next_steps": [
            "Manually fill FEN, verified_by, verified_at, and notes in a copy of candidate_labels_review.",
            "Move only verified labels to verified_seed_labels_target.",
            "Build templates with scripts/build_chess_piece_templates.py.",
            "Run scripts/evaluate_chess_fen_corpus.py --min-profile-count 2 before merging the manifest case.",
        ],
        "warnings": [] if crop_count else ["no_candidate_crops_extracted"],
    }
    summary_path = profile_dir / "intake_summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["summary_path"] = str(summary_path)
    return payload


def _candidate_label_row(profile: str, crop: Mapping[str, Any], index: int) -> dict[str, Any]:
    page = int(crop.get("page") or 0)
    candidate = crop.get("candidate") or crop.get("diagram_index") or index
    return {
        "id": f"{profile}_p{page:03d}_c{int(candidate):02d}" if str(candidate).isdigit() else f"{profile}_p{page:03d}_c{index:02d}",
        "source_pdf": str(crop.get("source_pdf") or ""),
        "page": page,
        "diagram_index": candidate,
        "crop_path": str(crop.get("crop_path") or ""),
        "fen": "",
        "label_status": "needs_manual_fen",
        "verified_by": "",
        "verified_at": "",
        "notes": "Fill FEN manually. This review row is not accepted for corpus proof.",
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _readme(
    profile: str,
    candidate_labels: Path,
    manual_label_template: Path,
    contact_sheet: Path,
    verified_seed_target: Path,
    template_target: Path,
    manifest_draft: Path,
) -> str:
    return "\n".join(
        [
            f"# Chess FEN Intake: {profile}",
            "",
            "Status: review_required or insufficient_crops until human verification is complete.",
            "",
            "This package is review/intake only. Do not add it to `reference_inputs/manifest.json` until all gates pass.",
            "Do not commit the source PDF unless it is sanitized and intentionally curated as a reference input.",
            "",
            "## Project Paths",
            "",
            f"- Candidate review rows: `{candidate_labels}`",
            f"- Contact sheet: `{contact_sheet}`",
            f"- Manual label template: `{manual_label_template}`",
            f"- Manifest case draft: `{manifest_draft}`",
            f"- Future verified labels target: `{verified_seed_target}`",
            f"- Future template profile target: `{template_target}`",
            "",
            "## Manual Verification",
            "",
            "1. Open the contact sheet and grid aids under `label_aids/`.",
            "2. Fill `fen`, `verified_by`, `verified_at`, and `notes` in `label_aids/manual_label_template.jsonl`.",
            "3. Promote only human-verified rows into the future verified labels target.",
            "4. Build templates and run readiness/holdout/audit gates before editing the manifest.",
            "",
            "## Validation Commands",
            "",
            "The fresh manual template should fail validation until FEN and verification fields are manually filled:",
            "",
            "```powershell",
            f"python scripts/validate_chess_fen_labels.py {manual_label_template}",
            "```",
            "",
            "After manual verification:",
            "",
            "```powershell",
            f"python scripts/promote_chess_fen_label_draft.py {manual_label_template} --output {verified_seed_target}",
            f"python scripts/validate_chess_fen_labels.py {verified_seed_target}",
            f"python scripts/build_chess_piece_templates.py {verified_seed_target} --output-dir {template_target}",
            f"python scripts/check_chess_fen_profile_ready.py {verified_seed_target}",
            f"python scripts/evaluate_chess_fen_profile_holdout.py {verified_seed_target}",
            "python scripts/export_chess_fen_accepted_audit.py --help",
            "```",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a review-only intake package for a new scanned chess FEN profile.")
    parser.add_argument("pdf")
    parser.add_argument("--profile-id", default="")
    parser.add_argument("--output-dir", default="reports/chess_fen/intake")
    parser.add_argument("--pages", type=int, default=48)
    parser.add_argument("--dpi", type=int, default=72)
    parser.add_argument("--max-candidates-per-page", type=int, default=4)
    parser.add_argument("--min-grid-confidence", type=float, default=0.50)
    parser.add_argument("--min-seed-labels", type=int, default=20)
    parser.add_argument("--sliding-probe", action="store_true")
    args = parser.parse_args()

    payload = prepare_chess_fen_profile_intake(
        args.pdf,
        profile_id=args.profile_id,
        output_dir=args.output_dir,
        pages=args.pages,
        dpi=args.dpi,
        max_candidates_per_page=args.max_candidates_per_page,
        min_grid_confidence=args.min_grid_confidence,
        min_seed_labels=args.min_seed_labels,
        enable_sliding_probe=args.sliding_probe,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] in {"review_required", "insufficient_crops"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
