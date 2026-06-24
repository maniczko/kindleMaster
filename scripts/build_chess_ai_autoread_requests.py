from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openai_chess_fen_reviewer import POLICY_ACKNOWLEDGEMENT


DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/ai_autoread/latest")
REQUEST_VARIANTS = ("direct_read", "skeptical_verify")


def build_chess_ai_autoread_requests(
    report_json: str | Path,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    model: str = DEFAULT_MODEL,
    max_image_bytes: int = 3_000_000,
) -> dict[str, Any]:
    report_path = Path(report_json)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    epub_path = Path(str(report.get("output_path") or "").strip())
    fen_records = list((((report.get("quality_report") or {}).get("chess_fen") or {}).get("records") or []))
    pgn_records = list((((report.get("quality_report") or {}).get("chess_pgn") or {}).get("records") or []))

    fen_readouts, fen_requests = _build_fen_readouts_and_requests(
        fen_records,
        epub_path=epub_path,
        model=model,
        max_image_bytes=max_image_bytes,
    )
    pgn_readouts, pgn_requests = _build_pgn_readouts_and_requests(pgn_records, model=model)
    requests = [*fen_requests, *pgn_requests]

    fen_path = target / "ai_fen_readout.jsonl"
    pgn_path = target / "ai_pgn_readout.jsonl"
    requests_path = target / "ai_autoread_requests.jsonl"
    summary_path = target / "ai_readout_summary.json"
    html_path = target / "ai_review.html"
    _write_jsonl(fen_path, fen_readouts)
    _write_jsonl(pgn_path, pgn_readouts)
    _write_jsonl(requests_path, requests)
    summary = {
        "status": "ok",
        "mode": "ai_autoread",
        "release_safe": False,
        "report_json": str(report_path),
        "source_epub": str(epub_path),
        "output_dir": str(target),
        "model": model,
        "fen_total": len(fen_readouts),
        "fen_request_count": len(fen_requests),
        "fen_ai_coverage": _coverage(len(fen_readouts), len(fen_readouts)),
        "pgn_total": len(pgn_readouts),
        "pgn_request_count": len(pgn_requests),
        "pgn_ai_coverage": _coverage(len(pgn_readouts), len(pgn_readouts)),
        "request_variants": list(REQUEST_VARIANTS),
        "ai_fen_readout": str(fen_path),
        "ai_pgn_readout": str(pgn_path),
        "ai_autoread_requests": str(requests_path),
        "ai_review_html": str(html_path),
        "policy": "ai_autoread_experimental_no_runtime_promotion",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_review_html(summary, fen_readouts, pgn_readouts), encoding="utf-8")
    return summary


def _build_fen_readouts_and_requests(
    records: list[dict[str, Any]],
    *,
    epub_path: Path,
    model: str,
    max_image_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    archive_names: dict[str, str] = {}
    archive: zipfile.ZipFile | None = None
    if epub_path.is_file():
        archive = zipfile.ZipFile(epub_path)
        archive_names = {Path(name).name: name for name in archive.namelist()}
    readouts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    try:
        for index, record in enumerate(records, start=1):
            row_id = _fen_record_id(record, index)
            base = _base_ai_row(row_id, kind="fen", record=record)
            filename = str(record.get("filename") or "").strip()
            request_image_url = ""
            if archive is not None and filename in archive_names:
                try:
                    request_image_url = _image_data_url_from_bytes(
                        archive.read(archive_names[filename]),
                        filename=filename,
                        max_bytes=max_image_bytes,
                    )
                except OSError as exc:
                    base["ai_readout_status"] = "ai_readout_unreadable"
                    base["ai_unreadable_reason"] = str(exc) or "fen_crop_unavailable"
            else:
                base["ai_readout_status"] = "ai_readout_unreadable"
                base["ai_unreadable_reason"] = "fen_crop_missing"
            readouts.append(
                {
                    **base,
                    **_initial_fen_ai_fields(record),
                    "deterministic_fen": str(record.get("fen") or ""),
                    "deterministic_full_fen": str(record.get("full_fen") or ""),
                    "deterministic_placement": str(record.get("placement") or record.get("placement_fen") or ""),
                    "candidate_warnings": list(record.get("warnings") or []),
                    "side_marker_candidates": list(record.get("side_marker_candidates") or []),
                    "ocr_line_items": _compact_ocr_line_items(record.get("ocr_line_items") or []),
                    "ai_readout_stage": _fen_readout_stage(record),
                }
            )
            if request_image_url and not _strict_fen_available(record):
                for variant in REQUEST_VARIANTS:
                    requests.append(_fen_request(row_id, variant=variant, record=record, image_url=request_image_url, model=model))
    finally:
        if archive is not None:
            archive.close()
    return readouts, requests


def _build_pgn_readouts_and_requests(records: list[dict[str, Any]], *, model: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    readouts: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        row_id = _pgn_record_id(record, index)
        raw_text = str(record.get("raw_text") or record.get("movetext") or record.get("annotated_pgn") or "")
        status = "ai_review_pending" if raw_text.strip() else "ai_readout_unreadable"
        readouts.append(
            {
                **_base_ai_row(row_id, kind="pgn", record=record),
                **_initial_pgn_ai_fields(record, default_status=status),
                "ai_unreadable_reason": "" if raw_text.strip() else "pgn_source_text_missing",
                "ai_pgn_feasibility": str(record.get("pgn_feasibility_reason") or ""),
                "deterministic_pgn": str(record.get("pgn") or ""),
                "deterministic_final_fen": str(record.get("final_fen") or ""),
                "deterministic_fen": str(record.get("fen") or ""),
                "candidate_warnings": list(record.get("warnings") or []),
                "ai_readout_stage": _pgn_readout_stage(record),
            }
        )
        if raw_text.strip() and not _strict_pgn_available(record):
            for variant in REQUEST_VARIANTS:
                requests.append(_pgn_request(row_id, variant=variant, record=record, model=model))
    return readouts, requests


def _base_ai_row(row_id: str, *, kind: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row_id,
        "kind": kind,
        "source": "ai_autoread",
        "release_safe": False,
        "human_verified": False,
        "accepted_for_corpus": False,
        "ai_readout_status": "ai_review_pending",
        "ai_consensus": False,
        "page": record.get("page"),
        "filename": record.get("filename", ""),
    }


def _initial_fen_ai_fields(record: dict[str, Any]) -> dict[str, Any]:
    fen = str(record.get("fen") or "").strip()
    if _strict_fen_available(record):
        parts = fen.split()
        return {
            "ai_readout_status": "strict_existing",
            "ai_consensus": True,
            "ai_fen": fen,
            "ai_placement": parts[0] if parts else "",
            "ai_side_to_move": parts[1] if len(parts) >= 2 else "unknown",
            "ai_confidence": 1.0,
            "ai_reason": "Runtime strict FEN already accepted; copied as AI-read baseline.",
        }
    return {"ai_fen": "", "ai_placement": "", "ai_side_to_move": "unknown"}


def _initial_pgn_ai_fields(record: dict[str, Any], *, default_status: str) -> dict[str, Any]:
    if _strict_pgn_available(record):
        return {
            "ai_readout_status": "strict_existing",
            "ai_consensus": True,
            "ai_pgn": str(record.get("pgn") or ""),
            "ai_movetext": str(record.get("movetext") or ""),
            "ai_confidence": 1.0,
            "ai_reason": "Runtime strict PGN already accepted; copied as AI-read baseline.",
            "ai_pgn_replay_legal": True,
        }
    return {"ai_readout_status": default_status, "ai_pgn": "", "ai_movetext": "", "ai_pgn_replay_legal": False}


def _strict_fen_available(record: dict[str, Any]) -> bool:
    return bool(str(record.get("fen") or "").strip()) and not bool(record.get("requires_review"))


def _strict_pgn_available(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").strip().lower()
    if status in {"accepted", "valid", "exportable"}:
        return bool(str(record.get("pgn") or "").strip())
    return bool(str(record.get("pgn") or "").strip() and str(record.get("final_fen") or "").strip())


def _fen_readout_stage(record: dict[str, Any]) -> str:
    if _strict_fen_available(record):
        return "strict_existing"
    warnings = {str(item) for item in (record.get("warnings") or [])}
    if "verified_exact_crop_label_used" in warnings or str(record.get("method") or "") == "verified-exact-crop-label":
        return "exact_label_evidence"
    if str(record.get("placement") or record.get("placement_fen") or "").strip():
        return "placement_plus_side"
    return "full_fen_vision"


def _pgn_readout_stage(record: dict[str, Any]) -> str:
    if _strict_pgn_available(record):
        return "strict_existing"
    if str(record.get("raw_text") or record.get("movetext") or "").strip():
        return "block_cleaned_source"
    return "ai_best_effort_text"


def _compact_ocr_line_items(items: list[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "text": str(item.get("text") or "")[:180],
                "bbox": item.get("bbox") or [],
                "confidence": item.get("confidence"),
            }
        )
    return compact


def _fen_request(row_id: str, *, variant: str, record: dict[str, Any], image_url: str, model: str) -> dict[str, Any]:
    context = {
        "id": row_id,
        "variant": variant,
        "page": record.get("page"),
        "filename": record.get("filename"),
        "readout_stage": _fen_readout_stage(record),
        "deterministic_placement": record.get("placement") or record.get("placement_fen") or "",
        "deterministic_full_fen": record.get("full_fen") or "",
        "deterministic_fen": record.get("fen") or "",
        "side_to_move_status": record.get("side_to_move_status") or "",
        "side_to_move_evidence": record.get("side_to_move_evidence") or "",
        "side_marker_candidates": record.get("side_marker_candidates") or [],
        "ocr_line_items": _compact_ocr_line_items(record.get("ocr_line_items") or []),
        "warnings": record.get("warnings") or [],
    }
    return _request(
        custom_id=f"fen::{row_id}::{variant}",
        model=model,
        instructions=(
            "You are an experimental AI autoread engine for a scanned chess diagram. Return JSON only. "
            "Read the full six-field FEN into ai_fen if visible; otherwise return readout_status='ai_readout_unreadable'. "
            "If deterministic_placement is present, prefer preserving that placement and focus on active color unless it is visibly wrong. "
            "Use side_marker_candidates and OCR line snippets as evidence, but say unreadable if active color is not visible. "
            "Do not output human_verified, verified_by, verified_at, accepted, accepted_for_corpus, label_status, "
            "manual_fen, fen, or canonical FEN authority fields. This is not release-safe evidence."
        ),
        context=context,
        image_url=image_url,
        schema=_fen_schema(),
    )


def _pgn_request(row_id: str, *, variant: str, record: dict[str, Any], model: str) -> dict[str, Any]:
    context = {
        "id": row_id,
        "variant": variant,
        "readout_stage": _pgn_readout_stage(record),
        "source_pages": record.get("source_pages") or [],
        "raw_text": record.get("raw_text") or "",
        "movetext": record.get("movetext") or "",
        "deterministic_fen": record.get("fen") or "",
        "deterministic_full_fen": record.get("full_fen") or "",
        "final_fen": record.get("final_fen") or "",
        "warnings": record.get("warnings") or [],
        "pgn_feasible": record.get("pgn_feasible"),
        "pgn_feasibility_reason": record.get("pgn_feasibility_reason") or "",
    }
    return _request(
        custom_id=f"pgn::{row_id}::{variant}",
        model=model,
        instructions=(
            "You are an experimental AI autoread engine for chess exercise notation. Return JSON only. "
            "If the text is diagram-only or prose-only, set pgn_feasibility='diagram_only' or 'ai_unreadable'. "
            "If there is one solution line, return ai_movetext and optional ai_pgn. Do not invent missing moves. "
            "Do not output pgn, accepted, human_verified, verified_by, verified_at, or strict export authority fields."
        ),
        context=context,
        image_url="",
        schema=_pgn_schema(),
    )


def _request(*, custom_id: str, model: str, instructions: str, context: dict[str, Any], image_url: str, schema: dict[str, Any]) -> dict[str, Any]:
    content = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=False)}]
    if image_url:
        content.append({"type": "input_image", "image_url": image_url})
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": instructions + f" Include policy_acknowledgement='{POLICY_ACKNOWLEDGEMENT}'.",
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": schema["name"], "strict": True, "schema": schema["schema"]}},
        },
    }


def _fen_schema() -> dict[str, Any]:
    return {
        "name": "kindlemaster_ai_fen_autoread",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "readout_status": {"type": "string", "enum": ["ai_readout_complete", "ai_readout_unreadable"]},
                "ai_fen": {"type": "string"},
                "placement": {"type": "string"},
                "side_to_move": {"type": "string", "enum": ["w", "b", "unknown"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
                "policy_acknowledgement": {"type": "string"},
            },
            "required": ["id", "readout_status", "ai_fen", "placement", "side_to_move", "confidence", "reason", "policy_acknowledgement"],
        },
    }


def _pgn_schema() -> dict[str, Any]:
    return {
        "name": "kindlemaster_ai_pgn_autoread",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "readout_status": {"type": "string", "enum": ["ai_readout_complete", "ai_readout_unreadable"]},
                "pgn_feasibility": {"type": "string", "enum": ["solution_line", "full_game", "diagram_only", "ai_unreadable"]},
                "ai_movetext": {"type": "string"},
                "ai_pgn": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
                "policy_acknowledgement": {"type": "string"},
            },
            "required": ["id", "readout_status", "pgn_feasibility", "ai_movetext", "ai_pgn", "confidence", "reason", "policy_acknowledgement"],
        },
    }


def _image_data_url_from_bytes(data: bytes, *, filename: str, max_bytes: int) -> str:
    if len(data) > max_bytes:
        raise OSError("image_too_large")
    mime_type = mimetypes.guess_type(filename)[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _fen_record_id(record: dict[str, Any], index: int) -> str:
    return f"fen_p{int(record.get('page') or 0):03d}_{Path(str(record.get('filename') or f'{index:04d}')).stem}"


def _pgn_record_id(record: dict[str, Any], index: int) -> str:
    raw = str(record.get("id") or "").strip()
    return raw or f"pgn_{index:04d}"


def _coverage(covered: int, total: int) -> float:
    return round((covered / total) if total else 1.0, 6)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _review_html(summary: dict[str, Any], fen_rows: list[dict[str, Any]], pgn_rows: list[dict[str, Any]]) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>AI Autoread</title>
<style>body{{font-family:sans-serif;margin:24px;background:#f4efe6}}.card{{background:white;border:1px solid #d8c8b5;border-radius:12px;padding:14px;margin:12px 0}}code{{background:#eee;padding:2px 5px}}</style>
</head><body>
<h1>AI Autoread - Experimental</h1>
<p><strong>Not release verified.</strong> AI outputs do not set canonical FEN/PGN.</p>
<div class="card">FEN coverage: {summary.get('fen_ai_coverage')} ({summary.get('fen_total')} rows)</div>
<div class="card">PGN coverage: {summary.get('pgn_ai_coverage')} ({summary.get('pgn_total')} rows)</div>
<h2>Sample FEN Rows</h2>{''.join(f'<div class="card"><code>{html.escape(str(r.get("id")))}</code> {html.escape(str(r.get("ai_readout_status")))}</div>' for r in fen_rows[:20])}
<h2>Sample PGN Rows</h2>{''.join(f'<div class="card"><code>{html.escape(str(r.get("id")))}</code> {html.escape(str(r.get("ai_readout_status")))}</div>' for r in pgn_rows[:20])}
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build experimental AI-only chess FEN/PGN autoread request batch and pending artifacts.")
    parser.add_argument("report_json")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-image-bytes", type=int, default=3_000_000)
    args = parser.parse_args(argv)
    summary = build_chess_ai_autoread_requests(args.report_json, output_dir=args.output_dir, model=args.model, max_image_bytes=args.max_image_bytes)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
