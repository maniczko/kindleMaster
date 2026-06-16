from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from local_env import resolve_runtime_environment


HttpRequest = Callable[..., Any]
DEFAULT_SUPABASE_ENV_FILES = (".env.local", ".env")


@dataclass(frozen=True)
class SupabaseAuthConfig:
    enabled: bool
    configured: bool
    url: str = ""
    publishable_key: str = ""
    service_role_key: str = ""
    require_login: bool = False
    missing_config: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return "supabase"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "provider": self.provider,
            "supabase_url": self.url if self.configured else self.url,
            "publishable_key": self.publishable_key if self.configured else self.publishable_key,
            "require_login": self.require_login,
            "missing_config": list(self.missing_config),
        }


@dataclass(frozen=True)
class AuthContext:
    authenticated: bool = False
    user_id: str = ""
    email_masked: str = ""
    error: str = ""
    error_code: str = ""
    status_code: int = 200

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "user_id": self.user_id if self.authenticated else "",
            "email_masked": self.email_masked if self.authenticated else "",
            "error": self.error,
            "error_code": self.error_code,
        }


def load_supabase_auth_config(environ: Mapping[str, str] | None = None) -> SupabaseAuthConfig:
    env = resolve_runtime_environment(
        environ,
        env_files=DEFAULT_SUPABASE_ENV_FILES,
        cwd=Path(__file__).resolve().parent,
    )
    provider = str(env.get("KINDLEMASTER_AUTH_PROVIDER", "") or "").strip().lower()
    enabled = provider == "supabase"
    require_login = _truthy(env.get("KINDLEMASTER_REQUIRE_LOGIN"))
    url = _normalize_url(env.get("SUPABASE_URL", ""))
    publishable_key = str(env.get("SUPABASE_PUBLISHABLE_KEY", "") or "").strip()
    service_role_key = str(env.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    missing: list[str] = []
    if enabled and not url:
        missing.append("SUPABASE_URL")
    if enabled and not publishable_key:
        missing.append("SUPABASE_PUBLISHABLE_KEY")
    return SupabaseAuthConfig(
        enabled=enabled,
        configured=enabled and not missing,
        url=url,
        publishable_key=publishable_key,
        service_role_key=service_role_key,
        require_login=require_login,
        missing_config=tuple(missing),
    )


def public_auth_config(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    return load_supabase_auth_config(environ).to_public_dict()


def resolve_bearer_token(authorization_header: str | None) -> str:
    text = str(authorization_header or "").strip()
    if not text.lower().startswith("bearer "):
        return ""
    return text[7:].strip()


def validate_bearer_token(
    token: str,
    *,
    config: SupabaseAuthConfig | None = None,
    http_request: HttpRequest | None = None,
) -> AuthContext:
    token = str(token or "").strip()
    resolved_config = config or load_supabase_auth_config()
    if not token:
        return AuthContext()
    if not resolved_config.enabled:
        return AuthContext(
            error="Logowanie Supabase jest wylaczone.",
            error_code="auth_disabled",
            status_code=401,
        )
    if not resolved_config.configured:
        return AuthContext(
            error="Logowanie Supabase jest niekompletnie skonfigurowane.",
            error_code="auth_unconfigured",
            status_code=503,
        )

    requester = http_request or _default_json_request
    try:
        payload = requester(
            f"{resolved_config.url}/auth/v1/user",
            method="GET",
            headers={
                "apikey": resolved_config.publishable_key,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
    except Exception:
        return AuthContext(
            error="Sesja wygasla albo token logowania jest nieprawidlowy.",
            error_code="invalid_auth_token",
            status_code=401,
        )

    if not isinstance(payload, Mapping):
        return AuthContext(
            error="Supabase Auth zwrocil nieprawidlowy profil uzytkownika.",
            error_code="invalid_auth_response",
            status_code=401,
        )
    user_id = str(payload.get("id", "") or "").strip()
    if not user_id:
        return AuthContext(
            error="Supabase Auth nie zwrocil identyfikatora uzytkownika.",
            error_code="invalid_auth_response",
            status_code=401,
        )
    return AuthContext(
        authenticated=True,
        user_id=user_id,
        email_masked=mask_email(str(payload.get("email", "") or "")),
    )


def mask_email(value: str) -> str:
    email = str(value or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return ""
    local, domain = email.split("@", 1)
    if not local:
        return ""
    return f"{local[0]}***@{domain}"


def _default_json_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> Any:
    request = urllib.request.Request(url, data=body, method=method.upper())
    for key, value in dict(headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        try:
            error.read()
        except Exception:
            pass
        raise RuntimeError(f"supabase_http_{error.code}") from error
    if not data:
        return {}
    return json.loads(data.decode("utf-8"))


def _normalize_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
