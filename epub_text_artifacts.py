from __future__ import annotations

from dataclasses import dataclass
import re
import zipfile
from io import BytesIO
from typing import Any

from bs4 import BeautifulSoup


XHTML_MEMBER_RE = re.compile(r"(?i)(?:^|/)(?:chapter|section|content|text|nav|cover)[^/]*\.x?html$")
WORD_RE = re.compile(r"\b[^\W\d_]{2,}\b", re.UNICODE)
SPLIT_WORD_RE = re.compile(r"\b[^\W\d_]{2,24}(?:-|\u00ad|\u2010|\u2011)\s+[^\W\d_]{2,24}\b", re.UNICODE)
CAMEL_GLUE_RE = re.compile(r"[a-z][A-Z]")
LONG_ALPHA_RE = re.compile(r"^[^\W\d_]{29,}$", re.UNICODE)
OCR_JUNK_RE = re.compile(r"(?:\ufffd|\u00c4|\u0139|\u0102|\u00c3|[\u00e2][\u20ac][\u201c-\u201d])")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+[,.!?;:]")
MISSING_SPACE_AFTER_SENTENCE_RE = re.compile(r"(?<=[.!?])(?=[A-Z])")
URL_FRAGMENT_RE = re.compile(r"(?i)(?:https?\s*:\s*/?\s*/|www\s*\.\s+|doi\s*:\s+|https?://\s+)")
TECHNICAL_PLACEHOLDER_RE = re.compile(
    r"(?i)(?:__KM_PROTECTED_\d+__|\bTODO\b|\bFIXME\b|\b(?:Object|State)\s+\d+\b|\bRank\s*=\s*\d+\s*\*\s*\d+\b)"
)

ARTIFACT_KEYS = (
    "split_word_count",
    "glued_word_count",
    "ocr_junk_count",
    "punctuation_spacing_count",
    "suspicious_url_fragment_count",
    "technical_placeholder_count",
)

PASSED_RATE_THRESHOLD = 1.0
REVIEW_RATE_THRESHOLD = 4.0


@dataclass(frozen=True)
class TextArtifactMetrics:
    document_path: str
    word_count: int
    counts: dict[str, int]

    @property
    def total_artifact_count(self) -> int:
        return sum(self.counts.values())

    @property
    def artifact_rate_per_1000_words(self) -> float:
        if self.word_count <= 0:
            return 0.0
        return round((self.total_artifact_count / self.word_count) * 1000.0, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_path": self.document_path,
            "word_count": self.word_count,
            "artifact_count": self.total_artifact_count,
            "artifact_rate_per_1000_words": self.artifact_rate_per_1000_words,
            "counts": dict(self.counts),
        }


def analyze_epub_text_artifacts(epub_bytes: bytes) -> dict[str, Any]:
    """Return compact text-artifact metrics for the final reader-facing EPUB."""

    documents: list[TextArtifactMetrics] = []
    try:
        with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
            for name in sorted(archive.namelist()):
                if not _is_text_document(name):
                    continue
                try:
                    raw = archive.read(name)
                except KeyError:
                    continue
                text = _extract_visible_text(raw)
                metrics = _analyze_text(name, text)
                documents.append(metrics)
    except zipfile.BadZipFile:
        return _empty_payload(status="failed", message="EPUB archive is not readable.")

    word_count = sum(item.word_count for item in documents)
    counts = {key: sum(item.counts.get(key, 0) for item in documents) for key in ARTIFACT_KEYS}
    total = sum(counts.values())
    rate = round((total / word_count) * 1000.0, 3) if word_count else 0.0
    status = _status_for_rate(rate=rate, word_count=word_count, counts=counts)
    return {
        "status": status,
        "word_count": word_count,
        "artifact_count": total,
        "artifact_rate_per_1000_words": rate,
        "counts": counts,
        "thresholds": {
            "passed_max_artifacts_per_1000_words": PASSED_RATE_THRESHOLD,
            "review_max_artifacts_per_1000_words": REVIEW_RATE_THRESHOLD,
        },
        "per_document": [item.to_dict() for item in documents if item.word_count or item.total_artifact_count],
    }


def _empty_payload(*, status: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "word_count": 0,
        "artifact_count": 0,
        "artifact_rate_per_1000_words": 0.0,
        "counts": {key: 0 for key in ARTIFACT_KEYS},
        "thresholds": {
            "passed_max_artifacts_per_1000_words": PASSED_RATE_THRESHOLD,
            "review_max_artifacts_per_1000_words": REVIEW_RATE_THRESHOLD,
        },
        "per_document": [],
    }


def _is_text_document(name: str) -> bool:
    lowered = name.lower()
    if lowered.endswith(".xhtml") or lowered.endswith(".html"):
        return XHTML_MEMBER_RE.search(name) is not None
    return False


def _extract_visible_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup(["script", "style", "svg", "math"]):
        node.decompose()
    return soup.get_text(" ", strip=True)


def _analyze_text(document_path: str, text: str) -> TextArtifactMetrics:
    tokens = WORD_RE.findall(text or "")
    counts = {
        "split_word_count": len(SPLIT_WORD_RE.findall(text or "")),
        "glued_word_count": _count_glued_tokens(tokens),
        "ocr_junk_count": len(OCR_JUNK_RE.findall(text or "")),
        "punctuation_spacing_count": len(SPACE_BEFORE_PUNCT_RE.findall(text or ""))
        + len(MISSING_SPACE_AFTER_SENTENCE_RE.findall(text or "")),
        "suspicious_url_fragment_count": len(URL_FRAGMENT_RE.findall(text or "")),
        "technical_placeholder_count": len(TECHNICAL_PLACEHOLDER_RE.findall(text or "")),
    }
    return TextArtifactMetrics(document_path=document_path, word_count=len(tokens), counts=counts)


def _count_glued_tokens(tokens: list[str]) -> int:
    count = 0
    for token in tokens:
        if LONG_ALPHA_RE.match(token):
            count += 1
            continue
        transitions = len(CAMEL_GLUE_RE.findall(token))
        if transitions >= 2:
            count += 1
            continue
        if transitions == 1 and len(token) >= 14 and not token.endswith(("API", "HTTP", "URL")):
            count += 1
    return count


def _status_for_rate(*, rate: float, word_count: int, counts: dict[str, int]) -> str:
    if word_count <= 0:
        return "unavailable"
    hard_visible = counts.get("technical_placeholder_count", 0) + counts.get("ocr_junk_count", 0)
    if rate > REVIEW_RATE_THRESHOLD or hard_visible >= 3:
        return "failed"
    if rate > PASSED_RATE_THRESHOLD or hard_visible > 0:
        return "passed_with_warnings"
    return "passed"

