from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PGN_INPUT_TYPE_CHOICES = ("full_game_text", "exercise_solution", "diagram_only", "insufficient_text")
DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/pgn_ground_truth_intake")
DEFAULT_TARGET_DATASET = Path("reference_inputs/chess_fen/audit_2026_06")


def normalize_pgn_ground_truth_profile_id(value: str | Path) -> str:
    stem = Path(str(value)).stem if str(value).lower().endswith(".pdf") else str(value)
    normalized = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return normalized or "pgn_ground_truth"


def prepare_chess_pgn_ground_truth_intake(
    *,
    profile_id: str,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    source_pdf: str | Path = "",
    source_report: str | Path = "",
    target_dataset_dir: str | Path = DEFAULT_TARGET_DATASET,
    count: int = 8,
    default_input_type: str = "exercise_solution",
) -> dict[str, Any]:
    if default_input_type not in PGN_INPUT_TYPE_CHOICES:
        raise ValueError(f"default_input_type must be one of: {', '.join(PGN_INPUT_TYPE_CHOICES)}")
    profile = normalize_pgn_ground_truth_profile_id(profile_id or source_pdf or "pgn_ground_truth")
    target = Path(output_dir) / profile
    target.mkdir(parents=True, exist_ok=True)
    template_path = target / "pgn_ground_truth_template.jsonl"
    review_path = target / "candidate_pgn_ground_truth_review.jsonl"
    summary_path = target / "pgn_ground_truth_intake_summary.json"
    readme_path = target / "README.md"
    source_records = _extract_pgn_records_from_report(Path(source_report), limit=max(1, int(count))) if str(source_report).strip() else []
    if source_records:
        rows = [
            _pgn_candidate_row_from_report(profile, index, record, source_pdf=source_pdf, source_report=source_report)
            for index, record in enumerate(source_records, start=1)
        ]
    else:
        rows = [_pgn_template_row(profile, index, source_pdf=source_pdf, input_type=default_input_type) for index in range(1, max(1, int(count)) + 1)]
    _write_jsonl(template_path, rows)
    _write_jsonl(review_path, rows)
    target_pgn_ground_truth = Path(target_dataset_dir) / "labels" / "pgn_ground_truth.jsonl"
    payload = {
        "status": "review_required",
        "accepted_for_release_proof": False,
        "profile_id": profile,
        "source_pdf": str(source_pdf),
        "source_report": str(source_report),
        "row_count": len(rows),
        "candidate_source": "runtime_report" if source_records else "blank_template",
        "candidate_counts": _candidate_counts(rows),
        "template": str(template_path),
        "candidate_review": str(review_path),
        "readme": str(readme_path),
        "target_pgn_ground_truth": str(target_pgn_ground_truth),
        "target_dataset_dir": str(target_dataset_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": "review_only_no_pgn_success_without_human_feasibility_and_movetext",
        "next_steps": [
            "Classify each row as full_game_text, exercise_solution, diagram_only, or insufficient_text.",
            "Treat candidate_movetext/candidate_pgn/raw_text_excerpt as review evidence only.",
            "For feasible rows, fill expected_movetext or expected_pgn from human-reviewed source text.",
            "For diagram_only rows, set pgn_feasible=false and do not add movetext.",
            "Run scripts/apply_chess_audit_dataset_intake.py as dry-run, then rerun it with --apply only after reviewing the summary.",
            "Run scripts/validate_chess_audit_dataset.py and scripts/audit_chess_pipeline_breakdown.py.",
        ],
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    readme_path.write_text(_readme(payload), encoding="utf-8")
    payload["summary_path"] = str(summary_path)
    return payload


def _pgn_template_row(profile: str, index: int, *, source_pdf: str | Path, input_type: str) -> dict[str, Any]:
    feasible = input_type not in {"diagram_only", "insufficient_text"}
    return {
        "id": f"{profile}_pgn_{index:03d}",
        "source_pdf": str(source_pdf),
        "page": 0,
        "input_type": input_type,
        "pgn_feasible": feasible,
        "pgn_feasibility_reason": "has_movetext_pending_review" if feasible else f"{input_type}_no_movetext",
        "expected_movetext": "",
        "expected_pgn": "",
        "candidate_movetext": "",
        "candidate_pgn": "",
        "raw_text_excerpt": "",
        "source_record_id": "",
        "source_status": "",
        "source_warnings": [],
        "linked_fen_id": "",
        "human_verified": False,
        "verified_by": "",
        "verified_at": "",
        "accepted_for_release_proof": False,
        "notes": "Fill only after human classifies PGN feasibility and verifies source movetext.",
    }


def _pgn_candidate_row_from_report(
    profile: str,
    index: int,
    record: dict[str, Any],
    *,
    source_pdf: str | Path,
    source_report: str | Path,
) -> dict[str, Any]:
    raw_text = str(record.get("raw_text") or "")
    movetext = str(record.get("movetext") or "")
    pgn = str(record.get("pgn") or "")
    input_type, feasible, reason = _suggest_pgn_input_type(record, raw_text=raw_text, movetext=movetext, pgn=pgn)
    source_pages = record.get("source_pages") if isinstance(record.get("source_pages"), list) else []
    page = int(source_pages[0]) if source_pages and str(source_pages[0]).isdigit() else int(record.get("page") or 0)
    return {
        "id": f"{profile}_pgn_{index:03d}",
        "source_pdf": str(source_pdf),
        "page": page,
        "input_type": input_type,
        "pgn_feasible": feasible,
        "pgn_feasibility_reason": reason,
        "expected_movetext": "",
        "expected_pgn": "",
        "candidate_movetext": movetext,
        "candidate_pgn": pgn,
        "raw_text_excerpt": _excerpt(raw_text or movetext or pgn),
        "source_record_id": str(record.get("id") or ""),
        "source_report": str(source_report),
        "source_status": str(record.get("status") or ""),
        "source_warnings": [str(item) for item in list(record.get("warnings") or []) if str(item).strip()],
        "linked_fen_id": "",
        "human_verified": False,
        "verified_by": "",
        "verified_at": "",
        "accepted_for_release_proof": False,
        "notes": "Review candidate_* fields against the source page; copy only human-verified text into expected_movetext/expected_pgn.",
    }


def _extract_pgn_records_from_report(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for section_path in (
        ("quality_report", "chess_pgn", "records"),
        ("document", "quality_report", "chess_pgn", "records"),
        ("document", "metadata", "source_metadata", "chess_pgn", "records"),
    ):
        current: Any = payload
        for key in section_path:
            current = current.get(key) if isinstance(current, dict) else None
        if isinstance(current, list):
            candidates.extend(dict(item) for item in current if isinstance(item, dict))
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for record in candidates:
        record_id = str(record.get("id") or "")
        fingerprint = record_id or _excerpt(str(record.get("raw_text") or record.get("movetext") or record.get("pgn") or ""), limit=120)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append(record)
        if len(rows) >= limit:
            break
    return rows


def _suggest_pgn_input_type(record: dict[str, Any], *, raw_text: str, movetext: str, pgn: str) -> tuple[str, bool, str]:
    sample = "\n".join(value for value in (raw_text, movetext, pgn) if value).strip()
    if not sample:
        return "insufficient_text", False, "insufficient_text_no_movetext"
    has_movetext = bool(re.search(r"\b\d{1,3}\.(?:\.\.)?\s*\S+", sample))
    if not has_movetext:
        if re.search(r"\bDiagram\b", sample, flags=re.IGNORECASE):
            return "diagram_only", False, "diagram_only_no_movetext"
        return "insufficient_text", False, "insufficient_text_no_movetext"
    if re.search(r"\bDiagram\b", sample, flags=re.IGNORECASE):
        return "exercise_solution", True, "candidate_has_diagram_movetext_pending_human_review"
    if str(record.get("status") or "").strip() == "accepted":
        return "full_game_text", True, "candidate_full_game_text_pending_human_review"
    return "exercise_solution", True, "candidate_movetext_pending_human_review"


def _candidate_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "rows": len(rows),
        "feasible_suggested": sum(1 for row in rows if row.get("pgn_feasible") is True),
        "infeasible_suggested": sum(1 for row in rows if row.get("pgn_feasible") is False),
        "with_candidate_movetext": sum(1 for row in rows if str(row.get("candidate_movetext") or "").strip()),
        "with_candidate_pgn": sum(1 for row in rows if str(row.get("candidate_pgn") or "").strip()),
    }
    for row in rows:
        key = f"input_type_{row.get('input_type') or 'unknown'}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _excerpt(value: str, *, limit: int = 600) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return normalized[:limit]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _readme(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Chess PGN Ground Truth Intake: {payload['profile_id']}",
            "",
            "Status: review_required until every row has human feasibility classification and required movetext.",
            "",
            "`diagram_only` must never count as a PGN failure. It should be marked `pgn_feasible=false` and excluded from export-rate denominators.",
            "",
            "## Files",
            "",
            f"- Template: `{payload['template']}`",
            f"- Candidate review JSONL: `{payload['candidate_review']}`",
            f"- Target audit dataset: `{payload['target_dataset_dir']}`",
            f"- Target PGN ground truth: `{payload['target_pgn_ground_truth']}`",
            f"- Source report: `{payload.get('source_report') or ''}`",
            "",
            "## Candidate Policy",
            "",
            "- `candidate_movetext`, `candidate_pgn`, and `raw_text_excerpt` are evidence only.",
            "- Do not copy candidates into release proof by setting only `human_verified=true`.",
            "- A row becomes usable only after a human writes verified text into `expected_movetext` or `expected_pgn`.",
            "",
            "## Required Manual Fields",
            "",
            "- `input_type`: `full_game_text`, `exercise_solution`, `diagram_only`, or `insufficient_text`",
            "- `pgn_feasible`: `true` only for real movetext, solution line, or full game text",
            "- `pgn_feasibility_reason`: reason for infeasible rows, especially `diagram_only_no_movetext`",
            "- `expected_movetext` or `expected_pgn`: required for feasible rows",
            "- `human_verified`: `true` only after visual/source review",
            "- `verified_by`: reviewer identity",
            "- `verified_at`: `YYYY-MM-DD`",
            "",
            "## Apply Completed Rows",
            "",
            "Run a dry-run first. The importer only accepts `human_verified=true` rows with explicit expected text and reviewer evidence.",
            "",
            "```powershell",
            f"python scripts/apply_chess_audit_dataset_intake.py --target-dataset-dir {payload['target_dataset_dir']} --pgn-draft {payload['template']} --output-summary reports/chess_audit/latest_intake_apply_dry_run.json",
            "```",
            "",
            "After reviewing the dry-run summary, apply completed rows:",
            "",
            "```powershell",
            f"python scripts/apply_chess_audit_dataset_intake.py --target-dataset-dir {payload['target_dataset_dir']} --pgn-draft {payload['template']} --output-summary reports/chess_audit/latest_intake_apply.json --apply",
            "```",
            "",
            "## Validation",
            "",
            "The fresh template should fail validation until human verification and required text are filled:",
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
    parser = argparse.ArgumentParser(description="Prepare a review-only intake package for chess PGN ground truth rows.")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--source-pdf", default="")
    parser.add_argument("--source-report", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target-dataset-dir", default=str(DEFAULT_TARGET_DATASET))
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--default-input-type", choices=PGN_INPUT_TYPE_CHOICES, default="exercise_solution")
    args = parser.parse_args()
    payload = prepare_chess_pgn_ground_truth_intake(
        profile_id=args.profile_id,
        output_dir=args.output_dir,
        source_pdf=args.source_pdf,
        source_report=args.source_report,
        target_dataset_dir=args.target_dataset_dir,
        count=args.count,
        default_input_type=args.default_input_type,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
