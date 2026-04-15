# R3-005 Medium-Confidence False-Positive Suppression

Date: 2026-04-13
Task: `R3-005`
Owner: `Text Cleanup`
Supporting agent:
- `Goodall`: deterministic false-positive suppression pass for joined-word audit

## Goal

Reduce medium-confidence joined-word noise without paraphrasing or rewriting publication content.

## Strategy

- No body-text rewriting was performed in this pass.
- The joined-word audit was made more conservative by suppressing obvious brand and proper-name false positives before they enter review metrics.
- Auto-fix eligibility for short lower-to-upper joins is now stricter and requires stronger evidence.

## Deterministic Suppressions Added

- Exact or normalized false positives:
  - `AeroPress`
  - `InterContinental`
  - `YouTubie`
  - `McDonald`
  - `McDonaldzie`
  - `MacMurray`
  - `McInerney`
  - `McCudden`
  - `MacDonnel`
  - `InPlus`
- Pattern-level suppression:
  - `Mc...`
  - `Mac...`
  when they match proper-name casing rather than an editorial glue defect.

## Evidence

- Active release sample text audit:
  - `split_word_count = 0`
  - `joined_word_count = 0`
  - `boundary_count = 0`
- Magazine guard text audit:
  - `split_word_count = 0`
  - `joined_word_count = 0`
  - `boundary_count = 0`
- Book guard text audit:
  - `split_word_count = 0`
  - `joined_word_count = 0`
  - `boundary_count = 0`

## Guardrails

- No publication text was paraphrased.
- No author style was rewritten.
- No medium-confidence sentence or paragraph content was auto-merged in this pass.
- Short CamelCase joins now remain `review_only` unless both sides are strong enough to justify an automatic boundary suspicion.

## Validation

- `python -m pytest -q tests\test_text_quality_thresholds.py`
- `python -m pytest -q`

## Result

`R3-005` remains `IN_PROGRESS`.

This pass improves the trustworthiness of the text audit and removes a misleading source of cleanup pressure, but it is not the final medium-confidence recovery verdict. The next step is `R3-006`, which must prove whether visible reading quality improved, not just whether the audit got cleaner.

