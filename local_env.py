from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_LOCAL_ENV_FILES = (".env.local", ".env")
APP_ENV_FILE_NAME = "secrets.env"


def resolve_app_env_file(environ: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environ is None else environ
    explicit_path = str(environment.get("KINDLEMASTER_ENV_FILE", "") or "").strip()
    if explicit_path:
        return Path(explicit_path)
    appdata = str(environment.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / "KindleMaster" / APP_ENV_FILE_NAME
    home = str(environment.get("USERPROFILE", "") or environment.get("HOME", "") or "").strip()
    if home:
        return Path(home) / ".kindlemaster" / APP_ENV_FILE_NAME
    try:
        return Path.home() / ".kindlemaster" / APP_ENV_FILE_NAME
    except RuntimeError:
        return Path.cwd() / ".kindlemaster" / APP_ENV_FILE_NAME


def load_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    resolved_path = Path(path)
    if not resolved_path.is_file():
        return values
    try:
        lines = resolved_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def resolve_runtime_environment(
    environ: Mapping[str, str] | None = None,
    *,
    env_files: Sequence[str] = DEFAULT_LOCAL_ENV_FILES,
    cwd: str | Path | None = None,
) -> dict[str, str]:
    resolved = {str(key): str(value) for key, value in environ.items()} if environ is not None else dict(os.environ)
    root = Path(cwd or Path.cwd())
    configured_files = [] if environ is not None else list(env_files)
    configured_files.append(str(resolve_app_env_file(resolved)))
    for file_name in configured_files:
        env_path = Path(file_name)
        if not env_path.is_absolute():
            env_path = root / env_path
        for key, value in load_env_file(env_path).items():
            resolved.setdefault(key, value)
    return resolved
