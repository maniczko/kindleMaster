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
POLICY_ACKNOWLEDGEMENT = "review_only_no_corpus_promotion"
EVIDENCE_LEVELS = {"clear", "ambiguous", "insufficient_crop", "missing_crop"}
SIDE_TO_MOVE_VALUES = {"w", "b", "unknown"}
SIDE_TO_MOVE_EVIDENCE = {"marker", "caption", "inferred", "none"}
FORBIDDEN_AUTHORITY_FIELDS = {"verified", "accepted", "accepted_for_corpus", "label_status", "verified_by", "verified_at"}

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

    def propose_chess_fen_from_crop(self, context: dict[str, Any]) -> Mapping[str, Any]:
        """Return an AI-suggested FEN candidate from a diagram crop.

        This is intentionally review-only: the caller must run deterministic
        FEN validation, render checks, and any human/template gate before an
        output can become accepted.
        """
        if not self.api_key:
            return self._candidate_disabled()

        payload = self._candidate_payload(context)
        response = self._call(payload)
        parsed = _extract_json(response)
        fen = str(parsed.get("fen") or "").strip()
        side_to_move_evidence = _side_to_move_evidence(parsed.get("side_to_move"))
        issues = _policy_and_authority_issues(parsed)
        return {
            "status": "ai_suggested",
            "provider": self.name,
            "model": self.model,
            "mode": "review_only",
            "mutates_fen": False,
            "fen": fen,
            "side_to_move": side_to_move_evidence["value"],
            "side_to_move_evidence": side_to_move_evidence,
            "confidence": _clamp(parsed.get("confidence")),
            "uncertain_squares": _string_list(parsed.get("uncertain_squares")),
            "square_diffs": _square_diff_list(parsed.get("square_diffs")),
            "cannot_verify_reason": str(parsed.get("cannot_verify_reason") or ""),
            "evidence_level": _evidence_level(parsed.get("evidence_level")),
            "crop_quality_notes": _string_list(parsed.get("crop_quality_notes")),
            "policy_acknowledgement": str(parsed.get("policy_acknowledgement") or ""),
            "issues": issues,
            "reason": str(parsed.get("reason") or ""),
            "needs_review": bool(parsed.get("needs_review", True)),
            "estimated_cost_usd": _estimated_cost(response),
            "metadata": {
                "usage": response.get("usage", {}),
                "response_id": str(response.get("id") or ""),
            },
            "changed_output": False,
        }

    def review_chess_fen(self, context: dict[str, Any]) -> Mapping[str, Any]:
        candidate = dict(context.get("candidate") or {})
        candidate_fen = str(candidate.get("fen") or "")
        if not self.api_key:
            return self._review_disabled(candidate_fen)

        payload = self._responses_payload(context)
        response = self._call(payload)
        parsed = _extract_json(response)
        model_issues = _string_list(parsed.get("issues"))
        issues = [*model_issues, *_policy_and_authority_issues(parsed)]
        ambiguous_squares = _string_list(parsed.get("ambiguous_squares"))
        review_opinion = str(parsed.get("review_opinion") or "").strip()
        approved = bool(parsed.get("approved")) or review_opinion == "supports_candidate"
        requires_review = bool(parsed.get("requires_review", True))
        if ambiguous_squares and not requires_review:
            requires_review = True
            issues.append("ambiguous_squares_require_manual_review")
        suggested_fen = str(parsed.get("suggested_fen") or parsed.get("corrected_fen") or "")
        if not review_opinion:
            review_opinion = _review_opinion(
                approved=approved,
                requires_review=requires_review,
                issues=model_issues,
                ambiguous_squares=ambiguous_squares,
            )
        return {
            "status": "reviewed",
            "provider": self.name,
            "model": self.model,
            "mode": "review_only",
            "mutates_fen": False,
            "candidate_fen": candidate_fen,
            "suggested_label": suggested_fen,
            "suggested_fen": suggested_fen,
            "approved": approved,
            "review_opinion": review_opinion,
            "requires_review": requires_review,
            "ambiguous_squares": ambiguous_squares,
            "square_diffs": _square_diff_list(parsed.get("square_diffs")),
            "side_to_move": _side_to_move_evidence(parsed.get("side_to_move")),
            "cannot_verify_reason": str(parsed.get("cannot_verify_reason") or ""),
            "evidence_level": _evidence_level(parsed.get("evidence_level")),
            "crop_quality_notes": _string_list(parsed.get("crop_quality_notes")),
            "policy_acknowledgement": str(parsed.get("policy_acknowledgement") or ""),
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

    def _candidate_disabled(self) -> dict[str, Any]:
        return {
            "status": "needs_review",
            "provider": self.name,
            "model": self.model,
            "mode": "review_only",
            "mutates_fen": False,
            "fen": "",
            "side_to_move": "unknown",
            "side_to_move_evidence": {"value": "unknown", "evidence": "none", "confidence": 0.0},
            "confidence": 0.0,
            "uncertain_squares": [],
            "square_diffs": [],
            "cannot_verify_reason": "provider_not_configured",
            "evidence_level": "missing_crop",
            "crop_quality_notes": [],
            "policy_acknowledgement": POLICY_ACKNOWLEDGEMENT,
            "reason": "live OpenAI chess FEN candidate review is opt-in and requires env configuration",
            "needs_review": True,
            "issues": ["live_openai_review_not_configured"],
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
            "suggested_fen": "",
            "approved": False,
            "review_opinion": "uncertain",
            "requires_review": True,
            "ambiguous_squares": [],
            "square_diffs": [],
            "side_to_move": {"value": "unknown", "evidence": "none", "confidence": 0.0},
            "cannot_verify_reason": "provider_not_configured",
            "evidence_level": "missing_crop",
            "crop_quality_notes": [],
            "policy_acknowledgement": POLICY_ACKNOWLEDGEMENT,
            "issues": ["live_openai_review_not_configured"],
            "reason": "live OpenAI chess FEN review is opt-in and requires env configuration",
            "changed_output": False,
        }

    def _candidate_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": json.dumps(_compact_candidate_context(context), ensure_ascii=False),
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
                "You are a conservative chess diagram-to-FEN candidate generator for KindleMaster. "
                "Inspect only the supplied board crop and metadata. Return JSON only. The fen field "
                "must be either an empty string or a complete six-field FEN: piece placement, active "
                "color, castling availability, en-passant target, halfmove clock, and fullmove number. "
                "Use '-' for unavailable castling/en-passant, and '0 1' for unknown clocks. Return "
                "side_to_move as an object with value, evidence, and confidence. If side-to-move is not "
                "explicitly visible from a marker or caption, set value='unknown' and evidence='none'. "
                "If any occupied square is uncertain, keep needs_review=true and list uncertain_squares. "
                "Never output verified, accepted, accepted_for_corpus, label_status, verified_by, or verified_at. "
                "Your output is review evidence only and must include "
                f"policy_acknowledgement='{POLICY_ACKNOWLEDGEMENT}'."
            ),
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "kindlemaster_chess_fen_candidate",
                    "strict": True,
                    "schema": _candidate_schema(),
                }
            },
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
                "candidate data and board crop if present. Return JSON only. Do not invent pieces. Return a "
                "review_opinion, not an authoritative approval. If you disagree with the candidate, include "
                "square_diffs such as e5 candidate black pawn, observed black rook. Prefer requires_review=true "
                "over guessing. Do not infer side-to-move without marker or caption evidence. Never output "
                "verified, accepted, accepted_for_corpus, label_status, verified_by, or verified_at. Your response "
                f"is audit evidence only, must include policy_acknowledgement='{POLICY_ACKNOWLEDGEMENT}', and must "
                "never mutate EPUB output or corpus labels."
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


def _candidate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fen": {"type": "string"},
            "side_to_move": _side_to_move_schema(),
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "uncertain_squares": {"type": "array", "items": {"type": "string"}},
            "square_diffs": _square_diffs_schema(),
            "cannot_verify_reason": {"type": "string"},
            "evidence_level": {"type": "string", "enum": ["clear", "ambiguous", "insufficient_crop", "missing_crop"]},
            "crop_quality_notes": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "needs_review": {"type": "boolean"},
            "policy_acknowledgement": {"type": "string", "enum": [POLICY_ACKNOWLEDGEMENT]},
        },
        "required": [
            "fen",
            "side_to_move",
            "confidence",
            "uncertain_squares",
            "square_diffs",
            "cannot_verify_reason",
            "evidence_level",
            "crop_quality_notes",
            "reason",
            "needs_review",
            "policy_acknowledgement",
        ],
    }


def _review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "review_opinion": {"type": "string", "enum": ["supports_candidate", "flags_candidate", "uncertain", "cannot_verify"]},
            "candidate_fen": {"type": "string"},
            "suggested_fen": {"type": "string"},
            "requires_review": {"type": "boolean"},
            "ambiguous_squares": {"type": "array", "items": {"type": "string"}},
            "square_diffs": _square_diffs_schema(),
            "side_to_move": _side_to_move_schema(),
            "issues": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "cannot_verify_reason": {"type": "string"},
            "evidence_level": {"type": "string", "enum": ["clear", "ambiguous", "insufficient_crop", "missing_crop"]},
            "crop_quality_notes": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
            "policy_acknowledgement": {"type": "string", "enum": [POLICY_ACKNOWLEDGEMENT]},
        },
        "required": [
            "review_opinion",
            "candidate_fen",
            "suggested_fen",
            "requires_review",
            "ambiguous_squares",
            "square_diffs",
            "side_to_move",
            "issues",
            "confidence",
            "cannot_verify_reason",
            "evidence_level",
            "crop_quality_notes",
            "notes",
            "policy_acknowledgement",
        ],
    }


def _side_to_move_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": "string", "enum": ["w", "b", "unknown"]},
            "evidence": {"type": "string", "enum": ["marker", "caption", "inferred", "none"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["value", "evidence", "confidence"],
    }


def _square_diffs_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "square": {"type": "string"},
                "candidate_piece": {"type": "string"},
                "observed_piece": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": ["square", "candidate_piece", "observed_piece", "confidence", "reason"],
        },
    }


def _review_opinion(*, approved: bool, requires_review: bool, issues: list[str], ambiguous_squares: list[str]) -> str:
    if approved and not requires_review and not issues and not ambiguous_squares:
        return "supports_candidate"
    if issues or ambiguous_squares:
        return "flags_candidate"
    return "uncertain"


def _compact_candidate_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagram_id": str(context.get("diagram_id") or ""),
        "page": context.get("page"),
        "caption": str(context.get("caption") or "")[:160],
        "bbox": context.get("bbox"),
        "side_to_move_hint": str(context.get("side_to_move_hint") or "unknown"),
        "has_image": bool(context.get("has_image") or isinstance(context.get("image_data"), bytes)),
        "policy": "review_only_no_epub_or_pgn_mutation",
        "output_contract": {
            "fen": "empty string or full six-field FEN, never piece-placement-only",
            "side_to_move": "object with value=w|b|unknown, evidence=marker|caption|inferred|none, confidence=0..1",
            "needs_review": "true unless every occupied square and side-to-move are clear",
        },
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


def _square_diff_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    diffs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        diffs.append(
            {
                "square": str(item.get("square") or ""),
                "candidate_piece": str(item.get("candidate_piece") or ""),
                "observed_piece": str(item.get("observed_piece") or item.get("manual_piece") or ""),
                "confidence": _clamp(item.get("confidence")),
                "reason": str(item.get("reason") or ""),
            }
        )
    return diffs


def _side_to_move_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        side = str(value.get("value") or "unknown").strip().lower()
        evidence = str(value.get("evidence") or "none").strip().lower()
        return {
            "value": side if side in SIDE_TO_MOVE_VALUES else "unknown",
            "evidence": evidence if evidence in SIDE_TO_MOVE_EVIDENCE else "none",
            "confidence": _clamp(value.get("confidence")),
        }
    side = str(value or "unknown").strip().lower()
    return {
        "value": side if side in SIDE_TO_MOVE_VALUES else "unknown",
        "evidence": "inferred" if side in {"w", "b"} else "none",
        "confidence": 0.0,
    }


def _evidence_level(value: Any) -> str:
    level = str(value or "ambiguous").strip()
    return level if level in EVIDENCE_LEVELS else "ambiguous"


def _policy_and_authority_issues(parsed: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if str(parsed.get("policy_acknowledgement") or "") != POLICY_ACKNOWLEDGEMENT:
        issues.append("ai_policy_acknowledgement_missing")
    if any(field in parsed for field in FORBIDDEN_AUTHORITY_FIELDS):
        issues.append("ai_authoritative_field_ignored")
    return issues


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
