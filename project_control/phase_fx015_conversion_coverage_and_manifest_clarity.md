# FX-015 Conversion Coverage And Manifest Clarity

## Goal

Resolve the user-facing trust gap where:

- the PDF to EPUB run looked unrealistically fast,
- the UI did not prove that all PDF pages were preserved,
- `publication_id` could be auto-suggested from known sample names,
- the runtime exposed repository benchmark/sample references in a way that looked like current-run coupling,
- the `Wymus OCR` toggle looked functional even though no separate OCR stage was actually executed.

## Findings

1. The runtime did convert the full source PDF into baseline page documents for the tracked samples, but this proof was hidden in JSON reports and not surfaced in the conversion UI.
2. The frontend auto-suggested known `publication_id` values based on sample file names, which was confusing and could look like silent fallback to example publications.
3. The conversion form exposed `profile` and `ocr` intent, but the runtime did not previously report back which profile was actually applied or whether OCR was truly executed.
4. The repository benchmark panel showed accepted sample state next to the current conversion flow, which made sample references look like they belonged to the active upload.
5. Coverage hardening initially reintroduced one special-section heading (`Strefa PMI w liczbach`) as `h1`; this was caught by tests and fixed in the same iteration by extending special-section term handling.

## Implemented Changes

- `kindlemaster_pdf_to_epub.py`
  - conversion profiles are now normalized and recorded explicitly,
  - hybrid conversion is now guarded by actual embedded-image presence instead of column count alone,
  - baseline reports now include `pdf_page_count`, page-coverage proof, profile traceability, OCR traceability, and warnings.
- `kindlemaster_end_to_end.py`
  - end-to-end reports now include conversion duration,
  - explicit `conversion_options`,
  - explicit `coverage` block proving source/baseline/final page counts,
  - hard failure if page coverage is not preserved end-to-end.
- `kindlemaster_webapp.py`
  - `/convert` now returns headers for source pages, baseline pages, final pages, coverage pass/ratio, text/hybrid/image counts, duration, requested/applied profile, and OCR requested/applied,
  - index now exposes manifest publications to the UI for transparent suggestion only.
- `kindlemaster_templates/index.html`
  - `publication_id` is no longer auto-applied from sample file names,
  - the UI now explains that leaving `publication_id` empty keeps the run exploratory,
  - the current conversion panel shows page coverage, duration, and OCR/profile traceability,
  - the benchmark panel is now clearly labelled as a repository-level benchmark rather than the current upload,
  - the OCR checkbox text now explicitly states that OCR is not silently run.
- `kindle_semantic_cleanup.py`
  - special-section handling now covers `Strefa PMI w liczbach`, preventing a same-iteration heading regression.
- `tests/test_conversion_traceability.py`
  - added executable assertions for page coverage proof and conversion-option traceability.
- `kindlemaster_release_gate_enforcer.py`
  - Phase 12 enforcement now includes the new conversion-traceability test suite.

## Evidence

### Active release sample

- Source: `samples/pdf/strefa-pmi-52-2026.pdf`
- Coverage: `84/84/84` (`source_pdf_page_count` / `baseline_page_records` / `final_page_documents`)
- Coverage pass: `true`
- Duration: `13985.77 ms`
- Profile requested/applied: `auto-premium` / `balanced_hybrid_with_image_guard`
- OCR requested/applied: `false` / `false`
- Quality score: `10.0/10`

### Long book-like sample

- Source: `samples/pdf/tactits.pdf`
- Coverage: `1184/1184/1184`
- Coverage pass: `true`
- Duration: `72946.14 ms`
- Profile requested/applied: `book` / `text_priority`
- OCR requested/applied: `false` / `false`
- Quality score: `8.65/10`

## Validation

- `python -m py_compile kindle_semantic_cleanup.py kindlemaster_pdf_to_epub.py kindlemaster_end_to_end.py kindlemaster_webapp.py`
- `python -m pytest -q`
  - result: `31 passed`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026`
  - result: `FAIL` only because repository-level high-severity blockers remain open, not because page coverage or smoke failed

## Outcome

This pass does not make the repository `READY`, but it closes the specific trust gap around silent sample linkage and unverifiable page loss. The runtime now proves whether the whole file was converted, how it was converted, and whether OCR/profile requests were actually honored.
