from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from chess_diagram_fingerprint import source_document_sha256
from chess_evidence_review_repository import ChessEvidenceReviewRepository
from chess_evidence_review_store import export_marker_labels


def import_evidence_review_queue_file(
    *,
    coverage_path: str | Path,
    source_pdf: str | Path,
    artifact_id: str,
    source_profile: str,
    owner_user_id: str = "",
    repository: ChessEvidenceReviewRepository | None = None,
) -> dict[str, Any]:
    rows = _load_jsonl(Path(coverage_path))
    source_sha = source_document_sha256(Path(source_pdf))
    result = (repository or ChessEvidenceReviewRepository()).import_queue(
        rows,
        artifact_id=artifact_id,
        source_document_sha256=source_sha,
        source_profile=source_profile,
        owner_user_id=owner_user_id,
    )
    summary = dict(result.get("summary") or {})
    return {
        "schema": "kindlemaster.chess.evidence_review.import_report.v1",
        "status": "imported",
        "artifact_id": artifact_id,
        "source_profile": source_profile,
        "source_verified": True,
        "storage": str(result.get("storage") or "database"),
        "counts": {
            "total": int(summary.get("total") or 0),
            "open": int(summary.get("open") or 0),
            "with_review_asset": sum(
                str(row.get("asset_kind") or "unavailable") != "unavailable"
                for row in result.get("rows") or []
            ),
        },
    }


def export_evidence_review_labels_file(
    *,
    artifact_id: str,
    output_path: str | Path,
    repository: ChessEvidenceReviewRepository | None = None,
) -> dict[str, Any]:
    payload = (repository or ChessEvidenceReviewRepository()).load(artifact_id=artifact_id)
    if payload is None:
        raise ValueError("evidence_review_queue_not_found")
    labels = export_marker_labels(list(payload.get("rows") or []))
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for row in labels:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "schema": "kindlemaster.chess.evidence_review.export_report.v1",
        "status": "exported",
        "artifact_id": artifact_id,
        "exported_count": len(labels),
        "output_path": str(target),
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError("evidence_review_coverage_not_found")
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"evidence_review_row[{index}]:must_be_object")
        rows.append(dict(payload))
    return rows
