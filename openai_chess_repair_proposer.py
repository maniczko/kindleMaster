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


DEFAULT_OPENAI_CHESS_PREMIUM_MODEL = "gpt-5.5"
DEFAULT_OPENAI_CHESS_BATCH_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_REASONING_EFFORT = "medium"
ENV_FILES = (".env.local", ".env")
ENABLE_KEYS = ("KINDLEMASTER_OPENAI_CHESS_REPAIR", "KINDLEMASTER_OPENAI_CHESS_REVIEW")
VALID_REPAIR_MODES = {"review_only", "validated_export"}

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass
class OpenAIChessRepairProposer:
    """Optional OpenAI-backed proposer for hard chess FEN/SAN exercise cases."""

    name: str = "openai-chess-repair-proposer"
    model: str = DEFAULT_OPENAI_CHESS_PREMIUM_MODEL
    mode: str = "review_only"
    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    transport: Transport | None = field(default=None, repr=False)

    def propose_chess_repair(self, context: dict[str, Any]) -> Mapping[str, Any]:
        if not self.api_key:
            return self._proposal_disabled()

        payload = self._responses_payload(context)
        response = self._call(payload)
        parsed = _extract_json(response)
        return {
            "status": "reviewed",
            "provider": self.name,
            "model": self.model,
            "mode": _normalize_mode(self.mode),
            "mutates_exportable_pgn": False,
            "requires_local_validation": True,
            "fen_candidates": _candidate_list(parsed.get("fen_candidates"), "fen"),
            "solution_line_candidates": _candidate_list(parsed.get("solution_line_candidates"), "movetext"),
            "ocr_token_repairs": _repair_list(parsed.get("ocr_token_repairs")),
            "confidence": _clamp(parsed.get("confidence")),
            "requires_human_review": bool(parsed.get("requires_human_review", True)),
            "notes": str(parsed.get("notes") or ""),
            "estimated_cost_usd": _estimated_cost(response),
            "metadata": {
                "usage": response.get("usage", {}),
                "response_id": str(response.get("id") or ""),
            },
            "changed_output": False,
        }

    def _proposal_disabled(self) -> dict[str, Any]:
        return {
            "status": "not_configured",
            "provider": self.name,
            "model": self.model,
            "mode": _normalize_mode(self.mode),
            "mutates_exportable_pgn": False,
            "requires_local_validation": True,
            "fen_candidates": [],
            "solution_line_candidates": [],
            "ocr_token_repairs": [],
            "confidence": 0.0,
            "requires_human_review": True,
            "notes": "live OpenAI chess repair is opt-in and requires env configuration",
            "estimated_cost_usd": 0.0,
            "metadata": {},
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
                "You are a conservative chess exercise repair proposer for KindleMaster. You may propose FEN "
                "candidates, SAN solution lines, and OCR token repairs from the supplied board crop and OCR text. "
                "Return JSON only. Do not claim a result is valid. The local python-chess validator is the only "
                "authority for export. Prefer requires_human_review=true whenever the board, side to move, or SAN "
                "line is ambiguous."
            ),
            "input": [{"role": "user", "content": content}],
            "reasoning": {"effort": self.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kindlemaster_chess_repair_proposal",
                    "strict": True,
                    "schema": _repair_schema(),
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


def build_openai_chess_repair_proposer_from_env(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    model: str | None = None,
    mode: str | None = None,
    transport: Transport | None = None,
) -> OpenAIChessRepairProposer | None:
    resolved = _runtime_env(env=env, cwd=cwd)
    if not any(_env_truthy(resolved.get(key)) for key in ENABLE_KEYS):
        return None
    api_key = str(resolved.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return None
    selected_model = (
        str(model or "").strip()
        or str(resolved.get("KINDLEMASTER_OPENAI_CHESS_REPAIR_MODEL") or "").strip()
        or str(resolved.get("KINDLEMASTER_OPENAI_CHESS_PREMIUM_MODEL") or DEFAULT_OPENAI_CHESS_PREMIUM_MODEL).strip()
        or DEFAULT_OPENAI_CHESS_PREMIUM_MODEL
    )
    return OpenAIChessRepairProposer(
        model=selected_model,
        mode=_normalize_mode(
            mode
            or resolved.get("KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE")
            or resolved.get("KINDLEMASTER_OPENAI_CHESS_REVIEW_MODE")
            or "review_only"
        ),
        api_key=api_key,
        base_url=str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL,
        timeout_seconds=_coerce_float(
            resolved.get("KINDLEMASTER_OPENAI_CHESS_REPAIR_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
        ),
        reasoning_effort=str(
            resolved.get("KINDLEMASTER_OPENAI_CHESS_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT
        ).strip()
        or DEFAULT_REASONING_EFFORT,
        transport=transport,
    )


def openai_chess_repair_proposer_status(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    resolved = _runtime_env(env=env, cwd=cwd)
    enabled_flag = any(_env_truthy(resolved.get(key)) for key in ENABLE_KEYS)
    api_key_present = bool(str(resolved.get("OPENAI_API_KEY", "") or "").strip())
    provider_enabled = enabled_flag and api_key_present
    mode = _normalize_mode(
        resolved.get("KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE")
        or resolved.get("KINDLEMASTER_OPENAI_CHESS_REVIEW_MODE")
        or "review_only"
    )
    return {
        "enabled": provider_enabled,
        "configured": provider_enabled,
        "api_key_present": api_key_present,
        "provider": "openai-chess-repair-proposer" if provider_enabled else "none",
        "premium_model": str(
            resolved.get("KINDLEMASTER_OPENAI_CHESS_PREMIUM_MODEL") or DEFAULT_OPENAI_CHESS_PREMIUM_MODEL
        ),
        "batch_model": str(resolved.get("KINDLEMASTER_OPENAI_CHESS_BATCH_MODEL") or DEFAULT_OPENAI_CHESS_BATCH_MODEL),
        "base_url": str(resolved.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL),
        "mode": mode,
        "mutates_exportable_pgn": mode == "validated_export",
        "requires_local_validation": True,
        "full_document_upload": False,
    }


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(context.get("raw_ocr_text") or "")
    return {
        "record_id": str(context.get("record_id") or ""),
        "source_pages": context.get("source_pages") or [],
        "diagram_number": str(context.get("diagram_number") or ""),
        "caption": _truncate(str(context.get("caption") or ""), 300),
        "raw_ocr_text": _truncate(raw_text, 3000),
        "local_fen": str(context.get("local_fen") or ""),
        "fen_available": str(context.get("fen_available") or ""),
        "fen_candidates": [_compact_fen_candidate(item) for item in (context.get("fen_candidates") or [])[:8]],
        "rejected_candidate_lines": [
            _compact_line_candidate(item) for item in (context.get("rejected_candidate_lines") or [])[:8]
        ],
        "warnings": _string_list(context.get("warnings"))[:12],
        "blocker": str(context.get("blocker") or ""),
        "has_image": bool(context.get("has_image") or isinstance(context.get("image_data"), bytes)),
        "policy": "ai_proposes_only_local_python_chess_validates_export",
    }


def _compact_fen_candidate(item: Any) -> dict[str, Any]:
    candidate = dict(item or {}) if isinstance(item, Mapping) else {"fen": str(item or "")}
    return {
        "fen": str(candidate.get("fen") or ""),
        "source": str(candidate.get("source") or ""),
        "sources": _string_list(candidate.get("sources")),
        "confidence": candidate.get("confidence"),
        "status": str(candidate.get("status") or ""),
        "warnings": _string_list(candidate.get("warnings"))[:8],
        "piece_count": candidate.get("piece_count"),
        "score": candidate.get("score"),
        "match_score": candidate.get("match_score"),
    }


def _compact_line_candidate(item: Any) -> dict[str, Any]:
    candidate = dict(item or {}) if isinstance(item, Mapping) else {"raw_text": str(item or "")}
    return {
        "raw_text": _truncate(str(candidate.get("raw_text") or candidate.get("source_text") or ""), 600),
        "movetext": _truncate(str(candidate.get("movetext") or ""), 500),
        "legal_from_fen": bool(candidate.get("legal_from_fen")),
        "warnings": _string_list(candidate.get("warnings"))[:8],
    }


def _repair_schema() -> dict[str, Any]:
    confidence_object = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fen_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "fen": {"type": "string"},
                        "confidence": confidence_object,
                        "notes": {"type": "string"},
                    },
                    "required": ["fen", "confidence", "notes"],
                },
            },
            "solution_line_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "movetext": {"type": "string"},
                        "confidence": confidence_object,
                        "notes": {"type": "string"},
                    },
                    "required": ["movetext", "confidence", "notes"],
                },
            },
            "ocr_token_repairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "raw": {"type": "string"},
                        "corrected": {"type": "string"},
                        "confidence": confidence_object,
                        "notes": {"type": "string"},
                    },
                    "required": ["raw", "corrected", "confidence", "notes"],
                },
            },
            "confidence": confidence_object,
            "requires_human_review": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        "required": [
            "fen_candidates",
            "solution_line_candidates",
            "ocr_token_repairs",
            "confidence",
            "requires_human_review",
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


def _candidate_list(value: Any, primary_key: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            text = str(item.get(primary_key) or item.get("text") or item.get("value") or "").strip()
            if not text:
                continue
            rows.append(
                {
                    primary_key: text,
                    "confidence": _clamp(item.get("confidence")),
                    "notes": str(item.get("notes") or ""),
                }
            )
        else:
            text = str(item or "").strip()
            if text:
                rows.append({primary_key: text, "confidence": 0.0, "notes": ""})
    return rows[:8]


def _repair_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        raw = str(item.get("raw") or "").strip()
        corrected = str(item.get("corrected") or "").strip()
        if not raw and not corrected:
            continue
        rows.append(
            {
                "raw": raw,
                "corrected": corrected,
                "confidence": _clamp(item.get("confidence")),
                "notes": str(item.get("notes") or ""),
            }
        )
    return rows[:20]


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


def _env_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_mode(value: Any) -> str:
    mode = str(value or "review_only").strip()
    return mode if mode in VALID_REPAIR_MODES else "review_only"


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _estimated_cost(response: dict[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    if not usage:
        return 0.0
    return 0.0


def main() -> int:
    print(json.dumps(openai_chess_repair_proposer_status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
