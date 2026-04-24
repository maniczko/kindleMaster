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
from size_budget_policy import build_document_budget_proposals, get_document_size_budget, load_size_budget_policy


def benchmark_size_budgets(
    *,
    manifest_path: str | Path = "reference_inputs/manifest.json",
    case_filters: list[str] | None = None,
) -> dict[str, Any]:
    resolved_manifest = Path(manifest_path).resolve()
    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    policy = load_size_budget_policy()
    filters = [token.strip().lower() for token in (case_filters or []) if token.strip()]
    rows: list[dict[str, Any]] = []

    for case in manifest.get("cases", []):
        haystacks = [
            str(case.get("id", "")).lower(),
            str(case.get("document_class", "")).lower(),
            str(Path(case.get("target_path", "")).name).lower(),
        ]
        if filters and not any(token in haystack for token in filters for haystack in haystacks):
            continue

        path = Path(case["target_path"]).resolve()
        if case["input_type"] in {"pdf", "docx"}:
            result = convert_document_to_epub_with_report(
                str(path),
                config=ConversionConfig(profile="auto-premium", language=case.get("language", "pl")),
                original_filename=path.name,
                source_type=case["input_type"],
            )
            epub_size_bytes = len(result["epub_bytes"])
        else:
            epub_size_bytes = len(path.read_bytes())

        rows.append(
            {
                "id": case["id"],
                "document_class": case["document_class"],
                "input_type": case["input_type"],
                "path": str(path),
                "epub_size_bytes": epub_size_bytes,
                "current_budget": get_document_size_budget(str(case.get("document_class", "")), policy=policy),
            }
        )

    return {
        "manifest": str(resolved_manifest),
        "cases_benchmarked": len(rows),
        "rows": rows,
        "proposals": build_document_budget_proposals(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark current EPUB sizes and propose per-class size budgets.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()

    payload = benchmark_size_budgets(manifest_path=args.manifest, case_filters=args.case)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
