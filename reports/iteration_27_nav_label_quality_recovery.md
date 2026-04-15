# Iteration 27 - Nav Label Quality Recovery

## Summary

This iteration fixed a real user-facing quality gap: final EPUB bookmarks could still be too short, too generic, or structurally incomplete even when the EPUB passed technical checks. The remediation targeted context-aware nav label recovery and made truncated bookmark labels a first-class regression in the quality gate.

## Key Results

- recovered short dangling magazine bookmarks from nearby opening context
- split compact `title + kicker` openings more reliably for nav purposes
- repaired dangling parenthetical chess record labels with year or `Date unknown` closures
- added release-smoke and pytest enforcement for suspicious nav labels

## Validation

- `33 passed` in `pytest`
- `strefa-pmi-52-2026`: `10.0/10`, `suspicious_nav_label_count = 0`
- `newsweek-food-living-2026-01`: `10.0/10`, representative labels now include `Na casting do MasterChefa` and `W drodze ku smakom - Śniadania jak w domu`
- `tactits`: `8.65/10`, `suspicious_nav_label_count = 0`, labels now close book-record parentheticals such as `Helbig – Schroder (Place unknown - 1933)`
- executable release gate still returns `FAIL`, but only because existing high-severity blockers remain open

## Remaining Release Blockers

- `ISSUE-004`
- `ISSUE-020`
- `ISSUE-022`

## Next Step

Return to `FX-011` and `R3-005`. Bookmark quality is now guarded explicitly, so the next meaningful work remains deeper finalizer decomposition and medium-confidence cleanup without paraphrase.
