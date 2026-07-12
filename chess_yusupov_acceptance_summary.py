from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path


METRIC_NAMES = (
    "expected_diagram_recall",
    "marker_candidate_recall_visible_subset",
    "marker_ownership_accuracy",
    "clear_marker_classification_accuracy",
    "false_trusted_marker_count",
    "trusted_marker_rate",
    "side_to_move_coverage_rate",
    "unknown_count",
    "full_fen_safe_acceptance_rate",
    "hard_negative_evidence_rate",
)
SAFE_STATUSES = {"passed", "failed", "corpus_unavailable"}
SAFE_BLOCKERS = {
    "source_document_sha256_match",
    "runtime_commit_matches_validator",
    "minimum_expected_diagram_recall",
    "minimum_marker_candidate_recall_visible_subset",
    "minimum_marker_ownership_accuracy",
    "minimum_clear_marker_classification_accuracy",
    "maximum_false_trusted_marker_count",
    "minimum_side_to_move_coverage_rate",
    "maximum_unknown_count",
    "hard_negative_evidence_complete",
    "acceptance_profile_missing_or_invalid",
    "secure_verified_manifest_not_found",
}


def write_markdown_summary(json_path: str | Path, markdown_path: str | Path) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Acceptance summary JSON must contain an object.")
    status_value = str(payload.get("status") or "").strip().lower()
    status = status_value if status_value in SAFE_STATUSES else "failed"
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    raw_errors = payload.get("errors")
    error_items = raw_errors if isinstance(raw_errors, list) else []
    errors = [
        value if value in SAFE_BLOCKERS else "unclassified_acceptance_blocker"
        for value in (str(item or "").strip() for item in error_items)
    ]
    lines = [
        "# Side-to-move acceptance",
        "",
        f"- status: `{status}`",
        f"- closing evidence eligible: `{payload.get('closing_evidence_eligible') is True}`",
        "- synthetic fixtures may claim real acceptance: `false`",
        "",
        "## Required metrics",
        "",
    ]
    for name in METRIC_NAMES:
        lines.append(f"- {name}: `{_safe_number(metrics.get(name))}`")
    lines.extend(["", "## Blockers", ""])
    lines.extend([f"- `{error}`" for error in errors] or ["- None"])
    Path(markdown_path).write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def _safe_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 6)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    try:
        write_markdown_summary(args[0], args[1])
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
