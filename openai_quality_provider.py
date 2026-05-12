from __future__ import annotations

import json
import os
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


def _compact_scoring(scoring: Any) -> dict[str, Any]:
    if not isinstance(scoring, dict):
        return {}
    return {
        "premium_score": scoring.get("premium_score"),
        "release_verdict": scoring.get("release_verdict"),
        "scores": scoring.get("scores", {}),
        "issues": (scoring.get("issues") or [])[:20],
    }


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
