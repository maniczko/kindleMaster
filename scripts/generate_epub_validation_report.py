from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from epub_validation import build_validation_markdown, validate_epub_path


DEFAULT_OUTPUT_JSON = Path("reports/epub_validation.json")
DEFAULT_OUTPUT_MD = Path("reports/epub_validation.md")


def generate_epub_validation_report(
    epub_path: str | Path | None = None,
    *,
    output_json: str | Path = DEFAULT_OUTPUT_JSON,
    output_md: str | Path = DEFAULT_OUTPUT_MD,
) -> dict[str, Any]:
    if not epub_path:
        payload = _missing_epub_payload("No EPUB path was supplied for release validation.")
    else:
        path = Path(epub_path)
        if not path.is_file():
            payload = _missing_epub_payload(f"EPUB path does not exist: {path}", epub_path=str(path))
        else:
            payload = validate_epub_path(path)
            payload["status"] = str(payload.get("summary", {}).get("status") or "unknown")
            payload["release_ready"] = payload["status"] == "passed"

    json_path = Path(output_json)
    md_path = Path(output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown_for_payload(payload), encoding="utf-8")
    return payload


def _missing_epub_payload(message: str, *, epub_path: str = "") -> dict[str, Any]:
    return {
        "schema_version": "kindlemaster.epub_validation_report.v1",
        "status": "failed",
        "release_ready": False,
        "epub_path": epub_path,
        "summary": {
            "status": "failed",
            "error_count": 1,
            "warning_count": 0,
            "epubcheck_status": "not_run",
        },
        "errors": [{"code": "epub_path_missing", "message": message}],
    }


def _markdown_for_payload(payload: dict[str, Any]) -> str:
    if "package" in payload or "internal_links" in payload:
        return build_validation_markdown(payload)
    return (
        f"# EPUB Validation Report: {payload.get('epub_path') or '<missing>'}\n\n"
        f"- Overall status: `{payload.get('status', 'unknown')}`\n"
        f"- Error count: `{payload.get('summary', {}).get('error_count', 0)}`\n"
        f"- Warning count: `{payload.get('summary', {}).get('warning_count', 0)}`\n\n"
        "## Errors\n\n"
        + "\n".join(f"- {error.get('code')}: {error.get('message')}" for error in payload.get("errors", []))
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Write standard EPUB validation evidence for final chess readiness.")
    parser.add_argument("--epub", default="")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()
    payload = generate_epub_validation_report(
        args.epub or None,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if str(payload.get("status") or "").lower() == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
