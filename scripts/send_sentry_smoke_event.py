from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentry_observability import configure_sentry_backend


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one controlled KindleMaster Sentry smoke event.")
    parser.add_argument("--send", action="store_true", help="Actually send the smoke event to Sentry.")
    parser.add_argument(
        "--output-json",
        default="reports/sentry/smoke_event_latest.json",
        help="Path for the local machine-readable evidence artifact.",
    )
    args = parser.parse_args(argv)

    payload: dict[str, Any] = {
        "generated_at": _now_iso(),
        "command": "python scripts/send_sentry_smoke_event.py --send"
        if args.send
        else "python scripts/send_sentry_smoke_event.py",
        "sent": False,
        "status": "not_sent",
        "event_id": "",
        "notes": [],
    }

    output_json = Path(args.output_json)
    if not args.send and args.output_json == "reports/sentry/smoke_event_latest.json":
        output_json = Path("reports/sentry/smoke_event_dry_run_latest.json")

    if not args.send:
        payload["status"] = "skipped"
        payload["notes"].append("Use --send to emit the controlled Sentry smoke event.")
        _write_json(output_json, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    try:
        import sentry_sdk
    except ModuleNotFoundError:
        payload["status"] = "failed"
        payload["reason"] = "sentry_sdk_missing"
        payload["notes"].append("Install runtime dependencies with python -m pip install -r requirements.txt.")
        _write_json(output_json, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    state = configure_sentry_backend(sentry_sdk=sentry_sdk)
    payload["sentry"] = {
        "enabled": bool(state.get("enabled")),
        "environment": state.get("environment", ""),
        "release": state.get("release", ""),
        "reason": state.get("reason", ""),
    }
    if not state.get("enabled"):
        payload["status"] = "failed"
        payload["reason"] = state.get("reason", "unknown")
        _write_json(output_json, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    context = {
        "purpose": "controlled-observability-smoke",
        "component": "kindlemaster",
        "conversion_path": "none",
        "user_visible_impact": "none",
    }
    sentry_sdk.set_tag("component", "observability")
    sentry_sdk.set_tag("smoke", "controlled")
    sentry_sdk.set_tag("kindlemaster.smoke", "sentry")
    sentry_sdk.set_context("kindlemaster_smoke", context)

    event_id = sentry_sdk.capture_message("KindleMaster controlled Sentry smoke event", level="info")
    sentry_sdk.flush(timeout=5)

    payload.update(
        {
            "sent": bool(event_id),
            "status": "sent" if event_id else "failed",
            "event_id": str(event_id or ""),
            "context": context,
        }
    )
    if not event_id:
        payload["notes"].append("Sentry SDK did not return an event id.")
    _write_json(output_json, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if event_id else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
