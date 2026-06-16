from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from supabase_auth import SupabaseAuthConfig, load_supabase_auth_config
from user_profile import sanitize_user_profile


HttpRequest = Callable[..., Any]


def load_cloud_user_profile(
    *,
    user_id: str,
    access_token: str,
    config: SupabaseAuthConfig | None = None,
    transport: HttpRequest | None = None,
) -> dict[str, Any] | None:
    client = SupabaseProfileClient(config=config, access_token=access_token, transport=transport)
    return client.load_profile(user_id=user_id)


def save_cloud_user_profile(
    *,
    user_id: str,
    access_token: str,
    profile: Mapping[str, Any],
    config: SupabaseAuthConfig | None = None,
    transport: HttpRequest | None = None,
) -> dict[str, Any]:
    client = SupabaseProfileClient(config=config, access_token=access_token, transport=transport)
    return client.save_profile(user_id=user_id, profile=profile)


class SupabaseProfileClient:
    def __init__(
        self,
        *,
        config: SupabaseAuthConfig | None = None,
        access_token: str = "",
        transport: HttpRequest | None = None,
    ) -> None:
        self.config = config or load_supabase_auth_config()
        self.access_token = str(access_token or "").strip()
        self._transport = transport or _default_json_request

    def load_profile(self, *, user_id: str) -> dict[str, Any] | None:
        self._ensure_available()
        clean_user_id = _required_user_id(user_id)
        query = urllib.parse.urlencode(
            {
                "user_id": f"eq.{clean_user_id}",
                "select": "conversion_defaults,smtp_defaults",
                "limit": "1",
            },
            safe="(),",
        )
        rows = self._request(f"/rest/v1/user_profiles?{query}", method="GET")
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0] if isinstance(rows[0], Mapping) else {}
        return _row_to_user_profile(row)

    def save_profile(self, *, user_id: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_available()
        payload = _profile_to_row_payload(user_id=_required_user_id(user_id), profile=profile)
        result = self._request(
            "/rest/v1/user_profiles",
            method="POST",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            payload=payload,
        )
        if isinstance(result, list) and result and isinstance(result[0], Mapping):
            return _row_to_user_profile(result[0])
        return sanitize_user_profile(profile)

    def _request(
        self,
        path: str,
        *,
        method: str,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        request_headers = {
            "apikey": self.config.publishable_key,
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        request_headers.update(dict(headers or {}))
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        return self._transport(
            f"{self.config.url}{path}",
            method=method,
            headers=request_headers,
            body=body,
        )

    def _ensure_available(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("supabase_profile_disabled")
        if not self.config.configured:
            raise RuntimeError("supabase_profile_unconfigured")
        if not self.access_token:
            raise RuntimeError("supabase_profile_missing_token")


def _profile_to_row_payload(*, user_id: str, profile: Mapping[str, Any]) -> dict[str, Any]:
    clean = sanitize_user_profile(profile)
    return {
        "user_id": user_id,
        "conversion_defaults": clean["conversion"],
        "smtp_defaults": clean["email_delivery"],
    }


def _row_to_user_profile(row: Mapping[str, Any]) -> dict[str, Any]:
    return sanitize_user_profile(
        {
            "conversion": row.get("conversion_defaults") if isinstance(row.get("conversion_defaults"), Mapping) else {},
            "email_delivery": row.get("smtp_defaults") if isinstance(row.get("smtp_defaults"), Mapping) else {},
        }
    )


def _required_user_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError("supabase_profile_missing_user_id")
    return text


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
        raise RuntimeError(f"supabase_profile_http_{error.code}") from error
    if not data:
        return {}
    return json.loads(data.decode("utf-8"))
