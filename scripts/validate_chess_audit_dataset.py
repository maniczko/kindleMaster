from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "kindlemaster.chess_audit_dataset.v1"
FEN_SIDE_SOURCES = {"caption", "marker", "unknown"}
PGN_INPUT_TYPES = {"full_game_text", "exercise_solution", "diagram_only", "insufficient_text"}
NEGATIVE_REASONS = {"not_chess_diagram", "decorative_grid", "table", "text_only"}
PIECES = set("pnbrqkPNBRQK")
MIN_RELEASE_FEN_ROWS = 20
MIN_RELEASE_PGN_ROWS = 1
MIN_RELEASE_NEGATIVE_ROWS = 1


def validate_chess_audit_dataset(manifest_path: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    dataset_dir = manifest_file.parent
    issues: list[dict[str, Any]] = []
    if not manifest_file.exists():
        result = _result("failed", manifest_file, {}, issues=[_issue("manifest", 0, "", "manifest_missing")])
        _write_output(result, output_path)
        return result

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        result = _result("failed", manifest_file, {}, issues=[_issue("manifest", exc.lineno, "", "manifest_invalid_json")])
        _write_output(result, output_path)
        return result
    if not isinstance(manifest, dict):
        result = _result("failed", manifest_file, {}, issues=[_issue("manifest", 0, "", "manifest_must_be_object")])
        _write_output(result, output_path)
        return result

    if manifest.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("manifest", 0, "", "schema_version_invalid", expected=SCHEMA_VERSION))

    fen_rows = _load_jsonl(dataset_dir / str(manifest.get("fen_ground_truth") or ""), "fen", issues)
    pgn_rows = _load_jsonl(dataset_dir / str(manifest.get("pgn_ground_truth") or ""), "pgn", issues)
    negative_rows = _load_jsonl(dataset_dir / str(manifest.get("negative_samples") or ""), "negative", issues)

    fen_ids: set[str] = set()
    for line_number, row in fen_rows:
        fen_ids.add(str(row.get("id") or ""))
        issues.extend(_validate_fen_row(row, line_number=line_number, dataset_dir=dataset_dir))
    for line_number, row in pgn_rows:
        issues.extend(_validate_pgn_row(row, line_number=line_number, fen_ids=fen_ids))
    for line_number, row in negative_rows:
        issues.extend(_validate_negative_row(row, line_number=line_number, dataset_dir=dataset_dir))

    counts = {
        "fen_rows": len(fen_rows),
        "pgn_rows": len(pgn_rows),
        "negative_rows": len(negative_rows),
    }
    result = _result("passed" if not issues else "failed", manifest_file, counts, issues=issues)
    _write_output(result, output_path)
    return result


def _load_jsonl(path: Path, section: str, issues: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        issues.append(_issue(section, 0, "", "file_missing", path=str(path)))
        return []
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            issues.append(_issue(section, line_number, "", "invalid_json"))
            continue
        if not isinstance(row, dict):
            issues.append(_issue(section, line_number, "", "row_must_be_object"))
            continue
        rows.append((line_number, row))
    return rows


def _validate_fen_row(row: dict[str, Any], *, line_number: int, dataset_dir: Path) -> list[dict[str, Any]]:
    record_id = str(row.get("id") or "")
    issues: list[dict[str, Any]] = []
    _require_fields(
        row,
        section="fen",
        line_number=line_number,
        record_id=record_id,
        fields=(
            "id",
            "source_pdf",
            "page",
            "crop_path",
            "expected_placement",
            "side_to_move_source",
            "crop_expected_bbox",
            "crop_has_caption",
            "crop_has_coordinates",
            "human_verified",
            "verified_by",
            "verified_at",
        ),
        issues=issues,
    )
    placement = str(row.get("expected_placement") or "")
    if placement and not _placement_is_valid(placement):
        issues.append(_issue("fen", line_number, record_id, "expected_placement_invalid"))
    if row.get("side_to_move_source") not in FEN_SIDE_SOURCES:
        issues.append(_issue("fen", line_number, record_id, "side_to_move_source_invalid"))
    if row.get("human_verified") is not True:
        issues.append(_issue("fen", line_number, record_id, "human_verified_missing"))
    if not str(row.get("verified_by") or "").strip():
        issues.append(_issue("fen", line_number, record_id, "verified_by_missing"))
    if not _date_is_valid(str(row.get("verified_at") or "")):
        issues.append(_issue("fen", line_number, record_id, "verified_at_invalid"))
    if not _bbox_is_valid(row.get("crop_expected_bbox")):
        issues.append(_issue("fen", line_number, record_id, "crop_expected_bbox_invalid"))
    for flag in ("crop_has_caption", "crop_has_coordinates"):
        if not isinstance(row.get(flag), bool):
            issues.append(_issue("fen", line_number, record_id, f"{flag}_must_be_boolean"))
    _validate_relative_crop_path(row, section="fen", line_number=line_number, record_id=record_id, dataset_dir=dataset_dir, issues=issues)
    return issues


def _validate_pgn_row(row: dict[str, Any], *, line_number: int, fen_ids: set[str]) -> list[dict[str, Any]]:
    record_id = str(row.get("id") or "")
    issues: list[dict[str, Any]] = []
    _require_fields(
        row,
        section="pgn",
        line_number=line_number,
        record_id=record_id,
        fields=("id", "source_pdf", "page", "input_type", "pgn_feasible", "pgn_feasibility_reason", "human_verified", "verified_by", "verified_at"),
        issues=issues,
    )
    input_type = str(row.get("input_type") or "")
    if input_type not in PGN_INPUT_TYPES:
        issues.append(_issue("pgn", line_number, record_id, "input_type_invalid"))
    feasible = row.get("pgn_feasible")
    if not isinstance(feasible, bool):
        issues.append(_issue("pgn", line_number, record_id, "pgn_feasible_must_be_boolean"))
    if input_type == "diagram_only" and feasible is not False:
        issues.append(_issue("pgn", line_number, record_id, "diagram_only_must_be_infeasible"))
    if feasible is True and not str(row.get("expected_movetext") or row.get("expected_pgn") or "").strip():
        issues.append(_issue("pgn", line_number, record_id, "feasible_pgn_expected_text_missing"))
    if row.get("human_verified") is not True:
        issues.append(_issue("pgn", line_number, record_id, "human_verified_missing"))
    if not str(row.get("verified_by") or "").strip():
        issues.append(_issue("pgn", line_number, record_id, "verified_by_missing"))
    if not _date_is_valid(str(row.get("verified_at") or "")):
        issues.append(_issue("pgn", line_number, record_id, "verified_at_invalid"))
    linked_fen_id = str(row.get("linked_fen_id") or "").strip()
    if linked_fen_id and linked_fen_id not in fen_ids:
        issues.append(_issue("pgn", line_number, record_id, "linked_fen_id_missing", linked_fen_id=linked_fen_id))
    return issues


def _validate_negative_row(row: dict[str, Any], *, line_number: int, dataset_dir: Path) -> list[dict[str, Any]]:
    record_id = str(row.get("id") or "")
    issues: list[dict[str, Any]] = []
    _require_fields(
        row,
        section="negative",
        line_number=line_number,
        record_id=record_id,
        fields=("id", "source_pdf", "page", "reason", "crop_path", "human_verified", "verified_by", "verified_at"),
        issues=issues,
    )
    if row.get("reason") not in NEGATIVE_REASONS:
        issues.append(_issue("negative", line_number, record_id, "negative_reason_invalid"))
    if row.get("human_verified") is not True:
        issues.append(_issue("negative", line_number, record_id, "human_verified_missing"))
    if not str(row.get("verified_by") or "").strip():
        issues.append(_issue("negative", line_number, record_id, "verified_by_missing"))
    if not _date_is_valid(str(row.get("verified_at") or "")):
        issues.append(_issue("negative", line_number, record_id, "verified_at_invalid"))
    _validate_relative_crop_path(row, section="negative", line_number=line_number, record_id=record_id, dataset_dir=dataset_dir, issues=issues)
    return issues


def _validate_relative_crop_path(
    row: dict[str, Any],
    *,
    section: str,
    line_number: int,
    record_id: str,
    dataset_dir: Path,
    issues: list[dict[str, Any]],
) -> None:
    crop_path = str(row.get("crop_path") or "")
    if not crop_path:
        return
    resolved = (dataset_dir / crop_path).resolve()
    try:
        resolved.relative_to(dataset_dir.resolve())
    except ValueError:
        issues.append(_issue(section, line_number, record_id, "crop_path_outside_dataset", crop_path=crop_path))
    if not resolved.exists():
        issues.append(_issue(section, line_number, record_id, "crop_path_missing_on_disk", crop_path=crop_path))


def _require_fields(
    row: dict[str, Any],
    *,
    section: str,
    line_number: int,
    record_id: str,
    fields: tuple[str, ...],
    issues: list[dict[str, Any]],
) -> None:
    for field in fields:
        if field not in row or row.get(field) in (None, ""):
            issues.append(_issue(section, line_number, record_id, f"{field}_missing"))


def _placement_is_valid(placement: str) -> bool:
    ranks = placement.split("/")
    if len(ranks) != 8:
        return False
    for rank in ranks:
        total = 0
        previous_digit = False
        for char in rank:
            if char.isdigit():
                if char == "0" or previous_digit:
                    return False
                total += int(char)
                previous_digit = True
            elif char in PIECES:
                total += 1
                previous_digit = False
            else:
                return False
        if total != 8:
            return False
    return True


def _bbox_is_valid(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(isinstance(item, (int, float)) for item in value):
        return False
    x0, y0, x1, y1 = [float(item) for item in value]
    return x1 > x0 and y1 > y0


def _date_is_valid(value: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()))


def _issue(section: str, line_number: int, record_id: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"section": section, "line": line_number, "id": record_id, "code": code, **extra}


def _result(status: str, manifest_path: Path, counts: dict[str, int], *, issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "kindlemaster.chess_audit_dataset_validation.v1",
        "status": status,
        "manifest_path": str(manifest_path),
        "counts": counts,
        "issue_count": len(issues),
        "issues": issues,
        "release_readiness": _release_readiness(status=status, counts=counts, issue_count=len(issues)),
    }


def _release_readiness(*, status: str, counts: dict[str, int], issue_count: int) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    if status != "passed" or issue_count:
        blockers.append(
            {
                "code": "dataset_validation_failed",
                "message": "Audit dataset schema validation must pass before release proof can use it.",
            }
        )
    _append_min_count_blocker(
        blockers,
        code="fen_ground_truth_insufficient",
        current=int(counts.get("fen_rows") or 0),
        required=MIN_RELEASE_FEN_ROWS,
        message="Release proof requires enough human-verified FEN diagnostic cases.",
    )
    _append_min_count_blocker(
        blockers,
        code="pgn_ground_truth_missing",
        current=int(counts.get("pgn_rows") or 0),
        required=MIN_RELEASE_PGN_ROWS,
        message="Release proof requires at least one human-reviewed PGN feasibility case.",
    )
    _append_min_count_blocker(
        blockers,
        code="negative_samples_missing",
        current=int(counts.get("negative_rows") or 0),
        required=MIN_RELEASE_NEGATIVE_ROWS,
        message="Release proof requires at least one human-verified negative sample.",
    )
    return {
        "accepted_for_release_proof": not blockers,
        "status": "ready" if not blockers else "review_required",
        "min_fen_rows": MIN_RELEASE_FEN_ROWS,
        "min_pgn_rows": MIN_RELEASE_PGN_ROWS,
        "min_negative_rows": MIN_RELEASE_NEGATIVE_ROWS,
        "blockers": blockers,
    }


def _append_min_count_blocker(
    blockers: list[dict[str, Any]],
    *,
    code: str,
    current: int,
    required: int,
    message: str,
) -> None:
    if current >= required:
        return
    blockers.append(
        {
            "code": code,
            "message": message,
            "current": current,
            "required": required,
            "missing": required - current,
        }
    )


def _write_output(result: dict[str, Any], output_path: str | Path | None) -> None:
    if not output_path:
        return
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the diagnostic chess FEN/PGN audit dataset.")
    parser.add_argument("manifest")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = validate_chess_audit_dataset(args.manifest, output_path=args.output or None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
