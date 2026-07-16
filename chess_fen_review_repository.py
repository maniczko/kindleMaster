from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from chess_fen_review_store import (
    FenReviewStoreError,
    load_fen_review_progress,
    persist_fen_review_progress_snapshot,
    prepare_fen_review_progress,
    save_fen_review_progress,
)
from supabase_fen_review import SupabaseFenReviewClient


class ChessFenReviewRepository:
    def __init__(
        self,
        review_dir: str | Path,
        *,
        artifact_id: str,
        cloud_client: SupabaseFenReviewClient | None = None,
    ) -> None:
        self.review_dir = Path(review_dir)
        self.artifact_id = str(artifact_id or "").strip()
        self.cloud_client = cloud_client or SupabaseFenReviewClient()

    def load(self) -> dict[str, Any]:
        local_payload = load_fen_review_progress(self.review_dir)
        if not self.cloud_client.available:
            return local_payload
        try:
            database_payload = self.cloud_client.load_review(artifact_id=self.artifact_id)
        except Exception:
            return {
                **local_payload,
                "storage": "server_fallback",
                "database_status": "unavailable",
                "storage_warning": "database_read_failed",
            }
        if database_payload is None:
            return {
                **local_payload,
                "database_status": "empty",
            }
        expected_digest = _source_digest(local_payload["rows"])
        database_digest = str(database_payload.get("source_document_sha256") or "")
        if expected_digest and database_digest != expected_digest:
            raise FenReviewStoreError("SHA raportu w bazie nie zgadza się z artefaktem źródłowym.")
        return load_fen_review_progress(
            self.review_dir,
            persisted_rows=database_payload["rows"],
            persisted_saved_at=str(database_payload.get("saved_at") or ""),
            storage="database",
        )

    def save(
        self,
        submitted_rows: Sequence[Mapping[str, Any]],
        *,
        source_digest: str = "",
        owner_user_id: str = "",
    ) -> dict[str, Any]:
        if not self.cloud_client.available:
            return save_fen_review_progress(
                self.review_dir,
                submitted_rows,
                artifact_id=self.artifact_id,
                source_digest=source_digest,
            )

        try:
            existing = self.cloud_client.load_review(artifact_id=self.artifact_id)
            prepared = prepare_fen_review_progress(
                self.review_dir,
                submitted_rows,
                artifact_id=self.artifact_id,
                source_digest=source_digest,
                existing_rows=existing["rows"] if existing else None,
            )
            database_payload = self.cloud_client.save_review(
                artifact_id=self.artifact_id,
                source_document_sha256=str(prepared["source_document_sha256"]),
                rows=prepared["rows"],
                summary=prepared["summary"],
                owner_user_id=owner_user_id,
            )
        except FenReviewStoreError:
            raise
        except Exception:
            fallback = save_fen_review_progress(
                self.review_dir,
                submitted_rows,
                artifact_id=self.artifact_id,
                source_digest=source_digest,
            )
            return {
                **fallback,
                "storage": "server_fallback",
                "database_status": "unavailable",
                "storage_warning": "database_write_failed",
            }

        try:
            persist_fen_review_progress_snapshot(
                self.review_dir,
                prepared["rows"],
                artifact_id=self.artifact_id,
                source_digest=str(prepared["source_document_sha256"]),
                saved_at=str(database_payload["saved_at"]),
                submitted_count=int(prepared["submitted_count"]),
            )
            cache_status = "synced"
        except OSError:
            cache_status = "write_failed"
        return {
            **database_payload,
            "submitted_count": int(prepared["submitted_count"]),
            "summary": dict(prepared["summary"]),
            "storage": "database",
            "cache_status": cache_status,
        }


def _source_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    for row in rows:
        value = str(row.get("source_document_sha256") or row.get("source_artifact_sha256") or "").strip()
        if value:
            return value
    return ""
