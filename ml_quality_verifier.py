from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_QUALITY_VERIFIER_PATH = REPO_ROOT / "models" / "quality_verifier_v1.json"


def build_ai_quality_verification(
    *,
    premium_scoring: Mapping[str, Any] | None,
    quality_report: Mapping[str, Any] | None = None,
    analysis: Mapping[str, Any] | None = None,
    quality_gate_mode: str = "draft",
    model_path: str | Path | None = None,
) -> dict[str, Any]:
    """Local, auditable quality verifier layered on top of premium scoring.

    This is intentionally deterministic. It does not mutate model weights at
    runtime; learning happens through feedback datasets and explicit retraining.
    """

    premium = dict(premium_scoring or {})
    report = dict(quality_report or {})
    source_analysis = dict(analysis or {})
    model = _load_quality_model(model_path)
    thresholds = dict(model.get("thresholds") or {})
    min_ready_score = _float_value(thresholds.get("min_ready_premium_score"), 7.0)
    min_premium_score = _float_value(thresholds.get("min_premium_ready_score"), 9.0)

    issues = [dict(item) for item in premium.get("issues", []) if isinstance(item, Mapping)]
    issue_counts = dict(premium.get("issue_counts") or {})
    blocker_count = int(issue_counts.get("blocker", 0) or sum(1 for item in issues if item.get("severity") == "blocker"))
    review_count = int(issue_counts.get("review", 0) or sum(1 for item in issues if item.get("severity") == "review"))
    warning_count = int(issue_counts.get("warning", 0) or sum(1 for item in issues if item.get("severity") == "warning"))
    premium_score = _float_value(premium.get("premium_score"), 0.0)
    kindle_ready = bool(premium.get("kindle_ready"))
    premium_ready = bool(premium.get("premium_ready"))
    technical_valid = bool(premium.get("technical_valid"))
    route_decision = dict(source_analysis.get("route_decision") or {})
    quality_selection = dict(report.get("quality_selection") or {})
    quality_selection_status = str(quality_selection.get("status", "") or "").strip().lower()

    reason_codes: list[str] = []
    if not premium:
        reason_codes.append("premium-scoring-missing")
    if not technical_valid:
        reason_codes.append("technical-invalid")
    if blocker_count:
        reason_codes.append("premium-blockers")
    if premium_score < min_ready_score:
        reason_codes.append("premium-score-below-ready-threshold")
    if review_count:
        reason_codes.append("manual-review-needed")
    if not kindle_ready:
        reason_codes.append("kindle-not-ready")
    if quality_selection_status == "rejected":
        reason_codes.append("quality-regression-prevented")

    if not premium:
        decision = "review"
        status = "not_reported"
        confidence = 0.0
    elif blocker_count or not technical_valid or premium_score < min_ready_score or not kindle_ready:
        decision = "block"
        status = "failed"
        confidence = min(0.99, 0.78 + blocker_count * 0.04 + max(0.0, min_ready_score - premium_score) * 0.03)
    elif premium_ready and premium_score >= min_premium_score and warning_count == 0 and review_count == 0:
        decision = "ready"
        status = "passed"
        confidence = min(0.98, 0.75 + (premium_score - min_ready_score) * 0.04)
    else:
        decision = "review"
        status = "passed_with_warnings"
        confidence = min(0.94, 0.62 + premium_score * 0.025)
        reason_codes.append("quality-review")

    features = {
        "premium_score": round(premium_score, 3),
        "technical_valid": technical_valid,
        "kindle_ready": kindle_ready,
        "premium_ready": premium_ready,
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "review_count": review_count,
        "validation_status": str(report.get("validation_status", "") or ""),
        "profile": str(source_analysis.get("profile", "") or ""),
        "route_model_mode": str(route_decision.get("mode", "") or ""),
        "route_override_used": bool(route_decision.get("override_used")),
        "quality_selection_status": quality_selection_status,
        "quality_selection_score_delta": _float_value(quality_selection.get("score_delta"), 0.0),
        "quality_selection_blocker_delta": int(_float_value(quality_selection.get("blocker_delta"), 0.0)),
    }
    top_issues = _top_issues(issues)
    return {
        "status": status,
        "decision": decision,
        "confidence": round(confidence, 6),
        "model_version": str(model.get("model_version", "quality-verifier-v1-bootstrap")),
        "model_type": str(model.get("model_type", "local_quality_policy")),
        "available": bool(model),
        "quality_gate_mode": str(quality_gate_mode or "draft"),
        "features": features,
        "features_hash": _features_hash(features),
        "quality_selection": _compact_quality_selection(quality_selection),
        "reason_codes": _dedupe(reason_codes),
        "top_issues": top_issues,
        "summary": _summary(decision=decision, premium_score=premium_score, blocker_count=blocker_count),
    }


def _load_quality_model(model_path: str | Path | None) -> dict[str, Any]:
    path = Path(model_path or DEFAULT_QUALITY_VERIFIER_PATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "model_version": "quality-verifier-v1-bootstrap",
            "model_type": "local_quality_policy",
            "thresholds": {"min_ready_premium_score": 7.0, "min_premium_ready_score": 9.0},
        }
    return payload if isinstance(payload, dict) else {}


def _top_issues(issues: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    severity_rank = {"blocker": 0, "failed": 0, "warning": 1, "review": 2}
    rows = sorted(
        issues,
        key=lambda item: (
            severity_rank.get(str(item.get("severity", "")).lower(), 3),
            str(item.get("code", "")),
        ),
    )
    return [
        {
            "severity": str(item.get("severity", "") or ""),
            "code": str(item.get("code", "") or ""),
            "message": str(item.get("message", "") or ""),
            "source": str(item.get("source", "") or ""),
            "file": str(item.get("file", "") or ""),
            "suggested_action": str(item.get("suggested_action", "") or ""),
        }
        for item in rows[:limit]
    ]


def _compact_quality_selection(quality_selection: Mapping[str, Any]) -> dict[str, Any]:
    if not quality_selection:
        return {}
    return {
        "status": str(quality_selection.get("status", "") or ""),
        "selected_candidate": str(
            quality_selection.get("selected_candidate") or quality_selection.get("selected_stage") or ""
        ),
        "rejected_candidate": str(
            quality_selection.get("rejected_candidate") or quality_selection.get("rejected_stage") or ""
        ),
        "score_delta": _float_value(quality_selection.get("score_delta"), 0.0),
        "blocker_delta": int(_float_value(quality_selection.get("blocker_delta"), 0.0)),
        "reason_codes": list(quality_selection.get("reason_codes") or []),
    }


def _features_hash(features: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(features), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _summary(*, decision: str, premium_score: float, blocker_count: int) -> str:
    if decision == "block":
        return f"Local quality verifier blocks publication; premium score {premium_score:g}/10, blockers {blocker_count}."
    if decision == "ready":
        return f"Local quality verifier accepts publication; premium score {premium_score:g}/10."
    return f"Local quality verifier requires review; premium score {premium_score:g}/10."
