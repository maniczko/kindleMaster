from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from local_env import resolve_runtime_environment

DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_SECURITY = "starttls"
DEFAULT_EMAIL_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
VALID_SMTP_SECURITY = {"starttls", "ssl", "none"}
VALID_DEFAULT_PROFILES = {"auto-premium", "book", "magazine", "technical-study", "preserve-layout"}
VALID_DEFAULT_LANGUAGES = {"pl", "en"}
EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def default_user_profile() -> dict[str, Any]:
    return {
        "conversion": {
            "default_profile": "auto-premium",
            "default_language": "pl",
            "force_ocr": False,
            "heading_repair": True,
        },
        "email_delivery": {
            "enabled": False,
            "host": "",
            "port": DEFAULT_SMTP_PORT,
            "security": DEFAULT_SMTP_SECURITY,
            "username": "",
            "from_address": "",
            "default_recipient": "",
            "max_attachment_bytes": DEFAULT_EMAIL_MAX_ATTACHMENT_BYTES,
        },
    }


def resolve_user_profile_path(environ: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environ is None else environ
    explicit_path = str(environment.get("KINDLEMASTER_USER_PROFILE_PATH", "") or "").strip()
    if explicit_path:
        return Path(explicit_path)
    appdata = str(environment.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / "KindleMaster" / "profile.json"
    return Path.home() / ".kindlemaster" / "profile.json"


def load_user_profile(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = resolve_user_profile_path(environ)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    return sanitize_user_profile(raw)


def save_user_profile(payload: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    profile = sanitize_user_profile(payload)
    path = resolve_user_profile_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return profile


def public_user_profile(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    environment = resolve_runtime_environment(environ)
    profile = load_user_profile(environment)
    email_delivery = dict(profile.get("email_delivery", {}) or {})
    email_delivery["secret_configured"] = bool(str(environment.get("KINDLEMASTER_SMTP_PASSWORD", "") or ""))
    profile["email_delivery"] = email_delivery
    return profile


def sanitize_user_profile(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, Mapping) else {}
    defaults = default_user_profile()
    conversion_source = source.get("conversion", {}) if isinstance(source.get("conversion"), Mapping) else {}
    email_source = source.get("email_delivery", {}) if isinstance(source.get("email_delivery"), Mapping) else {}

    conversion = {
        "default_profile": _choice(
            conversion_source.get("default_profile"),
            valid=VALID_DEFAULT_PROFILES,
            default=defaults["conversion"]["default_profile"],
        ),
        "default_language": _choice(
            conversion_source.get("default_language"),
            valid=VALID_DEFAULT_LANGUAGES,
            default=defaults["conversion"]["default_language"],
        ),
        "force_ocr": _bool(conversion_source.get("force_ocr"), defaults["conversion"]["force_ocr"]),
        "heading_repair": _bool(conversion_source.get("heading_repair"), defaults["conversion"]["heading_repair"]),
    }
    email_delivery = {
        "enabled": _bool(email_source.get("enabled"), defaults["email_delivery"]["enabled"]),
        "host": _text(email_source.get("host")),
        "port": _positive_int(email_source.get("port"), defaults["email_delivery"]["port"]),
        "security": _choice(
            email_source.get("security"),
            valid=VALID_SMTP_SECURITY,
            default=defaults["email_delivery"]["security"],
        ),
        "username": _text(email_source.get("username")),
        "from_address": _text(email_source.get("from_address")),
        "default_recipient": _email_or_empty(email_source.get("default_recipient")),
        "secret_registered": _bool(email_source.get("secret_registered"), False),
        "max_attachment_bytes": _positive_int(
            email_source.get("max_attachment_bytes"),
            defaults["email_delivery"]["max_attachment_bytes"],
        ),
    }
    return {"conversion": conversion, "email_delivery": email_delivery}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _email_or_empty(value: Any) -> str:
    text = _text(value)
    return text if EMAIL_RE.match(text) else ""


def _choice(value: Any, *, valid: set[str], default: str) -> str:
    text = _text(value).lower()
    return text if text in valid else default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _positive_int(value: Any, default: int) -> int:
    try:
        converted = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return converted if converted > 0 else default
