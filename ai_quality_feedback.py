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
        "learning_signals": learning,
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


def _enabled(env: Mapping[str, str]) -> bool:
    return any(str(env.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in ENV_ENABLE_KEYS)


def _timestamp_slug(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace(".", "").replace("+", "Z")
