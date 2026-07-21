from __future__ import annotations

import hashlib
import hmac
import io
import os
import secrets
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from durable_job_queue import DurableJobDatabase, DurableJobQueue


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after_seconds: int


@dataclass(frozen=True)
class ProductionGuardrailPolicy:
    authenticated_start_per_minute: int = 12
    guest_start_per_minute: int = 4
    authenticated_retry_per_minute: int = 10
    guest_retry_per_minute: int = 3
    mutation_per_minute: int = 30
    polling_per_minute: int = 180
    authenticated_active_jobs: int = 3
    guest_active_jobs: int = 1
    global_active_jobs: int = 4
    max_upload_bytes: int = 75 * 1024 * 1024
    max_pdf_pages: int = 1_200
    max_pdf_xref_objects: int = 250_000
    max_docx_members: int = 5_000
    max_docx_uncompressed_bytes: int = 300 * 1024 * 1024
    max_docx_member_bytes: int = 100 * 1024 * 1024
    max_archive_ratio: float = 200.0
    max_image_pixels: int = 100_000_000
    min_disk_free_bytes: int = 2 * 1024 * 1024 * 1024
    min_disk_free_ratio: float = 0.10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProductionGuardrailPolicy":
        source = os.environ if env is None else env

        def integer(name: str, default: int, minimum: int = 1) -> int:
            return max(minimum, int(source.get(name, str(default)) or default))

        def decimal(name: str, default: float, minimum: float = 0.0) -> float:
            return max(minimum, float(source.get(name, str(default)) or default))

        return cls(
            authenticated_start_per_minute=integer("KINDLEMASTER_AUTH_STARTS_PER_MINUTE", 12),
            guest_start_per_minute=integer("KINDLEMASTER_GUEST_STARTS_PER_MINUTE", 4),
            authenticated_retry_per_minute=integer("KINDLEMASTER_AUTH_RETRIES_PER_MINUTE", 10),
            guest_retry_per_minute=integer("KINDLEMASTER_GUEST_RETRIES_PER_MINUTE", 3),
            mutation_per_minute=integer("KINDLEMASTER_MUTATIONS_PER_MINUTE", 30),
            polling_per_minute=integer("KINDLEMASTER_POLLING_PER_MINUTE", 180),
            authenticated_active_jobs=integer("KINDLEMASTER_AUTH_ACTIVE_JOBS", 3),
            guest_active_jobs=integer("KINDLEMASTER_GUEST_ACTIVE_JOBS", 1),
            global_active_jobs=integer("KINDLEMASTER_GLOBAL_ACTIVE_JOBS", 4),
            max_upload_bytes=integer("KINDLEMASTER_MAX_UPLOAD_BYTES", 75 * 1024 * 1024),
            max_pdf_pages=integer("KINDLEMASTER_MAX_PDF_PAGES", 1_200),
            max_pdf_xref_objects=integer("KINDLEMASTER_MAX_PDF_XREF_OBJECTS", 250_000),
            max_docx_members=integer("KINDLEMASTER_MAX_DOCX_MEMBERS", 5_000),
            max_docx_uncompressed_bytes=integer(
                "KINDLEMASTER_MAX_DOCX_UNCOMPRESSED_BYTES", 300 * 1024 * 1024
            ),
            max_docx_member_bytes=integer("KINDLEMASTER_MAX_DOCX_MEMBER_BYTES", 100 * 1024 * 1024),
            max_archive_ratio=decimal("KINDLEMASTER_MAX_ARCHIVE_RATIO", 200.0, 1.0),
            max_image_pixels=integer("KINDLEMASTER_MAX_IMAGE_PIXELS", 100_000_000),
            min_disk_free_bytes=integer("KINDLEMASTER_MIN_DISK_FREE_BYTES", 2 * 1024 * 1024 * 1024),
            min_disk_free_ratio=decimal("KINDLEMASTER_MIN_DISK_FREE_RATIO", 0.10, 0.0),
        )


class InputPolicyError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SQLiteFixedWindowRateLimiter:
    def __init__(self, database: DurableJobDatabase) -> None:
        self.database = database

    def consume(self, key: str, *, limit: int, window_seconds: int = 60) -> RateLimitDecision:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError("rate limit key is required")
        now = time.time()
        window = max(1, int(window_seconds))
        normalized_limit = max(1, int(limit))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT window_started_at, count FROM rate_limit_windows WHERE key = ?",
                (normalized_key,),
            ).fetchone()
            if row is None or now - float(row["window_started_at"]) >= window:
                started_at = now
                count = 1
                connection.execute(
                    """
                    INSERT INTO rate_limit_windows(key, window_started_at, count, updated_at)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        window_started_at = excluded.window_started_at,
                        count = 1,
                        updated_at = excluded.updated_at
                    """,
                    (normalized_key, started_at, now),
                )
            else:
                started_at = float(row["window_started_at"])
                count = int(row["count"]) + 1
                connection.execute(
                    "UPDATE rate_limit_windows SET count = ?, updated_at = ? WHERE key = ?",
                    (count, now, normalized_key),
                )
            connection.commit()
        reset_at = int(started_at + window)
        allowed = count <= normalized_limit
        remaining = max(0, normalized_limit - count)
        retry_after = max(1, reset_at - int(now)) if not allowed else 0
        return RateLimitDecision(
            allowed=allowed,
            limit=normalized_limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after_seconds=retry_after,
        )

    def cleanup(self, *, older_than_seconds: int = 86_400) -> int:
        cutoff = time.time() - max(60, int(older_than_seconds))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM rate_limit_windows WHERE updated_at < ?", (cutoff,))
            connection.commit()
        return int(cursor.rowcount)


def load_or_create_rate_secret(database: DurableJobDatabase) -> bytes:
    configured = str(os.environ.get("KINDLEMASTER_RATE_LIMIT_SECRET") or "").strip()
    if configured:
        return configured.encode("utf-8")
    secret_path = database.path.with_name("rate_limit.secret")
    try:
        if secret_path.is_file():
            secret = secret_path.read_bytes().strip()
            if len(secret) >= 32:
                return secret
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(48).encode("ascii")
        temp_path = secret_path.with_suffix(secret_path.suffix + ".tmp")
        temp_path.write_bytes(secret)
        os.chmod(temp_path, 0o600)
        temp_path.replace(secret_path)
        return secret
    except OSError as error:
        raise RuntimeError("Cannot initialize the production rate-limit secret.") from error


def pseudonymous_owner_key(
    *,
    secret: bytes,
    authorization: str = "",
    guest_capability: str = "",
    remote_address: str = "",
    user_agent: str = "",
) -> tuple[str, bool]:
    authorization = str(authorization or "").strip()
    guest_capability = str(guest_capability or "").strip()
    if authorization:
        raw = f"auth\0{authorization}".encode("utf-8")
        return f"auth-token:{hmac.new(secret, raw, hashlib.sha256).hexdigest()}", True
    if guest_capability:
        raw = f"guest\0{guest_capability}".encode("utf-8")
        return f"guest:{hmac.new(secret, raw, hashlib.sha256).hexdigest()}", False
    raw = f"rate-only\0{remote_address}\0{user_agent[:200]}".encode("utf-8")
    return f"rate-fallback:{hmac.new(secret, raw, hashlib.sha256).hexdigest()}", False


def validate_upload_bytes(filename: str, content_type: str, data: bytes, policy: ProductionGuardrailPolicy) -> str:
    name = Path(str(filename or "")).name
    suffix = Path(name).suffix.lower()
    if len(data) > policy.max_upload_bytes:
        raise InputPolicyError("Plik przekracza dozwolony limit rozmiaru.", code="upload_size_limit", status_code=413)
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise InputPolicyError("Rozszerzenie PDF nie odpowiada zawartości pliku.", code="upload_magic_mismatch")
        if content_type and content_type.lower() not in {"application/pdf", "application/octet-stream"}:
            raise InputPolicyError("Nieprawidłowy typ MIME pliku PDF.", code="upload_mime_mismatch")
        _validate_pdf(data, policy)
        return "pdf"
    if suffix == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            raise InputPolicyError("Rozszerzenie DOCX nie odpowiada zawartości pliku.", code="upload_magic_mismatch")
        allowed_mime = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
            "application/octet-stream",
        }
        if content_type and content_type.lower() not in allowed_mime:
            raise InputPolicyError("Nieprawidłowy typ MIME pliku DOCX.", code="upload_mime_mismatch")
        _validate_docx(data, policy)
        return "docx"
    raise InputPolicyError("Obsługiwane są tylko pliki PDF i DOCX.", code="unsupported_source_type", status_code=400)


def _validate_pdf(data: bytes, policy: ProductionGuardrailPolicy) -> None:
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
    except Exception as error:
        raise InputPolicyError("Plik PDF jest uszkodzony lub nieobsługiwany.", code="malformed_pdf") from error
    try:
        if document.needs_pass:
            raise InputPolicyError("Pliki PDF chronione hasłem nie są obsługiwane.", code="password_protected_pdf")
        if document.page_count < 1:
            raise InputPolicyError("Plik PDF nie zawiera stron.", code="empty_pdf")
        if document.page_count > policy.max_pdf_pages:
            raise InputPolicyError("Plik PDF przekracza limit liczby stron.", code="pdf_page_limit", status_code=413)
        if document.xref_length() > policy.max_pdf_xref_objects:
            raise InputPolicyError("Plik PDF ma zbyt złożoną strukturę obiektów.", code="pdf_object_limit", status_code=413)
    finally:
        document.close()


def _validate_docx(data: bytes, policy: ProductionGuardrailPolicy) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as error:
        raise InputPolicyError("Plik DOCX jest uszkodzony.", code="malformed_docx") from error
    with archive:
        members = archive.infolist()
        if len(members) > policy.max_docx_members:
            raise InputPolicyError("Plik DOCX zawiera zbyt wiele elementów.", code="docx_member_limit", status_code=413)
        names = {member.filename for member in members}
        required = {"[Content_Types].xml", "word/document.xml"}
        if not required.issubset(names):
            raise InputPolicyError("Archiwum nie jest prawidłowym plikiem DOCX.", code="invalid_docx_structure")
        total_uncompressed = 0
        total_compressed = 0
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise InputPolicyError("Plik DOCX zawiera niebezpieczną ścieżkę.", code="docx_path_traversal")
            if member.file_size > policy.max_docx_member_bytes:
                raise InputPolicyError("Element DOCX przekracza limit rozmiaru.", code="docx_member_size_limit", status_code=413)
            total_uncompressed += int(member.file_size)
            total_compressed += max(1, int(member.compress_size))
        if total_uncompressed > policy.max_docx_uncompressed_bytes:
            raise InputPolicyError("Rozpakowana zawartość DOCX przekracza limit.", code="docx_uncompressed_limit", status_code=413)
        if total_uncompressed / max(1, total_compressed) > policy.max_archive_ratio:
            raise InputPolicyError("Plik DOCX ma podejrzany współczynnik kompresji.", code="archive_expansion_limit", status_code=413)
        _validate_docx_images(archive, members, policy)


def _validate_docx_images(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    policy: ProductionGuardrailPolicy,
) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    for member in members:
        if not member.filename.startswith("word/media/") or member.file_size == 0:
            continue
        try:
            with Image.open(io.BytesIO(archive.read(member))) as image:
                width, height = image.size
        except Exception:
            continue
        if int(width) * int(height) > policy.max_image_pixels:
            raise InputPolicyError("Obraz osadzony w DOCX przekracza limit pikseli.", code="image_pixel_limit", status_code=413)


def disk_headroom(path: str | os.PathLike[str], policy: ProductionGuardrailPolicy) -> dict[str, Any]:
    usage = shutil.disk_usage(Path(path))
    free_ratio = usage.free / max(1, usage.total)
    allowed = usage.free >= policy.min_disk_free_bytes and free_ratio >= policy.min_disk_free_ratio
    return {
        "allowed": allowed,
        "free_bytes": int(usage.free),
        "total_bytes": int(usage.total),
        "free_ratio": float(free_ratio),
    }


def _route_policy(path: str, method: str, authenticated: bool, policy: ProductionGuardrailPolicy) -> tuple[str, int] | None:
    normalized_path = str(path or "")
    normalized_method = str(method or "GET").upper()
    if normalized_path in {"/convert/start", "/convert"} and normalized_method == "POST":
        return "start", policy.authenticated_start_per_minute if authenticated else policy.guest_start_per_minute
    if normalized_path.startswith("/convert/retry/") and normalized_method == "POST":
        return "retry", policy.authenticated_retry_per_minute if authenticated else policy.guest_retry_per_minute
    if normalized_method in {"POST", "PUT", "DELETE"} and normalized_path.startswith("/convert/"):
        return "mutation", policy.mutation_per_minute
    if normalized_path.startswith("/convert/") or normalized_path == "/convert/jobs":
        return "poll", policy.polling_per_minute
    return None


def install_production_guardrails(
    app_module: Any,
    *,
    database: DurableJobDatabase,
    queue: DurableJobQueue,
    policy: ProductionGuardrailPolicy | None = None,
) -> None:
    from flask import g, request

    policy = policy or ProductionGuardrailPolicy.from_env()
    limiter = SQLiteFixedWindowRateLimiter(database)
    secret = load_or_create_rate_secret(database)

    def current_owner() -> tuple[str, bool]:
        try:
            auth_context = app_module._resolve_request_auth_context()
        except Exception:
            auth_context = None
        if auth_context is not None and getattr(auth_context, "authenticated", False):
            return f"user:{auth_context.user_id}", True
        capability = str(
            request.headers.get("X-KindleMaster-Guest-Capability")
            or request.cookies.get("kindlemaster_guest")
            or ""
        ).strip()
        return pseudonymous_owner_key(
            secret=secret,
            authorization=str(request.headers.get("Authorization") or ""),
            guest_capability=capability,
            remote_address=str(request.remote_addr or ""),
            user_agent=str(request.headers.get("User-Agent") or ""),
        )

    @app_module.app.before_request
    def enforce_production_guardrails():
        owner_key, authenticated = current_owner()
        g.kindlemaster_rate_owner_key = owner_key
        g.kindlemaster_rate_authenticated = authenticated
        route_policy = _route_policy(request.path, request.method, authenticated, policy)
        if route_policy is not None:
            route_class, limit = route_policy
            decision = limiter.consume(f"{owner_key}:{route_class}", limit=limit, window_seconds=60)
            g.kindlemaster_rate_limit_decision = decision
            if not decision.allowed:
                response = app_module._json_error(
                    "Przekroczono limit żądań. Spróbuj ponownie później.",
                    error_code="rate_limit_exceeded",
                    status_code=429,
                    phase="admission",
                    retryable=True,
                    extra={"retry_after_seconds": decision.retry_after_seconds, "rule": route_class},
                )
                response.headers["Retry-After"] = str(decision.retry_after_seconds)
                return response

        if request.path in {"/convert/start", "/convert"} and request.method == "POST":
            global_active = queue.active_count()
            owner_active = queue.active_count(owner_key=owner_key)
            owner_limit = policy.authenticated_active_jobs if authenticated else policy.guest_active_jobs
            if global_active >= policy.global_active_jobs:
                response = app_module._json_error(
                    "Usługa osiągnęła bezpieczny limit aktywnych konwersji.",
                    error_code="global_capacity_exceeded",
                    status_code=503,
                    phase="admission",
                    retryable=True,
                    extra={"retry_after_seconds": 30},
                )
                response.headers["Retry-After"] = "30"
                return response
            if owner_active >= owner_limit:
                response = app_module._json_error(
                    "Osiągnięto limit aktywnych konwersji dla tego konta lub sesji.",
                    error_code="owner_concurrency_exceeded",
                    status_code=429,
                    phase="admission",
                    retryable=True,
                    extra={"retry_after_seconds": 30},
                )
                response.headers["Retry-After"] = "30"
                return response
            headroom = disk_headroom(app_module.UPLOAD_DIR, policy)
            if not headroom["allowed"]:
                response = app_module._json_error(
                    "Brak bezpiecznego zapasu miejsca na rozpoczęcie konwersji.",
                    error_code="storage_capacity_exceeded",
                    status_code=503,
                    phase="admission",
                    retryable=True,
                    extra={"retry_after_seconds": 60},
                )
                response.headers["Retry-After"] = "60"
                return response
            upload = request.files.get("file") or request.files.get("pdf")
            if upload is not None and upload.filename:
                if request.content_length and request.content_length > policy.max_upload_bytes + 2 * 1024 * 1024:
                    return app_module._json_error(
                        "Plik przekracza dozwolony limit rozmiaru.",
                        error_code="upload_size_limit",
                        status_code=413,
                        phase="upload",
                        retryable=False,
                    )
                data = upload.stream.read(policy.max_upload_bytes + 1)
                upload.stream.seek(0)
                try:
                    validate_upload_bytes(upload.filename, upload.content_type or "", data, policy)
                except InputPolicyError as error:
                    return app_module._json_error(
                        str(error),
                        error_code=error.code,
                        status_code=error.status_code,
                        phase="upload",
                        retryable=False,
                    )
        return None

    @app_module.app.after_request
    def expose_rate_limit_headers(response):
        decision = getattr(g, "kindlemaster_rate_limit_decision", None)
        if decision is not None:
            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
            response.headers["X-RateLimit-Reset"] = str(decision.reset_at)
        return response

    # Durable submission resolves this global at call time, so guest queue
    # ownership uses the same pseudonymous key as distributed rate limits.
    import production_runtime

    original_owner_key = production_runtime._safe_owner_key

    def shared_owner_key(*, cloud_user_id: str, job_id: str) -> str:
        if str(cloud_user_id or "").strip():
            return original_owner_key(cloud_user_id=cloud_user_id, job_id=job_id)
        try:
            return str(getattr(g, "kindlemaster_rate_owner_key", "") or "") or original_owner_key(
                cloud_user_id="", job_id=job_id
            )
        except Exception:
            return original_owner_key(cloud_user_id="", job_id=job_id)

    production_runtime._safe_owner_key = shared_owner_key
    app_module._PRODUCTION_GUARDRAIL_POLICY = policy
    app_module._PRODUCTION_RATE_LIMITER = limiter
