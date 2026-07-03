from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping


CHESS_LABEL_SCHEMA = "kindlemaster.chess_learning.label.v1"
CHESS_BENCHMARK_SCHEMA = "kindlemaster.chess_learning.benchmark.v1"
REVIEW_ONLY_POLICY = "manual_chess_labels_train_and_evaluate_only_no_direct_fen_publication"

LABEL_TYPE_VALUES: dict[str, set[str]] = {
    "board_crop": {"correct", "shifted", "wrong", "missing"},
    "side_marker_crop": {"correct", "wrong", "missing"},
    "side_marker": {"white", "black", "none", "unclear", "multiple", "bad_crop"},
    "fen": {"correct", "wrong", "unavailable"},
    "pgn": {"correct", "wrong", "unavailable"},
    "diagram_text_link": {"correct", "wrong", "unclear"},
}

DEFAULT_LABEL_FILES: dict[str, str] = {
    "board_crop": "board_crop_labels.jsonl",
    "side_marker": "side_marker_labels.jsonl",
    "fen": "fen_labels.jsonl",
    "pgn": "pgn_labels.jsonl",
    "diagram_text_link": "diagram_text_link_labels.jsonl",
}

HUMAN_SOURCES = {"human", "human_visual", "human_manual", "legacy_human_visual"}
AI_ONLY_SOURCES = {"ai", "ai_assist", "ai_candidate", "ai_review", "ai_review_only", "openai", "openai_review", "gpt"}


def build_chess_learning_benchmark(
    *,
    labels_dir: str | Path = "reference_inputs/chess_fen/labels",
    repo_root: str | Path = ".",
    min_per_type: int = 30,
    report_path: str | Path | None = None,
    write_ledger: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    labels_root = _resolve_path(root, labels_dir)
    rows, source_files = load_chess_learning_label_rows(labels_root)
    usable, rejected = validate_chess_learning_labels(rows)
    counts = Counter(str(row.get("label_type") or "") for row in usable)
    required_types = tuple(LABEL_TYPE_VALUES)
    missing = [
        {"label_type": label_type, "count": int(counts.get(label_type, 0)), "minimum": int(min_per_type)}
        for label_type in required_types
        if int(counts.get(label_type, 0)) < int(min_per_type)
    ]
    status = "READY_FOR_BENCHMARK" if not missing else "TRAINING_DATA_GAP"
    payload = {
        "schema": CHESS_BENCHMARK_SCHEMA,
        "generated_at": _utc_now(),
        "status": status,
        "policy": REVIEW_ONLY_POLICY,
        "labels_dir": str(labels_root),
        "source_files": [str(path) for path in source_files],
        "summary": {
            "raw_label_count": len(rows),
            "usable_label_count": len(usable),
            "rejected_label_count": len(rejected),
            "min_per_type": int(min_per_type),
            "label_type_counts": {key: int(value) for key, value in sorted(counts.items())},
            "missing_label_types": missing,
        },
        "full_fen_gate": {
            "labels_bypass_full_fen_gate": False,
            "reason": "human labels train and evaluate chess workflows; runtime FEN publication remains gated",
        },
        "usable_labels": usable,
        "rejected_labels": rejected,
    }
    resolved_report: Path | None = None
    if report_path:
        resolved_report = _resolve_path(root, report_path)
        payload["report_path"] = str(resolved_report)
    if write_ledger:
        try:
            from learning_ledger import record_chess_benchmark_built, record_chess_label_added

            label_event_ids = []
            for label in usable:
                label_record = record_chess_label_added(label_payload=label, repo_root=root)
                label_event_ids.append(str(label_record.get("event_id", "") or ""))
            ledger_record = record_chess_benchmark_built(benchmark_payload=payload, repo_root=root)
            payload["learning_ledger"] = {
                "status": "recorded",
                "event_id": str(ledger_record.get("event_id", "") or ""),
                "label_event_count": len(label_event_ids),
                "label_event_ids": label_event_ids[:20],
                "events_path": str(ledger_record.get("events_path", "") or ""),
                "index_path": str(ledger_record.get("index_path", "") or ""),
            }
        except Exception as error:
            payload["learning_ledger"] = {"status": "failed", "error": str(error)}
    else:
        payload["learning_ledger"] = {"status": "skipped", "reason": "write_ledger_false"}
    if resolved_report is not None:
        resolved_report.parent.mkdir(parents=True, exist_ok=True)
        resolved_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_chess_learning_label_rows(labels_dir: str | Path) -> tuple[list[dict[str, Any]], list[Path]]:
    root = Path(labels_dir)
    if not root.exists():
        return [], []
    files = sorted({path for path in root.glob("*.jsonl") if path.is_file()})
    rows: list[dict[str, Any]] = []
    for path in files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                rows.append({"source_file": str(path), "line": line_number, "invalid_json": True, "error": str(error)})
                continue
            if isinstance(payload, Mapping):
                row = dict(payload)
                row.setdefault("source_file", str(path))
                row.setdefault("line", line_number)
                rows.append(row)
    return rows, files


def validate_chess_learning_labels(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    usable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        normalized, issues = normalize_chess_learning_label(row, line=index)
        if issues:
            rejected.append(
                {
                    "line": row.get("line") or index,
                    "source_file": str(row.get("source_file") or ""),
                    "diagram_id": str(row.get("diagram_id") or row.get("id") or ""),
                    "codes": issues,
                }
            )
            continue
        usable.append(normalized)
    return usable, rejected


def normalize_chess_learning_label(row: Mapping[str, Any], *, line: int = 0) -> tuple[dict[str, Any], list[str]]:
    if row.get("invalid_json"):
        return {}, ["invalid_json"]
    label_type = _normalize_label_type(row)
    label_value = _normalize_label_value(label_type, row)
    diagram_id = str(row.get("diagram_id") or row.get("id") or "").strip()
    reviewer = str(row.get("reviewer") or row.get("verified_by") or "").strip()
    created_at = str(row.get("created_at") or row.get("verified_at") or "").strip()
    verification_source = str(row.get("verification_source") or row.get("label_source") or "").strip().lower()
    confidence = str(row.get("confidence") or "").strip().lower()
    human_verified = _human_verified(row, verification_source=verification_source, confidence=confidence)
    board_crop_hash = _normalize_hash(row.get("board_crop_hash") or row.get("board_crop_sha256") or row.get("crop_sha256"))
    marker_crop_hash = _normalize_hash(row.get("marker_crop_hash") or row.get("marker_crop_sha256"))

    issues: list[str] = []
    if not diagram_id:
        issues.append("diagram_id_missing")
    if not label_type:
        issues.append("label_type_missing")
    elif label_type not in LABEL_TYPE_VALUES:
        issues.append("label_type_unknown")
    if not label_value:
        issues.append("label_value_missing")
    elif label_type in LABEL_TYPE_VALUES and label_value not in LABEL_TYPE_VALUES[label_type]:
        issues.append("label_value_not_allowed")
    if verification_source in AI_ONLY_SOURCES and human_verified:
        issues.append("ai_only_label_cannot_be_human_verified")
    if not human_verified:
        issues.append("human_verification_missing")
    if not reviewer:
        issues.append("reviewer_missing")
    if not created_at:
        issues.append("created_at_missing")
    if not board_crop_hash:
        issues.append("board_crop_hash_missing")
    if label_type in {"side_marker", "side_marker_crop"} and not marker_crop_hash:
        issues.append("marker_crop_hash_missing")

    normalized = {
        "schema": CHESS_LABEL_SCHEMA,
        "label_id": str(row.get("label_id") or _label_id(diagram_id, label_type, board_crop_hash, marker_crop_hash)),
        "diagram_id": diagram_id,
        "page": row.get("page") or "",
        "board_crop_hash": board_crop_hash,
        "marker_crop_hash": marker_crop_hash,
        "label_type": label_type,
        "label_value": label_value,
        "reviewer": reviewer,
        "created_at": created_at,
        "confidence": "human_verified" if human_verified else "unverified",
        "human_verified": bool(human_verified),
        "verification_source": verification_source or ("human_visual" if human_verified else ""),
        "notes": str(row.get("notes") or row.get("manual_notes") or ""),
        "source_file": str(row.get("source_file") or ""),
        "line": int(row.get("line") or line or 0),
        "accepted_for_runtime": False,
        "accepted_for_corpus": False,
        "bypasses_full_fen_gate": False,
        "policy": REVIEW_ONLY_POLICY,
    }
    return normalized, issues


def label_rows_from_review_record(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reviewer = str(record.get("reviewer") or record.get("verified_by") or "").strip()
    created_at = str(record.get("created_at") or record.get("verified_at") or "").strip()
    base = {
        "schema": CHESS_LABEL_SCHEMA,
        "diagram_id": str(record.get("diagram_id") or ""),
        "page": record.get("page") or "",
        "board_crop_hash": _normalize_hash(record.get("board_crop_hash")),
        "marker_crop_hash": _normalize_hash(record.get("marker_crop_hash")),
        "reviewer": reviewer,
        "created_at": created_at,
        "confidence": "human_verified" if record.get("human_verified") is True else "unverified",
        "human_verified": bool(record.get("human_verified") is True),
        "verification_source": str(record.get("verification_source") or "").strip().lower(),
        "notes": str(record.get("manual_notes") or record.get("notes") or ""),
        "accepted_for_runtime": False,
        "accepted_for_corpus": False,
        "bypasses_full_fen_gate": False,
        "policy": REVIEW_ONLY_POLICY,
    }
    fields = {
        "board_crop": record.get("board_crop_label"),
        "side_marker_crop": record.get("side_marker_crop_label"),
        "side_marker": record.get("manual_visible_marker") or record.get("side_marker_label"),
        "fen": record.get("fen_label"),
        "pgn": record.get("pgn_label"),
        "diagram_text_link": record.get("diagram_text_link_label"),
    }
    for label_type, value in fields.items():
        label_value = _normalize_label_value(label_type, {**record, "label_value": value})
        if not label_value:
            continue
        row = {**base, "label_type": label_type, "label_value": label_value}
        row["label_id"] = _label_id(row["diagram_id"], label_type, row["board_crop_hash"], row["marker_crop_hash"])
        rows.append(row)
    return rows


def crop_hash(path: str | Path, *, artifact_root: str | Path | None = None) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    candidate = Path(value)
    if not candidate.is_absolute() and artifact_root is not None:
        candidate = Path(artifact_root) / value
    try:
        digest = sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except OSError:
        return ""


def _normalize_label_type(row: Mapping[str, Any]) -> str:
    value = str(row.get("label_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value:
        return value
    if row.get("manual_visible_marker") or row.get("manual_side_to_move"):
        return "side_marker"
    return ""


def _normalize_label_value(label_type: str, row: Mapping[str, Any]) -> str:
    value = str(row.get("label_value") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not value and label_type == "side_marker":
        value = str(row.get("manual_visible_marker") or row.get("manual_marker_shape") or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "outline_triangle": "white",
        "white_marker": "white",
        "w": "white",
        "filled_triangle": "black",
        "black_marker": "black",
        "b": "black",
        "no_marker": "none",
        "not_marker": "none",
        "ambiguous": "unclear",
    }
    return aliases.get(value, value)


def _human_verified(row: Mapping[str, Any], *, verification_source: str, confidence: str) -> bool:
    if row.get("human_verified") is True:
        return True
    if confidence == "human_verified":
        return True
    if verification_source in HUMAN_SOURCES:
        return True
    return bool(row.get("verified_by") and row.get("verified_at") and str(row.get("label_status") or "").lower() == "verified")


def _normalize_hash(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("sha256:"):
        return text
    if len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text):
        return f"sha256:{text.lower()}"
    return text


def _label_id(diagram_id: str, label_type: str, board_crop_hash: str, marker_crop_hash: str) -> str:
    raw = "|".join([diagram_id, label_type, board_crop_hash, marker_crop_hash])
    return "cl_" + sha256(raw.encode("utf-8")).hexdigest()[:20]


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
