from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import exact_crop_label_release_safety, has_square_diff_ack, normalize_crop_sha256


DEFAULT_OUTPUT_LABELS = Path("reference_inputs/chess_fen/labels/ai_consensus_verified_crop_labels.jsonl")


def import_ai_consensus_fen_verification(
    verified_jsonl_path: str | Path,
    *,
    output_jsonl: str | Path = DEFAULT_OUTPUT_LABELS,
    apply_changes: bool = False,
) -> dict[str, Any]:
    source_path = Path(verified_jsonl_path)
    rows = _read_jsonl(source_path)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        result = _validate_verified_row(row)
        if result["accepted"]:
            accepted.append(_label_row(row, index, result))
        else:
            rejected.append(
                {
                    "index": index,
                    "diagram_id": _diagram_id(row, index),
                    "issues": result["issues"],
                }
            )

    output_path = Path(output_jsonl)
    if apply_changes and accepted:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in accepted), encoding="utf-8")

    return {
        "schema": "kindlemaster.chess_fen.ai_consensus_verification_import.v1",
        "status": "applied" if apply_changes else "dry_run",
        "source_path": str(source_path),
        "output_jsonl": str(output_path),
        "input_count": len(rows),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted": accepted,
        "rejected": rejected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import human-verified AI consensus FEN review rows into exact-label format.")
    parser.add_argument("verified_jsonl")
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT_LABELS))
    parser.add_argument("--report-json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = import_ai_consensus_fen_verification(
            args.verified_jsonl,
            output_jsonl=args.output_jsonl,
            apply_changes=args.apply,
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": exc.__class__.__name__, "message": str(exc)}, indent=2), file=sys.stderr)
        return 2

    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("status", "input_count", "accepted_count", "rejected_count")}, ensure_ascii=False, indent=2))
    return 0 if payload["rejected_count"] == 0 else 1


def _validate_verified_row(row: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    label = _candidate_label(row)
    safety = exact_crop_label_release_safety(label)
    issues.extend(safety["issues"])

    if not has_square_diff_ack(dict(label)):
        issues.append({"code": "square_diff_ack_missing", "message": "AI consensus verification requires square_diff_ack=true."})

    return {
        "accepted": not issues,
        "issues": issues,
        "safety": safety,
    }


def _candidate_label(row: Mapping[str, Any]) -> dict[str, Any]:
    nested = row.get("output_label_candidate") if isinstance(row.get("output_label_candidate"), Mapping) else {}
    label = {**dict(nested), **dict(row)}
    if not label.get("fen"):
        label["fen"] = row.get("ai_fen")
    if not label.get("sha256"):
        label["sha256"] = label.get("crop_sha256") or label.get("source_crop_hash")
    return label


def _label_row(row: Mapping[str, Any], index: int, result: Mapping[str, Any]) -> dict[str, Any]:
    label = _candidate_label(row)
    diagram_id = _diagram_id(row, index)
    crop_sha = normalize_crop_sha256(label.get("crop_sha256") or label.get("sha256") or label.get("source_crop_hash"))
    crop_path = str(label.get("crop_path") or row.get("crop_path") or "")
    filename = Path(crop_path).name if crop_path and crop_path != "MISSING_ARTIFACT" else ""
    return {
        "id": f"ai_consensus_verified_{_safe_id(diagram_id)}",
        "diagram_id": diagram_id,
        "source": "ai_consensus_human_verified",
        "page": _int_or_zero(row.get("page")),
        "filename": filename,
        "crop_path": crop_path,
        "sha256": crop_sha,
        "crop_sha256": crop_sha,
        "fen": str(label.get("fen") or "").strip(),
        "human_verified": True,
        "verification_source": "human_visual",
        "square_diff_ack": True,
        "label_status": "verified",
        "notes": "Imported from AI consensus promotion queue after human visual verification.",
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {line_number} must be an object")
        rows.append(value)
    return rows


def _diagram_id(row: Mapping[str, Any], index: int) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return f"row:{index}"


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value or "")).strip("_") or "row"


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
