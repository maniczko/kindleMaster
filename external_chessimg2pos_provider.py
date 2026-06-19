from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


_DEFAULT_TIMEOUT_MS = 4000
_MODULE_NAME = "chessimg2pos"
_PROVIDER_NAME = "chessimg2pos"
_METHOD_NAME = "external-chessimg2pos"
_PAYLOAD_SHAPE_VERSION = 2


@dataclass(frozen=True)
class ChessImg2PosProviderResult:
    fen: str = ""
    placement: str = ""
    confidence: float = 0.0
    effective_confidence: float = 0.0
    placement_confidence: float | None = None
    provider: str = _PROVIDER_NAME
    provider_version: str = "unknown"
    method: str = _METHOD_NAME
    warnings: list[str] = field(default_factory=list)
    raw_response: str = ""
    runtime_ms: int = 0
    debug_payload: dict[str, Any] = field(default_factory=dict)
    squares: list[dict[str, Any]] = field(default_factory=list)
    king_squares: dict[str, str] = field(default_factory=dict)
    piece_count_summary: dict[str, int] = field(default_factory=dict)
    variant_role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fen": self.fen,
            "placement": self.placement,
            "confidence": round(float(self.confidence or 0.0), 3),
            "effective_confidence": round(float(self.effective_confidence or 0.0), 3),
            "placement_confidence": (
                None if self.placement_confidence is None else round(float(self.placement_confidence or 0.0), 3)
            ),
            "provider": self.provider,
            "provider_version": self.provider_version,
            "method": self.method,
            "warnings": list(self.warnings or []),
            "raw_response": self.raw_response,
            "runtime_ms": int(self.runtime_ms or 0),
            "debug_payload": dict(self.debug_payload or {}),
            "squares": [dict(square) for square in self.squares],
            "king_squares": dict(self.king_squares or {}),
            "piece_count_summary": dict(self.piece_count_summary or {}),
            "variant_role": self.variant_role,
            "provider_payload_shape_version": _PAYLOAD_SHAPE_VERSION,
        }


def chessimg2pos_provider_settings() -> dict[str, Any]:
    mode = str(os.getenv("KINDLEMASTER_CHESSIMG2POS_MODE", "auto") or "auto").strip().lower()
    if mode not in {"auto", "import", "subprocess"}:
        mode = "auto"
    timeout_ms = _coerce_int(os.getenv("KINDLEMASTER_CHESSIMG2POS_TIMEOUT_MS"), _DEFAULT_TIMEOUT_MS)
    return {
        "enabled": _env_truthy("KINDLEMASTER_CHESSIMG2POS_ENABLED"),
        "mode": mode,
        "python": str(os.getenv("KINDLEMASTER_CHESSIMG2POS_PYTHON", "") or "").strip(),
        "model_path": str(os.getenv("KINDLEMASTER_CHESSIMG2POS_MODEL_PATH", "") or "").strip(),
        "timeout_ms": max(250, timeout_ms),
    }


def chessimg2pos_provider_available(settings: Mapping[str, Any] | None = None) -> bool:
    config = dict(settings or chessimg2pos_provider_settings())
    return bool(config.get("enabled"))


def resolve_chessimg2pos_provider_version(settings: Mapping[str, Any] | None = None) -> str:
    config = dict(settings or chessimg2pos_provider_settings())
    mode = str(config.get("mode") or "auto")
    if mode in {"auto", "import"}:
        version = _resolve_import_provider_version()
        if version:
            return version
    if mode in {"auto", "subprocess"} and str(config.get("python") or "").strip():
        version = _resolve_subprocess_provider_version(config)
        if version:
            return version
    return "unknown"


def recognize_fen_with_chessimg2pos(
    crop_path: str | Path,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    selected_preprocess_variant: str = "",
    display_variant_used: str = "",
    page: int | None = None,
    settings: Mapping[str, Any] | None = None,
    variant_role: str = "",
) -> ChessImg2PosProviderResult:
    config = dict(settings or chessimg2pos_provider_settings())
    started = time.perf_counter()
    if not bool(config.get("enabled")):
        return ChessImg2PosProviderResult(warnings=["external_fen_provider_failed"], runtime_ms=0, variant_role=variant_role)

    preferred_modes = _preferred_modes(config)
    last_result: ChessImg2PosProviderResult | None = None
    for mode in preferred_modes:
        if mode == "import":
            result = _recognize_import_mode(
                crop_path,
                bbox=bbox,
                selected_preprocess_variant=selected_preprocess_variant,
                display_variant_used=display_variant_used,
                page=page,
                settings=config,
                variant_role=variant_role,
            )
        else:
            result = _recognize_subprocess_mode(
                crop_path,
                bbox=bbox,
                selected_preprocess_variant=selected_preprocess_variant,
                display_variant_used=display_variant_used,
                page=page,
                settings=config,
                variant_role=variant_role,
            )
        runtime_ms = int((time.perf_counter() - started) * 1000)
        normalized = _normalize_provider_result(result, runtime_ms=runtime_ms, variant_role=variant_role)
        if "external_fen_provider_failed" not in normalized.warnings and "external_fen_provider_timeout" not in normalized.warnings:
            return normalized
        last_result = normalized
    return last_result or ChessImg2PosProviderResult(
        warnings=["external_fen_provider_failed"],
        runtime_ms=int((time.perf_counter() - started) * 1000),
        variant_role=variant_role,
    )


def _preferred_modes(settings: Mapping[str, Any]) -> list[str]:
    mode = str(settings.get("mode") or "auto")
    if mode == "import":
        return ["import"]
    if mode == "subprocess":
        return ["subprocess"]
    modes = ["import"]
    if str(settings.get("python") or "").strip():
        modes.append("subprocess")
    return modes


def _recognize_import_mode(
    crop_path: str | Path,
    *,
    bbox: tuple[float, float, float, float] | None,
    selected_preprocess_variant: str,
    display_variant_used: str,
    page: int | None,
    settings: Mapping[str, Any],
    variant_role: str,
) -> Any:
    try:
        module = importlib.import_module(_MODULE_NAME)
    except Exception as exc:
        return {"warnings": ["external_fen_provider_failed"], "raw_response": str(exc), "provider_version": "unknown"}
    provider_version = str(getattr(module, "__version__", "") or "unknown")
    model_path = str(settings.get("model_path") or "").strip()
    payload: Any
    try:
        if hasattr(module, "ChessPositionPredictor") and model_path:
            predictor = module.ChessPositionPredictor(model_path)
            try:
                payload = predictor.predict_chessboard(str(crop_path), return_tiles=True)
            except TypeError:
                payload = predictor.predict_chessboard(str(crop_path))
        elif hasattr(module, "predict_fen"):
            payload = module.predict_fen(str(crop_path))
        elif hasattr(module, "ChessPositionPredictor"):
            predictor = module.ChessPositionPredictor(model_path) if model_path else module.ChessPositionPredictor()
            try:
                payload = predictor.predict_chessboard(str(crop_path), return_tiles=True)
            except TypeError:
                payload = predictor.predict_chessboard(str(crop_path))
        else:
            return {
                "warnings": ["external_fen_provider_failed"],
                "raw_response": "No supported chessimg2pos API found",
                "provider_version": provider_version,
            }
    except Exception as exc:
        return {
            "warnings": ["external_fen_provider_failed"],
            "raw_response": str(exc),
            "provider_version": provider_version,
        }
    return {
        "payload": payload,
        "provider_version": provider_version,
        "debug_payload": {
            "page": page,
            "bbox": list(bbox) if bbox is not None else None,
            "selected_preprocess_variant": selected_preprocess_variant,
            "display_variant_used": display_variant_used,
            "variant_role": variant_role,
            "mode": "import",
        },
    }


def _recognize_subprocess_mode(
    crop_path: str | Path,
    *,
    bbox: tuple[float, float, float, float] | None,
    selected_preprocess_variant: str,
    display_variant_used: str,
    page: int | None,
    settings: Mapping[str, Any],
    variant_role: str,
) -> Any:
    python_exe = str(settings.get("python") or "").strip()
    if not python_exe:
        return {"warnings": ["external_fen_provider_failed"], "raw_response": "Missing KINDLEMASTER_CHESSIMG2POS_PYTHON"}
    model_path = str(settings.get("model_path") or "").strip()
    timeout_ms = max(250, _coerce_int(settings.get("timeout_ms"), _DEFAULT_TIMEOUT_MS))
    helper_script = Path(__file__).resolve().parent / "scripts" / "run_chessimg2pos_provider.py"
    try:
        completed = subprocess.run(
            [python_exe, str(helper_script), str(crop_path), "--json", "--model-path", model_path],
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout_ms / 1000.0),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {"warnings": ["external_fen_provider_timeout"], "raw_response": str(exc)}
    except Exception as exc:
        return {"warnings": ["external_fen_provider_failed"], "raw_response": str(exc)}
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    if completed.returncode != 0:
        return {
            "warnings": ["external_fen_provider_failed"],
            "raw_response": stderr or stdout or f"returncode={completed.returncode}",
        }
    try:
        payload = json.loads(stdout or "{}")
    except Exception:
        return {"warnings": ["external_fen_provider_failed"], "raw_response": stdout or stderr}
    payload.setdefault(
        "debug_payload",
        {
            "page": page,
            "bbox": list(bbox) if bbox is not None else None,
            "selected_preprocess_variant": selected_preprocess_variant,
            "display_variant_used": display_variant_used,
            "variant_role": variant_role,
            "mode": "subprocess",
        },
    )
    return payload


def _normalize_provider_result(result: Any, *, runtime_ms: int, variant_role: str) -> ChessImg2PosProviderResult:
    if isinstance(result, ChessImg2PosProviderResult):
        return ChessImg2PosProviderResult(
            fen=result.fen,
            placement=result.placement,
            confidence=result.confidence,
            effective_confidence=result.effective_confidence or _external_provider_effective_confidence(result.confidence, variant_role=variant_role),
            placement_confidence=result.placement_confidence,
            provider=result.provider,
            provider_version=result.provider_version,
            method=result.method,
            warnings=list(result.warnings or []),
            raw_response=result.raw_response,
            runtime_ms=runtime_ms or result.runtime_ms,
            debug_payload=dict(result.debug_payload or {}),
            squares=[dict(square) for square in result.squares],
            king_squares=dict(result.king_squares or {}),
            piece_count_summary=dict(result.piece_count_summary or {}),
            variant_role=variant_role or result.variant_role,
        )
    if isinstance(result, Mapping):
        payload = result.get("payload", result)
        provider_version = str(result.get("provider_version") or "unknown")
        warnings = [str(warning) for warning in result.get("warnings") or [] if str(warning).strip()]
        raw_response = str(result.get("raw_response") or "")
        debug_payload = dict(result.get("debug_payload") or {}) if isinstance(result.get("debug_payload"), Mapping) else {}
    else:
        payload = result
        provider_version = "unknown"
        warnings = []
        raw_response = ""
        debug_payload = {}

    fen, placement, confidence, placement_confidence, payload_response, payload_warnings, payload_debug, squares = _extract_payload_fields(payload)
    merged_warnings = sorted(set([*warnings, *payload_warnings]))
    merged_debug = dict(debug_payload)
    merged_debug.update(payload_debug)
    normalized_fen, normalize_warnings = _normalize_fen_or_placement(fen or placement)
    merged_warnings = sorted(set([*merged_warnings, *normalize_warnings]))
    normalized_placement = normalized_fen.split()[0] if normalized_fen else placement
    king_squares = _derive_king_squares(normalized_placement)
    piece_count_summary = _derive_piece_count_summary(normalized_placement, squares)
    return ChessImg2PosProviderResult(
        fen=normalized_fen,
        placement=normalized_placement,
        confidence=confidence,
        effective_confidence=_external_provider_effective_confidence(confidence, variant_role=variant_role),
        placement_confidence=placement_confidence,
        provider=_PROVIDER_NAME,
        provider_version=provider_version,
        method=_METHOD_NAME,
        warnings=merged_warnings,
        raw_response=raw_response or payload_response,
        runtime_ms=runtime_ms,
        debug_payload=merged_debug,
        squares=squares,
        king_squares=king_squares,
        piece_count_summary=piece_count_summary,
        variant_role=variant_role,
    )


def _extract_payload_fields(payload: Any) -> tuple[str, str, float, float | None, str, list[str], dict[str, Any], list[dict[str, Any]]]:
    if isinstance(payload, str):
        normalized = payload.strip()
        return normalized, normalized, 0.0, None, normalized, [], {}, []
    if not isinstance(payload, Mapping):
        return "", "", 0.0, None, repr(payload), ["external_fen_provider_failed"], {}, []
    fen = str(payload.get("fen") or payload.get("placement") or "").strip()
    placement = str(payload.get("placement") or "").strip()
    confidence = _coerce_float(payload.get("confidence"), 0.0)
    placement_confidence = _coerce_optional_float(
        payload.get("placement_confidence", payload.get("board_confidence", payload.get("confidence")))
    )
    raw_response = json.dumps(payload, ensure_ascii=False, default=str)
    warnings = [str(warning) for warning in payload.get("warnings") or [] if str(warning).strip()]
    debug_payload = dict(payload)
    squares = _normalize_squares_payload(payload)
    return fen, placement, confidence, placement_confidence, raw_response, warnings, debug_payload, squares


def _normalize_fen_or_placement(value: str) -> tuple[str, list[str]]:
    candidate = str(value or "").strip()
    if not candidate:
        return "", []
    if len(candidate.split()) == 1:
        return f"{candidate} w - - 0 1", []
    return candidate, []


def _normalize_squares_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    squares: list[dict[str, Any]] = []
    if isinstance(payload.get("tiles"), list):
        for index, item in enumerate(payload.get("tiles") or []):
            normalized = _normalize_square_item(item, fallback_index=index)
            if normalized is not None:
                squares.append(normalized)
    if not squares and isinstance(payload.get("squares"), list):
        for index, item in enumerate(payload.get("squares") or []):
            normalized = _normalize_square_item(item, fallback_index=index)
            if normalized is not None:
                squares.append(normalized)
    board = payload.get("board")
    if not squares and isinstance(board, list):
        for row_index, row in enumerate(board[:8]):
            if not isinstance(row, list):
                continue
            for col_index, piece in enumerate(row[:8]):
                piece_text = str(piece or "").strip()
                if not piece_text or piece_text == ".":
                    continue
                squares.append(
                    {
                        "square": _square_name_from_coords(row_index, col_index),
                        "piece": piece_text,
                        "confidence": None,
                    }
                )
    deduped: dict[str, dict[str, Any]] = {}
    for item in squares:
        square_name = str(item.get("square") or "").strip()
        if not square_name:
            continue
        deduped[square_name] = item
    return [deduped[key] for key in sorted(deduped)]


def _normalize_square_item(item: Any, *, fallback_index: int) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    square = str(item.get("square") or item.get("name") or "").strip().lower()
    if not square:
        row = item.get("row")
        col = item.get("col", item.get("column"))
        if row is not None and col is not None:
            try:
                square = _square_name_from_coords(int(row), int(col))
            except (TypeError, ValueError):
                square = ""
    if not square and fallback_index < 64:
        square = _square_name_from_coords(fallback_index // 8, fallback_index % 8)
    if not square:
        return None
    piece = str(item.get("piece") or item.get("label") or item.get("value") or "").strip()
    confidence = _coerce_optional_float(item.get("confidence", item.get("score", item.get("prob"))))
    return {
        "square": square,
        "piece": piece,
        "confidence": confidence,
    }


def _square_name_from_coords(row: int, col: int) -> str:
    file_name = "abcdefgh"[max(0, min(7, col))]
    rank_name = str(8 - max(0, min(7, row)))
    return f"{file_name}{rank_name}"


def _derive_king_squares(placement: str) -> dict[str, str]:
    king_squares: dict[str, str] = {}
    ranks = str(placement or "").split("/")
    if len(ranks) != 8:
        return king_squares
    for row_index, rank in enumerate(ranks):
        col_index = 0
        for char in rank:
            if char.isdigit():
                col_index += int(char)
                continue
            if char == "K":
                king_squares["white"] = _square_name_from_coords(row_index, col_index)
            elif char == "k":
                king_squares["black"] = _square_name_from_coords(row_index, col_index)
            col_index += 1
    return king_squares


def _derive_piece_count_summary(placement: str, squares: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    if squares:
        for square in squares:
            piece = str(square.get("piece") or "").strip()
            if piece:
                summary[piece] = int(summary.get(piece, 0)) + 1
        if summary:
            return dict(sorted(summary.items()))
    for char in str(placement or ""):
        if char.isalpha():
            summary[char] = int(summary.get(char, 0)) + 1
    return dict(sorted(summary.items()))


def _external_provider_effective_confidence(confidence: float, *, variant_role: str = "") -> float:
    base = max(0.0, min(1.0, float(confidence or 0.0)))
    penalty = 0.03
    if str(variant_role or "").strip().lower() == "reader_visible":
        penalty = 0.04
    return max(0.0, min(1.0, base - penalty))


def _resolve_import_provider_version() -> str:
    try:
        module = importlib.import_module(_MODULE_NAME)
    except Exception:
        return ""
    return str(getattr(module, "__version__", "") or "unknown")


def _resolve_subprocess_provider_version(settings: Mapping[str, Any]) -> str:
    python_exe = str(settings.get("python") or "").strip()
    if not python_exe:
        return ""
    helper_script = Path(__file__).resolve().parent / "scripts" / "run_chessimg2pos_provider.py"
    try:
        completed = subprocess.run(
            [python_exe, str(helper_script), "--version"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "").strip() or "unknown"


def _env_truthy(key: str) -> bool:
    return str(os.getenv(key, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ChessImg2PosProviderResult",
    "chessimg2pos_provider_available",
    "chessimg2pos_provider_settings",
    "recognize_fen_with_chessimg2pos",
    "resolve_chessimg2pos_provider_version",
]
