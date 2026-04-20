from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from epub_quality_recovery import run_epub_publishing_quality_recovery


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster release audit on an EPUB.")
    parser.add_argument("epub_path", help="Input EPUB path")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--language", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--publication-profile", default="")
    args = parser.parse_args()

    result = run_epub_publishing_quality_recovery(
        Path(args.epub_path),
        output_dir=Path(args.output_dir),
        reports_dir=Path(args.reports_dir),
        expected_title=args.title,
        expected_author=args.author,
        expected_description=args.description,
        expected_language=args.language,
        publication_profile=args.publication_profile or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("decision") != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
