from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


LOW_DETERMINISTIC_CONFIDENCE_THRESHOLD = 0.75
MIN_AI_CONFIDENCE_THRESHOLD = 0.72

_NON_CONTENT_LABEL_PATTERNS = (
    r"^\s*ad\s*$",
    r"\badvertisement\b",
    r"\badvertorial\b",
    r"\bsponsored\b",
    r"\breklama\b",
    r"\bmaterial\s+sponsorowany\b",
    r"^\s*(fig|figure|rys|rycina|image|photo|table|tabela)\.?\s*\d+\b",
    r"^\s*(caption|chart|diagram|wykres|schemat)\s*[:.-]",
    r"^\s*(chart|diagram|wykres|schemat)\b",
)


@dataclass(frozen=True)
class DeterministicTocResult:
    entries: list[dict[str, Any]]
    confidence: float
    provider: str = "deterministic"


@dataclass(frozen=True)
class AiTocCandidate:
    label: str
    href: str
    confidence: float
    level: int | None = None


@dataclass(frozen=True)
class AiTocProviderResult:
    entries: list[AiTocCandidate]
    confidence: float
    estimated_cost_usd: float = 0.0
    provider: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AiTocDetectionResult:
    entries: list[dict[str, Any]]
    audit: dict[str, Any]


class AiTocProvider(Protocol):
    def detect_toc(self, context: dict[str, Any]) -> AiTocProviderResult:
        raise NotImplementedError


def detect_ai_toc_if_needed(
    deterministic: DeterministicTocResult,
    *,
    provider: AiTocProvider | None,
    context: dict[str, Any] | None,
    deterministic_threshold: float = LOW_DETERMINISTIC_CONFIDENCE_THRESHOLD,
    ai_threshold: float = MIN_AI_CONFIDENCE_THRESHOLD,
) -> AiTocDetectionResult:
    """Optionally replace a weak deterministic TOC with injected AI results.

    The function never creates a provider and never reads credentials. Calling
    code must inject a fake or real provider explicitly, which keeps tests and
    default runtime behavior offline-safe.
    """

    started = time.perf_counter()
    audit = _base_audit(deterministic, provider)

    if deterministic.confidence >= deterministic_threshold:
        audit.update(
            {
                "status": "skipped",
                "elapsed_ms": _elapsed_ms(started),
                "fallback_reason": "deterministic-confidence-high",
            }
        )
        return AiTocDetectionResult(entries=deterministic.entries, audit=audit)

    if provider is None:
        audit.update(
            {
                "status": "fallback",
                "elapsed_ms": _elapsed_ms(started),
                "fallback_reason": "provider-unavailable",
            }
        )
        return AiTocDetectionResult(entries=deterministic.entries, audit=audit)

    try:
        provider_result = provider.detect_toc(dict(context or {}))
    except Exception as exc:
        audit.update(
            {
                "status": "fallback",
                "elapsed_ms": _elapsed_ms(started),
                "fallback_reason": f"provider-failed: {exc}",
            }
        )
        return AiTocDetectionResult(entries=deterministic.entries, audit=audit)

    audit["provider"] = provider_result.provider or audit["provider"]
    audit["confidence"] = _clamp_confidence(provider_result.confidence)
    audit["estimated_cost_usd"] = _safe_cost(provider_result.estimated_cost_usd)

    accepted_entries, rejected_entries = _filter_ai_entries(provider_result.entries)
    audit["rejected_entries"] = rejected_entries

    if provider_result.confidence < ai_threshold:
        audit.update(
            {
                "status": "fallback",
                "elapsed_ms": _elapsed_ms(started),
                "fallback_reason": "ai-confidence-low",
            }
        )
        return AiTocDetectionResult(entries=deterministic.entries, audit=audit)

    if not accepted_entries:
        audit.update(
            {
                "status": "fallback",
                "elapsed_ms": _elapsed_ms(started),
                "fallback_reason": "no-usable-ai-entries",
            }
        )
        return AiTocDetectionResult(entries=deterministic.entries, audit=audit)

    audit.update(
        {
            "status": "accepted",
            "elapsed_ms": _elapsed_ms(started),
            "changed_entries": _changed_entries(deterministic.entries, accepted_entries),
            "fallback_reason": "",
        }
    )
    return AiTocDetectionResult(entries=accepted_entries, audit=audit)


def is_non_content_toc_label(label: str) -> bool:
    normalized = _normalize_label(label)
    if not normalized:
        return True
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _NON_CONTENT_LABEL_PATTERNS)


def _filter_ai_entries(entries: list[AiTocCandidate]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()

    for entry in entries:
        label = _normalize_label(entry.label)
        href = str(entry.href or "").strip()
        if is_non_content_toc_label(label):
            rejected.append({"label": label, "reason": "non-content-label"})
            continue
        if not href:
            rejected.append({"label": label, "reason": "missing-href"})
            continue
        key = (label.lower(), href)
        if key in seen_targets:
            rejected.append({"label": label, "reason": "duplicate"})
            continue
        seen_targets.add(key)

        payload: dict[str, Any] = {
            "label": label,
            "href": href,
            "confidence": _clamp_confidence(entry.confidence),
        }
        if entry.level is not None:
            payload["level"] = max(1, int(entry.level))
        accepted.append(payload)

    return accepted, rejected


def _base_audit(deterministic: DeterministicTocResult, provider: AiTocProvider | None) -> dict[str, Any]:
    return {
        "status": "pending",
        "confidence": _clamp_confidence(deterministic.confidence),
        "deterministic_confidence": _clamp_confidence(deterministic.confidence),
        "deterministic_provider": deterministic.provider,
        "provider": _provider_name(provider),
        "elapsed_ms": 0,
        "estimated_cost_usd": 0.0,
        "changed_entries": {"added": [], "removed": [], "kept": []},
        "rejected_entries": [],
        "fallback_reason": "",
    }


def _provider_name(provider: AiTocProvider | None) -> str:
    if provider is None:
        return "none"
    explicit_name = getattr(provider, "name", "")
    if explicit_name:
        return str(explicit_name)
    return provider.__class__.__name__


def _changed_entries(old_entries: list[dict[str, Any]], new_entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    old_labels = {_normalize_label(str(entry.get("label") or entry.get("title") or "")) for entry in old_entries}
    new_labels = {_normalize_label(str(entry.get("label") or entry.get("title") or "")) for entry in new_entries}
    old_labels.discard("")
    new_labels.discard("")
    return {
        "added": sorted(new_labels - old_labels),
        "removed": sorted(old_labels - new_labels),
        "kept": sorted(old_labels & new_labels),
    }


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "").replace("\n", " ")).strip(" -")


def _clamp_confidence(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _safe_cost(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, numeric)


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))
