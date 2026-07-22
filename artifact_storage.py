from __future__ import annotations

import importlib
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


DEFAULT_LOCAL_ARTIFACT_ROOT = Path("output") / "artifacts"
DEFAULT_SIGNED_URL_EXPIRES_SECONDS = 15 * 60


class ArtifactKind(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    REPORT = "report"
    LOG = "log"


@dataclass(frozen=True)
class RetentionPolicy:
    days: int
    expires_at: str = ""

    def __post_init__(self) -> None:
        if self.days < 1:
            raise ValueError("RetentionPolicy.days must be positive.")

    @classmethod
    def for_kind(cls, kind: ArtifactKind | str) -> "RetentionPolicy":
        normalized = _normalize_kind(kind)
        defaults = {
            ArtifactKind.INPUT: 7,
            ArtifactKind.OUTPUT: 30,
            ArtifactKind.REPORT: 90,
            ArtifactKind.LOG: 14,
        }
        return cls(days=defaults[normalized])

    def to_metadata(self) -> dict[str, Any]:
        return {"days": self.days, "expires_at": self.expires_at}


@dataclass(frozen=True)
class ArtifactRecord:
    provider: str
    status: str
    kind: str
    job_id: str
    filename: str
    location: str
    size_bytes: int
    content_type: str
    retention: dict[str, Any]
    signed_url: dict[str, Any]
    error: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "kind": self.kind,
            "job_id": self.job_id,
            "filename": self.filename,
            "location": self.location,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "retention": dict(self.retention),
            "signed_url": dict(self.signed_url),
            "error": self.error,
        }


@dataclass(frozen=True)
class ArtifactStorageConfig:
    provider: str = "local"
    bucket: str = ""
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    region_name: str = "auto"
    signed_url_expires_seconds: int = DEFAULT_SIGNED_URL_EXPIRES_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ArtifactStorageConfig":
        env = os.environ if environ is None else environ
        provider = _first_env(env, "KINDLEMASTER_ARTIFACT_STORAGE", "ARTIFACT_STORAGE_PROVIDER") or "local"
        bucket = _first_env(env, "R2_BUCKET", "S3_BUCKET", "AWS_BUCKET_NAME")
        endpoint_url = _first_env(env, "R2_ENDPOINT_URL", "S3_ENDPOINT_URL", "AWS_ENDPOINT_URL")
        access_key_id = _first_env(env, "R2_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
        secret_access_key = _first_env(env, "R2_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
        region_name = _first_env(env, "R2_REGION", "S3_REGION", "AWS_REGION") or "auto"
        expires_raw = _first_env(env, "ARTIFACT_SIGNED_URL_EXPIRES_SECONDS")

        if provider == "local" and (bucket or endpoint_url or access_key_id or secret_access_key):
            provider = "r2"

        try:
            expires = int(expires_raw) if expires_raw else DEFAULT_SIGNED_URL_EXPIRES_SECONDS
        except ValueError:
            expires = DEFAULT_SIGNED_URL_EXPIRES_SECONDS

        return cls(
            provider=provider.strip().lower() or "local",
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region_name=region_name,
            signed_url_expires_seconds=max(60, expires),
        )

    @property
    def is_remote_requested(self) -> bool:
        return self.provider in {"r2", "s3"}

    @property
    def is_remote_configured(self) -> bool:
        return bool(self.bucket and self.endpoint_url and self.access_key_id and self.secret_access_key)


class LocalArtifactStorage:
    provider = "local"

    def __init__(self, root: str | Path = DEFAULT_LOCAL_ARTIFACT_ROOT) -> None:
        self.root = Path(root)

    def availability(self) -> dict[str, Any]:
        return {"provider": self.provider, "status": "available", "reason": ""}

    def put_bytes(
        self,
        *,
        job_id: str,
        kind: ArtifactKind | str,
        filename: str,
        data: bytes,
        retention: RetentionPolicy | None = None,
        content_type: str | None = None,
    ) -> ArtifactRecord:
        normalized_kind = _normalize_kind(kind)
        safe_job_id = _safe_path_part(job_id, fallback="job")
        safe_filename = _safe_filename(filename)
        policy = retention or RetentionPolicy.for_kind(normalized_kind)
        artifact_path = _resolve_local_artifact_path(
            self.root,
            safe_job_id,
            normalized_kind,
            safe_filename,
        )
        _atomic_write_bytes(self.root, artifact_path, data)

        return ArtifactRecord(
            provider=self.provider,
            status="stored",
            kind=normalized_kind.value,
            job_id=safe_job_id,
            filename=safe_filename,
            location=str(artifact_path),
            size_bytes=len(data),
            content_type=content_type or _guess_content_type(safe_filename),
            retention=policy.to_metadata(),
            signed_url=_unavailable_signed_url("local_storage", expires_in_seconds=0),
        )

    def signed_url(self, record: ArtifactRecord, *, expires_in_seconds: int = 0) -> dict[str, Any]:
        return _unavailable_signed_url("local_storage", expires_in_seconds=expires_in_seconds)


class R2ArtifactStorage:
    provider = "r2"

    def __init__(self, config: ArtifactStorageConfig) -> None:
        self.config = config
        self._client: Any | None = None
        self._unavailable_reason = ""
        if not config.is_remote_configured:
            self._unavailable_reason = "R2/S3 artifact storage is not fully configured."
            return
        try:
            boto3 = importlib.import_module("boto3")
        except ImportError:
            self._unavailable_reason = "boto3 is not installed; R2/S3 artifact storage is unavailable."
            return
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name=config.region_name,
        )

    def availability(self) -> dict[str, Any]:
        if self._client is None:
            return {"provider": self.provider, "status": "unavailable", "reason": self._unavailable_reason}
        return {"provider": self.provider, "status": "available", "reason": ""}

    def put_bytes(
        self,
        *,
        job_id: str,
        kind: ArtifactKind | str,
        filename: str,
        data: bytes,
        retention: RetentionPolicy | None = None,
        content_type: str | None = None,
    ) -> ArtifactRecord:
        normalized_kind = _normalize_kind(kind)
        safe_job_id = _safe_path_part(job_id, fallback="job")
        safe_filename = _safe_filename(filename)
        policy = retention or RetentionPolicy.for_kind(normalized_kind)
        key = f"{safe_job_id}/{normalized_kind.value}/{safe_filename}"
        location = f"r2://{self.config.bucket}/{key}"
        resolved_content_type = content_type or _guess_content_type(safe_filename)

        if self._client is None:
            return ArtifactRecord(
                provider=self.provider,
                status="unavailable",
                kind=normalized_kind.value,
                job_id=safe_job_id,
                filename=safe_filename,
                location=location,
                size_bytes=len(data),
                content_type=resolved_content_type,
                retention=policy.to_metadata(),
                signed_url=_unavailable_signed_url(
                    "storage_unavailable",
                    expires_in_seconds=self.config.signed_url_expires_seconds,
                ),
                error=self._unavailable_reason,
            )

        self._client.put_object(
            Bucket=self.config.bucket,
            Key=key,
            Body=data,
            ContentType=resolved_content_type,
            Metadata={
                "kindlemaster-job-id": safe_job_id,
                "kindlemaster-artifact-kind": normalized_kind.value,
                "kindlemaster-retention-days": str(policy.days),
            },
        )
        record = ArtifactRecord(
            provider=self.provider,
            status="stored",
            kind=normalized_kind.value,
            job_id=safe_job_id,
            filename=safe_filename,
            location=location,
            size_bytes=len(data),
            content_type=resolved_content_type,
            retention=policy.to_metadata(),
            signed_url=_unavailable_signed_url("not_requested", expires_in_seconds=0),
        )
        return record

    def signed_url(
        self,
        record: ArtifactRecord,
        *,
        expires_in_seconds: int | None = None,
    ) -> dict[str, Any]:
        expires = expires_in_seconds or self.config.signed_url_expires_seconds
        if self._client is None:
            return _unavailable_signed_url("storage_unavailable", expires_in_seconds=expires)
        prefix = f"r2://{self.config.bucket}/"
        key = record.location.removeprefix(prefix)
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket, "Key": key},
                ExpiresIn=expires,
            )
        except Exception as error:
            return _unavailable_signed_url(str(error), expires_in_seconds=expires)
        return {"available": True, "url": url, "expires_in_seconds": expires, "reason": ""}


def build_artifact_storage(
    environ: Mapping[str, str] | None = None,
    *,
    local_root: str | Path = DEFAULT_LOCAL_ARTIFACT_ROOT,
) -> LocalArtifactStorage | R2ArtifactStorage:
    env = os.environ if environ is None else environ
    config = ArtifactStorageConfig.from_env(env)
    if config.is_remote_requested and config.is_remote_configured:
        return R2ArtifactStorage(config)
    configured_local_root = _first_env(env, "KINDLEMASTER_ARTIFACT_ROOT")
    return LocalArtifactStorage(configured_local_root or local_root)


def _resolve_local_artifact_path(
    root: str | Path,
    safe_job_id: str,
    kind: ArtifactKind,
    safe_filename: str,
) -> Path:
    resolved_root = os.path.realpath(os.fspath(root))
    candidate = os.path.realpath(os.path.join(resolved_root, safe_job_id, kind.value, safe_filename))
    if not candidate.startswith(resolved_root + os.sep):
        raise ValueError("Artifact path must remain inside the configured storage root.")
    return Path(candidate)


def _atomic_write_bytes(root: str | Path, path: Path, data: bytes) -> None:
    resolved_root = os.path.realpath(os.fspath(root))
    resolved_path_value = os.path.realpath(os.fspath(path))
    if not resolved_path_value.startswith(resolved_root + os.sep):
        raise ValueError("Artifact path must remain inside the configured storage root.")
    resolved_path = Path(resolved_path_value)

    # All filesystem sinks below use the path proven relative to resolved_root.
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefix = f".{resolved_path.name}."
    temp_directory = resolved_path.parent
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=temp_prefix,
        suffix=".tmp",
        dir=temp_directory,
    )
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, resolved_path)
        _fsync_directory(resolved_path.parent)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            # The descriptor may already be owned and closed by fdopen.
            pass
        temp_path.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Some filesystems do not support directory fsync.
        pass
    finally:
        os.close(descriptor)


def _first_env(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_kind(kind: ArtifactKind | str) -> ArtifactKind:
    return kind if isinstance(kind, ArtifactKind) else ArtifactKind(str(kind).strip().lower())


def _safe_path_part(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return normalized or fallback


def _safe_filename(value: str) -> str:
    name = Path(str(value or "")).name
    return _safe_path_part(name, fallback="artifact.bin")


def _guess_content_type(filename: str) -> str:
    if filename.lower().endswith(".epub"):
        return "application/epub+zip"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _unavailable_signed_url(reason: str, *, expires_in_seconds: int) -> dict[str, Any]:
    return {
        "available": False,
        "url": "",
        "expires_in_seconds": expires_in_seconds,
        "reason": reason,
    }
