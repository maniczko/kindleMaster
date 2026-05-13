from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Protocol

from epub_text_artifacts import ARTIFACT_KEYS, _analyze_text


AI_OCR_ARTIFACT_KEYS = (
    "split_word_count",
    "glued_word_count",
    "ocr_junk_count",
    "suspicious_url_fragment_count",
)
DEFAULT_MIN_CONFIDENCE = 0.75
MAX_SAFE_LENGTH_RATIO = 1.5
MIN_SAFE_LENGTH_RATIO = 0.5


@dataclass(frozen=True)
class SuspiciousOcrFragment:
    index: int
    text: str
    artifact_counts: dict[str, int]

    @property
    def artifact_count(self) -> int:
        return sum(self.artifact_counts.get(key, 0) for key in AI_OCR_ARTIFACT_KEYS)

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "text": self.text,
            "artifact_counts": dict(self.artifact_counts),
            "artifact_count": self.artifact_count,
        }


@dataclass(frozen=True)
class AIOcrCleanupProviderResult:
    text: str
    confidence: float
    estimated_cost: float = 0.0


class AIOcrCleanupProvider(Protocol):
    name: str

    def cleanup_fragment(self, fragment: str) -> AIOcrCleanupProviderResult:
        raise NotImplementedError


class NoOpAIOcrCleanupProvider:
    name = "noop"

    def cleanup_fragment(self, fragment: str) -> AIOcrCleanupProviderResult:
        return AIOcrCleanupProviderResult(text=fragment, confidence=1.0, estimated_cost=0.0)


@dataclass(frozen=True)
class AIOcrCleanupFragmentAudit:
    index: int
    before: str
    after: str
    artifact_counts: dict[str, int]
    confidence: float
    provider: str
    elapsed_ms: float
    estimated_cost: float
    accepted: bool
    fallback_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "before": self.before,
            "after": self.after,
            "artifact_counts": dict(self.artifact_counts),
            "confidence": self.confidence,
            "provider": self.provider,
            "elapsed_ms": self.elapsed_ms,
            "estimated_cost": self.estimated_cost,
            "accepted": self.accepted,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class AIOcrCleanupResult:
    text: str
    fragments: list[AIOcrCleanupFragmentAudit]
    provider: str
    elapsed_ms: float
    estimated_cost: float
    changed_fragment_count: int
    fallback_reason: str
    deterministic_output_preserved: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "fragments": [fragment.to_dict() for fragment in self.fragments],
            "provider": self.provider,
            "elapsed_ms": self.elapsed_ms,
            "estimated_cost": self.estimated_cost,
            "changed_fragment_count": self.changed_fragment_count,
            "fallback_reason": self.fallback_reason,
            "deterministic_output_preserved": self.deterministic_output_preserved,
        }


def select_suspicious_fragments(text: str) -> list[SuspiciousOcrFragment]:
    """Return only text fragments already flagged by deterministic artifact signals."""

    fragments: list[SuspiciousOcrFragment] = []
    for index, fragment in enumerate(_split_preserving_blank_lines(text)):
        if not fragment.strip():
            continue
        metrics = _analyze_text(f"fragment:{index}", fragment)
        counts = {key: int(metrics.counts.get(key, 0)) for key in ARTIFACT_KEYS}
        if any(counts.get(key, 0) > 0 for key in AI_OCR_ARTIFACT_KEYS):
            fragments.append(SuspiciousOcrFragment(index=index, text=fragment, artifact_counts=counts))
    return fragments


def cleanup_suspicious_ocr_fragments(
    text: str,
    *,
    provider: AIOcrCleanupProvider | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> AIOcrCleanupResult:
    started = time.perf_counter()
    parts = _split_preserving_blank_lines(text)
    suspicious = select_suspicious_fragments(text)
    provider_name = _provider_name(provider)

    if not suspicious:
        return _result(
            text=text,
            fragments=[],
            provider=provider_name,
            started=started,
            estimated_cost=0.0,
            fallback_reason="no-suspicious-fragments",
            deterministic_output_preserved=True,
        )

    if provider is None:
        audits = [
            _audit(
                fragment=fragment,
                after=fragment.text,
                confidence=0.0,
                provider=provider_name,
                elapsed_ms=0.0,
                estimated_cost=0.0,
                accepted=False,
                fallback_reason="provider-not-configured",
            )
            for fragment in suspicious
        ]
        return _result(
            text=text,
            fragments=audits,
            provider=provider_name,
            started=started,
            estimated_cost=0.0,
            fallback_reason="provider-not-configured",
            deterministic_output_preserved=True,
        )

    audits: list[AIOcrCleanupFragmentAudit] = []
    estimated_cost = 0.0
    for fragment in suspicious:
        fragment_started = time.perf_counter()
        try:
            provider_result = provider.cleanup_fragment(fragment.text)
        except Exception:
            audits.append(
                _audit(
                    fragment=fragment,
                    after=fragment.text,
                    confidence=0.0,
                    provider=provider_name,
                    elapsed_ms=_elapsed_ms(fragment_started),
                    estimated_cost=0.0,
                    accepted=False,
                    fallback_reason="provider-error",
                )
            )
            continue

        estimated_cost += max(float(provider_result.estimated_cost or 0.0), 0.0)
        fallback_reason = _rejection_reason(
            before=fragment.text,
            after=provider_result.text,
            confidence=provider_result.confidence,
            min_confidence=min_confidence,
        )
        accepted = fallback_reason == ""
        after = provider_result.text if accepted else fragment.text
        if accepted:
            parts[fragment.index] = after
        audits.append(
            _audit(
                fragment=fragment,
                after=after,
                confidence=float(provider_result.confidence),
                provider=provider_name,
                elapsed_ms=_elapsed_ms(fragment_started),
                estimated_cost=max(float(provider_result.estimated_cost or 0.0), 0.0),
                accepted=accepted,
                fallback_reason=fallback_reason,
            )
        )

    changed_count = sum(1 for audit in audits if audit.accepted and audit.before != audit.after)
    final_text = "".join(parts)
    return _result(
        text=final_text,
        fragments=audits,
        provider=provider_name,
        started=started,
        estimated_cost=estimated_cost,
        fallback_reason=_first_fallback_reason(audits),
        deterministic_output_preserved=final_text == text,
    )


def _split_preserving_blank_lines(text: str) -> list[str]:
    if not text:
        return [""]
    return re.split(r"(\n\s*\n+)", text)


def _provider_name(provider: AIOcrCleanupProvider | None) -> str:
    if provider is None:
        return "none"
    return str(getattr(provider, "name", provider.__class__.__name__) or provider.__class__.__name__)


def _rejection_reason(*, before: str, after: str, confidence: float, min_confidence: float) -> str:
    if confidence < min_confidence:
        return "low-confidence"
    if not after or not after.strip():
        return "empty-output"
    if after == before:
        return "unchanged"
    before_len = max(len(before), 1)
    ratio = len(after) / before_len
    if ratio < MIN_SAFE_LENGTH_RATIO or ratio > MAX_SAFE_LENGTH_RATIO:
        return "unsafe-length-change"
    return ""


def _audit(
    *,
    fragment: SuspiciousOcrFragment,
    after: str,
    confidence: float,
    provider: str,
    elapsed_ms: float,
    estimated_cost: float,
    accepted: bool,
    fallback_reason: str,
) -> AIOcrCleanupFragmentAudit:
    return AIOcrCleanupFragmentAudit(
        index=fragment.index,
        before=fragment.text,
        after=after,
        artifact_counts=dict(fragment.artifact_counts),
        confidence=confidence,
        provider=provider,
        elapsed_ms=elapsed_ms,
        estimated_cost=estimated_cost,
        accepted=accepted,
        fallback_reason=fallback_reason,
    )


def _result(
    *,
    text: str,
    fragments: list[AIOcrCleanupFragmentAudit],
    provider: str,
    started: float,
    estimated_cost: float,
    fallback_reason: str,
    deterministic_output_preserved: bool,
) -> AIOcrCleanupResult:
    return AIOcrCleanupResult(
        text=text,
        fragments=fragments,
        provider=provider,
        elapsed_ms=_elapsed_ms(started),
        estimated_cost=estimated_cost,
        changed_fragment_count=sum(1 for fragment in fragments if fragment.accepted and fragment.before != fragment.after),
        fallback_reason=fallback_reason,
        deterministic_output_preserved=deterministic_output_preserved,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _first_fallback_reason(fragments: list[AIOcrCleanupFragmentAudit]) -> str:
    for fragment in fragments:
        if fragment.fallback_reason:
            return fragment.fallback_reason
    return ""
