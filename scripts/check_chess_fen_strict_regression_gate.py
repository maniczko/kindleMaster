from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


STRICT_ACCEPTED_STATUSES = {
    "accepted",
    "fen_machine_accepted",
    "machine_accepted",
    "verified_exact_crop_label_used",
}
STRICT_EXCLUDED_STATUSES = {
    "fen_placement_machine_accepted",
    "placement_machine_accepted",
    "fen_placement_valid",
    "ai_consensus",
    "ai_tie_break_resolved",
    "ai_best_effort",
    "ai_autoread",
}


def evaluate_strict_regression_gate(
    candidate_report_path: str | Path,
    *,
    baseline_path: str | Path,
) -> dict[str, Any]:
    baseline_file = Path(baseline_path)
    candidate_file = Path(candidate_report_path)
    baseline = _load_json(baseline_file, label="baseline")
    candidate_report = _load_json(candidate_file, label="candidate_report")

    best_known = int(baseline.get("best_known_strict_accepted", 0))
    allowed_drop = int(baseline.get("allowed_strict_drop", 0))
    case_count = int(baseline.get("case_count", 0))
    required_min = max(0, best_known - allowed_drop)
    strict_accepted = count_strict_accepted(candidate_report)
    strict_rate = (strict_accepted / case_count) if case_count else 0.0
    passed = strict_accepted >= required_min
    baseline_update_candidate = strict_accepted > best_known

    blockers: list[str] = []
    if not passed:
        blockers.append(
            f"strict accepted {strict_accepted} is below required minimum {required_min} "
            f"(best known {best_known}, allowed drop {allowed_drop})"
        )

    return {
        "schema": "kindlemaster.chess_fen.strict_regression_gate.v1",
        "status": "passed" if passed else "failed",
        "candidate_report": str(candidate_report_path),
        "baseline_path": str(baseline_path),
        "baseline": {
            "schema": baseline.get("schema", ""),
            "corpus": baseline.get("corpus", ""),
            "case_count": case_count,
            "best_known_report": baseline.get("best_known_report", ""),
            "best_known_strict_accepted": best_known,
            "best_known_strict_rate": baseline.get("best_known_strict_rate", 0),
            "allowed_strict_drop": allowed_drop,
            "required_min_strict_accepted": required_min,
        },
        "candidate": {
            "strict_accepted": strict_accepted,
            "case_count": case_count,
            "strict_rate": strict_rate,
            "strict_delta_vs_baseline": strict_accepted - best_known,
        },
        "baseline_update_candidate": baseline_update_candidate,
        "blockers": blockers,
    }


def count_strict_accepted(report: Any) -> int:
    return sum(1 for record in _extract_records(report) if _is_strict_accepted(record))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when chess FEN strict acceptance regresses below a baseline.")
    parser.add_argument("candidate_report")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    try:
        payload = evaluate_strict_regression_gate(args.candidate_report, baseline_path=args.baseline)
    except FileNotFoundError as error:
        missing_path = error.filename or (error.args[0] if error.args else "")
        payload = {
            "schema": "kindlemaster.chess_fen.strict_regression_gate.v1",
            "status": "failed",
            "error": "missing_input",
            "path": str(missing_path),
        }
        _emit_payload(payload, args.output_json, stderr=True)
        return 2
    except (json.JSONDecodeError, ValueError) as error:
        payload = {
            "schema": "kindlemaster.chess_fen.strict_regression_gate.v1",
            "status": "failed",
            "error": "invalid_input",
            "message": str(error),
        }
        _emit_payload(payload, args.output_json, stderr=True)
        return 2

    _emit_payload(payload, args.output_json, stderr=payload["status"] != "passed")
    return 0 if payload["status"] == "passed" else 1


def _emit_payload(payload: Mapping[str, Any], output_json: str | None, *, stderr: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text, file=sys.stderr if stderr else sys.stdout)
    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")


def _load_json(path: Path, *, label: str) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return data


def _extract_records(report: Any) -> list[Mapping[str, Any]]:
    if not isinstance(report, Mapping):
        return []
    roots: list[Any] = [report]
    quality_report = report.get("quality_report")
    if isinstance(quality_report, Mapping):
        roots.append(quality_report)
        chess_fen = quality_report.get("chess_fen")
        if isinstance(chess_fen, Mapping):
            roots.append(chess_fen)
    chess_fen = report.get("chess_fen")
    if isinstance(chess_fen, Mapping):
        roots.append(chess_fen)
    for root in roots:
        if not isinstance(root, Mapping):
            continue
        for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
            value = root.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        summary = root.get("summary")
        if isinstance(summary, Mapping):
            for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
                value = summary.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, Mapping)]
    return []


def _is_strict_accepted(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    status_blob = " ".join(
        str(value or "").strip().lower()
        for value in (
            record.get("status"),
            record.get("runtime_status"),
            record.get("method"),
            record.get("source"),
            record.get("label_source"),
        )
    )
    if any(excluded in status_blob for excluded in STRICT_EXCLUDED_STATUSES):
        return False
    if "ai_" in status_blob or "ai-autoread" in status_blob:
        return False
    if record.get("requires_review") is True:
        return False
    if not _looks_like_full_fen(_selected_value(record)):
        return False
    if record.get("requires_review") is False:
        return True
    return any(status in status_blob for status in STRICT_ACCEPTED_STATUSES)


def _selected_value(record: Mapping[str, Any]) -> str:
    return str(
        record.get("selected_value")
        or record.get("fen")
        or record.get("full_fen")
        or record.get("candidate_fen")
        or ""
    ).strip()


def _looks_like_full_fen(value: str) -> bool:
    parts = str(value or "").strip().split()
    return len(parts) == 6 and parts[1] in {"w", "b"}


if __name__ == "__main__":
    raise SystemExit(main())
