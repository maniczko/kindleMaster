from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deepseek_quality_provider import build_deepseek_audit_provider_from_env, deepseek_audit_configuration_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check optional DeepSeek audit provider configuration.")
    parser.add_argument("--live", action="store_true", help="Perform one small live DeepSeek audit call.")
    args = parser.parse_args(argv)

    status = deepseek_audit_configuration_status(cwd=PROJECT_ROOT)
    payload = {
        "status": "configured" if status["enabled"] and status["api_key_present"] else "disabled_or_missing_key",
        "configuration": status,
        "live_checked": False,
        "live_status": "not_run",
    }
    if args.live:
        provider = build_deepseek_audit_provider_from_env(cwd=PROJECT_ROOT)
        payload["live_checked"] = True
        if provider is None:
            payload["live_status"] = "provider_unavailable"
        else:
            try:
                result = provider.review_conversion_quality(
                    {
                        "source": "configuration-smoke",
                        "sample": "Return a compact audit-only JSON response.",
                    }
                )
                payload["live_status"] = "passed" if result.get("evidence_only") else "failed"
                payload["provider"] = result.get("provider")
                payload["model"] = result.get("model")
            except Exception as exc:
                payload["live_status"] = "failed"
                payload["error_class"] = exc.__class__.__name__

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload["status"] != "configured":
        return 1
    if args.live and payload["live_status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
