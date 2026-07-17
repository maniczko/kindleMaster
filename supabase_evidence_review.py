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


IMPORT_RPC = "import_chess_evidence_review_queue"
SAVE_ITEM_RPC = "save_chess_evidence_review_item"


class SupabaseEvidenceReviewClient:
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
                "select": (
                    "artifact_id,source_document_sha256,source_profile,schema_version,"
                    "status,summary,row_count,revision,saved_at"
                ),
                "limit": "1",
            }
        )
        sessions = self._request(f"/rest/v1/chess_evidence_review_sessions?{session_query}", method="GET")
        if not isinstance(sessions, list) or not sessions:
            return None
        item_query = urllib.parse.urlencode(
            {
                "artifact_id": f"eq.{artifact_id}",
                "select": "row_payload,revision,label_status,saved_at",
                "order": "page.asc,canonical_diagram_id.asc",
            }
        )
        items = self._request(f"/rest/v1/chess_evidence_review_items?{item_query}", method="GET")
        if not isinstance(items, list):
            raise RuntimeError("supabase_evidence_review_invalid_items")
        rows = []
        for item in items:
            if not isinstance(item, Mapping) or not isinstance(item.get("row_payload"), Mapping):
                continue
            row = dict(item["row_payload"])
            row["revision"] = int(item.get("revision") or 0)
            row["label_status"] = str(item.get("label_status") or row.get("label_status") or "open")
            row["saved_at"] = str(item.get("saved_at") or row.get("saved_at") or "")
            rows.append(row)
        session = dict(sessions[0])
        if int(session.get("row_count") or 0) != len(rows):
            raise RuntimeError("supabase_evidence_review_row_count_mismatch")
        return {
            "schema": str(session.get("schema_version") or ""),
            "status": str(session.get("status") or "active"),
            "artifact_id": str(session.get("artifact_id") or artifact_id),
            "source_document_sha256": str(session.get("source_document_sha256") or ""),
            "source_profile": str(session.get("source_profile") or ""),
            "summary": dict(session.get("summary") or {}),
            "row_count": len(rows),
            "revision": int(session.get("revision") or 0),
            "saved_at": str(session.get("saved_at") or ""),
            "rows": rows,
            "storage": "database",
        }

    def load_item(
        self,
        *,
        artifact_id: str,
        canonical_diagram_fingerprint: str,
    ) -> dict[str, Any] | None:
        self._ensure_available()
        query = urllib.parse.urlencode(
            {
                "artifact_id": f"eq.{artifact_id}",
                "canonical_diagram_fingerprint": f"eq.{canonical_diagram_fingerprint}",
                "select": (
                    "source_document_sha256,source_profile,row_payload,revision,"
                    "label_status,saved_at"
                ),
                "limit": "1",
            }
        )
        items = self._request(f"/rest/v1/chess_evidence_review_items?{query}", method="GET")
        if not isinstance(items, list) or not items:
            return None
        item = items[0]
        if not isinstance(item, Mapping) or not isinstance(item.get("row_payload"), Mapping):
            raise RuntimeError("supabase_evidence_review_invalid_item")
        row = dict(item["row_payload"])
        row["revision"] = int(item.get("revision") or 0)
        row["label_status"] = str(item.get("label_status") or row.get("label_status") or "open")
        row["saved_at"] = str(item.get("saved_at") or row.get("saved_at") or "")
        return {
            "source_document_sha256": str(item.get("source_document_sha256") or ""),
            "source_profile": str(item.get("source_profile") or ""),
            "row": row,
        }

    def import_queue(
        self,
        *,
        artifact_id: str,
        owner_user_id: str,
        source_document_sha256: str,
        source_profile: str,
        rows: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        saved_at = _now()
        result = self._request(
            f"/rest/v1/rpc/{IMPORT_RPC}",
            method="POST",
            payload={
                "p_artifact_id": artifact_id,
                "p_owner_user_id": owner_user_id or None,
                "p_source_document_sha256": source_document_sha256,
                "p_source_profile": source_profile,
                "p_rows": [dict(row) for row in rows],
                "p_summary": dict(summary),
                "p_saved_at": saved_at,
            },
        )
        if not isinstance(result, Mapping):
            raise RuntimeError("supabase_evidence_review_invalid_import_response")
        return {**dict(result), "storage": "database", "saved_at": str(result.get("saved_at") or saved_at)}

    def save_item(
        self,
        *,
        artifact_id: str,
        source_document_sha256: str,
        source_profile: str,
        canonical_diagram_fingerprint: str,
        expected_revision: int,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        saved_at = _now()
        result = self._request(
            f"/rest/v1/rpc/{SAVE_ITEM_RPC}",
            method="POST",
            payload={
                "p_artifact_id": artifact_id,
                "p_source_document_sha256": source_document_sha256,
                "p_source_profile": source_profile,
                "p_canonical_diagram_fingerprint": canonical_diagram_fingerprint,
                "p_expected_revision": int(expected_revision),
                "p_row": dict(row),
                "p_saved_at": saved_at,
            },
        )
        if not isinstance(result, Mapping):
            raise RuntimeError("supabase_evidence_review_invalid_save_response")
        return {**dict(result), "storage": "database", "saved_at": str(result.get("saved_at") or saved_at)}

    def _request(self, path: str, *, method: str, payload: Mapping[str, Any] | None = None) -> Any:
        self._ensure_available()
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
            return self._transport(f"{self.config.url}{path}", method=method, headers=headers, body=body)

    def _ensure_available(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("supabase_evidence_review_disabled")
        if not self.config.configured:
            raise RuntimeError("supabase_evidence_review_unconfigured")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
