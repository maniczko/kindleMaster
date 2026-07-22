from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class VerifiedFenReuseError(ValueError):
    pass


def bind_complete_review_to_artifact(
    review_payload: Mapping[str, Any],
    *,
    artifact_id: str,
    source_document_sha256: str,
) -> dict[str, Any]:
    """Rebind an exact-source completed review while preserving its provenance."""
    target_artifact = str(artifact_id or "").strip()
    source_digest = str(source_document_sha256 or "").strip().lower()
    if not target_artifact:
        raise VerifiedFenReuseError("artifact_id_missing")
    if not _SHA256_PATTERN.fullmatch(source_digest):
        raise VerifiedFenReuseError("source_document_sha256_invalid")
    if str(review_payload.get("session_status") or review_payload.get("status") or "").strip().lower() != "complete":
        raise VerifiedFenReuseError("review_session_not_complete")
    payload_digest = str(review_payload.get("source_document_sha256") or "").strip().lower()
    if payload_digest != source_digest:
        raise VerifiedFenReuseError("source_document_sha256_mismatch")

    source_artifact = str(
        review_payload.get("reused_from_artifact_id")
        or next(
            (
                row.get("artifact_id")
                for row in review_payload.get("rows") or []
                if isinstance(row, Mapping) and row.get("artifact_id")
            ),
            target_artifact,
        )
        or target_artifact
    ).strip()
    rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(review_payload.get("rows") or []):
        if not isinstance(raw_row, Mapping):
            raise VerifiedFenReuseError(f"review_row_invalid:{index}")
        row = dict(raw_row)
        row_digest = str(
            row.get("source_document_sha256")
            or row.get("source_artifact_sha256")
            or ""
        ).strip().lower()
        if row_digest != source_digest:
            raise VerifiedFenReuseError(f"review_row_source_mismatch:{index}")
        original_artifact = str(row.get("artifact_id") or source_artifact).strip()
        if original_artifact != source_artifact:
            raise VerifiedFenReuseError(f"review_row_artifact_mismatch:{index}")
        row["source_review_artifact_id"] = original_artifact
        row["artifact_id"] = target_artifact
        rows.append(row)

    if not rows:
        raise VerifiedFenReuseError("review_rows_missing")
    rebound = dict(review_payload)
    rebound.update(
        {
            "artifact_id": target_artifact,
            "source_document_sha256": source_digest,
            "session_status": "complete",
            "status": "complete",
            "rows": rows,
            "reused_from_artifact_id": source_artifact if source_artifact != target_artifact else "",
            "publication_binding": "exact_source_sha256_and_runtime_crop_validation",
        }
    )
    return rebound
