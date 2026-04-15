# Conversion Strategy

## Current Start Mode

`PDF_AND_EPUB`

The current corpus contains valid EPUB inputs in `samples/epub/` and valid PDF fixtures in `samples/pdf/`. The PDF-first path has been validated on `strefa-pmi-52-2026.pdf` using a dedicated Kindle Master toolchain.

## Generic Strategy

### When only PDF exists

1. inventory PDFs and classify complexity
2. decide whether the profile is text-heavy, OCR-risky, image-heavy, or mixed-layout
3. choose baseline EPUB creation strategy
4. preserve page-like or image-heavy sections only where reflow would create worse Kindle reading quality
5. register the baseline EPUB artifact before remediation begins

### When EPUB already exists

1. register EPUB intake
2. skip baseline conversion
3. start from EPUB analysis and remediation

### When both PDF and EPUB exist

1. keep EPUB inputs available for comparison and remediation
2. use PDF-first conversion on at least one publication as end-to-end proof
3. keep baseline and final EPUB outputs in Kindle Master-only output paths

## Validated Toolchain

- PDF parser and renderer: `PyMuPDF`
- baseline converter: `kindlemaster_pdf_to_epub.py`
- end-to-end runner: `kindlemaster_end_to_end.py`
- local tester: `kindlemaster_local_server.py`

## Handling Principles

- deterministic extraction and cleanup first
- image-based retention only when reflow would materially damage reading quality
- sparse-text or page-like pages may use image fallback in the baseline EPUB
- mastheads, ads, galleries, and publisher sections stay traceable instead of being silently removed
- fallback path must exist for mixed-layout and OCR-risky content
