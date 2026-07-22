from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import sqlite3
from typing import Mapping


@dataclass(frozen=True)
class AdmissionPolicy:
    window_seconds: int = 60
    anonymous_requests: int = 10
    authenticated_requests: int = 30
    anonymous_poll_requests: int = 120
    authenticated_poll_requests: int = 240
    anonymous_read_requests: int = 60
    authenticated_read_requests: int = 180
    max_active_jobs_per_owner: int = 2
    max_queued_jobs_per_owner: int = 5
    max_global_jobs: int = 20
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    max_file_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 1200
    max_pdf_objects: int = 200_000
    max_archive_entries: int = 5_000
    max_archive_uncompressed_bytes: int = 512 * 1024 * 1024
    max_archive_ratio: int = 100

    def request_limit(self, *, authenticated: bool, category: str) -> int:
        normalized = str(category or "mutation").strip().lower()
        if normalized == "poll":
            return self.authenticated_poll_requests if authenticated else self.anonymous_poll_requests
        if normalized == "read":
            return self.authenticated_read_requests if authenticated else self.anonymous_read_requests
        return self.authenticated_requests if authenticated else self.anonymous_requests


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    code: str
    status_code: int
    retry_after_seconds: int
    details: dict[str, int | str]


class DistributedAdmissionController:
    """SQLite-backed distributed request and capacity admission control."""

    def __init__(self, path: str | Path, policy: AdmissionPolicy | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy or AdmissionPolicy()
        self._initialize()

    def check_request(
        self,
        *,
        owner_id: str,
        route: str,
        authenticated: bool,
        category: str = "mutation",
        now: datetime | None = None,
    ) -> AdmissionDecision:
        current = _utc(now)
        owner_hash = _hash(owner_id)
        normalized_route = str(route or "unknown")[:160]
        normalized_category = str(category or "mutation").strip().lower()
        limit = self.policy.request_limit(authenticated=authenticated, category=normalized_category)
        bucket = int(current.timestamp()) // self.policy.window_seconds
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM request_counters WHERE bucket < ?", (bucket - 2,))
            row = connection.execute(
                """
                SELECT count FROM request_counters
                WHERE owner_hash = ? AND route = ? AND category = ? AND bucket = ?
                """,
                (owner_hash, normalized_route, normalized_category, bucket),
            ).fetchone()
            count = int(row["count"]) if row else 0
            if count >= limit:
                connection.commit()
                retry = self.policy.window_seconds - (int(current.timestamp()) % self.policy.window_seconds)
                return AdmissionDecision(
                    False,
                    "rate_limited",
                    429,
                    retry,
                    {"limit": limit, "route": normalized_route, "category": normalized_category},
                )
            connection.execute(
                """
                INSERT INTO request_counters(owner_hash, route, category, bucket, count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(owner_hash, route, category, bucket)
                DO UPDATE SET count = count + 1
                """,
                (owner_hash, normalized_route, normalized_category, bucket),
            )
            connection.commit()
        return AdmissionDecision(
            True,
            "allowed",
            200,
            0,
            {"limit": limit, "route": normalized_route, "category": normalized_category},
        )

    def check_job_admission(
        self,
        *,
        owner_id: str,
        active_jobs: int,
        queued_jobs: int,
        global_jobs: int,
        free_disk_bytes: int,
    ) -> AdmissionDecision:
        if free_disk_bytes < self.policy.min_free_disk_bytes:
            return AdmissionDecision(
                False,
                "insufficient_storage_capacity",
                503,
                60,
                {"free_disk_bytes": free_disk_bytes},
            )
        if global_jobs >= self.policy.max_global_jobs:
            return AdmissionDecision(
                False,
                "global_capacity_exhausted",
                503,
                30,
                {"global_jobs": global_jobs},
            )
        if active_jobs >= self.policy.max_active_jobs_per_owner:
            return AdmissionDecision(
                False,
                "owner_active_job_limit",
                429,
                30,
                {"active_jobs": active_jobs},
            )
        if queued_jobs >= self.policy.max_queued_jobs_per_owner:
            return AdmissionDecision(
                False,
                "owner_queue_limit",
                429,
                60,
                {"queued_jobs": queued_jobs},
            )
        return AdmissionDecision(True, "allowed", 200, 0, {"owner_hash": _hash(owner_id)[:12]})

    def validate_upload(
        self,
        *,
        filename: str,
        declared_mime: str,
        prefix: bytes,
        size_bytes: int,
        pdf_pages: int | None = None,
        pdf_objects: int | None = None,
        archive_entries: int | None = None,
        archive_uncompressed_bytes: int | None = None,
        archive_ratio: float | None = None,
        encrypted: bool = False,
    ) -> AdmissionDecision:
        if size_bytes <= 0:
            return AdmissionDecision(False, "empty_upload", 400, 0, {})
        if size_bytes > self.policy.max_file_bytes:
            return AdmissionDecision(
                False,
                "upload_too_large",
                413,
                0,
                {"max_file_bytes": self.policy.max_file_bytes},
            )
        extension = Path(filename).suffix.lower()
        detected = detect_document_type(prefix)
        expected_mime = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if detected not in {"pdf", "docx"}:
            return AdmissionDecision(False, "unsupported_magic_bytes", 415, 0, {"extension": extension})
        if extension != f".{detected}":
            return AdmissionDecision(
                False,
                "extension_magic_mismatch",
                415,
                0,
                {"detected": detected, "extension": extension},
            )
        accepted_declared = {"", expected_mime[detected], "application/octet-stream"}
        if declared_mime not in accepted_declared:
            return AdmissionDecision(False, "mime_magic_mismatch", 415, 0, {"detected": detected})
        if encrypted:
            return AdmissionDecision(False, "encrypted_document", 422, 0, {})
        if detected == "pdf" and pdf_pages is not None and pdf_pages > self.policy.max_pdf_pages:
            return AdmissionDecision(
                False,
                "pdf_page_limit",
                413,
                0,
                {"max_pdf_pages": self.policy.max_pdf_pages},
            )
        if detected == "pdf" and pdf_objects is not None and pdf_objects > self.policy.max_pdf_objects:
            return AdmissionDecision(
                False,
                "pdf_object_limit",
                413,
                0,
                {"max_pdf_objects": self.policy.max_pdf_objects},
            )
        if detected == "docx" and archive_entries is not None and archive_entries > self.policy.max_archive_entries:
            return AdmissionDecision(
                False,
                "archive_entry_limit",
                413,
                0,
                {"max_archive_entries": self.policy.max_archive_entries},
            )
        if (
            detected == "docx"
            and archive_uncompressed_bytes is not None
            and archive_uncompressed_bytes > self.policy.max_archive_uncompressed_bytes
        ):
            return AdmissionDecision(
                False,
                "archive_expansion_limit",
                413,
                0,
                {"max_archive_uncompressed_bytes": self.policy.max_archive_uncompressed_bytes},
            )
        if detected == "docx" and archive_ratio is not None and archive_ratio > self.policy.max_archive_ratio:
            return AdmissionDecision(
                False,
                "archive_ratio_limit",
                413,
                0,
                {"max_archive_ratio": self.policy.max_archive_ratio},
            )
        return AdmissionDecision(True, "allowed", 200, 0, {"detected": detected})

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(request_counters)").fetchall()
            }
            if columns and "category" not in columns:
                connection.execute("DROP TABLE request_counters")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_counters(
                    owner_hash TEXT NOT NULL,
                    route TEXT NOT NULL,
                    category TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY(owner_hash, route, category, bucket)
                )
                """
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()


def detect_document_type(prefix: bytes) -> str:
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(b"PK\x03\x04"):
        return "docx"
    return "unknown"


def decision_payload(decision: AdmissionDecision) -> dict:
    return {
        "allowed": decision.allowed,
        "code": decision.code,
        "status_code": decision.status_code,
        "retry_after_seconds": decision.retry_after_seconds,
        "details": decision.details,
    }


def policy_from_env(env: Mapping[str, str]) -> AdmissionPolicy:
    defaults = AdmissionPolicy()
    return AdmissionPolicy(
        window_seconds=_integer(env, "KINDLEMASTER_RATE_WINDOW_SECONDS", defaults.window_seconds, minimum=1),
        anonymous_requests=_integer(
            env, "KINDLEMASTER_ANON_REQUESTS_PER_WINDOW", defaults.anonymous_requests, minimum=1
        ),
        authenticated_requests=_integer(
            env, "KINDLEMASTER_AUTH_REQUESTS_PER_WINDOW", defaults.authenticated_requests, minimum=1
        ),
        anonymous_poll_requests=_integer(
            env, "KINDLEMASTER_ANON_POLLS_PER_WINDOW", defaults.anonymous_poll_requests, minimum=1
        ),
        authenticated_poll_requests=_integer(
            env, "KINDLEMASTER_AUTH_POLLS_PER_WINDOW", defaults.authenticated_poll_requests, minimum=1
        ),
        anonymous_read_requests=_integer(
            env, "KINDLEMASTER_ANON_READS_PER_WINDOW", defaults.anonymous_read_requests, minimum=1
        ),
        authenticated_read_requests=_integer(
            env, "KINDLEMASTER_AUTH_READS_PER_WINDOW", defaults.authenticated_read_requests, minimum=1
        ),
        max_active_jobs_per_owner=_integer(
            env, "KINDLEMASTER_MAX_ACTIVE_JOBS_PER_OWNER", defaults.max_active_jobs_per_owner, minimum=1
        ),
        max_queued_jobs_per_owner=_integer(
            env, "KINDLEMASTER_MAX_QUEUED_JOBS_PER_OWNER", defaults.max_queued_jobs_per_owner, minimum=1
        ),
        max_global_jobs=_integer(env, "KINDLEMASTER_MAX_GLOBAL_JOBS", defaults.max_global_jobs, minimum=1),
        min_free_disk_bytes=_integer(
            env, "KINDLEMASTER_MIN_FREE_DISK_BYTES", defaults.min_free_disk_bytes, minimum=1
        ),
        max_file_bytes=_integer(env, "KINDLEMASTER_MAX_FILE_BYTES", defaults.max_file_bytes, minimum=1),
        max_pdf_pages=_integer(env, "KINDLEMASTER_MAX_PDF_PAGES", defaults.max_pdf_pages, minimum=1),
        max_pdf_objects=_integer(env, "KINDLEMASTER_MAX_PDF_OBJECTS", defaults.max_pdf_objects, minimum=1),
        max_archive_entries=_integer(
            env, "KINDLEMASTER_MAX_ARCHIVE_ENTRIES", defaults.max_archive_entries, minimum=1
        ),
        max_archive_uncompressed_bytes=_integer(
            env,
            "KINDLEMASTER_MAX_ARCHIVE_UNCOMPRESSED_BYTES",
            defaults.max_archive_uncompressed_bytes,
            minimum=1,
        ),
        max_archive_ratio=_integer(
            env, "KINDLEMASTER_MAX_ARCHIVE_RATIO", defaults.max_archive_ratio, minimum=1
        ),
    )


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    try:
        value = int(str(env.get(name, default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "anonymous").encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)
