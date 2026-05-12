from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai_quality_provider import build_openai_quality_provider_from_env, openai_quality_configuration_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check optional OpenAI quality provider configuration.")
    parser.add_argument("--live", action="store_true", help="Perform one small live OpenAI OCR cleanup call.")
    args = parser.parse_args(argv)

    status = openai_quality_configuration_status(cwd=PROJECT_ROOT)
    payload = {
        "status": "configured" if status["enabled"] and status["api_key_present"] else "disabled_or_missing_key",
        "configuration": status,
        "live_checked": False,
        "live_status": "not_run",
    }
    if args.live:
        provider = build_openai_quality_provider_from_env(cwd=PROJECT_ROOT)
        payload["live_checked"] = True
        if provider is None:
            payload["live_status"] = "provider_unavailable"
        else:
            try:
                result = provider.cleanup_fragment("Broken fragment has Busi- nessAnalysisPlanning.")
                payload["live_status"] = "passed" if result.text else "failed"
                payload["confidence"] = result.confidence
                payload["changed"] = result.text != "Broken fragment has Busi- nessAnalysisPlanning."
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
