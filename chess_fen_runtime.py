from __future__ import annotations

import io
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from chess_fen_square_model import predict_portable_fen_board
from chess_position_recognizer import ChessFenResult


DEFAULT_FEN_MODEL_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "chess"
    / "chess_fen_square_rbf_svm_v2.npz"
)
DEFAULT_MARKER_CALIBRATION_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "chess"
    / "chess_marker_calibration_yusupov_v1.json"
)
_RUNTIME_CONTEXT: ContextVar[dict[str, str]] = ContextVar(
    "kindlemaster_chess_fen_runtime",
    default={"mode": "off", "marker_source_profile": "", "marker_calibration_path": ""},
)


@contextmanager
def fen_runtime_scope(config: Any):
    token = _RUNTIME_CONTEXT.set(
        {
            "mode": str(getattr(config, "chess_fen_model_mode", "off") or "off").strip().lower(),
            "marker_source_profile": str(
                getattr(config, "chess_marker_source_profile", "yusupov-fundamentals")
                or "yusupov-fundamentals"
            ).strip(),
            "marker_calibration_path": str(
                getattr(config, "chess_marker_calibration_path", DEFAULT_MARKER_CALIBRATION_PATH)
                or DEFAULT_MARKER_CALIBRATION_PATH
            ).strip(),
        }
    )
    try:
        yield
    finally:
        _RUNTIME_CONTEXT.reset(token)


def current_marker_runtime_calibration(source_profile: str) -> dict[str, Any]:
    context = _RUNTIME_CONTEXT.get()
    if context.get("mode") not in {"shadow", "assist"}:
        return {"status": "disabled", "provenance": {"mode": context.get("mode") or "off"}}
    configured_profile = str(context.get("marker_source_profile") or "")
    if configured_profile != str(source_profile or ""):
        return {
            "status": "invalid",
            "error": "marker_calibration_profile_mismatch",
            "provenance": {
                "configured_profile": configured_profile,
                "requested_profile": str(source_profile or ""),
            },
        }
    return load_marker_confidence_calibration(
        source_profile=configured_profile,
        calibration_path=context.get("marker_calibration_path") or DEFAULT_MARKER_CALIBRATION_PATH,
    )


def load_marker_confidence_calibration(
    *,
    source_profile: str,
    calibration_path: str | Path = DEFAULT_MARKER_CALIBRATION_PATH,
) -> dict[str, Any]:
    path = Path(calibration_path).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    manifest_path = path.with_suffix(".manifest.json")
    provenance = {
        "artifact_path": str(path),
        "manifest_path": str(manifest_path),
        "source_profile": str(source_profile or ""),
    }
    if not path.is_file() or not manifest_path.is_file():
        return {
            "status": "unavailable",
            "error": "marker_calibration_artifact_missing",
            "provenance": provenance,
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_hash = str(manifest.get("artifact_sha256") or "")
        calibration = _load_marker_confidence_calibration_cached(
            str(path.resolve()),
            path.stat().st_mtime_ns,
            path.stat().st_size,
            expected_hash,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return {
            "status": "invalid",
            "error": str(error) or "marker_calibration_contract_invalid",
            "provenance": provenance,
        }
    if str(calibration.get("source_profile") or "") != str(source_profile or ""):
        return {
            "status": "invalid",
            "error": "marker_calibration_profile_mismatch",
            "provenance": provenance,
        }
    provenance.update(
        {
            "artifact_sha256": expected_hash,
            "calibration_version": calibration.get("calibration_version"),
            "classifier_version": calibration.get("classifier_version"),
            "source_split": calibration.get("source_split"),
            "holdout_used_for_tuning": calibration.get("holdout_used_for_tuning"),
        }
    )
    return {
        "status": "ready",
        "calibration": calibration,
        "provenance": provenance,
    }


@lru_cache(maxsize=4)
def _load_marker_confidence_calibration_cached(
    artifact_path: str,
    artifact_mtime_ns: int,
    artifact_size: int,
    expected_hash: str,
) -> dict[str, Any]:
    del artifact_mtime_ns, artifact_size
    path = Path(artifact_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected_hash or digest != expected_hash:
        raise ValueError("marker_calibration_artifact_hash_mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != "kindlemaster.chess.marker_confidence_calibration.v1"
        or payload.get("status") != "ready"
        or not isinstance(payload.get("points"), list)
    ):
        raise ValueError("marker_calibration_contract_invalid")
    return dict(payload)


def apply_fen_square_runtime(
    recognition: ChessFenResult,
    crop_bytes: bytes,
    *,
    model_path: str | Path = DEFAULT_FEN_MODEL_PATH,
    mode: str = "shadow",
) -> ChessFenResult:
    """Attach calibrated board evidence without crossing side-to-move trust."""
    normalized_mode = str(mode or "shadow").strip().lower()
    if normalized_mode == "off":
        return recognition
    resolved_model_path = Path(model_path).expanduser()
    if not resolved_model_path.is_absolute():
        resolved_model_path = Path(__file__).resolve().parent / resolved_model_path
    try:
        with Image.open(io.BytesIO(crop_bytes)) as source:
            board = source.convert("RGB")
        runtime = predict_portable_fen_board(
            board,
            model_path=resolved_model_path,
            mode=normalized_mode,
        )
    except Exception as error:
        runtime = {
            "schema": "kindlemaster.fen_square_runtime.v1",
            "status": "invalid",
            "mode": normalized_mode,
            "placement": "",
            "confidence": 0.0,
            "candidate_accepted": False,
            "publishable": False,
            "blockers": [f"model_runtime_error:{type(error).__name__}"],
            "publish_blockers": [f"model_runtime_error:{type(error).__name__}"],
            "owning_blocker": f"model_runtime_error:{type(error).__name__}",
            "squares": [],
            "provenance": {"artifact_path": str(resolved_model_path)},
        }

    template_placement = str(recognition.placement or "").strip()
    model_placement = str(runtime.get("placement") or "").strip()
    comparison = "unavailable"
    if template_placement and model_placement:
        comparison = "exact" if template_placement == model_placement else "conflict"
    runtime["template_comparison"] = comparison
    runtime["trust_boundaries"] = {
        "orientation": str(
            (runtime.get("orientation") or {}).get("source") or "unknown"
        ),
        "board_placement": (
            "calibrated_candidate"
            if runtime.get("candidate_accepted")
            else "review"
        ),
        "side_to_move": str(recognition.side_to_move_status or "unknown"),
        "full_fen": "review",
    }

    publish_blockers = list(runtime.get("publish_blockers") or [])
    if comparison == "conflict":
        runtime["publishable"] = False
        publish_blockers.insert(0, "model_template_conflict")
    runtime["publish_blockers"] = list(dict.fromkeys(publish_blockers))
    runtime["owning_blocker"] = (
        runtime["publish_blockers"][0]
        if runtime["publish_blockers"]
        else str(runtime.get("owning_blocker") or "")
    )
    blockers = list(dict.fromkeys([*recognition.recognition_blockers, *runtime["publish_blockers"]]))

    if not runtime.get("publishable") or comparison == "conflict":
        return replace(
            recognition,
            model_runtime=runtime,
            recognition_blockers=blockers,
        )

    warnings = list(
        dict.fromkeys(
            [
                *recognition.warnings,
                "calibrated_square_model_placement_used",
                "side_to_move_inferred",
            ]
        )
    )
    return ChessFenResult(
        fen="",
        placement=model_placement,
        full_fen="",
        confidence=float(runtime.get("confidence") or 0.0),
        side_to_move="w",
        side_to_move_status="inferred",
        side_to_move_evidence="none",
        bbox=recognition.bbox,
        method="rbf-svm-square-model",
        warnings=warnings,
        requires_review=True,
        board_detected=True,
        squares=[dict(square) for square in runtime.get("squares") or []],
        model_runtime=runtime,
        recognition_blockers=blockers,
    )
