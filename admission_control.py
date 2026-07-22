from __future__ import annotations

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
    max_active_jobs_per_owner: int = 2
    max_queued_jobs_per_owner: int = 5
    max_global_jobs: int = 20
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    max_file_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 1200


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    code: str
    status_code: int
    retry_after_seconds: int
    details: dict[str, int | str]


class DistributedAdmissionController:
    """SQLite-backed distributed admission and quota control.

    The database must live on the same shared Railway volume as the durable job
    queue. Counters are owner-scoped hashes; raw tokens and filenames are never
    persisted.
    """

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
        now: datetime | None = None,
    ) -> AdmissionDecision:
        current = _utc(now)
        owner_hash = _hash(owner_id)
        limit = self.policy.authenticated_requests if authenticated else self.policy.anonymous_requests
        bucket = int(current.timestamp()) // self.policy.window_seconds
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM request_counters WHERE bucket < ?", (bucket - 2,))
            row = connection.execute(
                "SELECT count FROM request_counters WHERE owner_hash = ? AND route = ? AND bucket = ?",
                (owner_hash, route, bucket),
            ).fetchone()
            count = int(row["count"]) if row else 0
            if count >= limit:
                connection.commit()
                retry = self.policy.window_seconds - (int(current.timestamp()) % self.policy.window_seconds)
                return AdmissionDecision(False, "rate_limited", 429, retry, {"limit": limit, "route": route})
            connection.execute(
                """
                INSERT INTO request_counters(owner_hash, route, bucket, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(owner_hash, route, bucket) DO UPDATE SET count = count + 1
                """,
                (owner_hash, route, bucket),
            )
            connection.commit()
        return AdmissionDecision(True, "allowed", 200, 0, {"limit": limit, "route": route})

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
            return AdmissionDecision(False, "insufficient_storage_capacity", 503, 60, {"free_disk_bytes": free_disk_bytes})
        if global_jobs >= self.policy.max_global_jobs:
            return AdmissionDecision(False, "global_capacity_exhausted", 503, 30, {"global_jobs": global_jobs})
        if active_jobs >= self.policy.max_active_jobs_per_owner:
            return AdmissionDecision(False, "owner_active_job_limit", 429, 30, {"active_jobs": active_jobs})
        if queued_jobs >= self.policy.max_queued_jobs_per_owner:
            return AdmissionDecision(False, "owner_queue_limit", 429, 60, {"queued_jobs": queued_jobs})
        return AdmissionDecision(True, "allowed", 200, 0, {"owner_hash": _hash(owner_id)[:12]})

    def validate_upload(
        self,
        *,
        filename: str,
        declared_mime: str,
        prefix: bytes,
        size_bytes: int,
        pdf_pages: int | None = None,
    ) -> AdmissionDecision:
        if size_bytes <= 0:
            return AdmissionDecision(False, "empty_upload", 400, 0, {})
        if size_bytes > self.policy.max_file_bytes:
            return AdmissionDecision(False, "upload_too_large", 413, 0, {"max_file_bytes": self.policy.max_file_bytes})
        extension = Path(filename).suffix.lower()
        detected = detect_document_type(prefix)
        expected_mime = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if detected not in {"pdf", "docx"}:
            return AdmissionDecision(False, "unsupported_magic_bytes", 415, 0, {"extension": extension})
        if extension != f".{detected}":
            return AdmissionDecision(False, "extension_magic_mismatch", 415, 0, {"detected": detected, "extension": extension})
        if declared_mime and declared_mime != expected_mime[detected]:
            return AdmissionDecision(False, "mime_magic_mismatch", 415, 0, {"detected": detected})
        if detected == "pdf" and pdf_pages is not None and pdf_pages > self.policy.max_pdf_pages:
            return AdmissionDecision(False, "pdf_page_limit", 413, 0, {"max_pdf_pages": self.policy.max_pdf_pages})
        return AdmissionDecision(True, "allowed", 200, 0, {"detected": detected})

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_counters(
                    owner_hash TEXT NOT NULL,
                    route TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY(owner_hash, route, bucket)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection


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
        anonymous_requests=_integer(env, "KINDLEMASTER_ANON_REQUESTS_PER_WINDOW", defaults.anonymous_requests, minimum=1),
        authenticated_requests=_integer(env, "KINDLEMASTER_AUTH_REQUESTS_PER_WINDOW", defaults.authenticated_requests, minimum=1),
        max_active_jobs_per_owner=_integer(env, "KINDLEMASTER_MAX_ACTIVE_JOBS_PER_OWNER", defaults.max_active_jobs_per_owner, minimum=1),
        max_queued_jobs_per_owner=_integer(env, "KINDLEMASTER_MAX_QUEUED_JOBS_PER_OWNER", defaults.max_queued_jobs_per_owner, minimum=1),
        max_global_jobs=_integer(env, "KINDLEMASTER_MAX_GLOBAL_JOBS", defaults.max_global_jobs, minimum=1),
        min_free_disk_bytes=_integer(env, "KINDLEMASTER_MIN_FREE_DISK_BYTES", defaults.min_free_disk_bytes, minimum=1),
        max_file_bytes=_integer(env, "KINDLEMASTER_MAX_FILE_BYTES", defaults.max_file_bytes, minimum=1),
        max_pdf_pages=_integer(env, "KINDLEMASTER_MAX_PDF_PAGES", defaults.max_pdf_pages, minimum=1),
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
