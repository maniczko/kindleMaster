from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from flask import Flask, Response, g, has_request_context, request
from werkzeug.exceptions import BadRequest, Unauthorized

from app_runtime_services import ConversionJobStore
from conversion_job_access import (
    GUEST_ID_HEADER,
    GUEST_ID_QUERY_PARAM,
    GUEST_OWNER_FIELD,
    JOB_ACCESS_QUERY_PARAM,
    USER_OWNER_FIELD,
    InvalidGuestIdentity,
    append_job_access_token,
    create_job_access_token,
    guest_owner_id,
    is_local_request_host,
    legacy_local_guest_allowed,
    normalize_guest_id,
    verify_job_access_token,
)
from supabase_auth import load_supabase_auth_config, resolve_bearer_token, validate_bearer_token


_INSTALL_MARKER = "_kindlemaster_job_access_installed"
_REQUEST_CACHE_KEY = "_kindlemaster_job_access_identity"
_GUARDED_COLLECTION_ROUTES = {
    ("POST", "/convert/start"),
    ("GET", "/convert/jobs"),
    ("GET", "/convert/library"),
    ("GET", "/convert/archive"),
    ("GET", "/convert/search"),
}


@dataclass(frozen=True)
class RequestIdentity:
    bearer_present: bool = False
    authenticated: bool = False
    user_id: str = ""
    guest_owner_id: str = ""
    legacy_local: bool = False
    auth_error: str = ""
    guest_error: str = ""


def _raw_query_value(url: object, key: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        query = urlsplit(raw).query
    except ValueError:
        return ""
    for candidate_key, candidate_value in parse_qsl(query, keep_blank_values=True):
        if candidate_key == key:
            return candidate_value
    return ""


def _guest_id_from_request() -> str:
    direct = str(request.headers.get(GUEST_ID_HEADER) or request.args.get(GUEST_ID_QUERY_PARAM) or "").strip()
    if direct:
        return normalize_guest_id(direct)
    if request.method in {"GET", "HEAD"}:
        referrer_value = _raw_query_value(request.referrer, GUEST_ID_QUERY_PARAM)
        if referrer_value:
            return normalize_guest_id(referrer_value)
    return ""


def _request_access_token() -> str:
    direct = str(request.args.get(JOB_ACCESS_QUERY_PARAM) or "").strip()
    if direct:
        return direct
    if request.method in {"GET", "HEAD"}:
        return _raw_query_value(request.referrer, JOB_ACCESS_QUERY_PARAM)
    return ""


def _resolve_request_identity() -> RequestIdentity:
    if not has_request_context():
        return RequestIdentity()
    cached = getattr(g, _REQUEST_CACHE_KEY, None)
    if isinstance(cached, RequestIdentity):
        return cached

    bearer = resolve_bearer_token(request.headers.get("Authorization"))
    authenticated = False
    user_id = ""
    auth_error = ""
    if bearer:
        context = validate_bearer_token(bearer, config=load_supabase_auth_config())
        if context.authenticated:
            authenticated = True
            user_id = context.user_id
        else:
            auth_error = context.error_code or "invalid_auth_token"

    guest_owner = ""
    guest_error = ""
    try:
        raw_guest_id = _guest_id_from_request()
        if raw_guest_id:
            guest_owner = guest_owner_id(raw_guest_id)
    except InvalidGuestIdentity as error:
        guest_error = error.error_code

    identity = RequestIdentity(
        bearer_present=bool(bearer),
        authenticated=authenticated,
        user_id=user_id,
        guest_owner_id=guest_owner,
        legacy_local=not guest_owner and legacy_local_guest_allowed(request.host),
        auth_error=auth_error,
        guest_error=guest_error,
    )
    setattr(g, _REQUEST_CACHE_KEY, identity)
    return identity


def _job_user_owner(job: Mapping[str, Any]) -> str:
    return str(job.get(USER_OWNER_FIELD) or "").strip()


def _job_guest_owner(job: Mapping[str, Any]) -> str:
    return str(job.get(GUEST_OWNER_FIELD) or "").strip()


def _job_is_legacy_unowned(job: Mapping[str, Any]) -> bool:
    return not _job_user_owner(job) and not _job_guest_owner(job)


def _job_is_shared_artifact_recovery(job: Mapping[str, Any]) -> bool:
    return any(
        bool(job.get(field))
        for field in (
            "recovered_from_artifacts",
            "restored_from_artifacts",
            "restored_from_smoke",
            "imported_from_local",
        )
    )


def _read_allowed(job_id: str, job: Mapping[str, Any], identity: RequestIdentity) -> bool:
    if not has_request_context():
        return True
    if request.method in {"GET", "HEAD"} and verify_job_access_token(job_id, _request_access_token()):
        return True
    if identity.guest_error:
        return False
    if identity.auth_error:
        return identity.legacy_local and _job_is_legacy_unowned(job)
    if identity.authenticated:
        return bool(identity.user_id) and _job_user_owner(job) == identity.user_id
    if identity.guest_owner_id:
        return _job_guest_owner(job) == identity.guest_owner_id and not _job_is_shared_artifact_recovery(job)
    return identity.legacy_local and _job_is_legacy_unowned(job)


def _write_allowed(job: Mapping[str, Any], identity: RequestIdentity) -> bool:
    if not has_request_context():
        return True
    if identity.guest_error:
        return False
    if identity.auth_error:
        return identity.legacy_local and _job_is_legacy_unowned(job)
    user_owner = _job_user_owner(job)
    if user_owner:
        return identity.authenticated and identity.user_id == user_owner
    guest_owner = _job_guest_owner(job)
    if guest_owner:
        return (
            not identity.bearer_present
            and identity.guest_owner_id == guest_owner
            and not _job_is_shared_artifact_recovery(job)
        )
    return identity.legacy_local and _job_is_legacy_unowned(job)


def _claim_import_allowed(job: Mapping[str, Any], identity: RequestIdentity) -> bool:
    if identity.authenticated:
        if _job_user_owner(job):
            return _job_user_owner(job) == identity.user_id
        guest_owner = _job_guest_owner(job)
        return not guest_owner or guest_owner == identity.guest_owner_id
    if identity.auth_error and identity.legacy_local and identity.bearer_present:
        guest_owner = _job_guest_owner(job)
        return not _job_user_owner(job) and (not guest_owner or guest_owner == identity.guest_owner_id)
    return False


def _copy_owner_fields(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    user_owner = _job_user_owner(source)
    guest_owner = _job_guest_owner(source)
    if user_owner and not _job_user_owner(target):
        target[USER_OWNER_FIELD] = user_owner
    if guest_owner and not _job_guest_owner(target):
        target[GUEST_OWNER_FIELD] = guest_owner


def _prepare_new_job_owner(store: ConversionJobStore, payload: dict[str, Any]) -> dict[str, Any]:
    if not has_request_context():
        return payload

    existing: Mapping[str, Any] | None = None
    job_id = str(payload.get("job_id") or "").strip()
    retry_of = str(payload.get("retry_of") or "").strip()
    with store._lock:
        if job_id:
            existing = store._jobs.get(job_id)
        parent = store._jobs.get(retry_of) if retry_of else None
    if isinstance(existing, Mapping):
        _copy_owner_fields(existing, payload)
    if isinstance(parent, Mapping):
        _copy_owner_fields(parent, payload)

    identity = _resolve_request_identity()
    if identity.guest_error:
        raise BadRequest(description="Invalid anonymous conversion session identifier.")
    if identity.auth_error and not identity.legacy_local:
        raise Unauthorized(description="Invalid authenticated session for conversion job ownership.")

    user_owner = _job_user_owner(payload)
    if user_owner:
        if identity.authenticated and identity.user_id != user_owner:
            raise Unauthorized(description="Conversion job owner does not match the authenticated user.")
        local_server_owned_job = identity.auth_error and identity.legacy_local and identity.bearer_present
        signed_existing_job = bool(existing and _read_allowed(job_id, existing, identity))
        if not identity.authenticated and not local_server_owned_job and not signed_existing_job:
            raise Unauthorized(description="Authenticated ownership is required for this conversion job.")
        return payload

    guest_owner = _job_guest_owner(payload)
    if guest_owner:
        if identity.guest_owner_id != guest_owner and not identity.authenticated:
            raise Unauthorized(description="Anonymous conversion session does not own this job.")
        return payload

    if identity.authenticated:
        payload[USER_OWNER_FIELD] = identity.user_id
        return payload
    if identity.guest_owner_id:
        payload[GUEST_OWNER_FIELD] = identity.guest_owner_id
        return payload
    if identity.legacy_local:
        return payload
    raise Unauthorized(description="Anonymous conversion session identity is required.")


def _raw_snapshot(store: ConversionJobStore) -> dict[str, dict[str, Any]]:
    with store._lock:
        return {job_id: dict(job) for job_id, job in store._jobs.items()}


def _raw_persist(store: ConversionJobStore) -> dict[str, Any]:
    if not store._persistence_path:
        return {"persisted": False, "job_count": 0, "error": ""}
    snapshot = _raw_snapshot(store)
    payload = {
        "version": 1,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "jobs": snapshot,
    }
    try:
        store._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = store._persistence_path.with_suffix(store._persistence_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(store._persistence_path)
        store._persistence_fingerprint = store._read_persistence_fingerprint()
    except OSError as error:
        return {"persisted": False, "job_count": len(snapshot), "error": str(error)}
    return {"persisted": True, "job_count": len(snapshot), "error": ""}


def _json_identity_error(*, error_code: str, status_code: int) -> Response:
    payload = {
        "success": False,
        "error": "Nie można potwierdzić właściciela sesji konwersji.",
        "error_code": error_code,
        "phase": "auth",
        "retryable": False,
    }
    response = Response(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        status=status_code,
        mimetype="application/json",
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _collection_identity_guard() -> Response | None:
    if not has_request_context() or request.method == "OPTIONS":
        return None
    if (request.method, request.path) not in _GUARDED_COLLECTION_ROUTES:
        return None
    if is_local_request_host(request.host) and legacy_local_guest_allowed(request.host):
        return None
    if resolve_bearer_token(request.headers.get("Authorization")):
        return None
    try:
        if _guest_id_from_request():
            return None
    except InvalidGuestIdentity:
        return _json_identity_error(error_code="invalid_guest_identity", status_code=400)
    return _json_identity_error(error_code="guest_identity_required", status_code=401)


def _internal_job_url(value: str, job_id: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc:
        return False
    if not parsed.path.startswith(("/convert/", "/pdf/")):
        return False
    return f"/{job_id}" in parsed.path


def _sign_job_links(value: Any, job_id: str, token: str) -> Any:
    if isinstance(value, dict):
        nested_job_id = str(value.get("job_id") or job_id).strip()
        nested_token = token if nested_job_id == job_id else create_job_access_token(nested_job_id)
        return {key: _sign_job_links(item, nested_job_id, nested_token) for key, item in value.items()}
    if isinstance(value, list):
        return [_sign_job_links(item, job_id, token) for item in value]
    if isinstance(value, str) and _internal_job_url(value, job_id):
        return append_job_access_token(value, token)
    return value


def _sign_payload_job_links(value: Any) -> Any:
    if isinstance(value, dict):
        job_id = str(value.get("job_id") or "").strip()
        if job_id:
            return _sign_job_links(value, job_id, create_job_access_token(job_id))
        return {key: _sign_payload_job_links(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sign_payload_job_links(item) for item in value]
    return value


def _sign_json_response_links(response: Response) -> Response:
    if not has_request_context() or is_local_request_host(request.host):
        return response
    if not response.is_json or response.status_code >= 400:
        return response
    try:
        payload = response.get_json(silent=True)
    except Exception:
        return response
    if not isinstance(payload, (dict, list)):
        return response

    signed_payload = _sign_payload_job_links(payload)
    response.set_data(json.dumps(signed_payload, ensure_ascii=False, separators=(",", ":")))
    response.content_type = "application/json; charset=utf-8"
    response.headers["X-KindleMaster-Job-Links"] = "signed"
    return response


def install_conversion_job_store_security() -> None:
    if getattr(ConversionJobStore, _INSTALL_MARKER, False):
        return

    original_create = ConversionJobStore.create
    original_update = ConversionJobStore.update
    original_get = ConversionJobStore.get
    original_delete = ConversionJobStore.delete
    original_snapshot = ConversionJobStore.snapshot

    def secure_create(self: ConversionJobStore, job: Mapping[str, Any]) -> dict[str, Any]:
        return original_create(self, _prepare_new_job_owner(self, dict(job)))

    def secure_update(
        self: ConversionJobStore,
        job_id: str,
        fields: Mapping[str, Any],
        *,
        updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        raw_job = original_get(self, job_id)
        if raw_job is not None and has_request_context():
            identity = _resolve_request_identity()
            if request.method in {"GET", "HEAD"}:
                allowed = _read_allowed(job_id, raw_job, identity)
            else:
                allowed = _write_allowed(raw_job, identity)
            if not allowed:
                return None
        return original_update(self, job_id, fields, updated_at=updated_at)

    def secure_get(self: ConversionJobStore, job_id: str) -> dict[str, Any] | None:
        job = original_get(self, job_id)
        if job is None or not has_request_context():
            return job
        identity = _resolve_request_identity()
        if request.method in {"GET", "HEAD"}:
            allowed = _read_allowed(job_id, job, identity)
        else:
            allowed = _write_allowed(job, identity)
        return job if allowed else None

    def secure_delete(self: ConversionJobStore, job_id: str) -> dict[str, Any] | None:
        job = original_get(self, job_id)
        if job is None:
            return None
        if has_request_context() and not _write_allowed(job, _resolve_request_identity()):
            return None
        return original_delete(self, job_id)

    def secure_snapshot(self: ConversionJobStore) -> dict[str, dict[str, Any]]:
        jobs = original_snapshot(self)
        if not has_request_context():
            return jobs
        identity = _resolve_request_identity()
        if request.endpoint == "user_library_import_local":
            return {job_id: job for job_id, job in jobs.items() if _claim_import_allowed(job, identity)}
        if identity.guest_error:
            return {}
        if identity.auth_error:
            if identity.legacy_local:
                return {job_id: job for job_id, job in jobs.items() if _job_is_legacy_unowned(job)}
            return {}
        if identity.authenticated:
            return {job_id: job for job_id, job in jobs.items() if _job_user_owner(job) == identity.user_id}
        if identity.guest_owner_id:
            return {
                job_id: job
                for job_id, job in jobs.items()
                if _job_guest_owner(job) == identity.guest_owner_id and not _job_is_shared_artifact_recovery(job)
            }
        if identity.legacy_local:
            return {job_id: job for job_id, job in jobs.items() if _job_is_legacy_unowned(job)}
        return {}

    def secure_persist(self: ConversionJobStore) -> dict[str, Any]:
        return _raw_persist(self)

    ConversionJobStore.create = secure_create
    ConversionJobStore.update = secure_update
    ConversionJobStore.get = secure_get
    ConversionJobStore.delete = secure_delete
    ConversionJobStore.snapshot = secure_snapshot
    ConversionJobStore.persist = secure_persist
    setattr(ConversionJobStore, _INSTALL_MARKER, True)

    original_full_dispatch_request = Flask.full_dispatch_request
    original_process_response = Flask.process_response

    def secure_full_dispatch_request(self: Flask):
        blocked = _collection_identity_guard()
        if blocked is not None:
            return blocked
        return original_full_dispatch_request(self)

    def secure_process_response(self: Flask, response: Response) -> Response:
        processed = original_process_response(self, response)
        return _sign_json_response_links(processed)

    Flask.full_dispatch_request = secure_full_dispatch_request
    Flask.process_response = secure_process_response
