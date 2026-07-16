from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Callable

from supabase_library import (
    HttpRequest,
    SupabaseLibraryConfig,
    default_supabase_json_request,
    load_supabase_library_config,
)


SAVE_REVIEW_RPC = "save_chess_fen_review"
DATABASE_ROW_FIELDS = frozenset(
    {
        "schema",
        "review_contract",
        "artifact_id",
        "diagram_id",
        "diagram_fingerprint",
        "source_document_sha256",
        "source_artifact_sha256",
        "source_binding",
        "crop_sha256",
        "board_crop_sha256",
        "context_crop_sha256",
        "marker_crop_sha256",
        "marker_search_crop_sha256",
        "crop_rel_path",
        "board_crop_rel_path",
        "context_crop_rel_path",
        "marker_crop_rel_path",
        "marker_search_crop_rel_path",
        "page",
        "review_index",
        "reading_order",
        "caption",
        "confidence",
        "candidate_source",
        "fen_candidate",
        "detected_marker_status",
        "detected_marker_symbol",
        "marker_review_crop_kind",
        "model_conflict",
        "review_priority",
        "review_reason",
        "review_blockers",
        "policy",
        "square_labels",
        "piece_labels_verified",
        "manual_side_to_move",
        "manual_side_evidence",
        "manual_visible_marker",
        "board_crop_label",
        "marker_crop_label",
        "label_status",
        "verified_by",
        "verified_at",
        "notes",
        "manual_placement",
        "manual_fen",
        "fen_human_verified",
        "piece_labels_source",
        "manual_label",
        "human_verified",
        "verification_source",
        "label_provenance",
    }
)


class SupabaseFenReviewClient:
    def __init__(
        self,
        config: SupabaseLibraryConfig | None = None,
        *,
        transport: HttpRequest | None = None,
    ) -> None:
        self.config = config or load_supabase_library_config()
        self._transport: Callable[..., Any] = transport or default_supabase_json_request

    @property
    def available(self) -> bool:
        return self.config.enabled and self.config.configured

    def load_review(self, *, artifact_id: str) -> dict[str, Any] | None:
        self._ensure_available()
        session_query = urllib.parse.urlencode(
            {
                "artifact_id": f"eq.{artifact_id}",
                "select": "artifact_id,source_document_sha256,schema_version,status,summary,row_count,saved_at",
                "limit": "1",
            }
        )
        sessions = self._request(f"/rest/v1/chess_fen_review_sessions?{session_query}", method="GET")
        if not isinstance(sessions, list) or not sessions:
            return None

        label_query = urllib.parse.urlencode(
            {
                "artifact_id": f"eq.{artifact_id}",
                "select": "row_payload",
                "order": "review_index.asc.nullslast,diagram_fingerprint.asc",
            }
        )
        labels = self._request(f"/rest/v1/chess_fen_review_labels?{label_query}", method="GET")
        if not isinstance(labels, list):
            raise RuntimeError("supabase_fen_review_invalid_labels")
        rows = [
            dict(label["row_payload"])
            for label in labels
            if isinstance(label, Mapping) and isinstance(label.get("row_payload"), Mapping)
        ]
        session = dict(sessions[0])
        expected_count = int(session.get("row_count") or 0)
        if expected_count != len(rows):
            raise RuntimeError("supabase_fen_review_row_count_mismatch")
        return {
            "schema": str(session.get("schema_version") or "kindlemaster.fen_review_progress.v1"),
            "status": str(session.get("status") or "active"),
            "source_document_sha256": str(session.get("source_document_sha256") or ""),
            "rows": rows,
            "summary": dict(session.get("summary") or {}),
            "saved_at": str(session.get("saved_at") or ""),
            "storage": "database",
        }

    def save_review(
        self,
        *,
        artifact_id: str,
        source_document_sha256: str,
        rows: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        owner_user_id: str = "",
    ) -> dict[str, Any]:
        self._ensure_available()
        saved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        result = self._request(
            f"/rest/v1/rpc/{SAVE_REVIEW_RPC}",
            method="POST",
            payload={
                "p_artifact_id": artifact_id,
                "p_owner_user_id": owner_user_id or None,
                "p_source_document_sha256": source_document_sha256,
                "p_rows": [_database_row(row) for row in rows],
                "p_summary": dict(summary),
                "p_saved_at": saved_at,
            },
        )
        if not isinstance(result, Mapping):
            raise RuntimeError("supabase_fen_review_invalid_save_response")
        payload = dict(result)
        payload.update(
            {
                "schema": "kindlemaster.fen_review_progress.v1",
                "status": "saved",
                "saved_at": str(payload.get("saved_at") or saved_at),
                "submitted_count": len(rows),
                "summary": dict(payload.get("summary") or summary),
                "storage": "database",
            }
        )
        return payload

    def _request(
        self,
        path: str,
        *,
        method: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        headers = {
            "apikey": self.config.service_role_key,
            "Authorization": f"Bearer {self.config.service_role_key}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            return self._transport(
                f"{self.config.url}{path}",
                method=method,
                headers=headers,
                body=body,
                expect_json=True,
            )
        except TypeError:
            return self._transport(
                f"{self.config.url}{path}",
                method=method,
                headers=headers,
                body=body,
            )

    def _ensure_available(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("supabase_fen_review_disabled")
        if not self.config.configured:
            raise RuntimeError("supabase_fen_review_unconfigured")


def _database_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key in DATABASE_ROW_FIELDS
    }
