from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from chess_fen_review_store import (
    FenReviewConflictError,
    FenReviewOwnershipError,
    FenReviewSessionClosedError,
    FenReviewStoreError,
    load_fen_review_progress,
    persist_fen_review_progress_snapshot,
    prepare_fen_review_progress,
    save_fen_review_progress,
)
from supabase_fen_review import (
    SupabaseFenReviewClient,
    SupabaseFenReviewConflictError,
    SupabaseFenReviewOwnershipError,
    SupabaseFenReviewSessionClosedError,
)


class ChessFenReviewRepository:
    def __init__(
        self,
        review_dir: str | Path,
        *,
        artifact_id: str,
        owner_user_id: str = "",
        cloud_client: SupabaseFenReviewClient | None = None,
    ) -> None:
        self.review_dir = Path(review_dir)
        self.artifact_id = str(artifact_id or "").strip()
        self.owner_user_id = str(owner_user_id or "").strip()
        self.cloud_client = cloud_client or SupabaseFenReviewClient()

    def load(self) -> dict[str, Any]:
        local_payload = load_fen_review_progress(self.review_dir)
        if not self.cloud_client.available:
            return local_payload
        try:
            load_options = {"artifact_id": self.artifact_id}
            if self.owner_user_id:
                load_options.update(
                    {
                        "source_document_sha256": _source_digest(local_payload["rows"]),
                        "owner_user_id": self.owner_user_id,
                    }
                )
            database_payload = self.cloud_client.load_review(**load_options)
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
        payload = load_fen_review_progress(
            self.review_dir,
            persisted_rows=database_payload["rows"],
            persisted_saved_at=str(database_payload.get("saved_at") or ""),
            storage="database",
        )
        reused_from_artifact_id = str(database_payload.get("reused_from_artifact_id") or "")
        payload.update(
            {
                "revision": 0 if reused_from_artifact_id else int(database_payload.get("revision") or 0),
                "session_status": "active" if reused_from_artifact_id else str(database_payload.get("session_status") or "active"),
                "closed_at": "" if reused_from_artifact_id else str(database_payload.get("closed_at") or ""),
                "reused_from_artifact_id": reused_from_artifact_id,
            }
        )
        return payload

    def save(
        self,
        submitted_rows: Sequence[Mapping[str, Any]],
        *,
        source_digest: str = "",
        owner_user_id: str = "",
        expected_revision: int = 0,
        action: str = "save",
        change_source: str = "autosave",
    ) -> dict[str, Any]:
        if action not in {"save", "close", "reopen"}:
            raise FenReviewStoreError("Nieznana akcja sesji oznaczania.")
        if not self.cloud_client.available:
            if action != "save":
                raise FenReviewStoreError("Zamknięcie i ponowne otwarcie zestawu wymaga działającej bazy danych.")
            return save_fen_review_progress(
                self.review_dir,
                submitted_rows,
                artifact_id=self.artifact_id,
                source_digest=source_digest,
            )

        try:
            existing = self.cloud_client.load_review(
                artifact_id=self.artifact_id,
                owner_user_id=owner_user_id,
            )
            prepared = prepare_fen_review_progress(
                self.review_dir,
                submitted_rows,
                artifact_id=self.artifact_id,
                source_digest=source_digest,
                existing_rows=existing["rows"] if existing else None,
            )
            if action == "close" and (
                int(prepared["summary"].get("pending") or 0) > 0
                or int(prepared["summary"].get("invalid") or 0) > 0
            ):
                raise FenReviewStoreError("Zestaw można zamknąć dopiero po poprawnym zakończeniu wszystkich diagramów.")
            database_payload = self.cloud_client.save_review(
                artifact_id=self.artifact_id,
                source_document_sha256=str(prepared["source_document_sha256"]),
                rows=prepared["rows"],
                summary=prepared["summary"],
                owner_user_id=owner_user_id,
                expected_revision=expected_revision,
                action=action,
                change_source=change_source,
            )
        except SupabaseFenReviewConflictError as error:
            raise FenReviewConflictError("Zapis jest nieaktualny. Wczytaj nowszą wersję przed ponowną próbą.") from error
        except SupabaseFenReviewSessionClosedError as error:
            raise FenReviewSessionClosedError("Zestaw jest zamknięty. Otwórz go ponownie przed edycją.") from error
        except SupabaseFenReviewOwnershipError as error:
            raise FenReviewOwnershipError("Zestaw należy do innego użytkownika.") from error
        except FenReviewStoreError:
            raise
        except Exception as error:
            if action != "save":
                raise FenReviewStoreError("Nie udało się zapisać stanu zestawu w bazie danych.") from error
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
