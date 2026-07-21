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
CLOSE_REVIEW_RPC = "close_chess_fen_review"
DATABASE_ROW_FIELDS = frozenset(
    {
        "id",
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
        "placement_human_verified",
        "manual_side_to_move",
        "manual_side_evidence",
        "manual_visible_marker",
        "board_crop_label",
        "marker_crop_label",
        "label_status",
        "status_migration",
        "verified_by",
        "verified_at",
        "notes",
        "manual_placement",
        "manual_fen",
        "fen",
        "fen_human_verified",
        "piece_labels_source",
        "manual_label",
        "human_verified",
        "verification_source",
        "square_diff_ack",
        "label_provenance",
    }
)


class SupabaseFenReviewConflictError(RuntimeError):
    pass


class SupabaseFenReviewSessionClosedError(RuntimeError):
    pass


class SupabaseFenReviewOwnershipError(RuntimeError):
    pass


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

    def load_review(
        self,
        *,
        artifact_id: str,
        source_document_sha256: str = "",
        owner_user_id: str = "",
    ) -> dict[str, Any] | None:
        self._ensure_available()
        session_filters = {
            "artifact_id": f"eq.{artifact_id}",
            "select": (
                "artifact_id,source_document_sha256,schema_version,status,summary,"
                "row_count,revision,saved_at,closed_at"
            ),
            "limit": "1",
        }
        if owner_user_id:
            # Legacy ownerless sessions may be claimed once; owned sessions stay private.
            session_filters["or"] = f"(owner_user_id.eq.{owner_user_id},owner_user_id.is.null)"
        session_query = urllib.parse.urlencode(session_filters)
        sessions = self._request(f"/rest/v1/chess_fen_review_sessions?{session_query}", method="GET")
        reused_from_artifact_id = ""
        preloaded_rows: list[dict[str, Any]] | None = None
        if (not isinstance(sessions, list) or not sessions) and source_document_sha256 and owner_user_id:
            reuse_query = urllib.parse.urlencode(
                {
                    "source_document_sha256": f"eq.{source_document_sha256}",
                    "owner_user_id": f"eq.{owner_user_id}",
                    "select": (
                        "artifact_id,source_document_sha256,schema_version,status,summary,"
                        "row_count,revision,saved_at,closed_at"
                    ),
                    "order": "updated_at.desc",
                    "limit": "1",
                }
            )
            sessions = self._request(f"/rest/v1/chess_fen_review_sessions?{reuse_query}", method="GET")
            if isinstance(sessions, list) and sessions:
                reused_from_artifact_id = str(sessions[0].get("artifact_id") or "")
        if (not isinstance(sessions, list) or not sessions) and source_document_sha256 and owner_user_id:
            # Older review sessions did not persist owner/source columns. Reuse
            # them only when their row payloads prove the exact same source SHA.
            legacy_query = urllib.parse.urlencode(
                {
                    "source_document_sha256": "is.null",
                    "owner_user_id": "is.null",
                    "select": (
                        "artifact_id,source_document_sha256,schema_version,status,summary,"
                        "row_count,revision,saved_at,closed_at"
                    ),
                    "order": "updated_at.desc",
                    "limit": "25",
                }
            )
            legacy_sessions = self._request(
                f"/rest/v1/chess_fen_review_sessions?{legacy_query}",
                method="GET",
            )
            for candidate in legacy_sessions if isinstance(legacy_sessions, list) else []:
                candidate_artifact_id = str(candidate.get("artifact_id") or "")
                if not candidate_artifact_id or not self._job_owned_by(
                    artifact_id=candidate_artifact_id,
                    owner_user_id=owner_user_id,
                ):
                    continue
                candidate_rows = self._load_label_rows(candidate_artifact_id)
                candidate_digests = _row_source_digests(candidate_rows)
                if candidate_digests == {source_document_sha256}:
                    sessions = [candidate]
                    reused_from_artifact_id = candidate_artifact_id
                    preloaded_rows = candidate_rows
                    break
        if not isinstance(sessions, list) or not sessions:
            return None

        label_artifact_id = reused_from_artifact_id or artifact_id
        rows = preloaded_rows if preloaded_rows is not None else self._load_label_rows(label_artifact_id)
        session = dict(sessions[0])
        expected_count = int(session.get("row_count") or 0)
        if expected_count != len(rows):
            raise RuntimeError("supabase_fen_review_row_count_mismatch")
        session_digest = str(session.get("source_document_sha256") or "").strip()
        row_digests = _row_source_digests(rows)
        if session_digest and row_digests and row_digests != {session_digest}:
            raise RuntimeError("supabase_fen_review_source_digest_mismatch")
        resolved_source_digest = session_digest or (next(iter(row_digests)) if len(row_digests) == 1 else "")
        return {
            "schema": str(session.get("schema_version") or "kindlemaster.fen_review_progress.v1"),
            "status": str(session.get("status") or "active"),
            "session_status": str(session.get("status") or "active"),
            "revision": int(session.get("revision") or 0),
            "closed_at": str(session.get("closed_at") or ""),
            "source_document_sha256": resolved_source_digest,
            "rows": rows,
            "summary": dict(session.get("summary") or {}),
            "saved_at": str(session.get("saved_at") or ""),
            "storage": "database",
            "reused_from_artifact_id": reused_from_artifact_id,
        }

    def _load_label_rows(self, artifact_id: str) -> list[dict[str, Any]]:
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
        return [
            dict(label["row_payload"])
            for label in labels
            if isinstance(label, Mapping) and isinstance(label.get("row_payload"), Mapping)
        ]

    def _job_owned_by(self, *, artifact_id: str, owner_user_id: str) -> bool:
        ownership_query = urllib.parse.urlencode(
            {
                "job_id": f"eq.{artifact_id}",
                "user_id": f"eq.{owner_user_id}",
                "select": "job_id",
                "limit": "1",
            }
        )
        rows = self._request(f"/rest/v1/conversion_jobs?{ownership_query}", method="GET")
        return isinstance(rows, list) and len(rows) == 1

    def save_review(
        self,
        *,
        artifact_id: str,
        source_document_sha256: str,
        rows: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        owner_user_id: str = "",
        expected_revision: int = 0,
        action: str = "save",
        change_source: str = "autosave",
    ) -> dict[str, Any]:
        self._ensure_available()
        saved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            rpc_name = CLOSE_REVIEW_RPC if action == "close" else SAVE_REVIEW_RPC
            rpc_payload = {
                "p_artifact_id": artifact_id,
                "p_owner_user_id": owner_user_id or None,
                "p_source_document_sha256": source_document_sha256,
                "p_rows": [_database_row(row) for row in rows],
                "p_summary": dict(summary),
                "p_saved_at": saved_at,
                "p_expected_revision": int(expected_revision),
                "p_change_source": change_source,
            }
            if action != "close":
                rpc_payload["p_action"] = action
            result = self._request(
                f"/rest/v1/rpc/{rpc_name}",
                method="POST",
                payload=rpc_payload,
            )
        except Exception as error:
            message = str(error)
            if "fen_review_revision_conflict" in message:
                raise SupabaseFenReviewConflictError(message) from error
            if "fen_review_session_closed" in message:
                raise SupabaseFenReviewSessionClosedError(message) from error
            if "fen_review_owner_mismatch" in message:
                raise SupabaseFenReviewOwnershipError(message) from error
            raise
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
                "session_status": str(payload.get("session_status") or "active"),
                "revision": int(payload.get("revision") or expected_revision),
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


def _row_source_digests(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        str(row.get("source_document_sha256") or row.get("source_artifact_sha256") or "").strip()
        for row in rows
        if str(row.get("source_document_sha256") or row.get("source_artifact_sha256") or "").strip()
    }
