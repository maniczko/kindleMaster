from __future__ import annotations

import argparse
import html
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen
from openai_chess_fen_reviewer import FORBIDDEN_AUTHORITY_FIELDS, POLICY_ACKNOWLEDGEMENT


FORBIDDEN_AI_AUTHORITY_FIELDS = FORBIDDEN_AUTHORITY_FIELDS | {
    "fen",
    "fen_canonical",
    "canonical_fen",
    "manual_fen",
    "pgn",
    "human_verified",
}


def import_chess_ai_autoread_responses(
    ai_autoread_dir: str | Path,
    responses_jsonl: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    source_dir = Path(ai_autoread_dir)
    target = Path(output_dir) if output_dir else source_dir
    target.mkdir(parents=True, exist_ok=True)
    responses = _group_responses(Path(responses_jsonl))
    fen_rows = [_finalize_fen_row(row, responses.get(("fen", str(row.get("id") or "")), [])) for row in _read_jsonl(source_dir / "ai_fen_readout.jsonl")]
    pgn_rows = [_finalize_pgn_row(row, responses.get(("pgn", str(row.get("id") or "")), [])) for row in _read_jsonl(source_dir / "ai_pgn_readout.jsonl")]

    fen_path = target / "ai_fen_readout.jsonl"
    pgn_path = target / "ai_pgn_readout.jsonl"
    summary_path = target / "ai_readout_summary.json"
    html_path = target / "ai_review.html"
    _write_jsonl(fen_path, fen_rows)
    _write_jsonl(pgn_path, pgn_rows)
    summary = {
        "status": "ok",
        "mode": "ai_autoread",
        "release_safe": False,
        "responses_jsonl": str(responses_jsonl),
        "output_dir": str(target),
        "fen_total": len(fen_rows),
        "fen_ai_coverage": 1.0,
        "fen_status_counts": dict(Counter(str(row.get("ai_readout_status") or "") for row in fen_rows)),
        "pgn_total": len(pgn_rows),
        "pgn_ai_coverage": 1.0,
        "pgn_status_counts": dict(Counter(str(row.get("ai_readout_status") or "") for row in pgn_rows)),
        "authority_issue_count": sum(len(row.get("ai_policy_issues") or []) for row in [*fen_rows, *pgn_rows]),
        "ai_fen_readout": str(fen_path),
        "ai_pgn_readout": str(pgn_path),
        "ai_review_html": str(html_path),
        "policy": "ai_autoread_experimental_no_runtime_promotion",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_review_html(summary, fen_rows, pgn_rows), encoding="utf-8")
    return summary


def _finalize_fen_row(row: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    updated = _preserve_non_authority(row)
    parsed = [_normalized_fen_response(item) for item in responses]
    valid = [item for item in parsed if item.get("readout_status") == "ai_readout_complete" and item.get("fen_valid")]
    issues = [issue for item in parsed for issue in item.get("policy_issues", [])]
    followup = _resolved_fen_followup(valid)
    if not parsed and _terminal_ai_status(row):
        updated.update(
            {
                "ai_readout_status": str(row.get("ai_readout_status") or ""),
                "ai_consensus": bool(row.get("ai_consensus")),
                "ai_fen": str(row.get("ai_fen") or ""),
                "ai_placement": str(row.get("ai_placement") or ""),
                "ai_side_to_move": str(row.get("ai_side_to_move") or "unknown"),
                "ai_confidence": float(row.get("ai_confidence") or 0.0),
                "ai_reason": str(row.get("ai_reason") or ""),
            }
        )
    elif not parsed:
        updated.update({"ai_readout_status": "ai_readout_unreadable", "ai_unreadable_reason": "ai_response_missing"})
    elif followup is not None:
        updated.update(
            {
                "ai_readout_status": followup["status"],
                "ai_consensus": False,
                "ai_fen": followup["candidate"].get("ai_fen", ""),
                "ai_placement": followup["candidate"].get("placement", ""),
                "ai_side_to_move": followup["candidate"].get("side_to_move", "unknown"),
                "ai_confidence": followup["candidate"].get("confidence", 0.0),
                "ai_reason": followup["reason"],
            }
        )
    elif len(valid) >= 2 and len({item.get("ai_fen") for item in valid}) == 1:
        chosen = valid[0]
        updated.update(
            {
                "ai_readout_status": "ai_consensus",
                "ai_consensus": True,
                "ai_fen": chosen.get("ai_fen", ""),
                "ai_placement": chosen.get("placement", ""),
                "ai_side_to_move": chosen.get("side_to_move", "unknown"),
                "ai_confidence": min(float(item.get("confidence") or 0.0) for item in valid),
                "ai_reason": "FEN direct_read and skeptical_verify agree.",
            }
        )
    elif len(valid) == 1 and len(parsed) == 1:
        chosen = valid[0]
        updated.update(
            {
                "ai_readout_status": "ai_readout_complete",
                "ai_fen": chosen.get("ai_fen", ""),
                "ai_placement": chosen.get("placement", ""),
                "ai_side_to_move": chosen.get("side_to_move", "unknown"),
                "ai_confidence": chosen.get("confidence", 0.0),
                "ai_reason": chosen.get("reason", ""),
            }
        )
    elif valid:
        best = _best_effort_candidate(valid)
        if best is not None:
            updated.update(
                {
                    "ai_readout_status": "ai_best_effort",
                    "ai_consensus": False,
                    "ai_fen": best.get("ai_fen", ""),
                    "ai_placement": best.get("placement", ""),
                    "ai_side_to_move": best.get("side_to_move", "unknown"),
                    "ai_confidence": best.get("confidence", 0.0),
                    "ai_reason": "Conflicting FEN responses; retained highest-confidence AI best effort.",
                    "ai_unreadable_reason": "ai_fen_conflict_best_effort",
                }
            )
        else:
            updated.update(
                {
                    "ai_readout_status": "ai_readout_conflict",
                    "ai_unreadable_reason": "ai_fen_responses_conflict",
                    "ai_tie_break_required": True,
                }
            )
    else:
        updated.update({"ai_readout_status": "ai_readout_unreadable", "ai_unreadable_reason": "ai_fen_invalid_or_unreadable"})
    updated["ai_policy_issues"] = sorted(dict.fromkeys(issues))
    return updated


def _resolved_fen_followup(valid: list[dict[str, Any]]) -> dict[str, Any] | None:
    priority = [
        ("tie_break_high_reasoning", "ai_tie_break_resolved", "FEN conflict resolved by tie-break follow-up."),
        ("enhanced_vision_retry", "ai_retry_resolved", "Unreadable FEN resolved by enhanced vision retry."),
    ]
    for variant, status, reason in priority:
        candidates = [item for item in valid if item.get("variant") == variant]
        if not candidates:
            continue
        candidate = sorted(candidates, key=lambda item: float(item.get("confidence") or 0.0), reverse=True)[0]
        if candidate.get("ai_fen"):
            return {"status": status, "candidate": candidate, "reason": reason}
    return None


def _finalize_pgn_row(row: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    updated = _preserve_non_authority(row)
    parsed = [_normalized_pgn_response(item) for item in responses]
    complete = [item for item in parsed if item.get("readout_status") == "ai_readout_complete"]
    issues = [issue for item in parsed for issue in item.get("policy_issues", [])]
    if not parsed and _terminal_ai_status(row):
        updated.update(
            {
                "ai_readout_status": str(row.get("ai_readout_status") or ""),
                "ai_consensus": bool(row.get("ai_consensus")),
                "ai_movetext": str(row.get("ai_movetext") or ""),
                "ai_pgn": str(row.get("ai_pgn") or ""),
                "ai_confidence": float(row.get("ai_confidence") or 0.0),
                "ai_reason": str(row.get("ai_reason") or ""),
                "ai_pgn_replay_legal": bool(row.get("ai_pgn_replay_legal")),
            }
        )
    elif not parsed:
        updated.update({"ai_readout_status": "ai_readout_unreadable", "ai_unreadable_reason": "ai_response_missing"})
    elif len(complete) >= 2 and len({item.get("ai_movetext") for item in complete}) == 1:
        chosen = complete[0]
        replay_legal = _ai_pgn_replay_legal(chosen.get("ai_movetext", ""), _row_start_fen(row))
        updated.update(
            {
                "ai_readout_status": "ai_consensus",
                "ai_consensus": True,
                "ai_movetext": chosen.get("ai_movetext", ""),
                "ai_pgn": chosen.get("ai_pgn", ""),
                "ai_pgn_feasibility": chosen.get("pgn_feasibility", ""),
                "ai_confidence": min(float(item.get("confidence") or 0.0) for item in complete),
                "ai_reason": "PGN direct_read and skeptical_verify agree.",
                "ai_pgn_replay_legal": replay_legal,
            }
        )
    elif complete:
        best = _best_effort_candidate(complete, key="ai_movetext")
        if best is not None:
            updated.update(
                {
                    "ai_readout_status": "ai_best_effort",
                    "ai_consensus": False,
                    "ai_movetext": best.get("ai_movetext", ""),
                    "ai_pgn": best.get("ai_pgn", ""),
                    "ai_pgn_feasibility": best.get("pgn_feasibility", ""),
                    "ai_confidence": best.get("confidence", 0.0),
                    "ai_reason": "Conflicting PGN responses; retained highest-confidence AI best effort.",
                    "ai_unreadable_reason": "ai_pgn_conflict_best_effort",
                    "ai_pgn_replay_legal": _ai_pgn_replay_legal(best.get("ai_movetext", ""), _row_start_fen(row)),
                }
            )
        else:
            updated.update(
                {
                    "ai_readout_status": "ai_readout_conflict",
                    "ai_unreadable_reason": "ai_pgn_responses_conflict",
                    "ai_tie_break_required": True,
                    "ai_pgn_replay_legal": False,
                }
            )
    else:
        unreadable = parsed[0] if parsed else {}
        updated.update(
            {
                "ai_readout_status": "ai_readout_unreadable",
                "ai_unreadable_reason": unreadable.get("reason", "ai_pgn_unreadable"),
                "ai_pgn_feasibility": unreadable.get("pgn_feasibility", "ai_unreadable"),
            }
        )
    updated["ai_policy_issues"] = sorted(dict.fromkeys(issues))
    return updated


def _preserve_non_authority(row: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    updated["source"] = "ai_autoread"
    updated["release_safe"] = False
    updated["human_verified"] = False
    updated["accepted_for_corpus"] = False
    return updated


def _terminal_ai_status(row: dict[str, Any]) -> bool:
    return str(row.get("ai_readout_status") or "") in {
        "strict_existing",
        "ai_consensus",
        "ai_best_effort",
        "ai_readout_complete",
        "ai_readout_unreadable",
        "ai_readout_conflict",
    }


def _normalized_fen_response(item: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(item.get("parsed") or {})
    issues = _policy_issues(parsed)
    fen = str(parsed.get("ai_fen") or parsed.get("fen") or "").strip()
    fen_valid, fen_warnings = validate_fen(fen) if fen else (False, ["fen_missing"])
    return {
        "variant": str(item.get("variant") or ""),
        "readout_status": str(parsed.get("readout_status") or ""),
        "ai_fen": fen,
        "placement": str(parsed.get("placement") or "").strip(),
        "side_to_move": _side(parsed.get("side_to_move")),
        "confidence": _clamp(parsed.get("confidence")),
        "reason": str(parsed.get("reason") or ""),
        "fen_valid": fen_valid,
        "fen_warnings": fen_warnings,
        "policy_issues": issues,
    }


def _normalized_pgn_response(item: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(item.get("parsed") or {})
    return {
        "readout_status": str(parsed.get("readout_status") or ""),
        "pgn_feasibility": str(parsed.get("pgn_feasibility") or ""),
        "ai_movetext": str(parsed.get("ai_movetext") or parsed.get("movetext") or "").strip(),
        "ai_pgn": str(parsed.get("ai_pgn") or parsed.get("pgn") or "").strip(),
        "confidence": _clamp(parsed.get("confidence")),
        "reason": str(parsed.get("reason") or ""),
        "policy_issues": _policy_issues(parsed),
    }


def _policy_issues(parsed: dict[str, Any]) -> list[str]:
    issues = []
    if str(parsed.get("policy_acknowledgement") or "") != POLICY_ACKNOWLEDGEMENT:
        issues.append("ai_policy_acknowledgement_missing")
    if any(field in parsed for field in FORBIDDEN_AI_AUTHORITY_FIELDS):
        issues.append("ai_authoritative_field_ignored")
    return issues


def _best_effort_candidate(items: list[dict[str, Any]], *, key: str = "ai_fen") -> dict[str, Any] | None:
    distinct = {str(item.get(key) or "") for item in items if str(item.get(key) or "")}
    if len(distinct) <= 1:
        return None
    ranked = sorted(items, key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    if len(ranked) < 2:
        return ranked[0] if ranked else None
    if float(ranked[0].get("confidence") or 0.0) - float(ranked[1].get("confidence") or 0.0) >= 0.15:
        return ranked[0]
    return None


def _row_start_fen(row: dict[str, Any]) -> str:
    return str(row.get("deterministic_fen") or row.get("deterministic_full_fen") or row.get("fen") or "").strip()


def _ai_pgn_replay_legal(movetext: str, start_fen: str) -> bool:
    if not str(movetext or "").strip() or not str(start_fen or "").strip():
        return False
    try:
        import chess
        import chess.pgn
    except Exception:
        return False
    try:
        board = chess.Board(start_fen)
    except Exception:
        return False
    pgn = (
        '[Event "AI Autoread"]\n'
        '[Site "?"]\n'
        '[Date "????.??.??"]\n'
        '[Round "?"]\n'
        '[White "?"]\n'
        '[Black "?"]\n'
        '[Result "*"]\n'
        '[SetUp "1"]\n'
        f'[FEN "{start_fen}"]\n\n'
        f'{movetext.strip()}\n'
    )
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
        if game is None:
            return False
        replay = board.copy()
        for move in game.mainline_moves():
            if move not in replay.legal_moves:
                return False
            replay.push(move)
        return True
    except Exception:
        return False


def _group_responses(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(path):
        custom_id = str(row.get("custom_id") or row.get("id") or "")
        parts = custom_id.split("::")
        if len(parts) < 3:
            continue
        kind, row_id, variant = parts[0], parts[1], parts[2]
        groups[(kind, row_id)].append({"variant": variant, "parsed": _parse_response(row)})
    return groups


def _parse_response(row: dict[str, Any]) -> dict[str, Any]:
    if "readout_status" in row:
        return row
    body = row.get("body") if isinstance(row.get("body"), dict) else None
    response = row.get("response") if isinstance(row.get("response"), dict) else None
    if body is None and response is not None:
        body = response.get("body") if isinstance(response.get("body"), dict) else None
    if body is None:
        return {}
    text = body.get("output_text")
    if not isinstance(text, str):
        for item in body.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    text = content["text"]
                    break
    try:
        parsed = json.loads(str(text or ""))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _side(value: Any) -> str:
    text = str(value or "unknown").lower().strip()
    return text if text in {"w", "b", "unknown"} else "unknown"


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _review_html(summary: dict[str, Any], fen_rows: list[dict[str, Any]], pgn_rows: list[dict[str, Any]]) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>AI Autoread Results</title>
<style>body{{font-family:sans-serif;margin:24px;background:#f4efe6}}.card{{background:#fff;border:1px solid #d7c7b2;border-radius:12px;padding:14px;margin:12px 0}}</style>
</head><body><h1>AI Autoread Results</h1><p>Experimental; not release verified.</p>
<div class="card">FEN: {html.escape(json.dumps(summary.get('fen_status_counts'), ensure_ascii=False))}</div>
<div class="card">PGN: {html.escape(json.dumps(summary.get('pgn_status_counts'), ensure_ascii=False))}</div>
{''.join(f'<div class="card">{html.escape(str(r.get("id")))}: {html.escape(str(r.get("ai_readout_status")))} {html.escape(str(r.get("ai_fen") or r.get("ai_movetext") or ""))}</div>' for r in [*fen_rows[:20], *pgn_rows[:20]])}
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import experimental AI autoread responses and compute consensus/conflict status.")
    parser.add_argument("ai_autoread_dir")
    parser.add_argument("responses_jsonl")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)
    summary = import_chess_ai_autoread_responses(args.ai_autoread_dir, args.responses_jsonl, output_dir=args.output_dir or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
