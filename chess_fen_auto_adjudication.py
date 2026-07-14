from __future__ import annotations

import html
import json
import os
import shutil
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol

import chess

from chess_fen_gold_corpus import INTAKE_MANIFEST_SCHEMA
from chess_position_recognizer import validate_fen
from openai_chess_fen_reviewer import build_openai_chess_fen_reviewer_from_env


AUTO_ROW_SCHEMA = "kindlemaster.chess.fen_auto_adjudication.row.v1"
SUMMARY_SCHEMA = "kindlemaster.chess.fen_auto_adjudication.summary.v1"
AUTO_STATUSES = {"auto_consensus", "needs_adjudication", "rejected", "unreadable"}
DEFAULT_MEDIUM_MODEL = "gpt-4.1-mini"
DEFAULT_STRONG_MODEL = "gpt-4.1"


class FenCandidateProvider(Protocol):
    name: str
    model: str

    def propose_chess_fen_from_crop(self, context: dict[str, Any]) -> Mapping[str, Any]: ...


def auto_label_fen_corpus(
    *,
    intake_manifest: str | Path,
    output_dir: str | Path,
    vision_mode: str = "replay",
    replay_path: str | Path | None = None,
    medium_provider: FenCandidateProvider | None = None,
    strong_provider: FenCandidateProvider | None = None,
    medium_confidence: float = 0.90,
    strong_confidence: float = 0.94,
) -> dict[str, Any]:
    manifest_path = Path(intake_manifest).resolve()
    out = Path(output_dir).resolve()
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != INTAKE_MANIFEST_SCHEMA:
        raise ValueError("intake_manifest_schema_invalid")
    package_root = manifest_path.parent
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    review_path = _package_member(package_root, artifacts.get("review_rows") or "full_fen_review.jsonl")
    rows = _load_jsonl(review_path)
    if not rows:
        raise ValueError("review_rows_empty")
    expected_source_sha = str((manifest.get("source") or {}).get("sha256") or "")
    if not expected_source_sha:
        raise ValueError("source_sha256_missing")
    replay = _load_replay(replay_path) if vision_mode == "replay" else {}
    if vision_mode not in {"off", "replay", "live"}:
        raise ValueError("vision_mode_invalid")
    if vision_mode == "live":
        medium_provider, strong_provider = _live_providers(medium_provider, strong_provider)

    out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_row in rows:
        fingerprint = str(source_row.get("diagram_fingerprint") or "")
        if not fingerprint or fingerprint in seen:
            raise ValueError("diagram_fingerprint_missing_or_duplicate")
        seen.add(fingerprint)
        if str(source_row.get("source_document_sha256") or "") != expected_source_sha:
            raise ValueError(f"source_sha256_mismatch:{fingerprint}")
        crop_path = _package_member(package_root, source_row.get("board_crop_path"))
        crop_data = crop_path.read_bytes()
        if sha256(crop_data).hexdigest() != str(source_row.get("board_crop_sha256") or ""):
            raise ValueError(f"board_crop_sha256_mismatch:{fingerprint}")

        local = _candidate(
            tier="local",
            fen=source_row.get("candidate_fen"),
            confidence=source_row.get("candidate_confidence"),
            provider="kindlemaster-runtime",
            model="source-candidate",
            eligible=True,
        )
        medium = _vision_candidate(
            tier="medium",
            fingerprint=fingerprint,
            replay=replay,
            provider=medium_provider,
            crop_data=crop_data,
            source_row=source_row,
            enabled=vision_mode != "off",
        )
        strong: dict[str, Any] | None = None
        if _needs_strong(local, medium, medium_confidence):
            strong = _vision_candidate(
                tier="strong",
                fingerprint=fingerprint,
                replay=replay,
                provider=strong_provider,
                crop_data=crop_data,
                source_row=source_row,
                enabled=vision_mode != "off",
            )
        decision = _decide(
            local=local,
            medium=medium,
            strong=strong,
            marker_side=str(source_row.get("manual_marker_side") or ""),
            medium_confidence=medium_confidence,
            strong_confidence=strong_confidence,
        )
        results.append(
            {
                "schema": AUTO_ROW_SCHEMA,
                "diagram_fingerprint": fingerprint,
                "source_document_sha256": expected_source_sha,
                "diagram_id": str(source_row.get("diagram_id") or ""),
                "page": source_row.get("page"),
                "split": str(source_row.get("split") or ""),
                "board_crop_path": str(source_row.get("board_crop_path") or ""),
                "board_crop_sha256": str(source_row.get("board_crop_sha256") or ""),
                "manual_marker_side": str(source_row.get("manual_marker_side") or ""),
                "status": decision["status"],
                "automatic_fen": decision["fen"],
                "decision_reason": decision["reason"],
                "candidate_chain": [candidate for candidate in (local, medium, strong) if candidate is not None],
                "label_source": "automatic_consensus" if decision["status"] == "auto_consensus" else "",
                "human_verified": False,
                "accepted_for_gold_corpus": False,
                "independent_holdout_required": True,
            }
        )

    queue = [row for row in results if row["status"] != "auto_consensus"]
    review_assets = out / "review_assets"
    review_assets.mkdir(exist_ok=True)
    for row in queue:
        source_crop = _package_member(package_root, row["board_crop_path"])
        target_name = f"{row['diagram_fingerprint']}{source_crop.suffix.lower() or '.png'}"
        target_crop = review_assets / target_name
        shutil.copyfile(source_crop, target_crop)
        row["exception_crop_path"] = target_crop.relative_to(out).as_posix()

    candidate_path = out / "automatic_fen_candidates.jsonl"
    queue_path = out / "fen_adjudication_queue.jsonl"
    _write_jsonl(candidate_path, results)
    _write_jsonl(queue_path, queue)
    counts = Counter(row["status"] for row in results)
    executed_candidates = [
        candidate
        for row in results
        for candidate in row["candidate_chain"]
        if candidate["status"] != "not_run"
    ]
    summary = {
        "schema": SUMMARY_SCHEMA,
        "status": "passed",
        "vision_mode": vision_mode,
        "source_document_sha256": expected_source_sha,
        "processed_count": len(results),
        "automatic_consensus_count": counts["auto_consensus"],
        "needs_adjudication_count": counts["needs_adjudication"],
        "rejected_count": counts["rejected"],
        "unreadable_count": counts["unreadable"],
        "automatic_coverage": round(counts["auto_consensus"] / len(results), 6),
        "independent_accuracy": None,
        "accuracy_claim_available": False,
        "accuracy_blocker": "independent_source_bound_holdout_labels_required",
        "model_routing": {
            "local": "all_rows",
            "medium_vision_required": len(results),
            "medium_vision_executed": sum(
                1
                for row in results
                if any(c["tier"] == "medium" and c["status"] != "not_run" for c in row["candidate_chain"])
            ),
            "strong_adjudication_required": sum(
                1 for row in results if any(c["tier"] == "strong" for c in row["candidate_chain"])
            ),
            "strong_adjudication_executed": sum(
                1
                for row in results
                if any(c["tier"] == "strong" and c["status"] != "not_run" for c in row["candidate_chain"])
            ),
        },
        "provider_counts": dict(sorted(Counter(candidate["provider"] for candidate in executed_candidates).items())),
        "model_counts": dict(sorted(Counter(candidate["model"] for candidate in executed_candidates).items())),
        "candidate_path": str(candidate_path),
        "adjudication_queue_path": str(queue_path),
        "exception_html_path": str(out / "fen_exception_review.html"),
        "exception_markdown_path": str(out / "fen_exception_review.md"),
    }
    summary_path = out / "fen_auto_adjudication_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "fen_auto_adjudication_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    (out / "fen_exception_review.html").write_text(_exception_html(queue), encoding="utf-8")
    (out / "fen_exception_review.md").write_text(_exception_markdown(queue), encoding="utf-8")
    return summary


def _live_providers(
    medium: FenCandidateProvider | None,
    strong: FenCandidateProvider | None,
) -> tuple[FenCandidateProvider, FenCandidateProvider]:
    base = medium or build_openai_chess_fen_reviewer_from_env()
    if base is None:
        raise ValueError("live_vision_provider_not_configured")
    medium_model = os.getenv("KINDLEMASTER_FEN_MEDIUM_MODEL", DEFAULT_MEDIUM_MODEL)
    strong_model = os.getenv("KINDLEMASTER_FEN_STRONG_MODEL", DEFAULT_STRONG_MODEL)
    medium = replace(base, model=medium_model) if hasattr(base, "__dataclass_fields__") else base
    if strong is None:
        strong = replace(base, model=strong_model) if hasattr(base, "__dataclass_fields__") else base
    return medium, strong


def _vision_candidate(
    *,
    tier: str,
    fingerprint: str,
    replay: dict[tuple[str, str], dict[str, Any]],
    provider: FenCandidateProvider | None,
    crop_data: bytes,
    source_row: Mapping[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return _candidate(tier=tier, fen="", confidence=0.0, provider="disabled", model="off", status="not_run")
    replay_row = replay.get((fingerprint, tier))
    if replay_row is not None:
        return _candidate(
            tier=tier,
            fen=replay_row.get("fen"),
            confidence=replay_row.get("confidence"),
            provider=str(replay_row.get("provider") or "replay"),
            model=str(replay_row.get("model") or tier),
            status=str(replay_row.get("status") or "suggested"),
            eligible=not bool(replay_row.get("needs_review")) and not bool(replay_row.get("uncertain_squares")),
        )
    if provider is None:
        return _candidate(
            tier=tier, fen="", confidence=0.0, provider="unavailable", model=tier, status="not_run", eligible=False
        )
    response = dict(
        provider.propose_chess_fen_from_crop(
            {
                "image_data": crop_data,
                "image_mime_type": _mime_type(str(source_row.get("board_crop_path") or "")),
                "diagram_fingerprint": fingerprint,
                "page": source_row.get("page"),
                "side_to_move_hint": source_row.get("manual_marker_side") or "unknown",
            }
        )
    )
    return _candidate(
        tier=tier,
        fen=response.get("fen"),
        confidence=response.get("confidence"),
        provider=str(response.get("provider") or provider.name),
        model=str(response.get("model") or provider.model),
        status=str(response.get("status") or "suggested"),
        eligible=(
            not bool(response.get("needs_review"))
            and not bool(response.get("uncertain_squares"))
            and not bool(response.get("issues"))
        ),
    )


def _candidate(
    *,
    tier: str,
    fen: Any,
    confidence: Any,
    provider: str,
    model: str,
    status: str = "suggested",
    eligible: bool = False,
) -> dict[str, Any]:
    normalized = " ".join(str(fen or "").strip().split())
    valid, issues = _validate_candidate_fen(normalized)
    try:
        score = max(0.0, min(1.0, float(confidence or 0.0)))
    except (TypeError, ValueError):
        score = 0.0
    return {
        "tier": tier,
        "provider": provider,
        "model": model,
        "status": status,
        "fen": normalized,
        "confidence": score,
        "valid": valid,
        "eligible_for_consensus": bool(eligible and valid),
        "issues": issues,
    }


def _needs_strong(local: Mapping[str, Any], medium: Mapping[str, Any], threshold: float) -> bool:
    return not (
        local.get("eligible_for_consensus")
        and medium.get("eligible_for_consensus")
        and local.get("fen") == medium.get("fen")
        and float(medium.get("confidence") or 0.0) >= threshold
    )


def _decide(
    *,
    local: Mapping[str, Any],
    medium: Mapping[str, Any],
    strong: Mapping[str, Any] | None,
    marker_side: str,
    medium_confidence: float,
    strong_confidence: float,
) -> dict[str, str]:
    candidates = [local, medium, *( [strong] if strong is not None else [])]
    for left, right, threshold in ((local, medium, medium_confidence), (medium, strong, strong_confidence)):
        if right is None:
            continue
        if (
            left.get("eligible_for_consensus")
            and right.get("eligible_for_consensus")
            and left.get("fen") == right.get("fen")
            and float(right.get("confidence") or 0.0) >= threshold
            and _side_matches(str(right.get("fen") or ""), marker_side)
        ):
            return {"status": "auto_consensus", "fen": str(right["fen"]), "reason": f"exact_{left['tier']}_{right['tier']}_consensus"}
    statuses = {str(candidate.get("status") or "") for candidate in candidates if candidate is not None}
    if statuses and statuses <= {"unreadable", "insufficient_crop", "missing_crop", "not_run"} and "unreadable" in statuses:
        return {"status": "unreadable", "fen": "", "reason": "vision_crop_unreadable"}
    legal = [candidate for candidate in candidates if candidate is not None and candidate.get("valid")]
    if not legal and all(str(candidate.get("status") or "") not in {"not_run", ""} for candidate in candidates):
        return {"status": "rejected", "fen": "", "reason": "no_legal_full_fen_candidate"}
    if legal and marker_side and not any(_side_matches(str(candidate.get("fen") or ""), marker_side) for candidate in legal):
        return {"status": "needs_adjudication", "fen": "", "reason": "marker_side_conflict"}
    return {"status": "needs_adjudication", "fen": "", "reason": "independent_candidates_do_not_agree"}


def _side_matches(fen: str, marker_side: str) -> bool:
    return not marker_side or (len(fen.split()) == 6 and fen.split()[1] == marker_side)


def _validate_candidate_fen(fen: str) -> tuple[bool, list[str]]:
    valid, issues = validate_fen(fen)
    if not valid:
        return False, issues
    try:
        board = chess.Board(fen)
    except ValueError:
        return False, [*issues, "python_chess_parse_failed"]
    if not board.is_valid():
        return False, [*issues, "python_chess_position_invalid"]
    return True, issues


def _load_replay(path: str | Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    rows = _load_jsonl(Path(path).resolve())
    replay: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("diagram_fingerprint") or ""), str(row.get("tier") or "medium"))
        if not key[0] or key in replay:
            raise ValueError("replay_key_missing_or_duplicate")
        replay[key] = row
    return replay


def _package_member(root: Path, value: Any) -> Path:
    raw = Path(str(value or ""))
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("package_member_outside_root")
    if not candidate.is_file():
        raise ValueError(f"package_member_not_found:{candidate}")
    return candidate


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _mime_type(path: str) -> str:
    return "image/jpeg" if Path(path).suffix.lower() in {".jpg", ".jpeg"} else "image/png"


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# FEN automatic adjudication",
            "",
            f"- processed: {summary['processed_count']}",
            f"- automatic consensus: {summary['automatic_consensus_count']}",
            f"- exception queue: {summary['needs_adjudication_count'] + summary['rejected_count'] + summary['unreadable_count']}",
            f"- automatic coverage: {summary['automatic_coverage']:.2%}",
            "- independent accuracy: unavailable",
            "",
            "Automatic consensus is not a human-verified gold label. Accuracy requires a separate source-bound holdout.",
        ]
    ) + "\n"


def _exception_html(rows: list[dict[str, Any]]) -> str:
    cards = []
    for row in rows:
        crop = html.escape(str(row.get("exception_crop_path") or ""), quote=True)
        cards.append(
            f"<article><img src=\"{crop}\" alt=\"board crop\"><h2>{html.escape(str(row.get('diagram_id') or 'diagram'))}</h2>"
            f"<p>{html.escape(str(row.get('decision_reason') or ''))}</p><pre>{html.escape(json.dumps(row.get('candidate_chain'), ensure_ascii=False, indent=2))}</pre></article>"
        )
    return "<!doctype html><meta charset=\"utf-8\"><title>FEN exceptions</title><style>body{font:16px Georgia;margin:2rem;background:#f4efe5}article{background:white;padding:1rem;margin:1rem auto;max-width:900px}img{width:260px;max-width:100%}pre{white-space:pre-wrap}</style><h1>FEN exception review</h1>" + "".join(cards)


def _exception_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# FEN exception review", "", f"Exceptions: {len(rows)}", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row.get('diagram_id') or 'diagram'}",
                "",
                f"- status: {row.get('status')}",
                f"- reason: {row.get('decision_reason')}",
                f"- fingerprint: `{row.get('diagram_fingerprint')}`",
                f"- crop: `{row.get('exception_crop_path')}`",
                "",
            ]
        )
    return "\n".join(lines)
