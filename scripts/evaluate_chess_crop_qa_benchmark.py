from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_crop_qa_benchmark import evaluate_crop_qa_benchmark, write_crop_qa_diff_reports


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate chess crop QA benchmark against runtime rows.")
    parser.add_argument("--labels", required=True, help="Path to qa_crop_validation_rows.jsonl")
    parser.add_argument("--actual", help="Optional runtime JSON/JSONL rows to compare by diagram_id")
    parser.add_argument("--manifest", help="Optional benchmark manifest JSON")
    parser.add_argument("--out", required=True, help="Output JSON report path")
    args = parser.parse_args()

    labels = Path(args.labels)
    manifest = Path(args.manifest) if args.manifest else labels.with_name("qa_crop_validation_manifest.json")
    report = evaluate_crop_qa_benchmark(
        labels,
        actual_path=Path(args.actual) if args.actual else None,
        manifest_path=manifest if manifest.is_file() else None,
    )
    json_path, md_path = write_crop_qa_diff_reports(report, args.out)
    print(json.dumps({"status": report["status"], "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False, indent=2))
    return 1 if report["summary"]["regression_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
