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
from size_budget_policy import evaluate_size_budget, get_document_size_budget, inspect_epub_archive, load_size_budget_policy


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
    policy = load_size_budget_policy()

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
        artifact_bytes: bytes | None = None
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
            artifact_bytes = result["epub_bytes"]
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
            artifact_bytes = path.read_bytes()
            if mode == "full":
                release_dir = resolved_output_dir / case["id"]
                audit_result = run_epub_publishing_quality_recovery(
                    path,
                    output_dir=release_dir,
                    reports_dir=resolved_reports_dir / case["id"],
                    expected_language=case.get("language", ""),
                )
                row["release_audit"] = audit_result
        if artifact_bytes is not None:
            row["epub_size_bytes"] = len(artifact_bytes)
            document_class = str(case.get("document_class", ""))
            row["size_gate"] = evaluate_size_budget(
                budget_key=document_class,
                budget=get_document_size_budget(document_class, policy=policy),
                epub_size_bytes=len(artifact_bytes),
                inspection=inspect_epub_archive(artifact_bytes),
                label="klasy dokumentu",
            )
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
    size_failures = 0
    size_warnings = 0
    for row in rows:
        validation_status = ((row.get("validation") or {}).get("summary") or {}).get("status", "failed")
        size_status = (row.get("size_gate") or {}).get("status", "passed")
        status = _merge_statuses(validation_status, size_status)
        if status == "failed":
            failures += 1
        elif status == "passed_with_warnings":
            warnings += 1
        if size_status == "failed":
            size_failures += 1
        elif size_status == "passed_with_warnings":
            size_warnings += 1
    overall = "failed" if failures else ("passed_with_warnings" if warnings else "passed")
    return {
        "cases_run": len(rows),
        "failed_cases": failures,
        "warning_cases": warnings,
        "size_failed_cases": size_failures,
        "size_warning_cases": size_warnings,
        "overall_status": overall,
    }


def _merge_statuses(validation_status: str, size_status: str) -> str:
    priority = {"passed": 0, "passed_with_warnings": 1, "failed": 2}
    return validation_status if priority.get(validation_status, 2) >= priority.get(size_status, 2) else size_status


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
        if row.get("size_gate"):
            size_gate = row["size_gate"]
            lines.append(f"- Size gate: `{size_gate.get('status', 'unknown')}`")
            lines.append(f"- EPUB size: `{size_gate.get('epub_size_bytes', 0)}` B")
            if size_gate.get("warn_bytes") is not None:
                lines.append(
                    f"- Size budget: warn `{size_gate['warn_bytes']}` B / hard `{size_gate['hard_bytes']}` B"
                )
            largest_assets = ((size_gate.get("inspection") or {}).get("largest_assets") or [])[:3]
            if largest_assets:
                lines.append("- Largest assets:")
                for asset in largest_assets:
                    lines.append(f"  - `{asset['name']}` -> `{asset['size_bytes']}` B")
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
