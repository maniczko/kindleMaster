from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from conversion_jobs import TERMINAL_CONVERSION_JOB_STATUSES
from local_env import resolve_runtime_environment


HttpRequest = Callable[..., Any]
SIGNED_URL_EXPIRES_SECONDS = 15 * 60
DEFAULT_SUPABASE_ENV_FILES = (".env.local", ".env")


@dataclass(frozen=True)
class SupabaseLibraryConfig:
    enabled: bool
    configured: bool
    url: str = ""
    service_role_key: str = ""
    bucket: str = "kindlemaster-artifacts"
    missing_config: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return "supabase"


def load_supabase_library_config(environ: Mapping[str, str] | None = None) -> SupabaseLibraryConfig:
    env = resolve_runtime_environment(
        environ,
        env_files=DEFAULT_SUPABASE_ENV_FILES,
        cwd=Path(__file__).resolve().parent,
    )
    enabled = str(env.get("KINDLEMASTER_AUTH_PROVIDER", "") or "").strip().lower() == "supabase"
    url = str(env.get("SUPABASE_URL", "") or "").strip().rstrip("/")
    service_role_key = str(env.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    bucket = str(env.get("SUPABASE_ARTIFACT_BUCKET", "") or "").strip() or "kindlemaster-artifacts"
    missing: list[str] = []
    if enabled and not url:
        missing.append("SUPABASE_URL")
    if enabled and not service_role_key:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    return SupabaseLibraryConfig(
        enabled=enabled,
        configured=enabled and not missing,
        url=url,
        service_role_key=service_role_key,
        bucket=bucket,
        missing_config=tuple(missing),
    )


def build_storage_path(*, user_id: str, job_id: str, kind: str, filename: str) -> str:
    return "/".join(
        [
            _safe_path_part(user_id, fallback="user"),
            _safe_path_part(job_id, fallback="job"),
            _safe_path_part(kind, fallback="artifact"),
            _safe_filename(filename),
        ]
    )


def job_row_to_runtime_job(row: Mapping[str, Any], *, artifacts: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    artifact_payload: dict[str, dict[str, Any]] = {}
    for artifact in artifacts or []:
        kind = str(artifact.get("kind", "") or "").strip()
        if not kind:
            continue
        artifact_payload[kind] = {
            "provider": "supabase",
            "status": "stored",
            "kind": kind,
            "job_id": str(artifact.get("job_id") or row.get("job_id") or ""),
            "filename": str(artifact.get("filename", "") or ""),
            "location": f"supabase://{artifact.get('storage_bucket', '')}/{artifact.get('storage_path', '')}",
            "storage_bucket": str(artifact.get("storage_bucket", "") or ""),
            "storage_path": str(artifact.get("storage_path", "") or ""),
            "size_bytes": _int_or_zero(artifact.get("size_bytes")),
            "content_type": str(artifact.get("content_type", "") or "application/octet-stream"),
            "retention": {"days": _int_or_zero(artifact.get("retention_days")) or 30, "expires_at": ""},
            "signed_url": {"available": False, "url": "", "expires_in_seconds": 0, "reason": "sign_on_demand"},
            "error": "",
        }

    return {
        "job_id": str(row.get("job_id", "") or ""),
        "user_id": str(row.get("user_id", "") or ""),
        "cloud": True,
        "status": str(row.get("status", "") or "unknown"),
        "message": str(row.get("message", "") or ""),
        "source_type": str(row.get("source_type", "") or ""),
        "filename": str(row.get("filename", "") or ""),
        "created_at": str(row.get("created_at", "") or ""),
        "updated_at": str(row.get("updated_at", "") or ""),
        "source_path": "",
        "output_path": "",
        "download_name": str(row.get("download_name", "") or f"{row.get('job_id', 'output')}.epub"),
        "metadata": _mapping(row.get("metadata")),
        "quality_state_snapshot": _mapping(row.get("quality_state_snapshot")),
        "auto_repair": _mapping(row.get("auto_repair")),
        "email_delivery": _mapping(row.get("email_delivery")),
        "runtime": _mapping(row.get("runtime")),
        "artifacts": artifact_payload,
        "artifact_storage": {"provider": "supabase", "status": "available", "reason": ""},
        "output_size_bytes": _int_or_zero(row.get("output_size_bytes")),
        "elapsed_seconds": _int_or_none(row.get("elapsed_seconds")),
        "error": str(row.get("error", "") or ""),
        "error_code": str(row.get("error_code", "") or ""),
        "cloud_sync": {"status": "synced", "provider": "supabase"},
    }


class SupabaseLibraryClient:
    def __init__(
        self,
        config: SupabaseLibraryConfig | None = None,
        *,
        transport: HttpRequest | None = None,
    ) -> None:
        self.config = config or load_supabase_library_config()
        self._transport = transport or default_supabase_json_request

    @property
    def available(self) -> bool:
        return self.config.enabled and self.config.configured

    def upsert_job_snapshot(
        self,
        *,
        user_id: str,
        job: Mapping[str, Any],
        quality_state: Mapping[str, Any],
        imported_from_local: bool = False,
    ) -> dict[str, Any]:
        self._ensure_available()
        payload = _job_to_row_payload(
            user_id=user_id,
            job=job,
            quality_state=quality_state,
            imported_from_local=imported_from_local,
        )
        result = self._request(
            "/rest/v1/conversion_jobs",
            method="POST",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            payload=payload,
        )
        if isinstance(result, list) and result:
            return dict(result[0])
        return payload

    def upload_artifact_bytes(
        self,
        *,
        user_id: str,
        job_id: str,
        kind: str,
        filename: str,
        data: bytes,
        content_type: str | None = None,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_available()
        resolved_content_type = content_type or _guess_content_type(filename)
        storage_path = build_storage_path(user_id=user_id, job_id=job_id, kind=kind, filename=filename)
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in storage_path.split("/"))
        self._request(
            f"/storage/v1/object/{urllib.parse.quote(self.config.bucket, safe='')}/{encoded_path}",
            method="POST",
            headers={
                "Content-Type": resolved_content_type,
                "x-upsert": "true",
            },
            raw_body=data,
        )
        record = {
            "job_id": job_id,
            "user_id": user_id,
            "kind": kind,
            "filename": Path(filename).name,
            "content_type": resolved_content_type,
            "size_bytes": len(data),
            "storage_bucket": self.config.bucket,
            "storage_path": storage_path,
            "signed_url_metadata": {"created": False},
            "retention_days": retention_days or _retention_days_for_kind(kind),
        }
        conflict_query = urllib.parse.urlencode(
            {"on_conflict": "job_id,user_id,kind,filename"},
            safe=",",
        )
        result = self._request(
            f"/rest/v1/conversion_artifacts?{conflict_query}",
            method="POST",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            payload=record,
        )
        if isinstance(result, list) and result:
            record.update(dict(result[0]))
        return record

    def list_user_jobs(self, *, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        self._ensure_available()
        query = urllib.parse.urlencode(
            {
                "user_id": f"eq.{user_id}",
                "order": "updated_at.desc",
                "limit": str(max(1, min(int(limit), 100))),
            }
        )
        rows = self._request(f"/rest/v1/conversion_jobs?{query}", method="GET")
        if not isinstance(rows, list):
            return []
        job_ids = [str(row.get("job_id") or "") for row in rows if isinstance(row, Mapping)]
        artifacts_by_job = self._fetch_artifacts_by_job(user_id=user_id, job_ids=job_ids)
        return [
            job_row_to_runtime_job(row, artifacts=artifacts_by_job.get(str(row.get("job_id") or ""), []))
            for row in rows
            if isinstance(row, Mapping)
        ]

    def get_user_job(self, *, user_id: str, job_id: str) -> dict[str, Any] | None:
        self._ensure_available()
        query = urllib.parse.urlencode(
            {
                "user_id": f"eq.{user_id}",
                "job_id": f"eq.{job_id}",
                "limit": "1",
            }
        )
        rows = self._request(f"/rest/v1/conversion_jobs?{query}", method="GET")
        if not isinstance(rows, list) or not rows:
            return None
        artifacts = self._fetch_artifacts_by_job(user_id=user_id, job_ids=[job_id]).get(job_id, [])
        return job_row_to_runtime_job(rows[0], artifacts=artifacts)

    def get_job_by_id(self, *, job_id: str) -> dict[str, Any] | None:
        """Load a cloud job after the caller has authorized its job-scoped capability."""
        self._ensure_available()
        query = urllib.parse.urlencode(
            {
                "job_id": f"eq.{job_id}",
                "limit": "1",
            }
        )
        rows = self._request(f"/rest/v1/conversion_jobs?{query}", method="GET")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
            return None
        row = rows[0]
        user_id = str(row.get("user_id") or "").strip()
        if not user_id or str(row.get("job_id") or "").strip() != job_id:
            return None
        artifacts = self._fetch_artifacts_by_job(user_id=user_id, job_ids=[job_id]).get(job_id, [])
        return job_row_to_runtime_job(row, artifacts=artifacts)

    def create_signed_artifact_url(
        self,
        *,
        storage_path: str,
        expires_in_seconds: int = SIGNED_URL_EXPIRES_SECONDS,
    ) -> dict[str, Any]:
        self._ensure_available()
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in storage_path.split("/"))
        payload = {"expiresIn": max(60, int(expires_in_seconds))}
        result = self._request(
            f"/storage/v1/object/sign/{urllib.parse.quote(self.config.bucket, safe='')}/{encoded_path}",
            method="POST",
            payload=payload,
        )
        if not isinstance(result, Mapping):
            return {"available": False, "url": "", "expires_in_seconds": payload["expiresIn"], "reason": "invalid_response"}
        signed = str(result.get("signedURL") or result.get("signedUrl") or result.get("signed_url") or "")
        if signed.startswith("/"):
            signed = f"{self.config.url}{signed}"
        return {
            "available": bool(signed),
            "url": signed,
            "expires_in_seconds": payload["expiresIn"],
            "reason": "" if signed else "missing_signed_url",
        }

    def download_artifact_bytes(self, *, storage_path: str) -> bytes:
        self._ensure_available()
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in storage_path.split("/"))
        result = self._request(
            f"/storage/v1/object/{urllib.parse.quote(self.config.bucket, safe='')}/{encoded_path}",
            method="GET",
            expect_json=False,
        )
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode("utf-8")
        raise RuntimeError("invalid_storage_download_response")

    def import_local_jobs(
        self,
        *,
        user_id: str,
        jobs: Mapping[str, Mapping[str, Any]],
        quality_state_builder: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        imported = 0
        skipped = 0
        failed = 0
        failures: list[dict[str, str]] = []
        for job_id, job in jobs.items():
            status = str(job.get("status", "") or "").lower()
            output_path = Path(str(job.get("output_path", "") or ""))
            if status not in TERMINAL_CONVERSION_JOB_STATUSES or status != "ready" or not output_path.is_file():
                skipped += 1
                continue
            try:
                quality_state = quality_state_builder(job_id, job)
                self.upsert_job_snapshot(
                    user_id=user_id,
                    job=job,
                    quality_state=quality_state,
                    imported_from_local=True,
                )
                self.upload_artifact_bytes(
                    user_id=user_id,
                    job_id=job_id,
                    kind="output",
                    filename=str(job.get("download_name") or f"{job_id}.epub"),
                    data=output_path.read_bytes(),
                    content_type="application/epub+zip",
                )
                imported += 1
            except Exception as error:
                failed += 1
                failures.append({"job_id": str(job_id), "error": str(error)})
        return {"success": failed == 0, "imported": imported, "skipped": skipped, "failed": failed, "failures": failures}

    def _fetch_artifacts_by_job(self, *, user_id: str, job_ids: list[str]) -> dict[str, list[Mapping[str, Any]]]:
        clean_job_ids = [job_id for job_id in job_ids if job_id]
        if not clean_job_ids:
            return {}
        quoted = ",".join(clean_job_ids)
        query = urllib.parse.urlencode(
            {
                "user_id": f"eq.{user_id}",
                "job_id": f"in.({quoted})",
                "order": "created_at.asc",
            },
            safe="().,",
        )
        rows = self._request(f"/rest/v1/conversion_artifacts?{query}", method="GET")
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                grouped.setdefault(str(row.get("job_id") or ""), []).append(row)
        return grouped

    def _request(
        self,
        path: str,
        *,
        method: str,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        expect_json: bool = True,
    ) -> Any:
        request_headers = {
            "apikey": self.config.service_role_key,
            "Authorization": f"Bearer {self.config.service_role_key}",
            "Accept": "application/json",
        }
        request_headers.update(dict(headers or {}))
        body = raw_body
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        try:
            return self._transport(
                f"{self.config.url}{path}",
                method=method,
                headers=request_headers,
                body=body,
                expect_json=expect_json,
            )
        except TypeError:
            return self._transport(
                f"{self.config.url}{path}",
                method=method,
                headers=request_headers,
                body=body,
            )

    def _ensure_available(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("supabase_library_disabled")
        if not self.config.configured:
            raise RuntimeError("supabase_library_unconfigured")


def _job_to_row_payload(
    *,
    user_id: str,
    job: Mapping[str, Any],
    quality_state: Mapping[str, Any],
    imported_from_local: bool,
) -> dict[str, Any]:
    return {
        "job_id": str(job.get("job_id", "") or ""),
        "user_id": user_id,
        "status": str(job.get("status", "") or ""),
        "message": str(job.get("message", "") or ""),
        "filename": str(job.get("filename", "") or ""),
        "source_type": str(job.get("source_type", "") or ""),
        "download_name": str(job.get("download_name", "") or ""),
        "created_at": str(job.get("created_at", "") or ""),
        "updated_at": str(job.get("updated_at", "") or ""),
        "elapsed_seconds": _int_or_none(job.get("elapsed_seconds")),
        "output_size_bytes": _int_or_zero(job.get("output_size_bytes")),
        "metadata": _mapping(job.get("metadata")),
        "quality_state_snapshot": dict(quality_state),
        "auto_repair": _mapping(job.get("auto_repair")),
        "email_delivery": _mapping(job.get("email_delivery")),
        "runtime": _mapping(job.get("runtime")),
        "error": str(job.get("error", "") or ""),
        "error_code": str(job.get("error_code", "") or ""),
        "imported_from_local": bool(imported_from_local),
    }


def default_supabase_json_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    expect_json: bool = True,
) -> Any:
    request = urllib.request.Request(url, data=body, method=method.upper())
    for key, value in dict(headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            pass
        raise RuntimeError(f"supabase_http_{error.code}: {detail}") from error
    if not expect_json:
        return data
    if not data:
        return {}
    return json.loads(data.decode("utf-8"))


def _safe_path_part(value: str, *, fallback: str, allow_dots: bool = False) -> str:
    pattern = r"[^A-Za-z0-9_.-]+" if allow_dots else r"[^A-Za-z0-9_-]+"
    normalized = re.sub(pattern, "-", str(value or "").strip()).strip(".-")
    return normalized or fallback


def _safe_filename(value: str) -> str:
    return _safe_path_part(Path(str(value or "")).name, fallback="artifact.bin", allow_dots=True)


def _guess_content_type(filename: str) -> str:
    if filename.lower().endswith(".epub"):
        return "application/epub+zip"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _retention_days_for_kind(kind: str) -> int:
    return {"output": 30, "report": 90, "log": 14, "input": 7}.get(str(kind), 30)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_or_none(value: Any) -> int | None:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


def _int_or_zero(value: Any) -> int:
    converted = _int_or_none(value)
    return converted if converted is not None else 0
