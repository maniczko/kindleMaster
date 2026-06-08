from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ai_ocr_cleanup import AIOcrCleanupProviderResult
from ai_toc_detection import AiTocCandidate, AiTocProviderResult


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_INPUT_CHARS = 8_000
MAX_MAGAZINE_TOC_ENTRIES = 80
MAX_MAGAZINE_ARTICLES = 60
MAX_MAGAZINE_FRAGMENTS = 24
MAX_MAGAZINE_ISSUES = 24
MAX_MAGAZINE_IMAGE_ROWS = 40
MAX_MAGAZINE_TEXT_CHARS = 420
MAX_DENSE_TOC_ENTRIES = 80
MAX_DENSE_HEADINGS = 40
MAX_DENSE_FRAGMENTS = 16
MAX_DENSE_CHAPTER_STATS = 24
MAX_DENSE_TEXT_CHARS = 420

QUALITY_ENABLE_KEYS = ("KINDLEMASTER_OPENAI_QUALITY", "KINDLEMASTER_AI_QUALITY_OPENAI")
ENV_FILES = (".env.local", ".env")

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass(frozen=True)
class OpenAIQualityConfig:
    api_key: str
    model: str = DEFAULT_OPENAI_MODEL
    base_url: str = DEFAULT_OPENAI_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS


class OpenAIQualityProvider:
    """Opt-in OpenAI-backed provider for KindleMaster quality intelligence.

    This provider returns proposed OCR/TOC improvements as audit evidence. It
    does not rewrite EPUB bytes by itself; the caller still applies existing
    deterministic safety gates before any output can change.
    """

    name = "openai-quality"

    def __init__(self, config: OpenAIQualityConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _http_transport

    def cleanup_fragment(self, fragment: str) -> AIOcrCleanupProviderResult:
        clipped = _clip(fragment, self.config.max_input_chars)
        payload = self._responses_payload(
            name="kindlemaster_ocr_cleanup",
            schema=_ocr_schema(),
            instructions=(
                "You are a conservative EPUB OCR cleanup reviewer. Fix only clear OCR artifacts, "
                "split words, glued words, broken punctuation spacing, and obvious encoding noise. "
                "Preserve language, meaning, URLs, names, citations, numbers, headings, and paragraph order. "
                "Do not summarize, translate, add facts, or remove content. Return JSON only."
            ),
            user_payload={"fragment": clipped},
        )
        result = self._call(payload)
        parsed = _extract_json(result)
        return AIOcrCleanupProviderResult(
            text=str(parsed.get("text") or clipped),
            confidence=_clamp(parsed.get("confidence")),
            estimated_cost=_estimated_cost(result),
        )

    def detect_toc(self, context: dict[str, Any]) -> AiTocProviderResult:
        toc_entries = [
            {
                "label": str(entry.get("label") or entry.get("title") or "")[:220],
                "href": str(entry.get("href") or ""),
            }
            for entry in (context.get("toc_entries") or [])[:80]
            if isinstance(entry, dict)
        ]
        payload = self._responses_payload(
            name="kindlemaster_toc_recovery",
            schema=_toc_schema(),
            instructions=(
                "You are a conservative EPUB navigation reviewer. Build a cleaner TOC only from the provided "
                "candidate entries and href values. Keep real chapter or section titles. Reject ads, galleries, "
                "figure/table captions, page debris, generic one-word labels, and article leads. Never invent hrefs. "
                "If evidence is weak, return low confidence and keep few entries. Return JSON only."
            ),
            user_payload={
                "toc_entries": toc_entries,
                "sample_text": _clip(str(context.get("sample_text") or ""), self.config.max_input_chars),
                "premium_scoring": _compact_scoring(context.get("premium_scoring")),
            },
        )
        result = self._call(payload)
        parsed = _extract_json(result)
        entries: list[AiTocCandidate] = []
        allowed_hrefs = {entry["href"] for entry in toc_entries if entry.get("href")}
        for item in parsed.get("entries") or []:
            if not isinstance(item, dict):
                continue
            href = str(item.get("href") or "").strip()
            label = str(item.get("label") or "").strip()
            if not href or href not in allowed_hrefs or not label:
                continue
            entries.append(
                AiTocCandidate(
                    label=label,
                    href=href,
                    confidence=_clamp(item.get("confidence")),
                    level=_coerce_level(item.get("level")),
                )
            )
        return AiTocProviderResult(
            entries=entries,
            confidence=_clamp(parsed.get("confidence")),
            estimated_cost_usd=_estimated_cost(result),
            provider=self.name,
            metadata={
                "model": self.config.model,
                "usage": result.get("usage", {}),
                "reasoning": str(parsed.get("reasoning") or "")[:1000],
            },
        )

    def review_magazine(self, context: dict[str, Any]) -> dict[str, Any]:
        compact_context = _compact_magazine_review_context(context, self.config.max_input_chars)
        payload = self._responses_payload(
            name="kindlemaster_magazine_review",
            schema=_magazine_review_schema(),
            instructions=(
                "You are a conservative EPUB magazine quality reviewer. Use only the compact evidence provided: "
                "suspicious OCR or flow fragments, TOC entries, article summaries, image metrics, and premium issues. "
                "Do not ask for or infer from full EPUB/PDF bytes. Return evidence only; never propose byte rewrites. "
                "Only reuse hrefs and fragment indexes that appear in the context. Flag likely reading-order, title, "
                "TOC coverage, non-content classification, and OCR cleanup issues. Return JSON only."
            ),
            user_payload=compact_context,
        )
        result = self._call(payload)
        parsed = _extract_json(result)
        review = _sanitize_magazine_review(parsed, compact_context)
        review["estimated_cost_usd"] = _estimated_cost(result)
        review["provider"] = self.name
        review["metadata"] = {
            "model": self.config.model,
            "usage": result.get("usage", {}),
        }
        return review

    def review_dense_handbook(self, context: dict[str, Any]) -> dict[str, Any]:
        compact_context = _compact_dense_handbook_review_context(context, self.config.max_input_chars)
        payload = self._responses_payload(
            name="kindlemaster_dense_handbook_review",
            schema=_dense_handbook_review_schema(),
            instructions=(
                "You are a conservative EPUB dense-handbook quality reviewer. Use only compact evidence provided: "
                "TOC entries, heading-noise samples, text-artifact snippets, chapter stats, and premium issues. "
                "Do not ask for or infer from full EPUB/PDF bytes. Return evidence only; never propose byte rewrites. "
                "Only reuse hrefs and fragment indexes that appear in the context. Flag likely TOC debris, heading "
                "noise, real OCR artifacts, and overly large navigation sections. Return JSON only."
            ),
            user_payload=compact_context,
        )
        result = self._call(payload)
        parsed = _extract_json(result)
        review = _sanitize_dense_handbook_review(parsed, compact_context)
        review["estimated_cost_usd"] = _estimated_cost(result)
        review["provider"] = self.name
        review["metadata"] = {
            "model": self.config.model,
            "usage": result.get("usage", {}),
        }
        return review

    def _responses_payload(self, *, name: str, schema: dict[str, Any], instructions: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "input": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/responses"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        return self._transport(url, headers, payload, self.config.timeout_seconds)


def build_openai_quality_provider_from_env(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    transport: Transport | None = None,
) -> OpenAIQualityProvider | None:
    resolved = _runtime_env(env=env, cwd=cwd)
    if not _openai_quality_enabled(resolved):
        return None
    api_key = str(resolved.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return None
    return OpenAIQualityProvider(
        OpenAIQualityConfig(
            api_key=api_key,
            model=str(resolved.get("KINDLEMASTER_OPENAI_QUALITY_MODEL") or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
            base_url=str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL,
            timeout_seconds=_coerce_float(resolved.get("KINDLEMASTER_OPENAI_QUALITY_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS),
            max_input_chars=max(1000, int(_coerce_float(resolved.get("KINDLEMASTER_OPENAI_QUALITY_MAX_INPUT_CHARS"), DEFAULT_MAX_INPUT_CHARS))),
        ),
        transport=transport,
    )


def openai_quality_configuration_status(*, env: Mapping[str, str] | None = None, cwd: str | Path | None = None) -> dict[str, Any]:
    resolved = _runtime_env(env=env, cwd=cwd)
    enabled = _openai_quality_enabled(resolved)
    return {
        "enabled": enabled,
        "api_key_present": bool(str(resolved.get("OPENAI_API_KEY", "") or "").strip()),
        "model": str(resolved.get("KINDLEMASTER_OPENAI_QUALITY_MODEL") or DEFAULT_OPENAI_MODEL),
        "base_url": str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL),
        "mode": "evidence_only",
        "evidence_only": True,
        "full_document_upload": False,
    }


def _runtime_env(*, env: Mapping[str, str] | None, cwd: str | Path | None) -> dict[str, str]:
    resolved = dict(os.environ)
    root = Path(cwd or Path.cwd())
    for file_name in ENV_FILES:
        resolved.update({key: value for key, value in _load_env_file(root / file_name).items() if key not in resolved})
    if env is not None:
        resolved.update({str(key): str(value) for key, value in env.items()})
    return resolved


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _openai_quality_enabled(env: Mapping[str, str]) -> bool:
    return any(str(env.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in QUALITY_ENABLE_KEYS)


def _http_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"openai-http-{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"openai-network-error: {exc.reason}") from exc
    response_payload.setdefault("_elapsed_ms", max(0, int(round((time.perf_counter() - started) * 1000))))
    return response_payload


def _extract_json(response: dict[str, Any]) -> dict[str, Any]:
    text = _extract_text(response)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("openai-invalid-json") from exc
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _extract_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct.strip()
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str):
                return content["text"].strip()
    return ""


def _estimated_cost(response: dict[str, Any]) -> float:
    # Keep cost conservative and auditable without hardcoding provider pricing.
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    if not usage:
        return 0.0
    return 0.0


def _ocr_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["text", "confidence"],
    }


def _toc_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string"},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string"},
                        "href": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "level": {"type": "integer", "minimum": 1, "maximum": 4},
                    },
                    "required": ["label", "href", "confidence", "level"],
                },
            },
        },
        "required": ["confidence", "reasoning", "entries"],
    }


def _magazine_review_schema() -> dict[str, Any]:
    href_evidence_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "href": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["href", "evidence", "confidence"],
    }
    title_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "href": {"type": "string"},
            "observed_title": {"type": "string"},
            "suggested_title": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["href", "observed_title", "suggested_title", "evidence", "confidence"],
    }
    missing_article_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "href": {"type": "string"},
            "title": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["href", "title", "evidence", "confidence"],
    }
    non_content_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "href": {"type": "string"},
            "label": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["href", "label", "evidence", "confidence"],
    }
    ocr_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fragment_index": {"type": "integer", "minimum": 0},
            "before": {"type": "string"},
            "suggested": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["fragment_index", "before", "suggested", "evidence", "confidence"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "suspected_bad_reading_order": {"type": "array", "items": href_evidence_item},
            "truncated_titles": {"type": "array", "items": title_item},
            "toc_missing_articles": {"type": "array", "items": missing_article_item},
            "non_content_misclassified": {"type": "array", "items": non_content_item},
            "ocr_cleanup_candidates": {"type": "array", "items": ocr_item},
            "suggested_fixture_tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "suspected_bad_reading_order",
            "truncated_titles",
            "toc_missing_articles",
            "non_content_misclassified",
            "ocr_cleanup_candidates",
            "suggested_fixture_tags",
            "confidence",
        ],
    }


def _dense_handbook_review_schema() -> dict[str, Any]:
    href_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "href": {"type": "string"},
            "label": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["href", "label", "evidence", "confidence"],
    }
    fragment_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fragment_index": {"type": "integer", "minimum": 0},
            "before": {"type": "string"},
            "classification": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["fragment_index", "before", "classification", "evidence", "confidence"],
    }
    chapter_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "href": {"type": "string"},
            "title": {"type": "string"},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["href", "title", "evidence", "confidence"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "toc_debris": {"type": "array", "items": href_item},
            "heading_noise": {"type": "array", "items": href_item},
            "text_artifact_reviews": {"type": "array", "items": fragment_item},
            "oversized_chapters": {"type": "array", "items": chapter_item},
            "suggested_fixture_tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "toc_debris",
            "heading_noise",
            "text_artifact_reviews",
            "oversized_chapters",
            "suggested_fixture_tags",
            "confidence",
        ],
    }


def _compact_scoring(scoring: Any) -> dict[str, Any]:
    if not isinstance(scoring, dict):
        return {}
    return {
        "premium_score": scoring.get("premium_score"),
        "release_verdict": scoring.get("release_verdict"),
        "scores": scoring.get("scores", {}),
        "issues": (scoring.get("issues") or [])[:20],
    }


def _compact_magazine_review_context(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    source = context if isinstance(context, dict) else {}
    payload = {
        "context_version": "magazine-review-v1",
        "context_truncated": False,
        "toc_entries": _compact_magazine_toc_entries(source.get("toc_entries")),
        "article_map": _compact_magazine_articles(source.get("article_map")),
        "suspicious_fragments": _compact_magazine_fragments(
            source.get("suspicious_fragments") or source.get("suspicious_ocr_fragments")
        ),
        "flow_fragments": _compact_magazine_flow_fragments(source.get("flow_fragments")),
        "image_metrics": _compact_magazine_image_metrics(source.get("image_metrics")),
        "premium_issues": _compact_magazine_issues(source.get("premium_issues") or source.get("issues")),
    }
    return _trim_json_payload(payload, max(600, int(max_chars or DEFAULT_MAX_INPUT_CHARS)))


def _compact_dense_handbook_review_context(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    source = context if isinstance(context, dict) else {}
    payload = {
        "context_version": "dense-handbook-review-v1",
        "context_truncated": False,
        "toc_entries": _compact_dense_toc_entries(source.get("toc_entries")),
        "heading_noise_samples": _compact_dense_heading_samples(source.get("heading_noise_samples")),
        "text_artifact_fragments": _compact_magazine_fragments(
            source.get("text_artifact_fragments") or source.get("suspicious_fragments")
        ),
        "chapter_stats": _compact_dense_chapter_stats(source.get("chapter_stats")),
        "premium_issues": _compact_magazine_issues(source.get("premium_issues") or source.get("issues")),
        "metrics": _compact_dense_metrics(source.get("metrics")),
    }
    return _trim_json_payload(payload, max(600, int(max_chars or DEFAULT_MAX_INPUT_CHARS)))


def _compact_magazine_toc_entries(value: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(_iter_dicts(value)):
        entries.append(
            {
                "index": _coerce_int(item.get("index"), index),
                "label": _clip_text_field(item.get("label") or item.get("title"), 180),
                "href": _clip_text_field(item.get("href"), 240),
                "level": _coerce_int(item.get("level"), 1),
            }
        )
        if len(entries) >= MAX_MAGAZINE_TOC_ENTRIES:
            break
    return entries


def _compact_dense_toc_entries(value: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(_iter_dicts(value)):
        entries.append(
            {
                "index": _coerce_int(item.get("index"), index),
                "label": _clip_text_field(item.get("label") or item.get("title"), 180),
                "href": _clip_text_field(item.get("href"), 240),
                "level": _coerce_int(item.get("level"), 1),
            }
        )
        if len(entries) >= MAX_DENSE_TOC_ENTRIES:
            break
    return entries


def _compact_dense_heading_samples(value: Any) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for index, item in enumerate(_iter_dicts(value)):
        samples.append(
            {
                "index": _coerce_int(item.get("index"), index),
                "label": _clip_text_field(item.get("label") or item.get("text") or item.get("title"), 180),
                "href": _clip_text_field(item.get("href") or item.get("file"), 240),
                "level": _coerce_int(item.get("level"), 0),
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), MAX_DENSE_TEXT_CHARS),
            }
        )
        if len(samples) >= MAX_DENSE_HEADINGS:
            break
    if samples:
        return samples
    for index, text in enumerate(_compact_string_list(value, limit=MAX_DENSE_HEADINGS, chars=180)):
        samples.append({"index": index, "label": text, "href": "", "level": 0, "evidence": ""})
    return samples


def _compact_dense_chapter_stats(value: Any) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for index, item in enumerate(_iter_dicts(value)):
        stats.append(
            {
                "index": _coerce_int(item.get("index"), index),
                "href": _clip_text_field(item.get("href") or item.get("file"), 240),
                "title": _clip_text_field(item.get("title") or item.get("label"), 180),
                "word_count": _coerce_int(item.get("word_count"), 0),
                "heading_count": _coerce_int(item.get("heading_count"), 0),
            }
        )
        if len(stats) >= MAX_DENSE_CHAPTER_STATS:
            break
    return stats


def _compact_dense_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "premium_score": value.get("premium_score"),
        "release_verdict": value.get("release_verdict"),
        "toc_noise_count": _coerce_int(value.get("toc_noise_count"), 0),
        "heading_noise_count": _coerce_int(value.get("heading_noise_count"), 0),
        "artifact_rate_per_1000_words": value.get("artifact_rate_per_1000_words"),
        "largest_chapter_words": _coerce_int(value.get("largest_chapter_words"), 0),
    }


def _compact_magazine_articles(value: Any) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    raw_articles = value.get("articles") if isinstance(value, Mapping) else value
    for index, item in enumerate(_iter_dicts(raw_articles)):
        articles.append(
            {
                "index": _coerce_int(item.get("index") or item.get("position"), index),
                "href": _clip_text_field(item.get("href"), 240),
                "title": _clip_text_field(item.get("title"), 180),
                "word_count": _coerce_int(item.get("word_count"), 0),
                "image_count": _coerce_int(item.get("image_count"), 0),
                "heading_count": _coerce_int(item.get("heading_count"), 0),
                "text_preview": _clip_text_field(item.get("text_preview") or item.get("sample_text"), MAX_MAGAZINE_TEXT_CHARS),
                "risk_flags": _compact_string_list(item.get("risk_flags"), limit=8, chars=80),
            }
        )
        if len(articles) >= MAX_MAGAZINE_ARTICLES:
            break
    return articles


def _compact_magazine_fragments(value: Any) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return fragments
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            fragment = {
                "index": _coerce_int(item.get("index") or item.get("fragment_index"), index),
                "href": _clip_text_field(item.get("href"), 240),
                "text": _clip_text_field(item.get("text") or item.get("before"), MAX_MAGAZINE_TEXT_CHARS),
                "artifact_counts": _compact_counts(item.get("artifact_counts")),
            }
        else:
            fragment = {
                "index": index,
                "href": "",
                "text": _clip_text_field(item, MAX_MAGAZINE_TEXT_CHARS),
                "artifact_counts": {},
            }
        fragments.append(fragment)
        if len(fragments) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return fragments


def _compact_magazine_flow_fragments(value: Any) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for index, item in enumerate(_iter_dicts(value)):
        fragments.append(
            {
                "index": _coerce_int(item.get("index"), index),
                "href": _clip_text_field(item.get("href"), 240),
                "kind": _clip_text_field(item.get("kind") or item.get("type"), 80),
                "title": _clip_text_field(item.get("title") or item.get("label"), 180),
                "evidence": _clip_text_field(item.get("evidence") or item.get("sample"), MAX_MAGAZINE_TEXT_CHARS),
            }
        )
        if len(fragments) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return fragments


def _compact_magazine_image_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    scalar_keys = (
        "total_images",
        "articles_with_images",
        "image_only_articles",
        "missing_alt_count",
        "low_resolution_image_count",
        "total_image_bytes",
        "largest_image_bytes",
    )
    metrics: dict[str, Any] = {
        key: _coerce_int(value.get(key), 0)
        for key in scalar_keys
        if key in value
    }
    per_article: list[dict[str, Any]] = []
    for item in _iter_dicts(value.get("per_article")):
        per_article.append(
            {
                "href": _clip_text_field(item.get("href"), 240),
                "image_count": _coerce_int(item.get("image_count"), 0),
                "missing_alt_count": _coerce_int(item.get("missing_alt_count"), 0),
                "text_chars": _coerce_int(item.get("text_chars"), 0),
            }
        )
        if len(per_article) >= MAX_MAGAZINE_IMAGE_ROWS:
            break
    if per_article:
        metrics["per_article"] = per_article
    return metrics


def _compact_magazine_issues(value: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        issues.append(
            {
                "code": _clip_text_field(item.get("code"), 100),
                "severity": _clip_text_field(item.get("severity"), 40),
                "source": _clip_text_field(item.get("source"), 80),
                "file": _clip_text_field(item.get("file"), 240),
                "message": _clip_text_field(item.get("message"), 260),
                "suggested_action": _clip_text_field(item.get("suggested_action"), 260),
            }
        )
        if len(issues) >= MAX_MAGAZINE_ISSUES:
            break
    return issues


def _sanitize_magazine_review(parsed: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed_hrefs = _allowed_magazine_hrefs(context)
    fragment_text_by_index = _fragment_text_by_index(context)
    return {
        "suspected_bad_reading_order": _sanitize_href_evidence_list(
            parsed.get("suspected_bad_reading_order"),
            allowed_hrefs=allowed_hrefs,
        ),
        "truncated_titles": _sanitize_title_list(parsed.get("truncated_titles"), allowed_hrefs=allowed_hrefs),
        "toc_missing_articles": _sanitize_missing_article_list(
            parsed.get("toc_missing_articles"),
            allowed_hrefs=allowed_hrefs,
        ),
        "non_content_misclassified": _sanitize_non_content_list(
            parsed.get("non_content_misclassified"),
            allowed_hrefs=allowed_hrefs,
        ),
        "ocr_cleanup_candidates": _sanitize_ocr_candidate_list(
            parsed.get("ocr_cleanup_candidates"),
            fragment_text_by_index=fragment_text_by_index,
        ),
        "suggested_fixture_tags": _sanitize_fixture_tags(parsed.get("suggested_fixture_tags")),
        "confidence": _clamp(parsed.get("confidence")),
    }


def _sanitize_dense_handbook_review(parsed: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed_hrefs = _allowed_dense_hrefs(context)
    fragment_text_by_index = _fragment_text_by_index({"suspicious_fragments": context.get("text_artifact_fragments")})
    return {
        "toc_debris": _sanitize_dense_href_list(parsed.get("toc_debris"), allowed_hrefs=allowed_hrefs),
        "heading_noise": _sanitize_dense_href_list(parsed.get("heading_noise"), allowed_hrefs=allowed_hrefs),
        "text_artifact_reviews": _sanitize_dense_artifact_reviews(
            parsed.get("text_artifact_reviews"),
            fragment_text_by_index=fragment_text_by_index,
        ),
        "oversized_chapters": _sanitize_dense_chapter_reviews(parsed.get("oversized_chapters"), allowed_hrefs=allowed_hrefs),
        "suggested_fixture_tags": _sanitize_fixture_tags(parsed.get("suggested_fixture_tags")),
        "confidence": _clamp(parsed.get("confidence")),
    }


def _sanitize_dense_href_list(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        href = _clean_allowed_href(item.get("href"), allowed_hrefs) if allowed_hrefs else _clip_text_field(item.get("href"), 240)
        label = _clip_text_field(item.get("label") or item.get("title"), 180)
        if allowed_hrefs and str(item.get("href") or "").strip() and not href:
            continue
        if not label:
            continue
        items.append(
            {
                "href": href,
                "label": label,
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_DENSE_FRAGMENTS:
            break
    return items


def _sanitize_dense_artifact_reviews(value: Any, *, fragment_text_by_index: dict[int, str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        index = _coerce_int(item.get("fragment_index") if "fragment_index" in item else item.get("index"), -1)
        if index not in fragment_text_by_index:
            continue
        items.append(
            {
                "fragment_index": index,
                "before": fragment_text_by_index[index],
                "classification": _clip_text_field(item.get("classification"), 80),
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_DENSE_FRAGMENTS:
            break
    return items


def _sanitize_dense_chapter_reviews(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        href = _clean_allowed_href(item.get("href"), allowed_hrefs) if allowed_hrefs else _clip_text_field(item.get("href"), 240)
        title = _clip_text_field(item.get("title") or item.get("label"), 180)
        if allowed_hrefs and str(item.get("href") or "").strip() and not href:
            continue
        if not title:
            continue
        items.append(
            {
                "href": href,
                "title": title,
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_DENSE_FRAGMENTS:
            break
    return items


def _sanitize_href_evidence_list(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        href = _clean_allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return items


def _sanitize_title_list(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        href = _clean_allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "observed_title": _clip_text_field(item.get("observed_title") or item.get("observed"), 180),
                "suggested_title": _clip_text_field(item.get("suggested_title") or item.get("suggested"), 180),
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return items


def _sanitize_missing_article_list(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        href = _clean_allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "title": _clip_text_field(item.get("title") or item.get("label"), 180),
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return items


def _sanitize_non_content_list(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        href = _clean_allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "label": _clip_text_field(item.get("label") or item.get("title"), 180),
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return items


def _sanitize_ocr_candidate_list(value: Any, *, fragment_text_by_index: dict[int, str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_dicts(value):
        index = _coerce_int(item.get("fragment_index") if "fragment_index" in item else item.get("index"), -1)
        if index not in fragment_text_by_index:
            continue
        items.append(
            {
                "fragment_index": index,
                "before": fragment_text_by_index[index],
                "suggested": _clip_text_field(item.get("suggested") or item.get("after"), MAX_MAGAZINE_TEXT_CHARS),
                "evidence": _clip_text_field(item.get("evidence") or item.get("reason"), 360),
                "confidence": _clamp(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_FRAGMENTS:
            break
    return items


def _sanitize_fixture_tags(value: Any) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    if not isinstance(value, list):
        return tags
    for item in value:
        tag = re.sub(r"[^a-z0-9_-]+", "-", str(item or "").strip().lower()).strip("-_")
        if len(tag) < 2:
            continue
        tag = tag[:60]
        if tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= 12:
            break
    return tags


def _allowed_magazine_hrefs(context: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for key in ("toc_entries", "article_map", "flow_fragments"):
        for item in _iter_dicts(context.get(key)):
            href = str(item.get("href") or "").strip()
            if href:
                allowed.add(href)
    return allowed


def _allowed_dense_hrefs(context: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for key in ("toc_entries", "heading_noise_samples", "chapter_stats"):
        for item in _iter_dicts(context.get(key)):
            href = str(item.get("href") or "").strip()
            if href:
                allowed.add(href)
    return allowed


def _fragment_text_by_index(context: dict[str, Any]) -> dict[int, str]:
    fragments: dict[int, str] = {}
    for item in _iter_dicts(context.get("suspicious_fragments")):
        index = _coerce_int(item.get("index"), -1)
        if index >= 0:
            fragments[index] = _clip_text_field(item.get("text"), MAX_MAGAZINE_TEXT_CHARS)
    return fragments


def _clean_allowed_href(value: Any, allowed_hrefs: set[str]) -> str:
    href = str(value or "").strip()
    if not href or href not in allowed_hrefs:
        return ""
    return href


def _trim_json_payload(payload: dict[str, Any], budget: int) -> dict[str, Any]:
    result = dict(payload)
    if _json_length(result) <= budget:
        return result

    result["context_truncated"] = True
    for key in ("premium_issues", "suspicious_fragments", "flow_fragments", "article_map", "toc_entries"):
        items = list(result.get(key) or [])
        while items and _json_length(result) > budget:
            items.pop()
            result[key] = items

    if _json_length(result) <= budget:
        return result

    for char_limit in (220, 140, 80, 40):
        clipped = _clip_strings_in_payload(result, char_limit)
        clipped["context_truncated"] = True
        result = clipped
        if _json_length(result) <= budget:
            return result

    minimal = {
        "context_version": "magazine-review-v1",
        "context_truncated": True,
        "toc_entries": [],
        "article_map": [],
        "suspicious_fragments": [],
        "flow_fragments": [],
        "image_metrics": {},
        "premium_issues": [],
    }
    if _json_length(minimal) <= budget:
        return minimal
    return {"context_truncated": True}


def _clip_strings_in_payload(value: Any, char_limit: int) -> Any:
    if isinstance(value, dict):
        return {key: _clip_strings_in_payload(item, char_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_strings_in_payload(item, char_limit) for item in value]
    if isinstance(value, str):
        return _clip_text_field(value, char_limit)
    return value


def _json_length(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _iter_dicts(value: Any):
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            yield item


def _compact_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key)[:80]: _coerce_int(count, 0) for key, count in list(value.items())[:12]}


def _compact_string_list(value: Any, *, limit: int, chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        result.append(_clip_text_field(item, chars))
        if len(result) >= limit:
            break
    return result


def _clip_text_field(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    suffix = " [...clipped]"
    return text[: max(0, limit - len(suffix))] + suffix


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[... clipped for AI quality review ...]"


def _clamp(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_level(value: Any) -> int | None:
    try:
        return max(1, min(4, int(value)))
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
