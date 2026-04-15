# Iteration 30

## Summary

This iteration focused on two generic quality goals:

- stop over-scoring or under-scoring publications because front matter was classified too broadly
- make finalizer stage outputs persistent and inspectable instead of relying only on in-memory or temp-state proofs

No publication-specific token hacks were introduced.

## Files changed

- `kindlemaster_quality_score.py`
- `kindle_semantic_cleanup.py`
- `kindlemaster_end_to_end.py`
- `tests/test_finalizer_stage_proofs.py`
- `tests/test_scenario_manifest.py`
- `project_control/phase_r4_front_matter_boundary_recovery.md`
- `project_control/phase_fx011_stage_artifact_manifest.md`

## Validation

- `python -m py_compile kindlemaster_quality_score.py kindle_semantic_cleanup.py kindlemaster_end_to_end.py tests/test_finalizer_stage_proofs.py tests/test_scenario_manifest.py`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf --publication-id newsweek-food-living-2026-01`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/cover-letter-iwo-2026.pdf --publication-id cover-letter-iwo-2026`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026`
- `python kindlemaster_quality_loop.py --publication-id strefa-pmi-52-2026 --max-iterations 1 --target-score 8.8 --resume-last`
- `python -m pytest -q`

## Publication scores after this pass

- `strefa-pmi-52-2026` -> `10.0/10`
- `newsweek-food-living-2026-01` -> `10.0/10`
- `chess-5334-problems-combinations-and-games` -> `10.0/10`
- `cover-letter-iwo-2026` -> `9.7/10`

## Notes

- front matter now uses a generic leading-boundary model with editorial-front-matter separation
- `tactits` no longer looks artificially weak because of overbroad front-matter scoring
- finalizer artifacts are now persisted per stage with proofs and hashes
- release is still `NOT READY` because high-severity blockers remain open even though the tracked guard set is now premium
