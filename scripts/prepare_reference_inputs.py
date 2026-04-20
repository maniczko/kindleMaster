from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from PIL import Image, ImageDraw


REFERENCE_CASES = [
    {
        "id": "ocr_probe_pdf",
        "document_class": "ocr_probe",
        "input_type": "pdf",
        "language": "pl",
        "quick_smoke": True,
        "source": "example/ocr_runtime_probe.pdf",
        "target": "reference_inputs/pdf/ocr_probe.pdf",
        "notes": "Tiny OCR probe for the fastest conversion smoke.",
    },
    {
        "id": "dense_business_guide_pdf",
        "document_class": "dense_business_guide",
        "input_type": "pdf",
        "language": "en",
        "quick_smoke": False,
        "source": "example/BABOK_Guide_v3_Member.pdf",
        "target": "reference_inputs/pdf/dense_business_guide.pdf",
        "notes": "Dense handbook fixture for heading, TOC, and text-cleanup regression.",
    },
    {
        "id": "diagram_training_book_pdf",
        "document_class": "diagram_training_book",
        "input_type": "pdf",
        "language": "en",
        "quick_smoke": False,
        "source": "example/tactits_sample_80pages.pdf",
        "target": "reference_inputs/pdf/diagram_training_book.pdf",
        "notes": "Training/diagram-heavy PDF for layout-sensitive smoke runs.",
    },
    {
        "id": "magazine_layout_pdf",
        "document_class": "magazine_layout",
        "input_type": "pdf",
        "language": "pl",
        "quick_smoke": False,
        "source": "example/02695ab2e05aab728b4b995caa682f947e8be2c3291ff490579797c5a3cc5e26.pdf",
        "target": "reference_inputs/pdf/magazine_layout.pdf",
        "notes": "Magazine/business-report style PDF with layout-heavy structures.",
    },
    {
        "id": "scan_probe_epub",
        "document_class": "scan_probe",
        "input_type": "epub",
        "language": "pl",
        "quick_smoke": True,
        "source": "example/scan_probe_premium.epub",
        "target": "reference_inputs/epub/scan_probe.epub",
        "notes": "Small EPUB for fast validator and repair smoke.",
    },
    {
        "id": "dense_business_guide_epub",
        "document_class": "dense_business_guide",
        "input_type": "epub",
        "language": "en",
        "quick_smoke": False,
        "source": "example/BABOK_current_output.epub",
        "target": "reference_inputs/epub/dense_business_guide.epub",
        "notes": "Representative generated EPUB for release-audit and validator smoke.",
    },
    {
        "id": "simple_report_docx",
        "document_class": "docx_structured_report",
        "input_type": "docx",
        "language": "pl",
        "quick_smoke": True,
        "target": "reference_inputs/docx/simple_report.docx",
        "notes": "Small heading-driven DOCX fixture for quick conversion smoke.",
        "generator": "simple_report",
    },
    {
        "id": "list_table_image_docx",
        "document_class": "docx_rich_content",
        "input_type": "docx",
        "language": "en",
        "quick_smoke": False,
        "target": "reference_inputs/docx/list_table_image.docx",
        "notes": "DOCX fixture with lists, tables, links, and inline imagery.",
        "generator": "list_table_image",
    },
    {
        "id": "no_heading_document_docx",
        "document_class": "docx_no_h1",
        "input_type": "docx",
        "language": "pl",
        "quick_smoke": False,
        "target": "reference_inputs/docx/no_heading_document.docx",
        "notes": "DOCX without Heading 1 styles to test deterministic fallback sectioning.",
        "generator": "no_heading_document",
    },
]


def prepare_reference_inputs(*, root_dir: str | Path = ".") -> dict[str, Any]:
    resolved_root = Path(root_dir).resolve()
    prepared: list[dict[str, Any]] = []
    for case in REFERENCE_CASES:
        target = resolved_root / case["target"]
        target.parent.mkdir(parents=True, exist_ok=True)

        generator = case.get("generator")
        if generator:
            _generate_docx_fixture(generator, target)
        else:
            source = resolved_root / case["source"]
            if not source.exists():
                raise FileNotFoundError(source)
            shutil.copy2(source, target)

        prepared_case = {
            **case,
            "source_path": case["source"] if "source" in case else f"<generated:{case['generator']}>",
            "target_path": case["target"],
            "size_bytes": target.stat().st_size,
        }
        prepared.append(prepared_case)

    manifest = {
        "version": 2,
        "root_dir": ".",
        "cases": prepared,
    }
    manifest_path = resolved_root / "reference_inputs" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _generate_docx_fixture(generator: str, target_path: Path) -> None:
    if generator == "simple_report":
        document = _build_simple_report_docx()
    elif generator == "list_table_image":
        document = _build_list_table_image_docx()
    elif generator == "no_heading_document":
        document = _build_no_heading_docx()
    else:  # pragma: no cover - defensive guard
        raise ValueError(f"Unknown DOCX fixture generator: {generator}")
    document.save(str(target_path))


def _build_simple_report_docx() -> Document:
    document = Document()
    document.core_properties.title = "Raport operacyjny"
    document.core_properties.author = "KindleMaster QA"
    title = document.add_heading("Raport operacyjny", level=1)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    document.add_paragraph("To jest mala, uporzadkowana probka DOCX do szybkiego smoke testu.")
    document.add_heading("Zakres", level=2)
    document.add_paragraph("Dokument ma sprawdzic podstawowe mapowanie headingow, akapitow i metadanych.")
    document.add_heading("Wnioski", level=2)
    paragraph = document.add_paragraph("Pelna analiza znajduje sie pod adresem ")
    _append_hyperlink(paragraph, "https://example.com/report", "https://example.com/report")
    paragraph.add_run(".")
    return document


def _build_list_table_image_docx() -> Document:
    document = Document()
    document.core_properties.title = "DOCX Rich Content Probe"
    document.core_properties.author = "KindleMaster QA"
    document.add_heading("Rich content sample", level=1)
    document.add_paragraph("This fixture exercises semantic lists, tables, hyperlinks, and inline imagery.")
    document.add_heading("Checklist", level=2)
    document.add_paragraph("Preserve unordered lists", style="List Bullet")
    document.add_paragraph("Preserve ordered lists", style="List Number")
    document.add_paragraph("Keep hyperlinks clickable", style="List Bullet")
    document.add_heading("Reference link", level=2)
    paragraph = document.add_paragraph("Canonical source: ")
    _append_hyperlink(paragraph, "https://example.com/source", "Example Source")
    table = document.add_table(rows=3, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Lists"
    table.cell(1, 1).text = "2"
    table.cell(2, 0).text = "Tables"
    table.cell(2, 1).text = "1"
    document.add_heading("Inline image", level=2)
    with _temporary_demo_image() as image_path:
        document.add_picture(str(image_path), width=Inches(2.3))
    return document


def _build_no_heading_docx() -> Document:
    document = Document()
    document.core_properties.title = "Dokument bez H1"
    document.core_properties.author = "KindleMaster QA"
    intro = document.add_paragraph()
    run = intro.add_run("Dokument bez H1")
    run.bold = True
    run.font.size = Pt(18)
    document.add_paragraph("Ta probka nie ma stylu Heading 1 i wymaga deterministycznego fallbacku sekcji.")
    document.add_paragraph("Pierwszy punkt kontrolny", style="List Bullet")
    document.add_paragraph("Drugi punkt kontrolny", style="List Bullet")
    paragraph = document.add_paragraph("Wsparcie: ")
    _append_hyperlink(paragraph, "https://example.com/support", "https://example.com/support")
    return document


class _temporary_demo_image:
    def __enter__(self) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        handle.close()
        self.path = Path(handle.name)
        image = Image.new("RGB", (420, 220), "#f5efe6")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((18, 18, 402, 202), radius=18, fill="#ffffff", outline="#d97f4a", width=6)
        draw.text((48, 88), "KindleMaster", fill="#1f2937")
        draw.text((48, 120), "DOCX fixture", fill="#6b7280")
        image.save(self.path, format="PNG")
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        if hasattr(self, "path") and self.path.exists():
            self.path.unlink()


def _append_hyperlink(paragraph, url: str, text: str) -> None:
    relation_id = paragraph.part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relation_id)

    run = OxmlElement("w:r")
    run_properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    run_properties.append(color)
    run_properties.append(underline)
    run.append(run_properties)

    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def main() -> int:
    manifest = prepare_reference_inputs()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
