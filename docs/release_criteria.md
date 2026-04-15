# Release Criteria

Release is forbidden unless ALL are true:
- all required test suites exist
- all required scenario tests exist
- all release-blocking tests pass
- full regression report exists
- premium Kindle quality audit passes
- final score meets threshold
- no unresolved high or critical release blockers remain
- no unresolved foreign frontend/runtime coupling remains
- final artifact is distinct from baseline and measurably improved
- immutable release candidate exists
- no unjustified screenshot or page-image fallback remains for normal text pages
- corpus-wide quality-first gate passes on the supported fixture bank

## Premium Kindle Threshold

Target quality:
- practical score target: `8.8 / 10` for supported guard fixtures and `9.0 / 10` for release-eligible fixtures in quality-first corpus mode
- stable reading flow
- clean semantics
- low-noise TOC
- controlled page-label noise
- Kindle-safe typography and layout behavior
- text-first article rendering by default

## Non-Release Conditions

Release must be denied if any of the following remain true:
- creator remains `Unknown` for release-ready publication
- human-facing title is an opaque slug or technical hash
- required scenario coverage is missing
- release-blocking isolation defect remains unresolved
- final artifact improvement is not measurable
- premium audit evidence is missing
- corpus-wide quality-first report fails
- unjustified screenshot or page-image fallback remains on normal text pages
