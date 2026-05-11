from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DECISION_RANKER_PATH = REPO_ROOT / "models" / "decision_ranker_v1.json"


def rank_manual_review_queue(
    items: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    source: str,
    model_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    raw_items = [dict(item) for item in (items or []) if isinstance(item, Mapping)]
    if not raw_items:
        return []
    model = load_decision_ranker(model_path)
    ranked = []
    for index, item in enumerate(raw_items):
        score, reason = score_review_item(item, source=source, model=model)
        priority = _priority_from_score(score)
        enriched = dict(item)
        enriched["ml_review_score"] = round(score, 6)
        enriched["ml_review_priority"] = priority
        enriched["ml_review_reason"] = reason
        enriched["ml_model_version"] = str(model.get("model_version", "") or "")
        ranked.append((priority, -score, index, enriched))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    return [item for *_prefix, item in ranked]


def score_review_item(
    item: Mapping[str, Any],
    *,
    source: str,
    model: Mapping[str, Any] | None = None,
) -> tuple[float, str]:
    ranker = dict(model or load_decision_ranker() or {})
    weights = dict(ranker.get("weights") or {})
    intercept = float(ranker.get("intercept", 0.0) or 0.0)
    signals = _review_signals(item, source=source)
    score = intercept
    contributions: list[tuple[str, float]] = []
    for signal, value in signals.items():
        contribution = float(weights.get(signal, 0.0) or 0.0) * float(value)
        if contribution:
            contributions.append((signal, contribution))
        score += contribution
    score = max(0.0, min(score, 1.0))
    reason = _review_reason(contributions)
    return score, reason


def load_decision_ranker(model_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(model_path or DEFAULT_DECISION_RANKER_PATH)
    if not path.exists():
        return _default_ranker()
    try:
        stat = path.stat()
    except OSError:
        return _default_ranker()
    loaded = _load_ranker_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    return loaded or _default_ranker()


@lru_cache(maxsize=8)
def _load_ranker_cached(path: str, _mtime_ns: int, _size: int) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("model_type") != "linear_review_priority":
        return None
    return payload


def _review_signals(item: Mapping[str, Any], *, source: str) -> dict[str, float]:
    text = " ".join(str(item.get(key, "") or "") for key in ("reason", "status", "action_taken", "link_status", "text"))
    text_lower = text.lower()
    confidence = _float_value(item.get("confidence", 0.0))
    review_flag = 1.0 if bool(item.get("review_flag", True)) else 0.0
    unresolved = 1.0 if "unresolved" in text_lower or item.get("unresolved_fragments") else 0.0
    broken = 1.0 if "broken" in text_lower or "missing" in text_lower or "fail" in text_lower else 0.0
    structural = 1.0 if source == "heading" and any(token in text_lower for token in ("toc", "heading", "candidate")) else 0.0
    reference = 1.0 if source == "reference" or any(token in text_lower for token in ("reference", "citation", "url")) else 0.0
    low_confidence = max(0.0, 1.0 - confidence) if confidence else 0.45
    return {
        "review_flag": review_flag,
        "low_confidence": low_confidence,
        "unresolved": unresolved,
        "broken_or_missing": broken,
        "heading_structural": structural,
        "reference_link": reference,
    }


def _priority_from_score(score: float) -> int:
    if score >= 0.78:
        return 1
    if score >= 0.48:
        return 2
    return 3


def _review_reason(contributions: list[tuple[str, float]]) -> str:
    if not contributions:
        return "low-risk review item; kept for audit visibility"
    top = sorted(contributions, key=lambda item: abs(item[1]), reverse=True)[:3]
    return "ranked by " + ", ".join(signal.replace("_", " ") for signal, _value in top)


def _default_ranker() -> dict[str, Any]:
    return {
        "model_version": "decision-ranker-v1-bootstrap",
        "model_type": "linear_review_priority",
        "intercept": 0.12,
        "weights": {
            "review_flag": 0.22,
            "low_confidence": 0.28,
            "unresolved": 0.25,
            "broken_or_missing": 0.25,
            "heading_structural": 0.12,
            "reference_link": 0.18,
        },
    }


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
