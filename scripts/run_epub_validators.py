from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from epub_validation import build_validation_markdown, validate_epub_path


def run_epub_validators(
    epub_paths: list[str | Path],
    *,
    reports_dir: str | Path = "reports/validators",
) -> dict[str, Any]:
    resolved_reports_dir = Path(reports_dir)
    resolved_reports_dir.mkdir(parents=True, exist_ok=True)
    results = [validate_epub_path(path) for path in epub_paths]
    overall_status = "passed"
    for result in results:
        status = result.get("summary", {}).get("status", "failed")
        if status == "failed":
            overall_status = "failed"
            break
        if status == "passed_with_warnings" and overall_status == "passed":
            overall_status = "passed_with_warnings"

    payload = {
        "overall_status": overall_status,
        "files_validated": len(results),
        "results": results,
    }
    (resolved_reports_dir / "epub_validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_chunks = []
    for result in results:
        markdown_chunks.append(build_validation_markdown(result).rstrip())
    (resolved_reports_dir / "epub_validation.md").write_text(
        "\n\n".join(markdown_chunks).rstrip() + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster EPUB validators on one or more EPUB files.")
    parser.add_argument("epub_paths", nargs="+", help="EPUB file paths")
    parser.add_argument("--reports-dir", default="reports/validators")
    args = parser.parse_args()

    result = run_epub_validators(args.epub_paths, reports_dir=args.reports_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["overall_status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
