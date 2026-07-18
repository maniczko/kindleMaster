from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Mapping, MutableMapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


GUEST_ID_HEADER = "X-KindleMaster-Guest-Id"
JOB_ACCESS_QUERY_PARAM = "access"
USER_OWNER_FIELD = "user_id"
GUEST_OWNER_FIELD = "guest_owner_id"
LEGACY_LOCAL_OWNER_ID = "legacy-local"
DEFAULT_JOB_ACCESS_TTL_SECONDS = 15 * 60

_GUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{19,127}$")
_LOCAL_HOSTNAMES = {
    "127.0.0.1",
    "::1",
    "localhost",
    "kindlemaster.localhost",
}
_EPHEMERAL_JOB_ACCESS_SECRET = secrets.token_bytes(32)


class JobOwnerResolutionError(ValueError):
    error_code = "job_owner_resolution_failed"


class MissingGuestIdentity(JobOwnerResolutionError):
    error_code = "guest_identity_required"


class InvalidGuestIdentity(JobOwnerResolutionError):
    error_code = "invalid_guest_identity"


class InvalidAuthenticatedIdentity(JobOwnerResolutionError):
    error_code = "invalid_authenticated_identity"


@dataclass(frozen=True)
class JobOwner:
    kind: str
    owner_id: str

    @property
    def authenticated(self) -> bool:
        return self.kind == "user"

    @property
    def guest(self) -> bool:
        return self.kind in {"guest", "legacy_local"}


def normalize_guest_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if not _GUEST_ID_PATTERN.fullmatch(normalized):
        raise InvalidGuestIdentity("Guest identity has an invalid format.")
    return normalized


def guest_owner_id(guest_id: str) -> str:
    normalized = normalize_guest_id(guest_id)
    if not normalized:
        raise MissingGuestIdentity("Guest identity is required.")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"guest:{digest}"


def request_hostname(request_host: object) -> str:
    host = str(request_host or "").strip().lower()
    if not host:
        return ""
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def is_local_request_host(request_host: object) -> bool:
    hostname = request_hostname(request_host)
    return hostname in _LOCAL_HOSTNAMES or hostname.endswith(".localhost")


def _environment_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def legacy_local_guest_allowed(request_host: object) -> bool:
    configured = _environment_flag("KINDLEMASTER_ALLOW_LEGACY_LOCAL_GUEST")
    if configured is not None:
        return configured
    return is_local_request_host(request_host)


def resolve_job_owner(
    *,
    authenticated: bool,
    user_id: object = "",
    guest_id: object = "",
    request_host: object = "",
) -> JobOwner:
    normalized_user_id = str(user_id or "").strip()
    if authenticated:
        if not normalized_user_id:
            raise InvalidAuthenticatedIdentity("Authenticated requests require a user identifier.")
        return JobOwner(kind="user", owner_id=normalized_user_id)

    normalized_guest_id = normalize_guest_id(guest_id)
    if normalized_guest_id:
        return JobOwner(kind="guest", owner_id=guest_owner_id(normalized_guest_id))

    if legacy_local_guest_allowed(request_host):
        return JobOwner(kind="legacy_local", owner_id=LEGACY_LOCAL_OWNER_ID)

    raise MissingGuestIdentity("Public anonymous requests require an opaque guest identity.")


def apply_job_owner(job: MutableMapping[str, object], owner: JobOwner) -> MutableMapping[str, object]:
    if owner.kind == "user":
        job[USER_OWNER_FIELD] = owner.owner_id
        job.pop(GUEST_OWNER_FIELD, None)
    elif owner.kind == "guest":
        job[GUEST_OWNER_FIELD] = owner.owner_id
        job.pop(USER_OWNER_FIELD, None)
    elif owner.kind == "legacy_local":
        job.pop(USER_OWNER_FIELD, None)
        job.pop(GUEST_OWNER_FIELD, None)
    else:
        raise ValueError(f"Unsupported job owner kind: {owner.kind}")
    return job


def job_owned_by(job: Mapping[str, object] | None, owner: JobOwner) -> bool:
    if not isinstance(job, Mapping):
        return False
    user_owner = str(job.get(USER_OWNER_FIELD) or "").strip()
    guest_owner = str(job.get(GUEST_OWNER_FIELD) or "").strip()

    if owner.kind == "user":
        return bool(user_owner) and user_owner == owner.owner_id
    if owner.kind == "guest":
        return bool(guest_owner) and guest_owner == owner.owner_id
    if owner.kind == "legacy_local":
        return not user_owner and not guest_owner
    return False


def owner_scope(owner: JobOwner) -> str:
    if owner.kind == "user":
        return "account"
    if owner.kind == "guest":
        return "guest"
    return "local"


def _job_access_secret() -> bytes:
    configured = os.environ.get("KINDLEMASTER_JOB_ACCESS_SECRET", "").strip()
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).digest()
    return _EPHEMERAL_JOB_ACCESS_SECRET


def _job_access_ttl_seconds(value: object = None) -> int:
    candidate = value if value is not None else os.environ.get("KINDLEMASTER_JOB_ACCESS_TTL_SECONDS")
    try:
        resolved = int(candidate) if candidate not in {None, ""} else DEFAULT_JOB_ACCESS_TTL_SECONDS
    except (TypeError, ValueError):
        resolved = DEFAULT_JOB_ACCESS_TTL_SECONDS
    return max(60, min(resolved, 24 * 60 * 60))


def _job_access_signature(job_id: str, expires_at: int) -> str:
    payload = f"kindlemaster-job-access-v1\n{job_id}\n{expires_at}".encode("utf-8")
    digest = hmac.new(_job_access_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_job_access_token(
    job_id: object,
    *,
    now: float | int | None = None,
    ttl_seconds: int | None = None,
) -> str:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise ValueError("Job identifier is required for a signed access token.")
    current_time = int(time.time() if now is None else now)
    expires_at = current_time + _job_access_ttl_seconds(ttl_seconds)
    return f"{expires_at}.{_job_access_signature(normalized_job_id, expires_at)}"


def verify_job_access_token(
    job_id: object,
    token: object,
    *,
    now: float | int | None = None,
) -> bool:
    normalized_job_id = str(job_id or "").strip()
    normalized_token = str(token or "").strip()
    if not normalized_job_id or not normalized_token or "." not in normalized_token:
        return False
    raw_expiry, supplied_signature = normalized_token.split(".", 1)
    try:
        expires_at = int(raw_expiry)
    except ValueError:
        return False
    current_time = int(time.time() if now is None else now)
    if expires_at < current_time:
        return False
    expected_signature = _job_access_signature(normalized_job_id, expires_at)
    return hmac.compare_digest(supplied_signature, expected_signature)


def append_job_access_token(url: object, token: object) -> str:
    normalized_url = str(url or "").strip()
    normalized_token = str(token or "").strip()
    if not normalized_url or not normalized_token:
        return normalized_url
    parsed = urlsplit(normalized_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != JOB_ACCESS_QUERY_PARAM]
    query.append((JOB_ACCESS_QUERY_PARAM, normalized_token))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def extract_job_access_token(url: object) -> str:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        return ""
    try:
        parsed = urlsplit(normalized_url)
    except ValueError:
        return ""
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == JOB_ACCESS_QUERY_PARAM:
            return value
    return ""
