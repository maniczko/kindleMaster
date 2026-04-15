# Iteration 28 - FX-011 Stage Proofs and R3 False-Positive Suppression

Date: 2026-04-13
Execution mode: `PDF_AND_EPUB`
Current tasks:
- `FX-011`
- `R3-005`

## Agent Allocation

- `Goodall`
  - scope: `kindlemaster_text_audit.py`, `tests/test_text_quality_thresholds.py`
  - mission: suppress deterministic joined-word false positives without touching publication content
- `Aquinas`
  - scope: `kindle_semantic_cleanup.py`, `kindlemaster_end_to_end.py`, `tests/test_finalizer_stage_proofs.py`
  - mission: expose stronger finalizer stage proofs for navigation survival and stage integrity
- `Lead / Orchestrator`
  - scope: integration, reruns, quality verification, control-plane updates

## What Improved

- Finalizer reports now prove that semantic TOC intent survives navigation rebuild.
- End-to-end JSON reports now surface stage-order and navigation-stage integrity directly.
- Joined-word audit noise no longer counts obvious brands and proper names as cleanup defects on tracked guard publications.
- The full pytest suite expanded from `33` to `38` passing checks.

## Verification

- `python -m py_compile ...` `PASS`
- `python -m pytest -q` `PASS` (`38 passed`)
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode` `PASS`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf --publication-id newsweek-food-living-2026-01` `PASS`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book` `PASS`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026` `FAIL` only because release-blocking issues remain open

## Quality Snapshot

- `strefa-pmi-52-2026`:
  - `10.0/10`
  - `joined_word_boundary_count = 0`
  - `finalizer_navigation_stage_integrity_ok = true`
- `newsweek-food-living-2026-01`:
  - `10.0/10`
  - `joined_word_boundary_count = 0`
  - `finalizer_navigation_stage_integrity_ok = true`
- `chess-5334-problems-combinations-and-games`:
  - `8.65/10`
  - `split/joined/boundary = 0/0/0`
  - `finalizer_navigation_stage_integrity_ok = true`
  - still blocked by broader mixed-layout/front-matter quality limits, not by text-audit noise

## Verdict

This iteration materially improves proof quality and measurement quality.

- `FX-011`: stronger, still not fully complete
- `R3-005`: safer and cleaner, still not final
- repository verdict: `NOT READY`

## Next

- `R3-006`: recompute text-recovery evidence and decide whether the medium-confidence lane produced visible improvement
- continue `FX-011`: move from proof-rich orchestration toward more independently persisted stage artifacts
