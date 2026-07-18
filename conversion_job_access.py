from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Mapping, MutableMapping


GUEST_ID_HEADER = "X-KindleMaster-Guest-Id"
USER_OWNER_FIELD = "user_id"
GUEST_OWNER_FIELD = "guest_owner_id"
LEGACY_LOCAL_OWNER_ID = "legacy-local"

_GUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{19,127}$")
_LOCAL_HOSTNAMES = {
    "127.0.0.1",
    "::1",
    "localhost",
    "kindlemaster.localhost",
}


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
