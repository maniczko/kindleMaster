from __future__ import annotations

import re
from pathlib import Path


VERSION_RE = re.compile(r"^(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.\d+)?$")


def normalize_display_version(raw_value: str) -> str:
    value = (raw_value or "").strip()
    match = VERSION_RE.match(value)
    if not match:
        return "0.0"
    major = match.group("major") or "0"
    minor = match.group("minor") or "0"
    return f"{major}.{minor}"


def read_display_version(version_path: Path) -> str:
    try:
        return normalize_display_version(version_path.read_text(encoding="utf-8"))
    except Exception:
        return "0.0"


def version_label(version: str) -> str:
    return f"v{normalize_display_version(version)}"


def build_identity(version: str, commit: str | None = None) -> str:
    label = version_label(version)
    clean_commit = (commit or "").strip()
    return f"{label}+{clean_commit}" if clean_commit else label
