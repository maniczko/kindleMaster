from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chess_auto_flow import validate_auto_chess_output


DEFAULT_OUTPUT = Path("reports/auto_strict_validation.json")


def generate_auto_strict_validation_report(
    *,
    out_dir: str | Path | None = None,
    output_path: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if out_dir:
        payload = validate_auto_chess_output(out_dir, strict=True)
        payload = {
            **payload,
            "source_out_dir": str(out_dir),
            "release_ready": _validation_passed(payload),
        }
    else:
        payload = {
            "schema": "kindlemaster.auto_chess_validation.v1",
            "overall_status": "failed",
            "strict": True,
            "release_ready": False,
            "source_out_dir": "",
            "summary": {},
            "errors": [
                {
                    "code": "auto_output_dir_missing",
                    "message": "No auto chess output directory was supplied for strict validation.",
                }
            ],
            "warnings": [],
        }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _validation_passed(payload: dict[str, Any]) -> bool:
    return str(payload.get("overall_status") or payload.get("status") or "").strip().lower() in {
        "ok",
        "pass",
        "passed",
        "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write standard auto-strict validation evidence for final chess readiness.")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    payload = generate_auto_strict_validation_report(
        out_dir=args.out_dir or None,
        output_path=args.output,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if _validation_passed(payload) else 1


if __name__ == "__main__":
    raise SystemExit(main())
