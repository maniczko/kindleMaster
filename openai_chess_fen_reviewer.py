from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_OPENAI_CHESS_FEN_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 45.0
ENV_FILES = (".env.local", ".env")
ENABLE_KEYS = ("KINDLEMASTER_OPENAI_CHESS_FEN_REVIEW",)

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass
class OpenAIChessFenReviewer:
    """Optional OpenAI-backed, review-only provider for chess FEN candidates."""

    name: str = "openai-chess-fen-reviewer"
    model: str = DEFAULT_OPENAI_CHESS_FEN_MODEL
    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    transport: Transport | None = field(default=None, repr=False)

    def review_chess_fen(self, context: dict[str, Any]) -> Mapping[str, Any]:
        candidate = dict(context.get("candidate") or {})
        candidate_fen = str(candidate.get("fen") or "")
        if not self.api_key:
            return self._review_disabled(candidate_fen)

        payload = self._responses_payload(context)
        response = self._call(payload)
        parsed = _extract_json(response)
        issues = _string_list(parsed.get("issues"))
        ambiguous_squares = _string_list(parsed.get("ambiguous_squares"))
        return {
            "status": "reviewed",
            "provider": self.name,
            "model": self.model,
            "mode": "review_only",
            "mutates_fen": False,
            "candidate_fen": candidate_fen,
            "suggested_label": str(parsed.get("corrected_fen") or ""),
            "approved": bool(parsed.get("approved")),
            "requires_review": bool(parsed.get("requires_review", True)),
            "ambiguous_squares": ambiguous_squares,
            "issues": issues,
            "confidence": _clamp(parsed.get("confidence")),
            "reason": str(parsed.get("notes") or ""),
            "estimated_cost_usd": _estimated_cost(response),
            "metadata": {
                "usage": response.get("usage", {}),
                "response_id": str(response.get("id") or ""),
            },
            "changed_output": False,
        }

    def _review_disabled(self, candidate_fen: str) -> dict[str, Any]:
        return {
            "status": "reviewed",
            "provider": self.name,
            "model": self.model,
            "mode": "review_only",
            "mutates_fen": False,
            "candidate_fen": candidate_fen,
            "suggested_label": "",
            "approved": False,
            "requires_review": True,
            "ambiguous_squares": [],
            "issues": ["live_openai_review_not_configured"],
            "reason": "live OpenAI chess FEN review is opt-in and requires env configuration",
            "changed_output": False,
        }

    def _responses_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": json.dumps(_compact_context(context), ensure_ascii=False),
            }
        ]
        image_data = context.get("image_data")
        if isinstance(image_data, bytes) and image_data:
            mime_type = str(context.get("image_mime_type") or "image/png")
            encoded = base64.b64encode(image_data).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"})
        return {
            "model": self.model,
            "instructions": (
                "You are a conservative chess FEN reviewer for KindleMaster. Use only the supplied deterministic "
                "candidate data and board crop if present. Return JSON only. Do not invent pieces. Do not approve "
                "or suggest a FEN when any occupied square is ambiguous. Your response is audit evidence only and "
                "must never mutate EPUB output or corpus labels."
            ),
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kindlemaster_chess_fen_review",
                    "strict": True,
                    "schema": _review_schema(),
                }
            },
        }

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        transport = self.transport or _http_transport
        return transport(url, headers, payload, float(self.timeout_seconds))


def build_openai_chess_fen_reviewer_from_env(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    transport: Transport | None = None,
) -> OpenAIChessFenReviewer | None:
    resolved = _runtime_env(env=env, cwd=cwd)
    if not any(str(resolved.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in ENABLE_KEYS):
        return None
    api_key = str(resolved.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return None
    return OpenAIChessFenReviewer(
        model=str(resolved.get("KINDLEMASTER_OPENAI_CHESS_FEN_MODEL") or DEFAULT_OPENAI_CHESS_FEN_MODEL).strip()
        or DEFAULT_OPENAI_CHESS_FEN_MODEL,
        api_key=api_key,
        base_url=str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL,
        timeout_seconds=_coerce_float(
            resolved.get("KINDLEMASTER_OPENAI_CHESS_FEN_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
        ),
        transport=transport,
    )


def openai_chess_fen_reviewer_status(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    resolved = _runtime_env(env=env, cwd=cwd)
    enabled_flag = any(str(resolved.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in ENABLE_KEYS)
    api_key_present = bool(str(resolved.get("OPENAI_API_KEY", "") or "").strip())
    provider_enabled = enabled_flag and api_key_present
    return {
        "enabled": provider_enabled,
        "configured": provider_enabled,
        "api_key_present": api_key_present,
        "provider": "openai-chess-fen-reviewer" if provider_enabled else "none",
        "model": str(resolved.get("KINDLEMASTER_OPENAI_CHESS_FEN_MODEL") or DEFAULT_OPENAI_CHESS_FEN_MODEL),
        "base_url": str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL),
        "mode": "review_only",
        "mutates_fen": False,
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


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(context.get("candidate") or {})
    return {
        "candidate": {
            "fen": str(candidate.get("fen") or ""),
            "placement": str(candidate.get("placement") or ""),
            "confidence": candidate.get("confidence"),
            "warnings": _string_list(candidate.get("warnings")),
            "method": str(candidate.get("method") or ""),
            "bbox": candidate.get("bbox"),
            "requires_review": bool(candidate.get("requires_review")),
        },
        "has_image": bool(context.get("has_image") or isinstance(context.get("image_data"), bytes)),
        "source": str(context.get("source") or ""),
        "page": context.get("page"),
        "diagram_index": context.get("diagram_index"),
        "policy": "review_only_no_epub_mutation",
    }


def _review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "approved": {"type": "boolean"},
            "corrected_fen": {"type": "string"},
            "requires_review": {"type": "boolean"},
            "ambiguous_squares": {"type": "array", "items": {"type": "string"}},
            "issues": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "notes": {"type": "string"},
        },
        "required": [
            "approved",
            "corrected_fen",
            "requires_review",
            "ambiguous_squares",
            "issues",
            "confidence",
            "notes",
        ],
    }


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, number))


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _estimated_cost(response: dict[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    if not usage:
        return 0.0
    return 0.0


def main() -> int:
    print(json.dumps(openai_chess_fen_reviewer_status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
