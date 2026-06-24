from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


def suggest_side_marker_calibration(input_jsonl: str | Path, *, output_jsonl: str | Path) -> dict[str, Any]:
    source = Path(input_jsonl)
    target = Path(output_jsonl)
    rows = [_suggest_row(row) for row in _read_jsonl(source)]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    suggestion_counts = Counter(str(row.get("ai_suggestion_status") or "unknown") for row in rows)
    side_counts = Counter(str(row.get("ai_suggested_side_to_move") or "none") for row in rows)
    summary = {
        "status": "ok",
        "input_jsonl": str(source),
        "output_jsonl": str(target),
        "row_count": len(rows),
        "suggestion_counts": dict(sorted(suggestion_counts.items())),
        "side_counts": dict(sorted(side_counts.items())),
        "rules": [
            "AI suggestions are evidence only.",
            "human_side_to_move and human_verified are never changed.",
            "A conflict gets a side suggestion only when the top marker score is materially stronger.",
            "Rows without detected visual marker candidates remain no_suggestion.",
        ],
    }
    summary_path = target.with_suffix(".summary.json")
    review_sheet_path = target.with_suffix(".review.html")
    summary["review_sheet_path"] = str(review_sheet_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    review_sheet_path.write_text(_review_sheet_html(summary, rows), encoding="utf-8")
    return summary


def _suggest_row(row: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    candidates = [
        candidate
        for candidate in (updated.get("side_marker_candidates") or [])
        if isinstance(candidate, dict) and str(candidate.get("detected_side") or "") in {"w", "b"}
    ]
    suggestion = _side_marker_suggestion(candidates)
    updated.update(suggestion)
    updated["ai_needs_human_review"] = True
    # Preserve the manual verification contract. This script is pre-label evidence only.
    updated.setdefault("human_side_to_move", "")
    updated.setdefault("human_verified", False)
    updated["human_verified"] = bool(row.get("human_verified") is True)
    return updated


def _side_marker_suggestion(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {
            "ai_suggestion_status": "no_suggestion",
            "ai_suggested_side_to_move": "",
            "ai_suggested_marker_role": "",
            "ai_suggested_marker_source": "",
            "ai_suggestion_confidence": 0.0,
            "ai_suggestion_reason": "No detected visual marker candidate in side_marker_candidates.",
        }
    ranked = sorted(candidates, key=_candidate_score, reverse=True)
    top = ranked[0]
    top_score = _candidate_score(top)
    top_side = str(top.get("detected_side") or "")
    sides = {str(candidate.get("detected_side") or "") for candidate in ranked}
    if len(sides) == 1:
        confidence = min(0.84, 0.62 + min(0.22, top_score / 8000.0))
        status = "suggested_same_side_agreement" if len(ranked) > 1 else "suggested_single_marker"
        return _suggestion_payload(
            status=status,
            side=top_side,
            role=str(top.get("role") or ""),
            confidence=confidence,
            reason=f"All detected marker candidates agree on side {top_side}; selected highest-score role {top.get('role')}.",
        )
    if len(ranked) >= 2:
        second_score = _candidate_score(ranked[1])
        if top_score >= max(450.0, second_score * 1.75):
            confidence = min(0.66, 0.48 + min(0.18, (top_score - second_score) / 5000.0))
            return _suggestion_payload(
                status="tentative_score_dominates_conflict",
                side=top_side,
                role=str(top.get("role") or ""),
                confidence=confidence,
                reason=(
                    f"Conflicting marker sides detected, but {top.get('role')} score "
                    f"{top_score:.1f} dominates next score {second_score:.1f}; human confirmation required."
                ),
            )
    return {
        "ai_suggestion_status": "ambiguous",
        "ai_suggested_side_to_move": "",
        "ai_suggested_marker_role": "",
        "ai_suggested_marker_source": "visual_marker",
        "ai_suggestion_confidence": 0.0,
        "ai_suggestion_reason": "Detected marker candidates conflict without a dominant score.",
    }


def _candidate_score(candidate: dict[str, Any]) -> float:
    try:
        return float(candidate.get("score") or 0.0)
    except Exception:
        return 0.0


def _suggestion_payload(*, status: str, side: str, role: str, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "ai_suggestion_status": status,
        "ai_suggested_side_to_move": side,
        "ai_suggested_marker_role": role,
        "ai_suggested_marker_source": "visual_marker",
        "ai_suggestion_confidence": round(float(confidence), 3),
        "ai_suggestion_reason": reason,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected object row at {path}:{line_number}")
        rows.append(row)
    return rows


def _review_sheet_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    sorted_rows = sorted(rows, key=_review_sort_key)
    cards = []
    for row in sorted_rows:
        crop = _html_path(row.get("crop_path"))
        context = _html_path(row.get("context_crop_path"))
        suggestion = str(row.get("ai_suggested_side_to_move") or "")
        status = str(row.get("ai_suggestion_status") or "")
        confidence = float(row.get("ai_suggestion_confidence") or 0.0)
        reason = str(row.get("ai_suggestion_reason") or "")
        marker_role = str(row.get("ai_suggested_marker_role") or "")
        candidate_summary = _marker_candidate_summary(row.get("side_marker_candidates") or [])
        cards.append(
            f"""
            <article class="card status-{html.escape(status)}">
              <header>
                <h2>{html.escape(str(row.get('id') or ''))}</h2>
                <p class="meta">page={row.get('page')} priority={html.escape(str(row.get('review_priority') or ''))}</p>
              </header>
              <div class="suggestion">
                <strong>AI suggestion:</strong>
                <span class="side">{html.escape(suggestion or 'none')}</span>
                <span>{html.escape(marker_role or '')}</span>
                <span>confidence={confidence:.3f}</span>
              </div>
              <p>{html.escape(reason)}</p>
              <p class="manual">Manual fields to fill in JSONL if confirmed: human_side_to_move, marker_source, marker_role, human_verified, verified_by, verified_at.</p>
              <div class="images">
                {'<img src="' + context + '" alt="context crop with marker probes">' if context else ''}
                {'<img src="' + crop + '" alt="board crop">' if crop else ''}
              </div>
              <pre>{html.escape(candidate_summary)}</pre>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>AI-Assisted Chess Side Marker Review</title>
  <style>
    body {{ margin: 24px; font-family: sans-serif; background: #f4efe6; color: #1d1914; }}
    .summary, .card {{ background: #fffaf2; border: 1px solid #d7c8b4; border-radius: 16px; padding: 16px; margin-bottom: 18px; }}
    h1, h2 {{ margin: 0 0 8px; }}
    .meta, .manual {{ color: #695b4a; }}
    .suggestion {{ display: flex; gap: 12px; align-items: center; background: #efe4d1; border-radius: 10px; padding: 8px 10px; }}
    .side {{ font-size: 22px; font-weight: 800; color: #6f3d00; }}
    .images {{ display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; margin: 12px 0; }}
    img {{ max-width: 560px; max-height: 560px; border: 1px solid #c7b8a5; background: white; }}
    pre {{ overflow: auto; background: #241f1a; color: #ffeed2; padding: 12px; border-radius: 10px; }}
    .status-no_suggestion {{ opacity: 0.82; }}
  </style>
</head>
<body>
  <section class="summary">
    <h1>AI-Assisted Chess Side Marker Review</h1>
    <p>Rows: {summary.get('row_count')} | Suggestions: {html.escape(json.dumps(summary.get('suggestion_counts'), ensure_ascii=False))}</p>
    <p>These are suggestions only. They must not be copied into canonical FEN without human verification.</p>
  </section>
  {''.join(cards)}
</body>
</html>"""


def _review_sort_key(row: dict[str, Any]) -> tuple[int, float, int, str]:
    status_order = {
        "tentative_score_dominates_conflict": 0,
        "suggested_same_side_agreement": 1,
        "suggested_single_marker": 2,
        "ambiguous": 3,
        "no_suggestion": 4,
    }
    return (
        status_order.get(str(row.get("ai_suggestion_status") or ""), 9),
        -float(row.get("ai_suggestion_confidence") or 0.0),
        int(row.get("page") or 0),
        str(row.get("filename") or ""),
    )


def _marker_candidate_summary(candidates: Any) -> str:
    if not isinstance(candidates, list):
        return "[]"
    compact = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        compact.append(
            {
                "role": candidate.get("role"),
                "side": candidate.get("detected_side", ""),
                "score": candidate.get("score", ""),
                "density": candidate.get("density", ""),
                "ambiguous_density": candidate.get("ambiguous_density", False),
            }
        )
    return json.dumps(compact, ensure_ascii=False, indent=2)


def _html_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return html.escape(Path(text).as_posix())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add evidence-only AI suggestions to a side marker calibration draft.")
    parser.add_argument("input_jsonl", help="side_marker_calibration_draft.jsonl")
    parser.add_argument("--output", required=True, help="Output JSONL with ai_suggested_* fields.")
    args = parser.parse_args(argv)
    summary = suggest_side_marker_calibration(args.input_jsonl, output_jsonl=args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
