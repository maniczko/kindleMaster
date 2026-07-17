from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from chess_evidence_review_store import (
    EvidenceReviewStoreError,
    prepare_evidence_review_queue,
    prepare_evidence_review_submission,
)
from supabase_evidence_review import SupabaseEvidenceReviewClient


class ChessEvidenceReviewRepository:
    def __init__(self, *, cloud_client: SupabaseEvidenceReviewClient | None = None) -> None:
        self.cloud_client = cloud_client or SupabaseEvidenceReviewClient()

    def load(self, *, artifact_id: str) -> dict[str, Any] | None:
        if not self.cloud_client.available:
            raise EvidenceReviewStoreError("Baza Supabase dla kolejki markerow nie jest skonfigurowana.")
        return self.cloud_client.load_review(artifact_id=artifact_id)

    def import_queue(
        self,
        coverage_rows: Iterable[Mapping[str, Any]],
        *,
        artifact_id: str,
        source_document_sha256: str,
        source_profile: str,
        owner_user_id: str = "",
    ) -> dict[str, Any]:
        if not self.cloud_client.available:
            raise EvidenceReviewStoreError("Baza Supabase dla kolejki markerow nie jest skonfigurowana.")
        prepared = prepare_evidence_review_queue(
            coverage_rows,
            artifact_id=artifact_id,
            source_document_sha256=source_document_sha256,
            source_profile=source_profile,
        )
        result = self.cloud_client.import_queue(
            artifact_id=artifact_id,
            owner_user_id=owner_user_id,
            source_document_sha256=source_document_sha256,
            source_profile=source_profile,
            rows=prepared["rows"],
            summary=prepared["summary"],
        )
        return {**prepared, **result, "rows": prepared["rows"]}

    def save_item(
        self,
        *,
        artifact_id: str,
        submitted: Mapping[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        fingerprint = str(submitted.get("canonical_diagram_fingerprint") or "")
        if not self.cloud_client.available:
            raise EvidenceReviewStoreError("Baza Supabase dla kolejki markerow nie jest skonfigurowana.")
        item = self.cloud_client.load_item(
            artifact_id=artifact_id,
            canonical_diagram_fingerprint=fingerprint,
        )
        if item is None:
            raise EvidenceReviewStoreError("Nie znaleziono diagramu w kolejce markerow.")
        prepared = prepare_evidence_review_submission(
            item["row"],
            submitted,
            expected_revision=expected_revision,
        )
        return self.cloud_client.save_item(
            artifact_id=artifact_id,
            source_document_sha256=str(item.get("source_document_sha256") or ""),
            source_profile=str(item.get("source_profile") or ""),
            canonical_diagram_fingerprint=fingerprint,
            expected_revision=expected_revision,
            row=prepared,
        )
