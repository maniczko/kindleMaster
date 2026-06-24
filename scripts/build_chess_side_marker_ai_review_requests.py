from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openai_chess_fen_reviewer import POLICY_ACKNOWLEDGEMENT


DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_MAX_IMAGE_BYTES = 3_000_000


def build_side_marker_ai_review_requests(
    input_jsonl: str | Path,
    *,
    output_jsonl: str | Path,
    model: str = DEFAULT_MODEL,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> dict[str, Any]:
    source = Path(input_jsonl)
    target = Path(output_jsonl)
    rows = _read_jsonl(source)
    requests: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        image_path = Path(str(row.get("context_crop_path") or row.get("crop_path") or "").strip())
        if not row_id:
            skipped.append({"id": "", "reason": "id_missing"})
            continue
        try:
            image_url = _image_data_url(image_path, max_bytes=max_image_bytes)
        except OSError as exc:
            skipped.append({"id": row_id, "reason": str(exc)})
            continue
        requests.append(
            {
                "custom_id": row_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": _request_body(row, image_url=image_url, model=model),
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests), encoding="utf-8")
    summary = {
        "status": "ok",
        "input_jsonl": str(source),
        "output_jsonl": str(target),
        "request_count": len(requests),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "model": model,
        "policy": "ai_side_marker_review_only_no_human_verification",
    }
    target.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _request_body(row: dict[str, Any], *, image_url: str, model: str) -> dict[str, Any]:
    review_context = {
        "id": row.get("id"),
        "page": row.get("page"),
        "filename": row.get("filename"),
        "review_priority": row.get("review_priority"),
        "candidate_placement": row.get("candidate_placement"),
        "candidate_full_fen": row.get("candidate_full_fen"),
        "candidate_confidence": row.get("candidate_confidence"),
        "candidate_warnings": row.get("candidate_warnings") or [],
        "side_marker_candidates": row.get("side_marker_candidates") or [],
        "detected_marker_sides": row.get("detected_marker_sides") or [],
        "ai_pre_suggestion": {
            "side": row.get("ai_suggested_side_to_move") or "",
            "role": row.get("ai_suggested_marker_role") or "",
            "confidence": row.get("ai_suggestion_confidence") or 0.0,
            "reason": row.get("ai_suggestion_reason") or "",
        },
        "policy": "ai_review_only_no_runtime_promotion",
    }
    return {
        "model": str(model or DEFAULT_MODEL),
        "instructions": (
            "You are an AI reviewer for KindleMaster side-to-move marker calibration. "
            "Inspect the supplied context crop with colored probe boxes and the marker candidate metadata. "
            "Return JSON only. Determine whether there is a visible explicit marker or caption that indicates "
            "which side is to move. If not explicit, return side_to_move='unknown'. Do not infer from chess position, "
            "piece placement, move text, or candidate FEN. Never output human_verified, verified_by, verified_at, "
            "accepted, accepted_for_corpus, label_status, fen, or canonical_fen. Your answer is review evidence only "
            f"and must include policy_acknowledgement='{POLICY_ACKNOWLEDGEMENT}'."
        ),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": json.dumps(review_context, ensure_ascii=False)},
                    {"type": "input_image", "image_url": image_url},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "kindlemaster_side_marker_ai_review",
                "strict": True,
                "schema": _response_schema(),
            }
        },
    }


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "side_to_move": {"type": "string", "enum": ["w", "b", "unknown"]},
            "marker_source": {"type": "string", "enum": ["visual_marker", "ocr_symbol", "caption", "none", "ambiguous"]},
            "marker_role": {"type": "string"},
            "marker_symbol": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_level": {"type": "string", "enum": ["clear", "ambiguous", "insufficient_crop", "no_marker"]},
            "requires_human_review": {"type": "boolean"},
            "reason": {"type": "string"},
            "policy_acknowledgement": {"type": "string"},
        },
        "required": [
            "id",
            "side_to_move",
            "marker_source",
            "marker_role",
            "marker_symbol",
            "confidence",
            "evidence_level",
            "requires_human_review",
            "reason",
            "policy_acknowledgement",
        ],
    }


def _image_data_url(path: Path, *, max_bytes: int) -> str:
    if not path.is_file():
        raise OSError("image_missing")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise OSError("image_too_large")
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Expected object row at {path}:{line_number}")
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build OpenAI Responses API requests for side-marker review.")
    parser.add_argument("input_jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    args = parser.parse_args(argv)
    summary = build_side_marker_ai_review_requests(
        args.input_jsonl,
        output_jsonl=args.output,
        model=args.model,
        max_image_bytes=args.max_image_bytes,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
