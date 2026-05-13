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

## OpenAI Provider

KindleMaster can use OpenAI as an optional live provider for quality review.
It is off unless explicitly enabled in local environment configuration:

```text
OPENAI_API_KEY=<local secret>
KINDLEMASTER_OPENAI_QUALITY=1
KINDLEMASTER_OPENAI_QUALITY_MODEL=gpt-4.1-mini
KINDLEMASTER_AI_FEEDBACK_RECORD=1
```

The implementation lives in `openai_quality_provider.py` and is wired into
`converter.py` through the existing `evaluate_ai_quality_intelligence(...)`
contract.

Safety rules:

- live OpenAI calls are opt-in,
- EPUB bytes are not rewritten by the AI provider,
- OCR suggestions still pass confidence and length safety checks,
- TOC suggestions may only reuse existing hrefs,
- provider failures fall back to deterministic quality reporting,
- feedback records are local derived evidence under `reports/ai-quality-feedback/`.

## OCR Cleanup

AI OCR cleanup is scoped to suspicious fragments only. The detector reuses deterministic text artifact signals such as split words, glued words, OCR junk, and suspicious URL fragments.

Accepted AI edits require confidence and safe length checks. The current integration records the before/after audit but does not rewrite EPUB bytes.

## TOC Detection

AI TOC detection only runs when deterministic TOC confidence is low. It rejects captions, chart labels, figure labels, ads, sponsored labels, duplicate labels, and missing links.

If AI confidence is low or no usable entries remain after filtering, KindleMaster falls back to deterministic TOC entries.

## Learning Loop

The v1 "learning" mechanism is evidence-driven, not self-modifying:

1. Deterministic audit finds suspicious OCR, TOC, structure, or metadata signals.
2. OpenAI provider proposes bounded fixes or cleaner TOC candidates.
3. KindleMaster records `learning_signals` and optional local feedback JSONL.
4. Repeated signals become fixtures, tests, or heuristic patches reviewed by an agent.
5. Corpus/release gates prove the change across document classes.

The system must not silently change its own heuristics or patch source code from
model output.

## Regression Fixtures

The Sprint 3 quick gate covers:

- clean book fixture
- OCR-heavy fixture
- magazine fixture with noisy TOC labels
- DOCX-like clean report fixture

Run:

```powershell
python -m unittest test_ai_quality_intelligence.py test_ai_ocr_cleanup.py test_ai_toc_detection.py test_openai_quality_provider.py test_ai_quality_feedback.py
python kindlemaster.py test --suite quick
```
