from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_fen_hardening import KNOWN_BAD_EXPECTED_FENS, compare_fen_placements, render_square_diff_text

HIGH_RISK_WARNINGS = {
    "side_to_move_inferred",
    "dense_board_area_crop_used",
    "reader_visible_crop_fen_used",
    "final_rendered_crop_fen_used",
    "sparse_exact_crop_consensus",
}

CRITICAL_WARNINGS = {
    "white_king_count_invalid",
    "black_king_count_invalid",
    "rank_width_invalid",
    "placement_contains_invalid_piece",
}

RECOVERY_METHOD_MARKERS = (
    "shift-recovered",
    "shift_recovered",
    "border-expanded",
    "border_expanded",
    "border-refined",
    "border_refined",
    "bbox_recovered",
    "bbox-recovered",
)

PROFILE_LOW_CORPUS_SEED_COUNT = 50
PROFILE_LOW_CORPUS_EXACT_ACCURACY = 0.95


def export_chess_fen_accepted_audit(
    smoke_report: str | Path,
    *,
    output_dir: str | Path,
    crop_source_dirs: Iterable[str | Path] | None = None,
    corpus_eval: str | Path | None = None,
    recognizer_eval: str | Path | None = None,
    sample_rate: float = 0.10,
    max_accepted_sample: int = 64,
    high_confidence_threshold: float = 0.90,
    low_grid_threshold: float = 0.55,
) -> dict[str, Any]:
    """Export an audit-only queue for accepted/high-confidence FEN candidates.

    The exporter never mutates labels, corpus profiles, EPUB, HTML, or PGN.
    It intentionally over-samples risky accepted-looking rows so false
    positives can be found before they become trusted corpus evidence.
    """

    report_path = Path(smoke_report)
    payload = _read_json(report_path)
    profile_info = _load_profile_info(corpus_eval)
    recognizer_expected = _load_recognizer_expected_fens(recognizer_eval)
    records = _records_from_payload(payload)

    selected = select_audit_records(
        records,
        profile_info=profile_info,
        recognizer_expected=recognizer_expected,
        sample_rate=sample_rate,
        max_accepted_sample=max_accepted_sample,
        high_confidence_threshold=high_confidence_threshold,
        low_grid_threshold=low_grid_threshold,
    )

    target = Path(output_dir)
    crops_dir = target / "crops"
    overlays_dir = target / "overlays"
    target.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)

    crop_stats = _copy_crops_and_overlays(
        selected,
        crops_dir=crops_dir,
        overlays_dir=overlays_dir,
        crop_source_dirs=[Path(path) for path in (crop_source_dirs or []) if str(path).strip()],
    )

    summary = _build_summary(
        source_report=report_path,
        records=records,
        queue=selected,
        crop_stats=crop_stats,
    )
    summary["outputs"] = {
        "queue_json": "accepted_audit_queue.json",
        "queue_jsonl": "accepted_audit_queue.jsonl",
        "review_html": "accepted_audit_review.html",
        "summary_json": "accepted_audit_summary.json",
    }

    (target / "accepted_audit_queue.json").write_text(
        json.dumps({"summary": summary, "queue": selected}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (target / "accepted_audit_queue.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected),
        encoding="utf-8",
    )
    (target / "accepted_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (target / "accepted_audit_review.html").write_text(
        _render_review_html(summary, selected),
        encoding="utf-8",
    )
    return summary


def select_audit_records(
    records: list[dict[str, Any]],
    *,
    profile_info: dict[str, dict[str, Any]] | None = None,
    recognizer_expected: dict[str, str] | None = None,
    sample_rate: float = 0.10,
    max_accepted_sample: int = 64,
    high_confidence_threshold: float = 0.90,
    low_grid_threshold: float = 0.55,
) -> list[dict[str, Any]]:
    profile_info = profile_info or {}
    recognizer_expected = recognizer_expected or {}
    scored: list[dict[str, Any]] = []
    sampled_candidates: list[dict[str, Any]] = []

    for record in records:
        normalized = _normalize_record(record)
        expected_fen = _expected_fen_for_record(normalized, recognizer_expected)
        profile = _profile_for_record(normalized, profile_info)
        risk = score_record_risk(
            normalized,
            profile,
            expected_fen=expected_fen,
            high_confidence_threshold=high_confidence_threshold,
            low_grid_threshold=low_grid_threshold,
        )
        item = {**normalized, **risk, "profile": profile}
        item["square_diff_text"] = render_square_diff_text(item["id"], item.get("square_diffs") or [])

        if item["requires_review"]:
            item["audit_category"] = "requires_review"
            scored.append(item)
            continue

        if _is_accepted_or_high_confidence(item, high_confidence_threshold):
            if item["risk_level"] in {"critical", "high"} or _has_profile_audit_risk(item):
                item["audit_category"] = "accepted_high_risk"
                scored.append(item)
                continue
            sampled_candidates.append(item)

    sampled = _select_deterministic_sample(
        sampled_candidates,
        sample_rate=sample_rate,
        max_accepted_sample=max_accepted_sample,
    )
    for item in sampled:
        item["audit_category"] = "sampled_accepted"
        scored.append(item)

    scored.sort(key=_queue_sort_key)
    return scored


def score_record_risk(
    record: dict[str, Any],
    profile_info: dict[str, Any] | None = None,
    *,
    expected_fen: str | None = None,
    high_confidence_threshold: float = 0.90,
    low_grid_threshold: float = 0.55,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    square_diffs: list[dict[str, str]] = []

    record_id = str(record.get("id") or "")
    current_fen = _current_fen(record)
    expected = expected_fen or KNOWN_BAD_EXPECTED_FENS.get(record_id, "")
    if record_id in KNOWN_BAD_EXPECTED_FENS:
        score += 100
        reasons.append(f"known_bad_{record_id}")

    if current_fen and expected:
        try:
            square_diffs = compare_fen_placements(current_fen, expected)
        except ValueError as exc:
            score += 80
            reasons.append(f"square_diff_error:{type(exc).__name__}")
        else:
            if square_diffs:
                score += 90
                reasons.append("candidate_conflicts_with_expected_fen")
                for diff in square_diffs:
                    confusion_reason = _piece_confusion_reason(diff)
                    if confusion_reason:
                        reasons.append(confusion_reason)
                        score += _piece_confusion_score(confusion_reason)

    warnings = {str(item) for item in record.get("warnings") or []}
    for warning in sorted(warnings & CRITICAL_WARNINGS):
        score += 80
        reasons.append(warning)
    for warning in sorted(warnings & HIGH_RISK_WARNINGS):
        score += 45 if warning == "side_to_move_inferred" else 40
        reasons.append(warning)

    if _truthy(record.get("ai_approved")) and not _human_verified(record):
        score += 75
        reasons.append("ai_approved_without_human_verification")
    if _truthy(record.get("arbiter_approved")) and not _human_verified(record):
        score += 75
        reasons.append("arbiter_approved_without_human_verification")
    if str(record.get("review_opinion") or "") == "supports_candidate" and not _human_verified(record):
        score += 75
        reasons.append("review_opinion_supports_candidate_without_human_verification")

    method = str(record.get("method") or "")
    for marker in RECOVERY_METHOD_MARKERS:
        if marker in method:
            score += 35
            reasons.append(f"recovery_method:{marker}")
            break

    grid_confidence = _grid_confidence(record)
    if grid_confidence is not None and grid_confidence < low_grid_threshold:
        score += 35
        reasons.append("low_grid_confidence")

    profile_risk = _profile_risk_reasons(profile_info)
    if profile_risk:
        score += 30
        reasons.extend(profile_risk)

    confidence = _float(record.get("confidence"))
    if confidence is not None and confidence < high_confidence_threshold:
        score += 20
        reasons.append("accepted_below_high_confidence_threshold")
    if confidence is not None and abs(confidence - high_confidence_threshold) <= 0.015:
        score += 20
        reasons.append("near_high_confidence_threshold")

    if not str(record.get("filename") or record.get("crop_path") or "").strip():
        score += 20
        reasons.append("crop_missing")
    if not record.get("squares") and not record.get("square_details"):
        score += 20
        reasons.append("per_square_details_missing")

    risk_level = _risk_level(score, reasons)
    return {
        "risk_score": score,
        "risk_level": risk_level,
        "risk_reasons": sorted(dict.fromkeys(reasons)),
        "square_diffs": square_diffs,
    }


def stable_sample_percent(record: dict[str, Any]) -> float:
    key = "|".join(
        [
            str(record.get("case_id") or ""),
            str(record.get("page") or ""),
            str(record.get("filename") or ""),
            str(_current_fen(record) or ""),
            str(record.get("source") or ""),
        ]
    )
    digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def _records_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("records"), list):
        return [_normalize_record(record) for record in payload["records"] if isinstance(record, dict)]
    records: list[dict[str, Any]] = []
    for case in payload.get("cases") or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or case.get("case_id") or case.get("name") or "")
        for container_key in ("quality_report", "quality"):
            chess_fen = (case.get(container_key) or {}).get("chess_fen") or {}
            for record in chess_fen.get("records") or []:
                if isinstance(record, dict):
                    enriched = dict(record)
                    enriched.setdefault("case_id", case_id)
                    enriched.setdefault("source_report_case_id", case_id)
                    records.append(_normalize_record(enriched))
    return records


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    filename = str(normalized.get("filename") or Path(str(normalized.get("crop_path") or "")).name)
    page = normalized.get("page", normalized.get("page_number", normalized.get("page_index")))
    normalized["filename"] = filename
    normalized["page"] = page
    normalized["id"] = str(normalized.get("id") or normalized.get("diagram_id") or _record_id(page, filename))
    normalized["warnings"] = _collect_warnings(normalized)
    if not normalized.get("fen") and normalized.get("placement"):
        normalized["fen"] = f"{normalized.get('placement')} {normalized.get('side_to_move') or 'w'} - - 0 1"
    if not normalized.get("placement") and normalized.get("fen"):
        normalized["placement"] = str(normalized.get("fen") or "").split()[0]
    normalized["confidence"] = _float(normalized.get("confidence")) or 0.0
    normalized["requires_review"] = bool(normalized.get("requires_review"))
    normalized["source"] = str(normalized.get("source") or normalized.get("source_kind") or "")
    return normalized


def _record_id(page: Any, filename: str) -> str:
    try:
        page_int = int(page or 0)
    except (TypeError, ValueError):
        page_int = 0
    stem = Path(filename).stem if filename else "record"
    if stem.startswith("p") and "_d" in stem:
        return stem
    return f"p{page_int:03d}_{stem}"


def _collect_warnings(record: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for key in ("warnings", "fen_warnings", "recognizer_warnings", "review_warnings"):
        value = record.get(key)
        if isinstance(value, list):
            warnings.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            warnings.append(value.strip())
    return sorted(dict.fromkeys(warnings))


def _current_fen(record: dict[str, Any]) -> str:
    return str(record.get("fen") or record.get("candidate_fen") or "").strip()


def _expected_fen_for_record(record: dict[str, Any], recognizer_expected: dict[str, str]) -> str:
    return str(
        record.get("expected_fen")
        or record.get("manual_fen")
        or record.get("verified_fen")
        or recognizer_expected.get(str(record.get("id") or ""))
        or ""
    ).strip()


def _profile_for_record(record: dict[str, Any], profile_info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    profile_id = _profile_id(record)
    if profile_id and profile_id in profile_info:
        profile = dict(profile_info[profile_id])
        profile.setdefault("id", profile_id)
        return profile
    return {
        "id": profile_id or "",
        "status": "missing_eval",
        "seed_label_count": None,
        "exact_fen_accuracy": None,
        "false_positive_count": None,
    }


def _profile_id(record: dict[str, Any]) -> str:
    profile = record.get("profile")
    if isinstance(profile, dict):
        return str(profile.get("id") or profile.get("profile_id") or "").strip()
    return str(
        record.get("profile_id")
        or record.get("fen_profile_id")
        or record.get("template_profile")
        or record.get("profile")
        or ""
    ).strip()


def _profile_risk_reasons(profile: dict[str, Any] | None) -> list[str]:
    if not profile:
        return ["profile_eval_missing"]
    reasons: list[str] = []
    status = str(profile.get("status") or "").lower()
    if status in {"", "missing_eval", "missing", "unknown"}:
        reasons.append("profile_eval_missing")
    seed_count = _int(profile.get("seed_label_count") or profile.get("label_count") or profile.get("verified_label_count"))
    if seed_count is not None and seed_count < PROFILE_LOW_CORPUS_SEED_COUNT:
        reasons.append("low_corpus_seed_label_count")
    accuracy = _float(profile.get("exact_fen_accuracy") or profile.get("exact_accuracy"))
    if accuracy is not None and accuracy < PROFILE_LOW_CORPUS_EXACT_ACCURACY:
        reasons.append("low_corpus_exact_accuracy")
    false_positive_count = _int(profile.get("false_positive_count"))
    if false_positive_count is not None and false_positive_count > 0:
        reasons.append("profile_has_false_positives")
    return reasons


def _load_profile_info(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    payload = _read_json(Path(path))
    profiles: dict[str, dict[str, Any]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            profile_id = str(value.get("id") or value.get("profile_id") or value.get("name") or "").strip()
            if profile_id and (
                "seed_label_count" in value
                or "exact_fen_accuracy" in value
                or "false_positive_count" in value
                or "label_count" in value
            ):
                profiles[profile_id] = dict(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return profiles


def _load_recognizer_expected_fens(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    payload = _read_json(Path(path))
    expected: dict[str, str] = {}
    for case in payload.get("cases") or payload.get("results") or []:
        if not isinstance(case, dict):
            continue
        record_id = str(case.get("id") or case.get("record_id") or case.get("diagram_id") or "").strip()
        fen = str(case.get("expected_fen") or case.get("manual_fen") or case.get("verified_fen") or "").strip()
        if record_id and fen:
            expected[record_id] = fen
    return expected


def _select_deterministic_sample(
    records: list[dict[str, Any]],
    *,
    sample_rate: float,
    max_accepted_sample: int,
) -> list[dict[str, Any]]:
    if max_accepted_sample <= 0:
        return []
    sample_rate = min(1.0, max(0.0, float(sample_rate)))
    hashed = [record for record in records if stable_sample_percent(record) <= sample_rate]
    if len(hashed) >= max_accepted_sample:
        return sorted(hashed, key=_sample_sort_key)[:max_accepted_sample]

    selected_ids = {id(record) for record in hashed}
    remainder = [record for record in records if id(record) not in selected_ids]
    remainder.sort(key=_sample_sort_key)
    return sorted([*hashed, *remainder[: max_accepted_sample - len(hashed)]], key=_sample_sort_key)


def _sample_sort_key(record: dict[str, Any]) -> tuple[str, int, float, float, int, str, str]:
    return (
        _confidence_bucket(_float(record.get("confidence")) or 0.0),
        -int(record.get("risk_score") or 0),
        _float(record.get("confidence")) or 0.0,
        stable_sample_percent(record),
        _safe_int(record.get("page")),
        str(record.get("filename") or ""),
        str(record.get("id") or ""),
    )


def _queue_sort_key(record: dict[str, Any]) -> tuple[int, int, int, str]:
    category_priority = {
        "requires_review": 0,
        "accepted_high_risk": 1,
        "sampled_accepted": 2,
    }.get(str(record.get("audit_category") or ""), 9)
    risk_priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(record.get("risk_level") or ""), 4)
    return (category_priority, risk_priority, _safe_int(record.get("page")), str(record.get("id") or ""))


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 0.95:
        return "0.95-1.00"
    if confidence >= 0.90:
        return "0.90-0.949"
    if confidence >= 0.85:
        return "0.85-0.899"
    return "below-threshold"


def _is_accepted_or_high_confidence(record: dict[str, Any], threshold: float) -> bool:
    return bool(_current_fen(record)) and (
        not record.get("requires_review") or (_float(record.get("confidence")) or 0.0) >= threshold
    )


def _has_profile_audit_risk(record: dict[str, Any]) -> bool:
    return any(
        reason in {"profile_eval_missing", "low_corpus_seed_label_count", "low_corpus_exact_accuracy", "profile_has_false_positives"}
        for reason in record.get("risk_reasons") or []
    )


def _piece_confusion_reason(diff: dict[str, str]) -> str:
    candidate = str(diff.get("candidate_fen_char") or "")
    manual = str(diff.get("manual_fen_char") or "")
    pair = {candidate, manual}
    if pair <= {"Q", "R"} or pair <= {"q", "r"}:
        return "piece_confusion_queen_rook"
    if pair <= {"R", "B"} or pair <= {"r", "b"}:
        return "piece_confusion_rook_bishop"
    if pair <= {"N", "B"} or pair <= {"n", "b"}:
        return "piece_confusion_knight_bishop"
    if "empty" in pair:
        return "piece_confusion_empty_piece"
    if "P" in pair or "p" in pair:
        return "piece_confusion_pawn_piece"
    return "piece_confusion_piece_piece"


def _piece_confusion_score(reason: str) -> int:
    return {
        "piece_confusion_queen_rook": 35,
        "piece_confusion_rook_bishop": 30,
        "piece_confusion_knight_bishop": 30,
        "piece_confusion_pawn_piece": 25,
        "piece_confusion_empty_piece": 25,
    }.get(reason, 20)


def _risk_level(score: int, reasons: list[str]) -> str:
    if score >= 80 or any(reason.startswith("known_bad_") for reason in reasons):
        return "critical"
    if score >= 45:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def _grid_confidence(record: dict[str, Any]) -> float | None:
    for key in ("grid_confidence", "board_grid_confidence"):
        value = _float(record.get(key))
        if value is not None:
            return value
    for key in ("diagnostics", "board_diagnostics"):
        nested = record.get(key)
        if isinstance(nested, dict):
            value = _float(nested.get("grid_confidence") or nested.get("board_grid_confidence"))
            if value is not None:
                return value
    return None


def _copy_crops_and_overlays(
    queue: list[dict[str, Any]],
    *,
    crops_dir: Path,
    overlays_dir: Path,
    crop_source_dirs: list[Path],
) -> dict[str, int]:
    copied = 0
    missing = 0
    overlays = 0
    for item in queue:
        source = _resolve_crop_source(item, crop_source_dirs)
        filename = str(item.get("filename") or Path(str(item.get("crop_path") or "")).name)
        if not filename:
            missing += 1
            continue
        destination = crops_dir / filename
        if source and source.exists():
            if source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
            item["crop_path"] = f"crops/{filename}"
            copied += 1
            overlay_path = _write_grid_overlay(destination, overlays_dir / f"{Path(filename).stem}_grid.png", item)
            if overlay_path:
                item["overlay_path"] = f"overlays/{overlay_path.name}"
                overlays += 1
        else:
            item["crop_path"] = f"crops/{filename}" if filename else ""
            missing += 1
    return {"copied_count": copied, "missing_count": missing, "overlay_count": overlays}


def _resolve_crop_source(item: dict[str, Any], crop_source_dirs: list[Path]) -> Path | None:
    for key in ("source_crop_path", "absolute_crop_path", "crop_file", "crop_path"):
        value = str(item.get(key) or "").strip()
        if value:
            path = Path(value)
            if path.is_file():
                return path
    filename = str(item.get("filename") or "").strip()
    if not filename:
        return None
    for directory in crop_source_dirs:
        direct = directory / filename
        if direct.is_file():
            return direct
    return None


def _write_grid_overlay(source: Path, destination: Path, item: dict[str, Any]) -> Path | None:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        image = Image.open(source).convert("RGBA")
    except Exception:
        return None
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for index in range(9):
        x = round(index * width / 8)
        y = round(index * height / 8)
        draw.line((x, 0, x, height), fill=(255, 94, 0, 180), width=max(1, width // 180))
        draw.line((0, y, width, y), fill=(255, 94, 0, 180), width=max(1, height // 180))
    label = f"{item.get('id', '')} conf={item.get('confidence', '')}"
    draw.rectangle((0, 0, min(width, max(160, len(label) * 7)), 18), fill=(255, 255, 255, 210))
    draw.text((4, 3), label, fill=(20, 20, 20, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return destination


def _build_summary(
    *,
    source_report: Path,
    records: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    crop_stats: dict[str, int],
) -> dict[str, Any]:
    category_counts = Counter(str(item.get("audit_category") or "") for item in queue)
    level_counts = Counter(str(item.get("risk_level") or "") for item in queue)
    reason_counts = Counter(reason for item in queue for reason in item.get("risk_reasons") or [])
    return {
        "status": "ok",
        "source_report": str(source_report),
        "case_count": len({str(item.get("case_id") or "") for item in records}),
        "record_count": len(records),
        "requires_review_count": sum(1 for item in records if item.get("requires_review")),
        "accepted_count": sum(1 for item in records if _current_fen(item) and not item.get("requires_review")),
        "exported_count": len(queue),
        "accepted_sampled_count": category_counts.get("sampled_accepted", 0),
        "accepted_high_risk_count": category_counts.get("accepted_high_risk", 0),
        "critical_risk_count": level_counts.get("critical", 0),
        "high_risk_count": level_counts.get("high", 0),
        "medium_risk_count": level_counts.get("medium", 0),
        "missing_crop_count": crop_stats.get("missing_count", 0),
        "copied_crop_count": crop_stats.get("copied_count", 0),
        "overlay_count": crop_stats.get("overlay_count", 0),
        "audit_category_counts": dict(sorted(category_counts.items())),
        "risk_reason_counts": dict(sorted(reason_counts.items())),
    }


def _render_review_html(summary: dict[str, Any], queue: list[dict[str, Any]]) -> str:
    cards = "\n".join(_render_card(item) for item in queue)
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            "<title>KindleMaster Accepted FEN Audit</title>",
            "<style>",
            "body{margin:0;background:#f4efe6;color:#20251f;font-family:Georgia,'Times New Roman',serif;}",
            "main{max-width:1180px;margin:0 auto;padding:32px 20px 60px;}",
            "h1{font-size:34px;margin:0 0 8px}.meta{color:#596150;margin:0 0 24px}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:18px}",
            ".card{background:#fffaf2;border:1px solid #decbb2;border-radius:18px;padding:15px;box-shadow:0 18px 46px rgba(47,38,24,.10)}",
            ".card img{width:100%;aspect-ratio:1/1;object-fit:contain;background:#efe7d8;border-radius:12px;border:1px solid #ead8c2}",
            ".badge{display:inline-block;margin:4px 6px 8px 0;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:700;background:#e9eef7;color:#1f4e79}",
            ".critical{background:#ffe1dc;color:#a12716}.high{background:#fff0cb;color:#905100}.medium{background:#efe6ff;color:#57399a}.low{background:#e7f5e7;color:#176426}",
            "dl{display:grid;grid-template-columns:112px 1fr;gap:6px 10px;font-size:13px;margin:10px 0}dt{font-weight:700;color:#5d6557}dd{margin:0;word-break:break-word}",
            "code,textarea{font-family:'Cascadia Mono','Courier New',monospace;font-size:12px}code{background:#f1eadf;border-radius:5px;padding:2px 4px}",
            "textarea{width:100%;box-sizing:border-box;min-height:52px;border:1px solid #d8c5ae;border-radius:10px;background:#fffdf8;padding:8px}",
            "table{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}td,th{border-bottom:1px solid #ead8c2;padding:5px;text-align:left}",
            "</style>",
            "</head><body><main>",
            "<h1>Accepted / High-Confidence FEN Audit</h1>",
            f"<p class=\"meta\">{_html(summary.get('exported_count'))} audit cards from {_html(summary.get('record_count'))} records. Audit-only: these cards do not change labels or publication output.</p>",
            "<section class=\"grid\">",
            cards,
            "</section>",
            "</main></body></html>",
        ]
    )


def _render_card(item: dict[str, Any]) -> str:
    risk = str(item.get("risk_level") or "low")
    square_rows = "".join(
        "<tr><td>{square}</td><td>{manual}</td><td>{candidate}</td><td>{severity}</td></tr>".format(
            square=_html(diff.get("square")),
            manual=_html(diff.get("manual_piece")),
            candidate=_html(diff.get("candidate_piece")),
            severity=_html(diff.get("severity")),
        )
        for diff in item.get("square_diffs") or []
    )
    if not square_rows:
        square_rows = "<tr><td colspan=\"4\">No square diff evidence available</td></tr>"
    image_html = ""
    if item.get("crop_path"):
        image_html = f"<img src=\"{_attr(item.get('crop_path'))}\" alt=\"{_attr(item.get('id'))} crop\">"
    overlay_link = f"<a href=\"{_attr(item.get('overlay_path'))}\">grid overlay</a>" if item.get("overlay_path") else "-"
    return "\n".join(
        [
            "<article class=\"card\">",
            f"<span class=\"badge {_attr(risk)}\">{_html(risk)}</span>",
            f"<span class=\"badge\">{_html(item.get('audit_category'))}</span>",
            image_html,
            "<dl>",
            f"<dt>ID</dt><dd><code>{_html(item.get('id'))}</code></dd>",
            f"<dt>Page</dt><dd>{_html(item.get('page'))}</dd>",
            f"<dt>FEN</dt><dd><code>{_html(_current_fen(item) or '-')}</code></dd>",
            f"<dt>Confidence</dt><dd>{_html(item.get('confidence'))}</dd>",
            f"<dt>Method</dt><dd>{_html(item.get('method') or '-')}</dd>",
            f"<dt>Warnings</dt><dd>{_html(', '.join(item.get('warnings') or []) or '-')}</dd>",
            f"<dt>Risk reasons</dt><dd>{_html(', '.join(item.get('risk_reasons') or []) or '-')}</dd>",
            f"<dt>Overlay</dt><dd>{overlay_link}</dd>",
            "</dl>",
            "<table><thead><tr><th>Square</th><th>Manual/expected</th><th>Candidate</th><th>Severity</th></tr></thead>",
            f"<tbody>{square_rows}</tbody></table>",
            "<label>manual_label</label><textarea placeholder=\"correct_fen | false_positive | wrong_piece | wrong_side_to_move | bad_crop | uncertain\"></textarea>",
            "<label>manual_fen</label><textarea placeholder=\"Manual FEN after visual audit\"></textarea>",
            "<label>manual_notes</label><textarea placeholder=\"Notes\"></textarea>",
            "</article>",
        ]
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    return _int(value) or 0


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "approved"}
    return bool(value)


def _human_verified(record: dict[str, Any]) -> bool:
    if record.get("human_verified") is True:
        return True
    source = str(record.get("verification_source") or record.get("label_source") or "").strip().lower()
    return source in {"human", "human_visual", "manual", "manual_visual"}


def _html(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _attr(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export accepted/high-confidence chess FEN false-positive audit queue.")
    parser.add_argument("smoke_report", nargs="?", default="")
    parser.add_argument("--smoke-report", dest="smoke_report_option", default="")
    parser.add_argument("--output-dir", default="reports/chess_fen/accepted_audit/latest")
    parser.add_argument("--crop-source-dir", action="append", default=[])
    parser.add_argument("--corpus-eval", default="")
    parser.add_argument("--recognizer-eval", default="")
    parser.add_argument("--sample-rate", type=float, default=0.10)
    parser.add_argument("--max-accepted-sample", type=int, default=64)
    parser.add_argument("--high-confidence-threshold", type=float, default=0.90)
    parser.add_argument("--low-grid-threshold", type=float, default=0.55)
    args = parser.parse_args()
    source_report = args.smoke_report_option or args.smoke_report
    if not source_report:
        parser.error("smoke_report or --smoke-report is required")
    summary = export_chess_fen_accepted_audit(
        source_report,
        output_dir=args.output_dir,
        crop_source_dirs=args.crop_source_dir,
        corpus_eval=args.corpus_eval or None,
        recognizer_eval=args.recognizer_eval or None,
        sample_rate=args.sample_rate,
        max_accepted_sample=args.max_accepted_sample,
        high_confidence_threshold=args.high_confidence_threshold,
        low_grid_threshold=args.low_grid_threshold,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
