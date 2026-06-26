from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import crop_sha256, validate_fen_detailed  # noqa: E402

AUTO_VERIFIED_SOURCE = "deterministic_consensus"
AUTO_CANDIDATE_SOURCE = "ai_consensus_review_only"
AUTO_LABEL_STATUSES = {"auto_verified", "auto_candidate"}
HUMAN_ONLY_FIELDS = {"verified_by", "verified_at"}
DETERMINISTIC_PROOF_FIELDS = (
    "deterministic_consensus",
    "deterministic_match",
    "candidate_matches_review_crop",
    "crop_hash_match",
    "strict_no_regression_passed",
)
AI_SOURCE_TOKENS = (
    "ai",
    "ai_consensus",
    "ai_review",
    "ai_review_only",
    "ai_tie_break",
    "openai",
    "gpt",
)


def build_ai_consensus_fen_promotion_queue(
    source_path: str | Path,
    *,
    output_jsonl: str | Path,
    output_report_json: str | Path,
    crop_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Build a non-human promotion queue from AI-consensus FEN evidence.

    The queue is deliberately not a verified-label file. Rows are either
    deterministic machine candidates (`auto_verified`) or review evidence
    (`auto_candidate`), with `human_verified` forced to false in both cases.
    """
    source = Path(source_path)
    payload = _load_json(source)
    records = _extract_records(payload)
    roots = [Path(root) for root in crop_roots]

    queue: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    for index, record in enumerate(records):
        candidate = _classify_record(record, index=index, source_path=source, crop_roots=roots)
        if candidate["decision"] == "rejected":
            rejected.append(candidate)
            continue
        dedupe_key = candidate["dedupe_key"]
        existing_index = seen.get(dedupe_key)
        if existing_index is None:
            seen[dedupe_key] = len(queue)
            queue.append(candidate["row"])
            continue
        duplicates.append(
            {
                "diagram_id": candidate["row"].get("diagram_id", ""),
                "dedupe_key": dedupe_key,
                "kept_diagram_id": queue[existing_index].get("diagram_id", ""),
                "reason": "duplicate_crop_or_diagram",
            }
        )
        if _row_rank(candidate["row"]) > _row_rank(queue[existing_index]):
            queue[existing_index] = candidate["row"]

    summary = {
        "schema": "kindlemaster.chess_fen.ai_consensus_promotion_queue.v1",
        "status": "completed",
        "source_path": str(source),
        "output_jsonl": str(output_jsonl),
        "input_count": len(records),
        "queue_count": len(queue),
        "auto_verified_count": sum(1 for row in queue if row.get("label_status") == "auto_verified"),
        "auto_candidate_count": sum(1 for row in queue if row.get("label_status") == "auto_candidate"),
        "promoted_count": sum(1 for row in queue if row.get("label_status") == "auto_verified"),
        "review_only_count": sum(1 for row in queue if row.get("label_status") == "auto_candidate"),
        "rejected_count": len(rejected),
        "duplicate_count": len(duplicates),
        "unchanged_count": 0,
        "human_verified_true_count": sum(1 for row in queue if row.get("human_verified") is True),
        "human_visual_source_count": sum(1 for row in queue if row.get("verification_source") == "human_visual"),
        "next_actions": _next_actions(queue, rejected),
        "rejected": rejected,
        "duplicates": duplicates,
        "unchanged": [],
        "policy": {
            "human_verified": "always_false_for_automatic_rows",
            "human_visual": "never_written_by_this_queue",
            "strict_acceptance": "requires_deterministic_validation_crop_hash_and_no_regression",
        },
    }
    _assert_queue_contract(queue)
    _write_jsonl(Path(output_jsonl), queue)
    _write_json(Path(output_report_json), summary)
    return summary


def _classify_record(
    record: Mapping[str, Any],
    *,
    index: int,
    source_path: Path,
    crop_roots: list[Path],
) -> dict[str, Any]:
    diagram_id = _diagram_id(record, index)
    candidate_fen = _candidate_fen(record)
    if not _has_ai_consensus_evidence(record):
        return _rejected(diagram_id, "not_ai_consensus_evidence")
    if not candidate_fen:
        return _rejected(diagram_id, "fen_candidate_missing")

    validation = validate_fen_detailed(candidate_fen)
    blockers: list[str] = []
    if not validation.is_syntax_valid or not validation.is_legal_position or validation.errors:
        blockers.extend(issue.code for issue in validation.errors)

    crop_path = _resolve_crop_path(record, source_path=source_path, crop_roots=crop_roots)
    crop_hash = str(record.get("crop_sha256") or record.get("crop_hash") or "").strip()
    if crop_path and not crop_hash:
        crop_hash = crop_sha256(crop_path)
    if not crop_hash:
        blockers.append("crop_hash_missing")

    deterministic_proof = _has_deterministic_proof(record)
    no_regression = _strict_no_regression_passed(record)
    if not deterministic_proof:
        blockers.append("deterministic_proof_missing")
    if not no_regression:
        blockers.append("strict_no_regression_missing")

    label_status = "auto_verified" if not blockers and deterministic_proof and no_regression else "auto_candidate"
    verification_source = AUTO_VERIFIED_SOURCE if label_status == "auto_verified" else AUTO_CANDIDATE_SOURCE
    if label_status == "auto_candidate" and not blockers:
        blockers.append("ai_review_only")

    row = {
        "id": str(record.get("id") or diagram_id),
        "diagram_id": diagram_id,
        "source_pdf": record.get("source_pdf", ""),
        "page": record.get("page", record.get("page_number")),
        "diagram_index": record.get("diagram_index"),
        "crop_path": str(crop_path) if crop_path else str(record.get("crop_path") or record.get("source_crop_path") or ""),
        "crop_sha256": crop_hash,
        "fen": validation.normalized_fen or candidate_fen,
        "label_status": label_status,
        "verification_source": verification_source,
        "human_verified": False,
        "deterministic_proof": deterministic_proof,
        "strict_no_regression_passed": no_regression,
        "blockers": _dedupe(blockers),
        "source_status": str(record.get("status") or record.get("label_status") or record.get("method") or ""),
        "notes": "Automatic AI-consensus queue row; not a human-verified label.",
    }
    return {
        "decision": "queued",
        "dedupe_key": crop_hash or diagram_id,
        "row": _drop_none(row),
    }


def _extract_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    roots: list[Any] = [payload]
    for key in ("quality_report", "chess_fen", "summary", "report"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            roots.append(value)
            nested = value.get("chess_fen")
            if isinstance(nested, Mapping):
                roots.append(nested)
    for root in roots:
        if not isinstance(root, Mapping):
            continue
        for key in ("records", "items", "cases", "diagrams", "fen_candidates", "accepted_candidates", "review_items"):
            value = root.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _candidate_fen(record: Mapping[str, Any]) -> str:
    for key in (
        "ai_consensus_fen",
        "ai_candidate_fen",
        "ai_suggested_fen",
        "candidate_fen",
        "selected_value",
        "full_fen",
        "fen",
    ):
        value = str(record.get(key) or "").strip()
        if _looks_like_full_fen(value):
            return value
    candidates = record.get("ai_candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, Mapping):
                value = _candidate_fen(candidate)
            else:
                value = str(candidate or "").strip()
            if _looks_like_full_fen(value):
                return value
    return ""


def _has_ai_consensus_evidence(record: Mapping[str, Any]) -> bool:
    if any(str(key).startswith("ai_") and value not in (None, "", []) for key, value in record.items()):
        return True
    text = " ".join(
        str(value or "").strip().lower()
        for value in (
            record.get("source"),
            record.get("method"),
            record.get("status"),
            record.get("runtime_status"),
            record.get("label_source"),
            record.get("verification_source"),
        )
    )
    return any(token in text for token in AI_SOURCE_TOKENS)


def _has_deterministic_proof(record: Mapping[str, Any]) -> bool:
    if any(record.get(field) is True for field in DETERMINISTIC_PROOF_FIELDS):
        return True
    source = str(record.get("source") or record.get("method") or record.get("label_source") or "").strip().lower()
    return source in {"deterministic_consensus", "deterministic_ensemble", "verified_exact_crop_label"}


def _strict_no_regression_passed(record: Mapping[str, Any]) -> bool:
    if record.get("strict_no_regression_passed") is True:
        return True
    gate = record.get("strict_regression_gate")
    if isinstance(gate, Mapping):
        return str(gate.get("status") or "").strip().lower() == "passed"
    return False


def _resolve_crop_path(record: Mapping[str, Any], *, source_path: Path, crop_roots: list[Path]) -> Path | None:
    raw = str(record.get("crop_path") or record.get("source_crop_path") or "").strip()
    if not raw:
        return None
    candidates = [Path(raw)]
    if not Path(raw).is_absolute():
        candidates.append(source_path.parent / raw)
        candidates.extend(root / raw for root in crop_roots)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _diagram_id(record: Mapping[str, Any], index: int) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    page = record.get("page", record.get("page_number"))
    if page not in (None, ""):
        return f"p{page}:record:{index}"
    return f"record:{index}"


def _rejected(diagram_id: str, reason: str) -> dict[str, Any]:
    return {"decision": "rejected", "diagram_id": diagram_id, "reason": reason}


def _row_rank(row: Mapping[str, Any]) -> int:
    return 2 if row.get("label_status") == "auto_verified" else 1


def _next_actions(queue: list[Mapping[str, Any]], rejected: list[Mapping[str, Any]]) -> list[str]:
    if any(row.get("label_status") == "auto_verified" for row in queue):
        return [
            "run scripts/check_chess_fen_strict_regression_gate.py on the generated candidate report",
            "only wire auto_verified rows into runtime acceptance after the strict gate remains passed",
        ]
    if queue:
        return [
            "feed auto_candidate rows into deterministic crop/template validation",
            "rerun this queue builder after crop hash provenance and strict no-regression evidence exist",
        ]
    if rejected:
        return ["refresh the AI consensus source report; no usable FEN candidates were found"]
    return ["provide an AI consensus source report with records/items/cases/diagrams"]


def _assert_queue_contract(queue: list[Mapping[str, Any]]) -> None:
    for row in queue:
        if row.get("human_verified") is True:
            raise ValueError("automatic queue attempted to write human_verified=true")
        if row.get("verification_source") == "human_visual":
            raise ValueError("automatic queue attempted to write verification_source=human_visual")
        if row.get("label_status") not in AUTO_LABEL_STATUSES:
            raise ValueError(f"automatic queue wrote unsupported label_status={row.get('label_status')!r}")
        forbidden = HUMAN_ONLY_FIELDS.intersection(row)
        if forbidden:
            raise ValueError(f"automatic queue wrote human-only fields: {sorted(forbidden)}")


def _looks_like_full_fen(value: str) -> bool:
    parts = str(value or "").strip().split()
    return len(parts) == 6 and parts[1] in {"w", "b"}


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _drop_none(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value is not None}


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe non-human FEN promotion queue from AI consensus evidence.")
    parser.add_argument("source_json")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-report-json", required=True)
    parser.add_argument("--crop-root", action="append", default=[])
    args = parser.parse_args(argv)

    payload = build_ai_consensus_fen_promotion_queue(
        args.source_json,
        output_jsonl=args.output_jsonl,
        output_report_json=args.output_report_json,
        crop_roots=args.crop_root,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
