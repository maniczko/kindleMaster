# Sprint 3 AI Quality Intelligence

Sprint 3 adds AI-assisted quality analysis without making AI a mandatory runtime dependency.

## Contract

The public report is produced by `evaluate_ai_quality_intelligence(epub_bytes)` and is attached to conversion metadata under:

- `metadata.text_cleanup.ai_quality`
- `metadata.ai_quality`

The report includes:

- `before_quality_score`
- `after_quality_score`
- `score_delta`
- `provider`
- `confidence`
- `estimated_cost_usd`
- `changed_fragment_count`
- `changed_toc_entry_count`
- `fallback_reasons`
- `deterministic_output_preserved`

Default behavior is offline-safe. If no AI provider is injected, KindleMaster records a fallback or skipped audit and preserves deterministic EPUB output.

## OCR Cleanup

AI OCR cleanup is scoped to suspicious fragments only. The detector reuses deterministic text artifact signals such as split words, glued words, OCR junk, and suspicious URL fragments.

Accepted AI edits require confidence and safe length checks. The current integration records the before/after audit but does not rewrite EPUB bytes.

## TOC Detection

AI TOC detection only runs when deterministic TOC confidence is low. It rejects captions, chart labels, figure labels, ads, sponsored labels, duplicate labels, and missing links.

If AI confidence is low or no usable entries remain after filtering, KindleMaster falls back to deterministic TOC entries.

## Regression Fixtures

The Sprint 3 quick gate covers:

- clean book fixture
- OCR-heavy fixture
- magazine fixture with noisy TOC labels
- DOCX-like clean report fixture

Run:

```powershell
python -m unittest test_ai_quality_intelligence.py test_ai_ocr_cleanup.py test_ai_toc_detection.py
python kindlemaster.py test --suite quick
```

