from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import re
import zipfile
from io import BytesIO
from typing import Any

from bs4 import BeautifulSoup

try:
    from wordfreq import zipf_frequency as _zipf_frequency
except Exception:  # pragma: no cover - optional dependency fallback
    _zipf_frequency = None


XHTML_MEMBER_RE = re.compile(r"(?i)(?:^|/)(?:chapter|section|content|text|nav|cover)[^/]*\.x?html$")
WORD_RE = re.compile(r"\b[^\W\d_]{2,}\b", re.UNICODE)
SPLIT_WORD_RE = re.compile(r"\b[^\W\d_]{2,24}(?:-|\u00ad|\u2010|\u2011)\s+[^\W\d_]{2,24}\b", re.UNICODE)
CAMEL_GLUE_RE = re.compile(r"[a-z][A-Z]")
LONG_ALPHA_RE = re.compile(r"^[^\W\d_]{29,}$", re.UNICODE)
LOWERCASE_CONNECTOR_GLUE_WORDS = ("oraz", "czy", "ale", "dla", "pod", "nad", "przez", "wobec", "i", "a", "w", "z", "u", "o")
OCR_JUNK_RE = re.compile(r"(?:\ufffd|\u00c4|\u0139|\u0102|\u00c3|[\u00e2][\u20ac][\u201c-\u201d])")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+[,.!?;:]")
MISSING_SPACE_AFTER_SENTENCE_RE = re.compile(r"(?<=[.!?])(?=[A-Z])")
URL_FRAGMENT_RE = re.compile(r"(?i)(?:https?\s*:\s*/?\s*/|www\s*\.\s+|doi\s*:\s+|https?://\s+)")
TECHNICAL_PLACEHOLDER_RE = re.compile(
    r"(?i)(?:__KM_PROTECTED_\d+__|\bTODO\b|\bFIXME\b)"
)
LAYOUT_PLACEHOLDER_RE = re.compile(r"(?i)\b(?:Object|State)\s+\d+\b|\bRank\s*=\s*\d+\s*\*\s*\d+\b")
KNOWN_CAMEL_DOMAIN_TOKENS = {
    "OrderRequest",
    "OrderResponse",
    "InvoiceDetailRequest",
    "InvoiceResponse",
}
POLISH_DIACRITIC_RE = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")

ARTIFACT_KEYS = (
    "split_word_count",
    "glued_word_count",
    "ocr_junk_count",
    "punctuation_spacing_count",
    "suspicious_url_fragment_count",
    "technical_placeholder_count",
)
IGNORED_ARTIFACT_KEYS = (
    "structural_punctuation_review_count",
)

PASSED_RATE_THRESHOLD = 1.0
REVIEW_RATE_THRESHOLD = 4.0
_TEXT_ARTIFACT_CACHE: dict[str, dict[str, Any]] = {}

PRODUCTIVE_PREFIX_PARTS = {
    "auto",
    "cyber",
    "euro",
    "mikro",
    "multi",
    "neuro",
    "super",
    "termo",
}
INFLECTION_LIKE_RIGHT_PARTS = (
    "aniu",
    "eniu",
    "ingu",
    "niem",
    "owych",
    "owego",
    "owej",
    "owscy",
    "owska",
    "owską",
    "owskie",
    "owskiej",
    "owskiego",
    "owskim",
    "owskich",
)


@dataclass(frozen=True)
class TextArtifactMetrics:
    document_path: str
    word_count: int
    counts: dict[str, int]
    ignored_counts: dict[str, int] | None = None

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
            "ignored_counts": dict(self.ignored_counts or {}),
        }


def analyze_epub_text_artifacts(epub_bytes: bytes) -> dict[str, Any]:
    """Return compact text-artifact metrics for the final reader-facing EPUB."""

    cache_key = hashlib.sha256(epub_bytes).hexdigest()
    cached = _TEXT_ARTIFACT_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)

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
        payload = _empty_payload(status="failed", message="EPUB archive is not readable.")
        _TEXT_ARTIFACT_CACHE[cache_key] = copy.deepcopy(payload)
        return payload

    word_count = sum(item.word_count for item in documents)
    counts = {key: sum(item.counts.get(key, 0) for item in documents) for key in ARTIFACT_KEYS}
    ignored_counts = {
        key: sum((item.ignored_counts or {}).get(key, 0) for item in documents)
        for key in IGNORED_ARTIFACT_KEYS
    }
    total = sum(counts.values())
    rate = round((total / word_count) * 1000.0, 3) if word_count else 0.0
    status = _status_for_rate(rate=rate, word_count=word_count, counts=counts)
    payload = {
        "status": status,
        "word_count": word_count,
        "artifact_count": total,
        "artifact_rate_per_1000_words": rate,
        "counts": counts,
        "ignored_counts": ignored_counts,
        "thresholds": {
            "passed_max_artifacts_per_1000_words": PASSED_RATE_THRESHOLD,
            "review_max_artifacts_per_1000_words": REVIEW_RATE_THRESHOLD,
        },
        "per_document": [item.to_dict() for item in documents if item.word_count or item.total_artifact_count],
    }
    _TEXT_ARTIFACT_CACHE[cache_key] = copy.deepcopy(payload)
    return payload


def _empty_payload(*, status: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "word_count": 0,
        "artifact_count": 0,
        "artifact_rate_per_1000_words": 0.0,
        "counts": {key: 0 for key in ARTIFACT_KEYS},
        "ignored_counts": {key: 0 for key in IGNORED_ARTIFACT_KEYS},
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
    dense_handbook_context = _looks_like_dense_handbook_text(text or "")
    layout_placeholder_count = len(LAYOUT_PLACEHOLDER_RE.findall(text or ""))
    technical_placeholder_count = len(TECHNICAL_PLACEHOLDER_RE.findall(text or ""))
    if not dense_handbook_context:
        technical_placeholder_count += layout_placeholder_count
    punctuation_spacing_count, structural_punctuation_review_count = _count_punctuation_spacing(
        text or "",
        dense_handbook_context=dense_handbook_context,
    )
    counts = {
        "split_word_count": len(SPLIT_WORD_RE.findall(text or "")),
        "glued_word_count": _count_glued_tokens(tokens),
        "ocr_junk_count": len(OCR_JUNK_RE.findall(text or "")),
        "punctuation_spacing_count": punctuation_spacing_count,
        "suspicious_url_fragment_count": len(URL_FRAGMENT_RE.findall(text or "")),
        "technical_placeholder_count": technical_placeholder_count,
    }
    ignored_counts = {
        "structural_punctuation_review_count": structural_punctuation_review_count,
    }
    return TextArtifactMetrics(document_path=document_path, word_count=len(tokens), counts=counts, ignored_counts=ignored_counts)


def _looks_like_dense_handbook_text(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    if len(normalized) < 800:
        return False
    signals = (
        "business analysis",
        "requirements",
        "stakeholder",
        "solution evaluation",
        "strategy analysis",
        "techniques",
        "appendix",
        "glossary",
    )
    return sum(1 for signal in signals if signal in normalized) >= 3


def _count_punctuation_spacing(text: str, *, dense_handbook_context: bool) -> tuple[int, int]:
    reader_artifacts = 0
    structural_review = 0
    for match in SPACE_BEFORE_PUNCT_RE.finditer(text or ""):
        if dense_handbook_context and _looks_like_dense_structural_punctuation(text, match.start(), match.end()):
            structural_review += 1
            continue
        reader_artifacts += 1
    for match in MISSING_SPACE_AFTER_SENTENCE_RE.finditer(text or ""):
        if dense_handbook_context and _looks_like_dense_missing_space_false_positive(text, match.start(), match.end()):
            structural_review += 1
            continue
        reader_artifacts += 1
    return reader_artifacts, structural_review


def _looks_like_dense_structural_punctuation(text: str, start: int, end: int) -> bool:
    marker = text[start:end].strip()
    if marker != ".":
        return False
    after = text[end : end + 80]
    before = text[max(0, start - 140) : start]
    if re.match(r"^\d+\s+[A-Z][A-Za-z]+(?:\s+(?:and|of|for|the|[A-Z][A-Za-z]+)){0,7}\b", after):
        return True
    if re.search(r"(?i)(?:purpose|description|inputs|outputs|elements|guidelines/tools|guidelines and tools|techniques|stakeholders|usage considerations)\s*$", before):
        return True
    return False


def _looks_like_dense_missing_space_false_positive(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 16) : start + 1]
    after = text[end : end + 32]
    if re.search(r"\b(?:[A-Z]\.){1,4}$", before) and re.match(r"^[A-Z](?:\.|\b)", after):
        return True
    if re.search(r"\b(?:Fig|No|Vol|Ch|Sec)\.$", before) and re.match(r"^[A-Z0-9]", after):
        return True
    return False


def _count_glued_tokens(tokens: list[str]) -> int:
    count = 0
    for token in tokens:
        if token in KNOWN_CAMEL_DOMAIN_TOKENS:
            continue
        if _looks_like_glued_token(token):
            count += 1
            continue
        transitions = len(CAMEL_GLUE_RE.findall(token))
        if transitions >= 2:
            count += 1
            continue
        if transitions == 1 and len(token) >= 14 and not token.endswith(("API", "HTTP", "URL")):
            count += 1
    return count


def _looks_like_glued_token(token: str) -> bool:
    if token in KNOWN_CAMEL_DOMAIN_TOKENS:
        return False
    if POLISH_DIACRITIC_RE.search(token) and not CAMEL_GLUE_RE.search(token) and len(token) < 18:
        return False
    if LONG_ALPHA_RE.match(token):
        return True
    if len(token) < 12 or len(token) > 36:
        return False
    if not token.isalpha() or token.isupper():
        return False

    lowered = token.lower()
    whole_score = _lexical_zipf(lowered)
    if whole_score >= 1.85:
        return False
    if _has_confident_two_word_split(lowered, whole_score=whole_score):
        return True
    if _has_confident_connector_split(lowered, whole_score=whole_score):
        return True

    for suffix_len in (1, 2, 3):
        stem = lowered[:-suffix_len]
        suffix = lowered[-suffix_len:]
        if len(stem) < 10:
            continue
        if not _should_try_noisy_stem_split(suffix=suffix, whole_score=whole_score):
            continue
        if _has_confident_two_word_split(stem, whole_score=min(whole_score, _lexical_zipf(stem))):
            return True
        if _has_confident_connector_split(stem, whole_score=min(whole_score, _lexical_zipf(stem))):
            return True
    return False


def _has_confident_two_word_split(token: str, *, whole_score: float) -> bool:
    for split_index in range(4, len(token) - 3):
        left = token[:split_index]
        right = token[split_index:]
        if _looks_like_morphological_or_compound_split(left, right):
            continue
        left_score = _lexical_zipf(left)
        right_score = _lexical_zipf(right)
        if left_score >= 3.0 and right_score >= 3.0 and whole_score <= 1.7:
            return True
        if min(left_score, right_score) >= 2.65 and (left_score + right_score) >= 7.0 and whole_score <= 1.25:
            return True
    return False


def _has_confident_connector_split(token: str, *, whole_score: float) -> bool:
    if whole_score > 1.7:
        return False
    for connector in LOWERCASE_CONNECTOR_GLUE_WORDS:
        if len(connector) == 1 and connector != "i":
            continue
        start = 4
        while True:
            index = token.find(connector, start)
            if index < 0:
                break
            left = token[:index]
            right = token[index + len(connector) :]
            start = index + 1
            if len(left) < 4 or len(right) < 4:
                continue
            left_score = _lexical_zipf(left)
            right_score = _lexical_zipf(right)
            if left_score >= 3.0 and right_score >= 2.45:
                return True
    return False


def _looks_like_morphological_or_compound_split(left: str, right: str) -> bool:
    """Avoid treating ordinary inflectional/compound morphology as PDF glue."""

    if left in PRODUCTIVE_PREFIX_PARTS:
        return True
    if right in INFLECTION_LIKE_RIGHT_PARTS:
        return True
    if right.startswith(("owsk", "oweg", "owej", "owym", "owych")):
        return True
    if len(right) <= 4 and right in {"abym", "aniu", "eniu", "niem"}:
        return True
    return False


def _should_try_noisy_stem_split(*, suffix: str, whole_score: float) -> bool:
    if whole_score > 0.05:
        return False
    return suffix in {"wn"}


def _lexical_zipf(word: str) -> float:
    if not word or _zipf_frequency is None:
        return 0.0
    try:
        return max(_zipf_frequency(word.lower(), "pl"), _zipf_frequency(word.lower(), "en"))
    except Exception:
        return 0.0


def _status_for_rate(*, rate: float, word_count: int, counts: dict[str, int]) -> str:
    if word_count <= 0:
        return "unavailable"
    hard_visible = counts.get("technical_placeholder_count", 0) + counts.get("ocr_junk_count", 0)
    if rate > REVIEW_RATE_THRESHOLD or hard_visible >= 3:
        return "failed"
    if rate > PASSED_RATE_THRESHOLD or hard_visible > 0:
        return "passed_with_warnings"
    return "passed"
