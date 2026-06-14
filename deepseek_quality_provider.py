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


DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_INPUT_CHARS = 12_000
ENV_FILES = (".env.local", ".env")
ENABLE_KEYS = ("KINDLEMASTER_DEEPSEEK_AUDIT",)

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass(frozen=True)
class DeepSeekAuditConfig:
    api_key: str
    model: str = DEFAULT_DEEPSEEK_MODEL
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS


class DeepSeekAuditProvider:
    """Optional DeepSeek-backed reviewer for audit evidence only.

    The provider summarizes bounded diagnostics and never returns data that is
    allowed to mutate EPUB, PGN, FEN, TOC, or conversion output.
    """

    name = "deepseek-audit"

    def __init__(self, config: DeepSeekAuditConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _http_transport

    def review_glyph_diagnostics(self, context: Mapping[str, Any]) -> dict[str, Any]:
        compact = _compact_glyph_context(context, self.config.max_input_chars)
        return self._review(
            audit_type="glyph_diagnostics",
            schema_hint={
                "glyph_clusters": "array of font/token/reason/page cluster summaries",
                "suspected_mappings": "array of hypotheses that require human confirmation",
                "false_positive_samples": "array of likely false-positive samples",
                "next_measurements": "array of recommended measurements",
            },
            instructions=(
                "Review chess PDF glyph diagnostics for triage. Use only supplied bounded evidence. "
                "Cluster repeated font/token/codepoint patterns and propose deterministic mapping hypotheses "
                "only as human-review suggestions. Do not generate PGN or FEN. Return JSON only."
            ),
            user_payload=compact,
        )

    def review_pgn_glyph_clusters(self, context: Mapping[str, Any]) -> dict[str, Any]:
        compact = _clip_mapping(context, self.config.max_input_chars)
        return self._review(
            audit_type="pgn_glyph_clusters",
            schema_hint={
                "token_clusters": "array of token/count/examples/reason summaries",
                "candidate_mappings": "array of draft token->SAN replacement suggestions",
                "near_accepted_records": "array of records that look closest to replayable PGN",
                "next_review_actions": "array of concrete manual review steps",
            },
            instructions=(
                "Review chess PGN OCR/glyph blockers for triage. Cluster repeated suspicious tokens and "
                "suggest draft SAN replacement candidates only as human-review evidence. Do not mark any "
                "mapping accepted and do not generate strict PGN. Return JSON only."
            ),
            user_payload=compact,
        )

    def review_chess_layout(self, context: Mapping[str, Any]) -> dict[str, Any]:
        compact = _compact_layout_context(context, self.config.max_input_chars)
        return self._review(
            audit_type="chess_layout",
            schema_hint={
                "layout_warnings": "array of diagram-record matching or page-order warnings",
                "unmatched_diagrams": "array of unmatched diagram summaries",
                "records_without_diagrams": "array of record ids/pages that may need diagram review",
                "next_measurements": "array of recommended measurements",
            },
            instructions=(
                "Review chess PDF logical layout diagnostics. Use only supplied diagram and PGN record metadata. "
                "Identify unmatched diagrams, records without diagrams, and suspicious page/order alignment. "
                "Do not rewrite HTML, PGN, FEN, or EPUB output. Return JSON only."
            ),
            user_payload=compact,
        )

    def review_conversion_quality(self, context: Mapping[str, Any]) -> dict[str, Any]:
        compact = _clip_mapping(context, self.config.max_input_chars)
        return self._review(
            audit_type="conversion_quality",
            schema_hint={
                "quality_findings": "array of compact OCR/TOC/layout audit findings",
                "risk_areas": "array of likely quality risks",
                "next_measurements": "array of recommended measurements",
            },
            instructions=(
                "Review KindleMaster conversion quality evidence. Use only supplied compact metrics and samples. "
                "Produce an audit report only. Do not propose direct EPUB mutations. Return JSON only."
            ),
            user_payload=compact,
        )

    def _review(self, *, audit_type: str, schema_hint: Mapping[str, Any], instructions: str, user_payload: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._chat_payload(
            instructions=instructions,
            user_payload={
                "audit_type": audit_type,
                "policy": {
                    "mode": "evidence_only",
                    "requires_human_confirmation": True,
                    "mutates_output": False,
                    "no_strict_pgn_export": True,
                },
                "expected_json_shape": schema_hint,
                "context": user_payload,
            },
        )
        response = self._call(payload)
        parsed = _extract_json(response)
        return _audit_result(
            audit_type=audit_type,
            model=self.config.model,
            parsed=parsed,
            response=response,
        )

    def _chat_payload(self, *, instructions: str, user_payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{instructions} Always include evidence_only=true, "
                        "requires_human_confirmation=true, mutates_output=false."
                    ),
                },
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        return self._transport(url, headers, payload, self.config.timeout_seconds)


def build_deepseek_audit_provider_from_env(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    transport: Transport | None = None,
) -> DeepSeekAuditProvider | None:
    resolved = _runtime_env(env=env, cwd=cwd)
    if not _deepseek_audit_enabled(resolved):
        return None
    api_key = str(resolved.get("DEEPSEEK_API_KEY", "") or "").strip()
    if not api_key:
        return None
    return DeepSeekAuditProvider(
        DeepSeekAuditConfig(
            api_key=api_key,
            model=str(resolved.get("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL,
            base_url=str(resolved.get("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL).strip() or DEFAULT_DEEPSEEK_BASE_URL,
            timeout_seconds=_coerce_float(resolved.get("KINDLEMASTER_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS),
            max_input_chars=max(
                1000,
                int(_coerce_float(resolved.get("KINDLEMASTER_DEEPSEEK_MAX_INPUT_CHARS"), DEFAULT_MAX_INPUT_CHARS)),
            ),
        ),
        transport=transport,
    )


def deepseek_audit_configuration_status(*, env: Mapping[str, str] | None = None, cwd: str | Path | None = None) -> dict[str, Any]:
    resolved = _runtime_env(env=env, cwd=cwd)
    enabled = _deepseek_audit_enabled(resolved)
    return {
        "enabled": enabled,
        "api_key_present": bool(str(resolved.get("DEEPSEEK_API_KEY", "") or "").strip()),
        "provider": "deepseek-audit" if enabled and str(resolved.get("DEEPSEEK_API_KEY", "") or "").strip() else "none",
        "model": str(resolved.get("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL),
        "base_url": str(resolved.get("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL),
        "endpoint": "/chat/completions",
        "mode": "evidence_only",
        "evidence_only": True,
        "requires_human_confirmation": True,
        "full_document_upload": False,
        "mutates_output": False,
    }


def build_deepseek_audit_payload(
    *,
    provider: DeepSeekAuditProvider,
    source_title: str,
    glyph_payload: Mapping[str, Any] | None = None,
    records: list[Any] | None = None,
    diagrams: list[Mapping[str, Any]] | None = None,
    conversion_quality: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    sections: dict[str, Any] = {}
    diagnostics = int((glyph_payload or {}).get("diagnostic_count", 0) or 0)
    if diagnostics:
        sections["glyph_diagnostics"] = provider.review_glyph_diagnostics(glyph_payload or {})

    layout_context = _layout_audit_context(records or [], diagrams or [], source_title=source_title)
    if layout_context["diagram_count"] or layout_context["record_count"]:
        sections["chess_layout"] = provider.review_chess_layout(layout_context)

    if conversion_quality:
        sections["conversion_quality"] = provider.review_conversion_quality(conversion_quality)

    if not sections:
        return None
    return {
        "source_title": source_title,
        "provider": provider.name,
        "model": provider.config.model,
        "mode": "evidence_only",
        "evidence_only": True,
        "requires_human_confirmation": True,
        "mutates_output": False,
        "full_document_upload": False,
        "sections": sections,
    }


def _layout_audit_context(records: list[Any], diagrams: list[Mapping[str, Any]], *, source_title: str) -> dict[str, Any]:
    compact_records: list[dict[str, Any]] = []
    for record in records[:120]:
        row = record.to_dict() if hasattr(record, "to_dict") else dict(record or {})
        compact_records.append(
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or "")[:120],
                "source_pages": list(row.get("source_pages") or [])[:8],
                "status": str(row.get("status") or ""),
                "warnings": list(row.get("warnings") or [])[:12],
                "has_pgn": bool(str(row.get("pgn") or "").strip()),
                "has_final_fen": bool(str(row.get("final_fen") or "").strip()),
            }
        )
    compact_diagrams: list[dict[str, Any]] = []
    for diagram in diagrams[:160]:
        compact_diagrams.append(
            {
                "id": str(diagram.get("id") or ""),
                "page_number": _safe_int(diagram.get("page_number", _safe_int(diagram.get("page_index", 0)) + 1)),
                "bbox": _bounded_list(diagram.get("bbox"), 4),
                "caption": str(diagram.get("caption") or "")[:120],
                "has_image": bool(str(diagram.get("image_data_uri") or "").strip()),
                "fen_candidate": str(diagram.get("fen_candidate") or "")[:120],
                "matched_record_id": str(diagram.get("matched_record_id") or ""),
                "match_confidence": _safe_float(diagram.get("match_confidence")),
            }
        )
    matched_ids = {item["matched_record_id"] for item in compact_diagrams if item.get("matched_record_id")}
    return {
        "source_title": source_title,
        "record_count": len(records),
        "diagram_count": len(diagrams),
        "records": compact_records,
        "diagrams": compact_diagrams,
        "summary": {
            "records_without_diagram_count": len([row for row in compact_records if row["id"] and row["id"] not in matched_ids]),
            "unmatched_diagram_count": len([row for row in compact_diagrams if not row.get("matched_record_id")]),
        },
    }


def _compact_glyph_context(context: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    compact_records: list[dict[str, Any]] = []
    for record in (context.get("records") or [])[:80]:
        if not isinstance(record, Mapping):
            continue
        diagnostics = []
        for diagnostic in (record.get("diagnostics") or [])[:8]:
            if not isinstance(diagnostic, Mapping):
                continue
            diagnostics.append(
                {
                    "font_name": str(diagnostic.get("font_name") or "Unknown")[:120],
                    "page": diagnostic.get("page"),
                    "span_index": diagnostic.get("span_index"),
                    "bbox": _bounded_list(diagnostic.get("bbox"), 4),
                    "reasons": [str(value)[:80] for value in (diagnostic.get("reasons") or [])[:8]],
                    "raw_text": str(diagnostic.get("raw_text") or "")[:240],
                    "codepoints": _compact_codepoints(diagnostic.get("codepoints")),
                }
            )
        compact_records.append(
            {
                "record_id": str(record.get("record_id") or "")[:120],
                "title": str(record.get("title") or "")[:160],
                "source_pages": list(record.get("source_pages") or [])[:8],
                "warnings": [str(value)[:80] for value in (record.get("warnings") or [])[:8]],
                "diagnostics": diagnostics,
            }
        )
    payload = {
        "source_title": str(context.get("source_title") or "")[:200],
        "warning": str(context.get("warning") or ""),
        "diagnostic_count": _safe_int(context.get("diagnostic_count")),
        "record_count": _safe_int(context.get("record_count")),
        "by_font": dict(list(dict(context.get("by_font") or {}).items())[:40]),
        "by_page": dict(list(dict(context.get("by_page") or {}).items())[:80]),
        "records": compact_records,
    }
    return _clip_mapping(payload, max_chars)


def _compact_layout_context(context: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    return _clip_mapping(dict(context), max_chars)


def _clip_mapping(payload: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return dict(payload)
    clipped = text[: max(0, max_chars - 160)]
    return {
        "truncated": True,
        "max_input_chars": max_chars,
        "json_prefix": clipped,
    }


def _compact_codepoints(value: Any) -> list[dict[str, Any]]:
    rows = []
    for item in (value or [])[:12]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "char": str(item.get("char") or "")[:8],
                "codepoint": str(item.get("codepoint") or "")[:16],
                "char_index": item.get("char_index"),
                "synthetic": bool(item.get("synthetic", False)),
            }
        )
    return rows


def _audit_result(*, audit_type: str, model: str, parsed: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(parsed)
    result.update(
        {
            "audit_type": audit_type,
            "provider": "deepseek-audit",
            "model": model,
            "evidence_only": True,
            "requires_human_confirmation": True,
            "mutates_output": False,
            "metadata": {
                "usage": dict(response.get("usage") or {}),
                "elapsed_ms": response.get("_elapsed_ms", 0),
            },
        }
    )
    return result


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


def _deepseek_audit_enabled(env: Mapping[str, str]) -> bool:
    return any(str(env.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"} for key in ENABLE_KEYS)


def _http_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"deepseek-http-{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"deepseek-network-error: {exc.reason}") from exc
    response_payload.setdefault("_elapsed_ms", max(0, int(round((time.perf_counter() - started) * 1000))))
    return response_payload


def _extract_json(response: Mapping[str, Any]) -> dict[str, Any]:
    text = ""
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else {}
        if isinstance(message, Mapping):
            text = str(message.get("content") or "")
    if not text:
        text = str(response.get("output_text") or "")
    if not text:
        return {}
    text = _strip_json_fence(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("deepseek-invalid-json") from exc
    return parsed if isinstance(parsed, dict) else {}


def _strip_json_fence(text: str) -> str:
    value = str(text or "").strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def _coerce_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any) -> float:
    return _coerce_float(value, 0.0)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bounded_list(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(value)[:limit]
