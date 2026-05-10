from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SENTRY_ENV_FILES = (".env.local", ".env")


def _first_env(env: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = str(env.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _load_sentry_sdk() -> Any | None:
    try:
        return importlib.import_module("sentry_sdk")
    except ModuleNotFoundError:
        return None


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return values

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def _runtime_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)

    resolved = dict(os.environ)
    for file_name in DEFAULT_SENTRY_ENV_FILES:
        for key, value in _load_env_file(Path(file_name)).items():
            resolved.setdefault(key, value)
    return resolved


def _sentry_release(env: Mapping[str, str]) -> str:
    return _first_env(env, "SENTRY_RELEASE", "KINDLEMASTER_RELEASE", "APP_RELEASE", "RELEASE")


def _sentry_environment(env: Mapping[str, str]) -> str:
    return _first_env(env, "SENTRY_ENVIRONMENT", "APP_ENV", "FLASK_ENV", "ENVIRONMENT")


def configure_sentry_backend(
    *,
    env: Mapping[str, str] | None = None,
    sentry_sdk: Any | None = None,
) -> dict[str, Any]:
    environment = _runtime_environment(env)
    dsn = _first_env(environment, "SENTRY_DSN")
    if not dsn:
        return {"enabled": False, "reason": "missing_dsn"}

    sdk = sentry_sdk if sentry_sdk is not None else _load_sentry_sdk()
    if sdk is None:
        return {"enabled": False, "reason": "sentry_sdk_missing"}

    release = _sentry_release(environment) or None
    runtime_environment = _sentry_environment(environment) or None
    init_kwargs: dict[str, Any] = {
        "dsn": dsn,
        "release": release,
        "environment": runtime_environment,
    }
    traces_sample_rate = _coerce_optional_float(environment.get("SENTRY_TRACES_SAMPLE_RATE"))
    if traces_sample_rate is not None:
        init_kwargs["traces_sample_rate"] = traces_sample_rate

    sdk.init(**init_kwargs)
    _apply_scope_tags(
        sdk,
        {
            "release": release,
            "environment": runtime_environment,
        },
    )
    return {
        "enabled": True,
        "release": release or "",
        "environment": runtime_environment or "",
    }


def build_conversion_context(
    *,
    job_id: str = "",
    input_type: str = "",
    source_type: str = "",
    profile: str = "",
    quality_score: int | float | None = None,
    premium_ready: bool | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key, value in {
        "job_id": job_id,
        "input_type": input_type,
        "source_type": source_type,
        "profile": profile,
    }.items():
        text = str(value or "").strip()
        if text:
            context[key] = text
    if quality_score is not None:
        context["quality_score"] = quality_score
    if premium_ready is not None:
        context["premium_ready"] = bool(premium_ready)
    return context


def capture_conversion_exception(
    error: BaseException,
    *,
    context: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    sentry_sdk: Any | None = None,
) -> str:
    environment = _runtime_environment(env)
    if not _first_env(environment, "SENTRY_DSN"):
        return ""

    sdk = sentry_sdk if sentry_sdk is not None else _load_sentry_sdk()
    if sdk is None:
        return ""

    conversion_context = dict(context or {})
    _apply_scope_tags(
        sdk,
        {
            "release": _sentry_release(environment),
            "environment": _sentry_environment(environment),
            "job_id": conversion_context.get("job_id"),
            "input_type": conversion_context.get("input_type"),
            "source_type": conversion_context.get("source_type"),
            "profile": conversion_context.get("profile"),
            "premium_ready": conversion_context.get("premium_ready"),
        },
        context=conversion_context,
    )
    try:
        event_id = sdk.capture_exception(error)
    except Exception:
        return ""
    return str(event_id or "")


def _coerce_optional_float(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if converted < 0:
        return 0.0
    if converted > 1:
        return 1.0
    return converted


def _apply_scope_tags(
    sentry_sdk: Any,
    tags: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    try:
        scope_manager = sentry_sdk.configure_scope()
    except Exception:
        return
    try:
        with scope_manager as scope:
            for key, value in tags.items():
                if value in (None, ""):
                    continue
                scope.set_tag(key, value)
            if context:
                scope.set_context("conversion", dict(context))
    except Exception:
        return
