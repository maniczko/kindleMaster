from __future__ import annotations

import argparse
import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse


DEFAULT_PRODUCTION_HOSTS = frozenset(
    {
        "kindlemaster.vercel.app",
        "kindlemaster-production.up.railway.app",
        "kindlemaster-api.up.railway.app",
    }
)
STAGING_HOST_MARKERS = frozenset({"stage", "staging", "test", "testing", "qa", "preview", "sandbox"})


class UnsafeAcceptanceTarget(ValueError):
    """Raised when hosted acceptance is pointed at an unsafe or ambiguous target."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AcceptanceTarget:
    base_url: str
    scheme: str
    host: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "base_url": self.base_url,
            "scheme": self.scheme,
            "host": self.host,
            "source": self.source,
        }


def _host_set(value: str | None) -> set[str]:
    return {
        item.strip().lower().rstrip(".")
        for item in str(value or "").split(",")
        if item.strip()
    }


def _is_loopback(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _has_staging_marker(host: str) -> bool:
    labels = host.replace("_", "-").replace(".", "-").split("-")
    return any(label in STAGING_HOST_MARKERS for label in labels)


def validate_staging_target(
    base_url: str,
    *,
    env: Mapping[str, str] | None = None,
) -> AcceptanceTarget:
    """Validate that a destructive/bounded acceptance run targets staging, never production.

    Remote targets must use HTTPS and must either have an explicit staging marker in
    the hostname or appear in KINDLEMASTER_STAGING_ALLOWED_HOSTS. Known production
    hosts, hosts containing a `production` label, and credential-bearing URLs fail
    closed. Local loopback targets remain available for developer verification.
    """

    source = os.environ if env is None else env
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        raise UnsafeAcceptanceTarget("missing_target", "Staging acceptance target is required.")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeAcceptanceTarget(
            "invalid_target_scheme",
            "Staging acceptance target must use http:// or https://.",
        )
    if parsed.username or parsed.password:
        raise UnsafeAcceptanceTarget(
            "credentialed_target_url",
            "Credentials must not be embedded in the staging target URL.",
        )

    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeAcceptanceTarget("missing_target_host", "Staging acceptance target has no hostname.")

    loopback = _is_loopback(host)
    if not loopback and parsed.scheme != "https":
        raise UnsafeAcceptanceTarget(
            "insecure_remote_target",
            "Remote staging acceptance targets must use HTTPS.",
        )

    production_hosts = set(DEFAULT_PRODUCTION_HOSTS)
    production_hosts.update(_host_set(source.get("KINDLEMASTER_PRODUCTION_HOSTS")))
    if host in production_hosts or any(host.endswith(f".{item}") for item in production_hosts):
        raise UnsafeAcceptanceTarget(
            "production_target_blocked",
            f"Refusing to run staging acceptance against production host: {host}",
        )

    host_labels = set(host.replace("_", "-").replace(".", "-").split("-"))
    if "production" in host_labels or "prod" in host_labels:
        raise UnsafeAcceptanceTarget(
            "production_named_target_blocked",
            f"Refusing an acceptance target identified as production: {host}",
        )

    if loopback:
        return AcceptanceTarget(normalized, parsed.scheme, host, "loopback")

    allowed_hosts = _host_set(source.get("KINDLEMASTER_STAGING_ALLOWED_HOSTS"))
    if host in allowed_hosts:
        return AcceptanceTarget(normalized, parsed.scheme, host, "explicit_allowlist")

    if _has_staging_marker(host):
        return AcceptanceTarget(normalized, parsed.scheme, host, "hostname_marker")

    raise UnsafeAcceptanceTarget(
        "ambiguous_staging_target",
        (
            f"Host {host} is not clearly a staging target. Use a staging/test/qa/preview hostname "
            "or add the exact host to KINDLEMASTER_STAGING_ALLOWED_HOSTS."
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a fail-closed KindleMaster staging target.")
    parser.add_argument(
        "base_url",
        nargs="?",
        default=os.environ.get("KINDLEMASTER_STAGING_BASE_URL", ""),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        target = validate_staging_target(args.base_url)
    except UnsafeAcceptanceTarget as error:
        print(
            json.dumps(
                {"status": "blocked", "error_code": error.code, "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps({"status": "accepted", "target": target.to_dict()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
