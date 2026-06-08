from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ENV_ENABLE_KEYS = ("KINDLEMASTER_AI_FEEDBACK_RECORD", "KINDLEMASTER_AI_LEARNING_RECORD")


def maybe_record_ai_quality_feedback(
    ai_report: Mapping[str, Any],
    *,
    original_filename: str,
    language: str,
    publication_profile: str | None,
    reports_dir: str | Path = "reports/ai-quality-feedback",
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    resolved_env = dict(os.environ if env is None else env)
    if not _enabled(resolved_env):
        return {"status": "skipped", "reason": "disabled"}

    learning = ai_report.get("learning_signals") if isinstance(ai_report.get("learning_signals"), dict) else {}
    record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": Path(original_filename or "").name,
        "language": language,
        "publication_profile": publication_profile or "",
        "ai_status": ai_report.get("status", ""),
        "providers": ai_report.get("provider", {}),
        "before_quality_score": ai_report.get("before_quality_score"),
        "after_quality_score": ai_report.get("after_quality_score"),
        "changed_fragment_count": ai_report.get("changed_fragment_count", 0),
        "changed_toc_entry_count": ai_report.get("changed_toc_entry_count", 0),
        "fallback_reasons": ai_report.get("fallback_reasons", []),
        "issue_candidates": _issue_candidates(ai_report),
        "learning_signals": learning,
        "evidence_only": True,
        "output_epub_changed": bool(ai_report.get("output_epub_changed", False)),
        "conversion_feedback": {
            "target_log": "reports/ml/feedback/conversion_feedback.jsonl",
            "requires_operator_acceptance": True,
            "online_learning": False,
        },
        "self_modifying_code_allowed": False,
    }
    target_dir = Path(reports_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = target_dir / "feedback_latest.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    snapshot_path = target_dir / f"feedback_{_timestamp_slug(record['generated_at'])}.json"
    snapshot_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "status": "recorded",
        "jsonl_path": str(jsonl_path),
        "snapshot_path": str(snapshot_path),
        "recommended_actions": learning.get("recommended_actions", []),
    }


def _issue_candidates(ai_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    magazine_review = ai_report.get("magazine_review") if isinstance(ai_report.get("magazine_review"), Mapping) else {}
    dense_review = ai_report.get("dense_handbook_review") if isinstance(ai_report.get("dense_handbook_review"), Mapping) else {}
    for key in (
        "suspected_bad_reading_order",
        "truncated_titles",
        "toc_missing_articles",
        "non_content_misclassified",
        "ocr_cleanup_candidates",
    ):
        candidates.extend(_candidate_rows(source="magazine_review", issue_type=key, rows=magazine_review.get(key)))
    for key in ("toc_debris", "heading_noise", "text_artifact_reviews", "oversized_chapters"):
        candidates.extend(_candidate_rows(source="dense_handbook_review", issue_type=key, rows=dense_review.get(key)))
    return candidates[:25]


def _candidate_rows(*, source: str, issue_type: str, rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        evidence = str(item.get("evidence") or item.get("suggested") or item.get("suggested_title") or "")[:500]
        href = str(item.get("href") or item.get("location") or "")
        result.append(
            {
                "source": source,
                "type": issue_type,
                "href": href,
                "label": str(item.get("label") or item.get("title") or "")[:220],
                "evidence": evidence,
                "confidence": _float_value(item.get("confidence")),
            }
        )
    return result


def _enabled(env: Mapping[str, str]) -> bool:
    return any(str(env.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in ENV_ENABLE_KEYS)


def _timestamp_slug(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace(".", "").replace("+", "Z")


def _float_value(value: Any) -> float:
    try:
        return round(max(0.0, min(float(value), 1.0)), 6)
    except (TypeError, ValueError):
        return 0.0
