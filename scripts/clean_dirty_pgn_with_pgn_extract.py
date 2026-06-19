from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from external_pgn_extract_provider import run_pgn_extract


def clean_dirty_pgn_with_pgn_extract(input_path: Path, output_path: Path, report_json: Path) -> dict[str, object]:
    pgn_text = input_path.read_text(encoding="utf-8", errors="replace")
    result = run_pgn_extract(pgn_text)
    cleaned = bool(result.available and result.returncode == 0 and result.stdout_pgn.strip())
    if cleaned:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.stdout_pgn.strip() + "\n", encoding="utf-8")
    report = {
        "status": "cleaned" if cleaned else "not_cleaned",
        "input_name": input_path.name,
        "output_name": output_path.name,
        "output_written": cleaned,
        "returncode": result.returncode,
        "warnings": list(result.warnings),
        "stdout_bytes": len(result.stdout_pgn.encode("utf-8", errors="ignore")),
        "stderr": result.stderr,
        "runtime_ms": result.runtime_ms,
        "tool_version": result.tool_version,
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean a dirty PGN file with optional pgn-extract CLI.")
    parser.add_argument("input_pgn", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    args = parser.parse_args(argv)

    report = clean_dirty_pgn_with_pgn_extract(args.input_pgn, args.output, args.report_json)
    return 0 if report.get("status") == "cleaned" else 2


if __name__ == "__main__":
    raise SystemExit(main())
