from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from converter import ConversionConfig, convert_document_to_epub_with_report
from epub_quality_recovery import run_epub_publishing_quality_recovery
from epub_validation import validate_epub_bytes, validate_epub_path


def run_smoke_tests(
    *,
    manifest_path: str | Path = "reference_inputs/manifest.json",
    mode: str = "quick",
    output_dir: str | Path = "output/smoke",
    reports_dir: str | Path = "reports/smoke",
    case_filters: list[str] | None = None,
) -> dict[str, Any]:
    resolved_manifest = Path(manifest_path).resolve()
    if not resolved_manifest.exists():
        raise FileNotFoundError(f"Reference input manifest not found: {resolved_manifest}")

    resolved_output_dir = Path(output_dir).resolve()
    resolved_reports_dir = Path(reports_dir).resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_reports_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    filters = [token.lower() for token in (case_filters or []) if token.strip()]
    rows: list[dict[str, Any]] = []

    for case in manifest.get("cases", []):
        if mode == "quick" and not case.get("quick_smoke", False):
            continue
        if filters and not _case_matches(case, filters):
            continue
        path = Path(case["target_path"]).resolve()
        row = {
            "id": case["id"],
            "document_class": case["document_class"],
            "input_type": case["input_type"],
            "path": str(path),
        }
        if case["input_type"] in {"pdf", "docx"}:
            result = convert_document_to_epub_with_report(
                str(path),
                config=ConversionConfig(profile="auto-premium", language=case.get("language", "pl")),
                original_filename=path.name,
                source_type=case["input_type"],
            )
            epub_path = resolved_output_dir / f"{case['id']}.epub"
            epub_path.write_bytes(result["epub_bytes"])
            validation = validate_epub_bytes(result["epub_bytes"], label=str(epub_path))
            row.update(
                {
                    "analysis": _json_safe(result.get("analysis", {})),
                    "quality_report": _json_safe(result.get("quality_report", {})),
                    "validation": validation,
                    "output_epub": str(epub_path),
                }
            )
        else:
            validation = validate_epub_path(path)
            row["validation"] = validation
            if mode == "full":
                release_dir = resolved_output_dir / case["id"]
                audit_result = run_epub_publishing_quality_recovery(
                    path,
                    output_dir=release_dir,
                    reports_dir=resolved_reports_dir / case["id"],
                    expected_language=case.get("language", ""),
                )
                row["release_audit"] = audit_result
        rows.append(row)

    summary = _build_smoke_summary(rows)
    payload = {
        "mode": mode,
        "manifest": str(resolved_manifest),
        "summary": summary,
        "cases": rows,
    }
    (resolved_reports_dir / f"smoke_{mode}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (resolved_reports_dir / f"smoke_{mode}.md").write_text(
        _build_smoke_markdown(payload),
        encoding="utf-8",
    )
    return payload


def _case_matches(case: dict[str, Any], filters: list[str]) -> bool:
    haystacks = [
        str(case.get("id", "")).lower(),
        str(case.get("document_class", "")).lower(),
        str(case.get("notes", "")).lower(),
        str(Path(case.get("target_path", "")).name).lower(),
    ]
    return any(token in haystack for token in filters for haystack in haystacks)


def _build_smoke_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    failures = 0
    warnings = 0
    for row in rows:
        status = ((row.get("validation") or {}).get("summary") or {}).get("status", "failed")
        if status == "failed":
            failures += 1
        elif status == "passed_with_warnings":
            warnings += 1
    overall = "failed" if failures else ("passed_with_warnings" if warnings else "passed")
    return {
        "cases_run": len(rows),
        "failed_cases": failures,
        "warning_cases": warnings,
        "overall_status": overall,
    }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _build_smoke_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# KindleMaster Smoke Report",
        "",
        f"- Mode: `{payload.get('mode', 'unknown')}`",
        f"- Cases run: `{payload.get('summary', {}).get('cases_run', 0)}`",
        f"- Overall status: `{payload.get('summary', {}).get('overall_status', 'unknown')}`",
        "",
    ]
    for row in payload.get("cases", []):
        validation = row.get("validation", {})
        lines.extend(
            [
                f"## {row.get('id', 'unknown')}",
                "",
                f"- Class: `{row.get('document_class', '')}`",
                f"- Input type: `{row.get('input_type', '')}`",
                f"- Validation: `{(validation.get('summary') or {}).get('status', 'unknown')}`",
            ]
        )
        if row.get("release_audit"):
            lines.append(f"- Release audit: `{row['release_audit'].get('decision', 'unknown')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster smoke tests on curated reference inputs.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--mode", choices=("quick", "full"), default="quick")
    parser.add_argument("--output-dir", default="output/smoke")
    parser.add_argument("--reports-dir", default="reports/smoke")
    parser.add_argument("--case", action="append", default=[], help="Optional filter by case id, class, or filename.")
    args = parser.parse_args()

    payload = run_smoke_tests(
        manifest_path=args.manifest,
        mode=args.mode,
        output_dir=args.output_dir,
        reports_dir=args.reports_dir,
        case_filters=args.case,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("summary", {}).get("overall_status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
