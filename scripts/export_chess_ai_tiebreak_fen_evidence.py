from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


RECOMMENDATION = "tie_break_evidence_needs_rule_or_exact_label"
JSONL_NAME = "ai_tiebreak_evidence.jsonl"
HTML_NAME = "ai_tiebreak_review.html"


def export_chess_ai_tiebreak_fen_evidence(
    ai_autoread_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(ai_autoread_dir)
    target = Path(output_dir) if output_dir else source
    target.mkdir(parents=True, exist_ok=True)

    cases_path = source / "strict_fen_recovery_cases.jsonl"
    rows = [_evidence_row(row) for row in _read_jsonl(cases_path) if row.get("recommendation") == RECOMMENDATION]
    rows.sort(key=lambda row: (_sortable_int(row.get("page")), str(row.get("filename") or ""), str(row.get("id") or "")))

    jsonl_path = target / JSONL_NAME
    html_path = target / HTML_NAME
    _write_jsonl(jsonl_path, rows)
    html_path.write_text(_html(rows, source=source), encoding="utf-8")

    return {
        "status": "ok",
        "mode": "ai_tiebreak_fen_evidence",
        "source_cases": str(cases_path),
        "output_dir": str(target),
        "evidence_count": len(rows),
        "release_safe": False,
        "accepted_for_corpus": False,
        "artifacts": {
            "jsonl": str(jsonl_path),
            "html": str(html_path),
        },
        "policy": "AI evidence only, not strict/corpus authority.",
    }


def _evidence_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "page": row.get("page"),
        "filename": str(row.get("filename") or ""),
        "ai_fen": str(row.get("ai_fen") or ""),
        "ai_side_to_move": str(row.get("ai_side_to_move") or ""),
        "deterministic_placement": str(row.get("deterministic_placement") or ""),
        "placement_matches_deterministic": bool(row.get("placement_matches_deterministic")),
        "marker_roles": _string_list(row.get("marker_roles")),
        "marker_sides": _string_list(row.get("marker_sides")),
        "marker_conflict": bool(row.get("marker_conflict")),
        "response_fens": _string_list(row.get("response_fens")),
        "release_safe": False,
        "accepted_for_corpus": False,
        "recommendation": RECOMMENDATION,
    }


def _html(rows: list[dict[str, Any]], *, source: Path) -> str:
    body_rows = "\n".join(_html_row(row) for row in rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>AI Tie-Break FEN Evidence Review</title>
  <style>
    body {{ font-family: Georgia, 'Times New Roman', serif; margin: 2rem; color: #1f2933; background: #f8f5ef; }}
    .notice {{ border: 2px solid #7c2d12; background: #fff7ed; padding: 1rem; margin-bottom: 1.5rem; }}
    table {{ border-collapse: collapse; width: 100%; background: #fffdf8; }}
    th, td {{ border: 1px solid #d6c7ad; padding: 0.45rem; vertical-align: top; }}
    th {{ background: #eadcc4; text-align: left; }}
    code {{ white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body>
  <h1>AI Tie-Break FEN Evidence Review</h1>
  <div class="notice">
    <strong>AI evidence only, not strict/corpus authority.</strong>
    These rows must not be copied into canonical FEN, verified labels, or runtime strict exports without a deterministic rule or exact human-reviewed label.
  </div>
  <p>Source: <code>{html.escape(str(source))}</code></p>
  <p>Rows: {len(rows)}</p>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Page</th>
        <th>Filename</th>
        <th>AI FEN</th>
        <th>Side</th>
        <th>Deterministic placement</th>
        <th>Placement match</th>
        <th>Marker roles</th>
        <th>Marker sides</th>
        <th>Marker conflict</th>
        <th>Response FENs</th>
        <th>Release safe</th>
        <th>Corpus accepted</th>
      </tr>
    </thead>
    <tbody>
{body_rows}
    </tbody>
  </table>
</body>
</html>
"""


def _html_row(row: dict[str, Any]) -> str:
    cells = [
        row.get("id"),
        row.get("page"),
        row.get("filename"),
        row.get("ai_fen"),
        row.get("ai_side_to_move"),
        row.get("deterministic_placement"),
        row.get("placement_matches_deterministic"),
        ", ".join(row.get("marker_roles") or []),
        ", ".join(row.get("marker_sides") or []),
        row.get("marker_conflict"),
        "\n".join(row.get("response_fens") or []),
        row.get("release_safe"),
        row.get("accepted_for_corpus"),
    ]
    rendered = "".join(f"<td><code>{html.escape(str(cell))}</code></td>" for cell in cells)
    return f"      <tr>{rendered}</tr>"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing recovery cases file: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _sortable_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 10**9


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export AI tie-break FEN rows as evidence-only review artifacts.")
    parser.add_argument("ai_autoread_dir")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)
    summary = export_chess_ai_tiebreak_fen_evidence(args.ai_autoread_dir, output_dir=args.output_dir or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
