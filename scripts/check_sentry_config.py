from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentry_observability import configure_sentry_backend


@dataclass
class _CheckScope:
    tags: dict[str, Any] = field(default_factory=dict)
    contexts: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> "_CheckScope":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    def set_context(self, key: str, value: Any) -> None:
        self.contexts[key] = value


class _CheckSentrySdk:
    def __init__(self) -> None:
        self.init_kwargs: dict[str, Any] = {}
        self.scope = _CheckScope()

    def init(self, **kwargs: Any) -> None:
        self.init_kwargs = dict(kwargs)

    def configure_scope(self) -> _CheckScope:
        return self.scope


def _redact_dsn(dsn: str) -> str:
    if "@" not in dsn:
        return "<set>"
    return "https://<redacted>@" + dsn.split("@", 1)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate KindleMaster Sentry configuration without sending events.")
    parser.add_argument("--json", action="store_true", help="Reserved for future machine-readable output.")
    args = parser.parse_args(argv)
    _ = args

    sdk = _CheckSentrySdk()
    state = configure_sentry_backend(sentry_sdk=sdk)
    if not state.get("enabled"):
        reason = state.get("reason", "unknown")
        print(f"Sentry disabled: {reason}")
        if reason == "missing_dsn":
            print("Set SENTRY_DSN in your environment or .env.local.")
        elif reason == "sentry_sdk_missing":
            print("Install dependencies: python -m pip install -r requirements.txt")
        return 1

    dsn = str(sdk.init_kwargs.get("dsn", ""))
    print("Sentry enabled: yes")
    print(f"dsn: {_redact_dsn(dsn)}")
    print(f"environment: {state.get('environment') or '<unset>'}")
    print(f"release: {state.get('release') or '<unset>'}")
    print(f"traces_sample_rate: {sdk.init_kwargs.get('traces_sample_rate', '<unset>')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
