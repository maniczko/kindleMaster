from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from golden_epub_regression import run_golden_epub_regression


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster golden EPUB feature regression checks.")
    parser.add_argument("--manifest", default="reference_inputs/golden_epub_expectations.json")
    parser.add_argument("--artifact-root", default="output/corpus/smoke")
    parser.add_argument("--reports-dir", default="reports/golden_epub_regression")
    args = parser.parse_args()

    payload = run_golden_epub_regression(
        manifest_path=args.manifest,
        artifact_root=args.artifact_root,
        reports_dir=args.reports_dir,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
