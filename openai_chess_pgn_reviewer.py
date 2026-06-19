from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_OPENAI_CHESS_PGN_MODEL = "gpt-5.4"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0
ENV_FILES = (".env.local", ".env")
ENABLE_KEYS = ("KINDLEMASTER_OPENAI_PGN_REPAIR", "KINDLEMASTER_OPENAI_CHESS_PGN_REPAIR")

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass
class OpenAIChessPgnReviewer:
    """Optional GPT-backed, review-only PGN candidate repair provider."""

    name: str = "openai-chess-pgn-reviewer"
    model: str = DEFAULT_OPENAI_CHESS_PGN_MODEL
    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    transport: Transport | None = field(default=None, repr=False)

    def propose_pgn_repair(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "needs_review",
                "provider": self.name,
                "model": self.model,
                "mode": "review_only",
                "candidate_pgn": "",
                "confidence": 0.0,
                "reason": "live OpenAI PGN repair is opt-in and requires env configuration",
                "warnings": ["live_openai_pgn_repair_not_configured"],
                "mutates_output": False,
            }
        payload = self._responses_payload(context)
        response = self._call(payload)
        parsed = _extract_json(response)
        return {
            "status": "ai_suggested",
            "provider": self.name,
            "model": self.model,
            "mode": "review_only",
            "candidate_pgn": str(parsed.get("candidate_pgn") or ""),
            "confidence": _clamp(parsed.get("confidence")),
            "reason": str(parsed.get("reason") or ""),
            "warnings": _string_list(parsed.get("warnings")),
            "commentary_preserved": bool(parsed.get("commentary_preserved", False)),
            "variations_preserved": bool(parsed.get("variations_preserved", False)),
            "estimated_cost_usd": _estimated_cost(response),
            "metadata": {
                "usage": response.get("usage", {}),
                "response_id": str(response.get("id") or ""),
            },
            "mutates_output": False,
        }

    def _responses_payload(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model": self.model,
            "instructions": (
                "You are a conservative chess PGN repair candidate generator for KindleMaster. "
                "Use only supplied bounded OCR/source text, manual glyph mapping hints, and FEN context. "
                "Return JSON only. Preserve prose comments in PGN comments {...} and variations in (...). "
                "Do not mark anything accepted; local python-chess replay is the only acceptance gate."
            ),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(_compact_context(context), ensure_ascii=False),
                        }
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kindlemaster_chess_pgn_candidate",
                    "strict": True,
                    "schema": _pgn_schema(),
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


def build_openai_chess_pgn_reviewer_from_env(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    transport: Transport | None = None,
) -> OpenAIChessPgnReviewer | None:
    resolved = _runtime_env(env=env, cwd=cwd)
    if not any(str(resolved.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in ENABLE_KEYS):
        return None
    api_key = str(resolved.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return None
    return OpenAIChessPgnReviewer(
        model=str(resolved.get("KINDLEMASTER_OPENAI_PGN_MODEL") or DEFAULT_OPENAI_CHESS_PGN_MODEL).strip()
        or DEFAULT_OPENAI_CHESS_PGN_MODEL,
        api_key=api_key,
        base_url=str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL,
        timeout_seconds=_coerce_float(resolved.get("KINDLEMASTER_OPENAI_PGN_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS),
        transport=transport,
    )


def _pgn_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidate_pgn": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "commentary_preserved": {"type": "boolean"},
            "variations_preserved": {"type": "boolean"},
        },
        "required": [
            "candidate_pgn",
            "confidence",
            "reason",
            "warnings",
            "commentary_preserved",
            "variations_preserved",
        ],
    }


def _compact_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_id": str(context.get("record_id") or ""),
        "page": context.get("page"),
        "label": str(context.get("label") or "")[:120],
        "raw_text": str(context.get("raw_text") or "")[:1600],
        "normalized_text": str(context.get("normalized_text") or "")[:1600],
        "pgn_candidate": str(context.get("pgn_candidate") or "")[:2200],
        "warnings": _string_list(context.get("warnings")),
        "unmapped_token_blockers": _string_list(context.get("unmapped_token_blockers")),
        "source_fen": str(context.get("source_fen") or ""),
        "requires_source_fen": bool(context.get("requires_source_fen")),
        "policy": "review_only_python_chess_replay_required_for_acceptance",
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


def _http_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"openai-pgn-http-{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"openai-pgn-network-error: {exc.reason}") from exc
    response_payload.setdefault("_elapsed_ms", max(0, int(round((time.perf_counter() - started) * 1000))))
    return response_payload


def _extract_json(response: dict[str, Any]) -> dict[str, Any]:
    text = _extract_text(response)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("openai-pgn-invalid-json") from exc
    return parsed if isinstance(parsed, dict) else {}


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


def _estimated_cost(response: Mapping[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    if not usage:
        return 0.0
    return 0.0
