from __future__ import annotations

import json
import sys
from pathlib import Path

from converter import ConversionConfig, convert_pdf_to_epub_with_report
from publication_analysis import analyze_publication


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python publication_audit.py <pdf-path> [output-json]")
        return 1

    pdf_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else pdf_path.with_suffix(".audit.json")
    if not pdf_path.exists():
        print(f"Missing PDF: {pdf_path}")
        return 2

    analysis = analyze_publication(str(pdf_path), preferred_profile="auto-premium")
    result = convert_pdf_to_epub_with_report(
        str(pdf_path),
        config=ConversionConfig(profile="auto-premium"),
        original_filename=pdf_path.name,
    )
    document = result.get("document") or {}
    document_metadata = document.get("metadata", {}) if isinstance(document, dict) else {}

    payload = {
        "pdf": str(pdf_path),
        "analysis": analysis.to_dict() if hasattr(analysis, "to_dict") else analysis,
        "document_summary": result.get("document_summary", {}),
        "quality_report": result.get("quality_report", {}),
        "audit": document_metadata.get("audit", {}),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
