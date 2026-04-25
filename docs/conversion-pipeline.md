# KindleMaster Conversion Pipeline

Related Linear scope: VAT-173.

This document is the agent-facing map for how KindleMaster turns PDF or DOCX input into a Kindle-ready EPUB. It is a mirror of current repo behavior, not an aspirational architecture. If it disagrees with `kindlemaster.py`, `AGENTS.md`, or the implementation modules named below, the code and `AGENTS.md` win and this document must be updated.

## Standard Flow

| Stage | Runtime owner | What happens | Primary tests or gates |
| --- | --- | --- | --- |
| 1. Input and command routing | `kindlemaster.py`, `app.py` | CLI and Flask routes accept PDF/DOCX input and route conversion through the same core conversion path. | `test_kindlemaster_entrypoint.py`, `test_app_async_convert.py`, `test_pdf_runtime_flow.py` |
| 2. Publication analysis | `publication_analysis.py` | Detects publication class and route hints for books, reports, scans, magazines, training/chess-like inputs, and DOCX. | `test_publication_pipeline.py`, `test_premium_corpus_smoke.py` |
| 3. Extraction and OCR | `premium_reflow.py`, `magazine_kindle_reflow.py`, `pymupdf_chess_extractor.py`, `docx_conversion.py` | Extracts text, headings, tables, images, diagrams, OCR text, and DOCX structure into the shared publication model or legacy content shape. | `test_docx_conversion.py`, `test_magazine_conversion.py`, `test_chess_fix.py`, `test_prepare_reference_inputs_ocr_fixture.py` |
| 4. Text cleanup | `text_cleanup_engine.py`, `text_normalization.py` | Repairs split/glued text and PL/EN artifacts using scored, conservative transformations. | `test_text_normalization.py`, `test_converter_text_cleanup.py` |
| 5. Publication model and quality report | `publication_pipeline.py`, `publication_model.py`, `quality_reporting.py`, `quality_state_service.py` | Shapes conversion output, metrics, quality state, fallback signals, and machine-readable report payloads. | `test_release_quality_recovery.py`, `test_quality_reporting.py`, `test_quality_state_service.py` |
| 6. EPUB build and semantic cleanup | `converter.py`, `kindle_semantic_cleanup.py` | Builds EPUB files, then normalizes package/nav/spine, IDs, headings, metadata, tables, lists, and links. | `test_semantic_epub_cleanup.py`, `test_epub_validation.py`, `test_converter_publication_budget.py` |
| 7. TOC and reference repair | `epub_heading_repair.py`, `epub_reference_repair.py` | Rebuilds section hierarchy, anchors, TOC, and bibliography/reference structures when confidence is high enough. | `test_epub_heading_repair.py`, `test_toc_segmentation.py`, `test_epub_reference_repair.py` |
| 8. Validation and release reporting | `epub_validation.py`, `epub_release_pipeline.py`, `epub_quality_recovery.py`, `premium_corpus_smoke.py` | Runs layered EPUB validation, optional EPUBCheck, corpus proof, release recovery, and derived status summaries. | `python kindlemaster.py test --suite corpus`, `python kindlemaster.py test --suite release`, `python kindlemaster.py status` |

## Fallback Reporting

Fallback is allowed, but it must be visible. Do not treat fallback output as premium-ready unless the release checklist says it is acceptable.

| Fallback class | Where to look | Expected handling |
| --- | --- | --- |
| Premium route falls back to legacy conversion | `converter.py` output payload and quality report fields | Record the fallback reason and keep validation gates active. |
| OCRmyPDF unavailable but direct OCR can run | `python kindlemaster.py doctor`, `docs/toolchain-matrix.md` | Treat as degraded capability, not an EPUB-quality failure by itself. |
| EPUBCheck unavailable | `python kindlemaster.py doctor`, validator payloads | Internal validators still run; release claims must mention EPUBCheck unavailability. |
| Browser/runtime optional tools unavailable | `python kindlemaster.py test --suite browser`, `python kindlemaster.py test --suite runtime` | Return an explicit unavailable/degraded result instead of silently passing. |
| Heading/reference ambiguity | manual review queue and release reports | Preserve valid existing structure or flag manual review; do not invent structure. |

## Where To Add Tests

- Route or async UI contract changes: add/update `test_app_async_convert.py`, `test_app_runtime_services.py`, and browser/runtime tests when the visible flow changes.
- Parser or extraction changes: add targeted fixture coverage in the closest parser test and run a relevant smoke case.
- Shared cleanup changes: update `test_semantic_epub_cleanup.py` and run the text/TOC/reference regression pack from `AGENTS.md`.
- Release or quality reporting changes: update quality/report tests and run `python kindlemaster.py status` only as derived evidence, not as a hand-maintained truth source.
- Corpus/generalization claims: run `python kindlemaster.py test --suite corpus` and keep blockers visible if any fixture fails.

## Release Gate Summary

The minimal release-readiness chain for a conversion-quality change is:

```powershell
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite corpus
python kindlemaster.py test --suite release
python kindlemaster.py status
```

Use `docs/premium-epub-release-checklist.md` for the final human-readable verdict. Use `docs/independent-audit-mode.md` when evaluating one EPUB artifact independently from project status.
