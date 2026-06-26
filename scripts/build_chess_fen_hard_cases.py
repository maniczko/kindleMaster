from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


NON_BLOCKING_CROP_RECOVERY_WARNINGS = {
    "final_rendered_crop_fen_used",
    "final_rendered_crop_sparse_consensus_fen_used",
    "reader_expanded_crop_fen_used",
    "reader_expanded_crop_sparse_consensus_fen_used",
    "reader_visible_crop_fen_used",
    "reader_visible_crop_sparse_consensus_fen_used",
    "sparse_exact_crop_consensus",
}
UNRESOLVED_CROP_GRID_BLOCKERS = {
    "board_grid_not_detected",
    "board_visual_pattern_not_detected",
    "candidate_bbox_out_of_bounds",
    "crop_invalid",
    "crop_missing",
    "partial_board_crop_without_dense_board_evidence",
    "review_crop_candidate_mismatch",
}


def build_hard_case_report(
    diagnostics_path: str | Path,
    *,
    baseline_path: str | Path | None = None,
) -> dict[str, Any]:
    diagnostics = _load_json(Path(diagnostics_path))
    current = _summarize_diagnostics(diagnostics)
    payload: dict[str, Any] = {
        "schema": "kindlemaster.chess_fen.hard_cases.v1",
        "diagnostics_path": str(diagnostics_path),
        "summary": current,
        "items": _hard_case_items(diagnostics),
    }
    if baseline_path is not None:
        baseline = _summarize_diagnostics(_load_json(Path(baseline_path)))
        deltas = {
            key: int(current.get(key, 0)) - int(baseline.get(key, 0))
            for key in (
                "review_total",
                "hard_case_total",
                "crop_grid_unresolved_count",
                "crop_recovery_evidence_count",
                "recognition_hard_case_count",
                "metadata_hard_case_count",
                "full_fen_validation_hard_case_count",
            )
        }
        payload["baseline_path"] = str(baseline_path)
        payload["baseline_summary"] = baseline
        payload["delta"] = deltas
        payload["crop_grid_blockers_decreased"] = (
            current["crop_grid_unresolved_count"] < baseline["crop_grid_unresolved_count"]
        )
    return payload


def write_markdown(payload: Mapping[str, Any], output_path: str | Path) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), Mapping) else {}
    lines = [
        "# Chess FEN Hard-Case Crop/Grid Report",
        "",
        f"Diagnostics: `{payload.get('diagnostics_path', '')}`",
        "",
        "## Summary",
        "",
        f"- Review total: `{summary.get('review_total', 0)}`",
        f"- Hard-case total: `{summary.get('hard_case_total', 0)}`",
        f"- Unresolved crop/grid blockers: `{summary.get('crop_grid_unresolved_count', 0)}`",
        f"- Crop recovery evidence: `{summary.get('crop_recovery_evidence_count', 0)}`",
        f"- Recognition hard cases: `{summary.get('recognition_hard_case_count', 0)}`",
        f"- Metadata hard cases: `{summary.get('metadata_hard_case_count', 0)}`",
        f"- Full-FEN validation hard cases: `{summary.get('full_fen_validation_hard_case_count', 0)}`",
        "",
        "## Tag Counts",
        "",
        "| Tag | Count |",
        "|---|---:|",
    ]
    for tag, count in _top_items(summary.get("by_hard_case_tag"), limit=20):
        lines.append(f"| `{_md(tag)}` | {count} |")
    if isinstance(payload.get("delta"), Mapping):
        lines.extend(["", "## Delta vs Baseline", "", "| Metric | Delta |", "|---|---:|"])
        for key, value in payload["delta"].items():  # type: ignore[index,union-attr]
            lines.append(f"| `{_md(str(key))}` | {int(value)} |")
        lines.append("")
        lines.append(f"Crop/grid blockers decreased: `{bool(payload.get('crop_grid_blockers_decreased'))}`")
    lines.extend(
        [
            "",
            "## First 50 Hard Cases",
            "",
            "| Diagram | Page | Tags | Primary blocker | Recommendation |",
            "|---|---:|---|---|---|",
        ]
    )
    for item in (payload.get("items") or [])[:50]:
        if not isinstance(item, Mapping):
            continue
        tags = ", ".join(str(tag) for tag in item.get("hard_case_tags", []) if str(tag))
        lines.append(
            "| {diagram} | {page} | `{tags}` | `{blocker}` | {recommendation} |".format(
                diagram=_md(str(item.get("diagram_id", ""))),
                page=_md(str(item.get("page", ""))),
                tags=_md(tags),
                blocker=_md(str(item.get("primary_blocker", ""))),
                recommendation=_md(str(item.get("recommendation", ""))),
            )
        )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a bounded chess FEN hard-case crop/grid report.")
    parser.add_argument("diagnostics_json")
    parser.add_argument("--baseline-json")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)

    payload = build_hard_case_report(args.diagnostics_json, baseline_path=args.baseline_json)
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(payload, args.output_md)
    print(json.dumps({"status": "ok", **payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


def _summarize_diagnostics(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    items = [item for item in diagnostics.get("items", []) if isinstance(item, Mapping)]
    tag_counter: Counter[str] = Counter()
    blocker_counter: Counter[str] = Counter()
    for item in items:
        for tag in _tags(item):
            tag_counter[tag] += 1
        blocker = str(item.get("primary_blocker") or "").strip()
        if blocker:
            blocker_counter[blocker] += 1
    return {
        "review_total": len(items),
        "hard_case_total": sum(1 for item in items if _tags(item)),
        "crop_grid_unresolved_count": tag_counter.get("crop_grid_unresolved", 0),
        "crop_recovery_evidence_count": tag_counter.get("crop_recovery_evidence", 0),
        "recognition_hard_case_count": tag_counter.get("recognition_hard_case", 0),
        "metadata_hard_case_count": tag_counter.get("metadata_hard_case", 0),
        "full_fen_validation_hard_case_count": tag_counter.get("full_fen_validation_hard_case", 0),
        "by_hard_case_tag": dict(sorted(tag_counter.items())),
        "top_primary_blockers": dict(blocker_counter.most_common(20)),
    }


def _hard_case_items(diagnostics: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in diagnostics.get("items", []):
        if not isinstance(item, Mapping) or not _tags(item):
            continue
        result.append(
            {
                "diagram_id": item.get("diagram_id", ""),
                "page": item.get("page", ""),
                "primary_blocker": item.get("primary_blocker", ""),
                "primary_category": item.get("primary_category", ""),
                "hard_case_tags": _tags(item),
                "non_blocking_crop_recovery_warnings": _crop_recovery_warnings(item),
                "recommendation": item.get("recommendation", ""),
            }
        )
    return result


def _tags(item: Mapping[str, Any]) -> list[str]:
    value = item.get("hard_case_tags")
    if isinstance(value, list):
        return sorted({str(tag) for tag in value if str(tag).strip()})
    blockers = {str(blocker) for blocker in item.get("all_blockers", []) if str(blocker).strip()}
    warnings = {str(warning) for warning in item.get("warnings", []) if str(warning).strip()}
    text = " ".join([*blockers, *warnings]).lower()
    tags: list[str] = []
    if blockers & UNRESOLVED_CROP_GRID_BLOCKERS:
        tags.append("crop_grid_unresolved")
    if (blockers | warnings) & NON_BLOCKING_CROP_RECOVERY_WARNINGS:
        tags.append("crop_recovery_evidence")
    if any(token in text for token in ("piece_template", "king_count", "queen_color", "pawn_on_back_rank")):
        tags.append("recognition_hard_case")
    if any(token in text for token in ("side_to_move", "caption", "marker")):
        tags.append("metadata_hard_case")
    if any(token in text for token in ("python_chess", "fen_parse", "fen_position")):
        tags.append("full_fen_validation_hard_case")
    return sorted(set(tags))


def _crop_recovery_warnings(item: Mapping[str, Any]) -> list[str]:
    value = item.get("non_blocking_crop_recovery_warnings")
    if isinstance(value, list):
        return sorted({str(warning) for warning in value if str(warning).strip()})
    blockers = {str(blocker) for blocker in item.get("all_blockers", []) if str(blocker).strip()}
    warnings = {str(warning) for warning in item.get("warnings", []) if str(warning).strip()}
    return sorted((blockers | warnings) & NON_BLOCKING_CROP_RECOVERY_WARNINGS)


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _top_items(value: Any, *, limit: int) -> list[tuple[str, int]]:
    if not isinstance(value, Mapping):
        return []
    return sorted(((str(key), int(count)) for key, count in value.items()), key=lambda item: (-item[1], item[0]))[:limit]


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
