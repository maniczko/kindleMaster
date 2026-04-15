from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz


def classify_pdf(pdf_path: Path) -> dict:
    with fitz.open(pdf_path) as doc:
        text_lengths: list[int] = []
        pages_with_text = 0
        for page in doc:
            text = page.get_text("text")
            length = len(text.strip())
            text_lengths.append(length)
            if length > 80:
                pages_with_text += 1

        page_count = doc.page_count
        metadata = doc.metadata or {}

    avg_text = sum(text_lengths) / page_count if page_count else 0
    text_ratio = (pages_with_text / page_count) if page_count else 0

    if text_ratio >= 0.8 and avg_text >= 600:
        profile = "text-heavy"
    elif text_ratio <= 0.2:
        profile = "image-heavy"
    elif text_ratio <= 0.5:
        profile = "ocr-risky"
    else:
        profile = "mixed-layout"

    risks: list[str] = []
    if text_ratio < 0.95:
        risks.append("possible_text_loss_or_nontext_pages")
    if profile in {"mixed-layout", "image-heavy"}:
        risks.append("layout_and_image_density_risk")
    if profile == "ocr-risky":
        risks.append("ocr_or_scan_quality_risk")
    if page_count > 100:
        risks.append("long_publication_navigation_risk")
    if avg_text < 250:
        risks.append("toc_and_heading_reconstruction_risk")

    return {
        "file": pdf_path.name,
        "path": str(pdf_path),
        "page_count": page_count,
        "pages_with_text": pages_with_text,
        "text_ratio": round(text_ratio, 4),
        "avg_chars_per_page": round(avg_text, 2),
        "profile": profile,
        "title": (metadata.get("title") or pdf_path.stem).strip() or pdf_path.stem,
        "author": (metadata.get("author") or "Unknown").strip() or "Unknown",
        "risks": risks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Kindle Master PDF fixtures")
    parser.add_argument("--pdf-dir", required=True, help="Directory containing PDF fixtures")
    parser.add_argument("--report", required=True, help="Output JSON report path")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir).resolve()
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    report = {
        "pdf_dir": str(pdf_dir),
        "pdf_count": len(pdfs),
        "items": [classify_pdf(pdf) for pdf in pdfs],
    }

    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
