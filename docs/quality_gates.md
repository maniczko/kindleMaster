# Quality Gates

Every gate is hard-blocking for the scope it governs. Gate failures must be explicit, logged, and traceable.

## G-2 Project Isolation

Required evidence:
- boundary audit report
- reference scan
- isolation status in control plane
- physical split evidence or explicit blocker

Pass condition:
- Kindle Master-side coupling is verified, logged, remediated, or explicitly release-blocked
- physical repository split is complete or explicitly blocks release

Fail condition:
- coupling is unverified, hidden, or unresolved

Failure actions:
- create or update issue: yes
- create `FX-*`: yes if severity is high or critical
- blocks release: yes

## G-1 Governance Alignment

Required evidence:
- hardened `AGENTS.md`
- hardened DoD, workflow, quality gates, and release criteria
- normalized backlog, orchestration, and progress tracking

Pass condition:
- repository control model enforces execution discipline

Fail condition:
- governance remains descriptive only or missing required controls

Failure actions:
- create or update issue: yes
- create `FX-*`: yes if governance defect is high severity
- blocks release: yes

## G0 Input Detection And Execution Mode

Required evidence:
- input registry
- execution mode
- input readiness report

Pass condition:
- inputs detected, execution mode explicit, blockers logged if missing

Fail condition:
- input mode is implicit, missing, or untracked

Failure actions:
- create or update issue: yes
- create `FX-*`: yes for missing or invalid required inputs
- blocks release: yes

## G1 PDF Analysis And Conversion Strategy

Required evidence:
- PDF inventory
- risk report
- conversion strategy

Pass condition:
- PDFs and risks are explicit and conversion strategy is documented

Fail condition:
- strategy is implicit or risks are unlogged

Failure actions:
- create or update issue: yes
- create `FX-*`: yes when strategy or risk gap is high severity
- blocks release: yes

## G2 Baseline EPUB Existence Or Fallback

Required evidence:
- baseline EPUB artifact path
- fallback path if conversion fails

Pass condition:
- baseline EPUB exists and is traceable, or a blocker plus fallback is logged explicitly

Fail condition:
- baseline artifact is missing without explicit blocker or fallback

Failure actions:
- create or update issue: yes
- create `FX-*`: yes for conversion blocker
- blocks release: yes

## G3 EPUB Inventory And BEFORE Metrics

Required evidence:
- EPUB inventory
- publication model
- BEFORE metrics
- initial issue log

Pass condition:
- EPUB structure and baseline quality are fully mapped

Fail condition:
- inventory, model, or baseline is incomplete or implicit

Failure actions:
- create or update issue: yes
- create `FX-*`: yes when high severity
- blocks release: yes

## G4 Text Anomaly Detection

Required evidence:
- split-word, joined-word, boundary, and furniture findings
- low-confidence routing evidence

Pass condition:
- anomalies are detected and uncertainty is routed, not guessed

Fail condition:
- findings are partial, untraceable, or ambiguous cases were silently changed

Failure actions:
- create or update issue: yes
- create `FX-*`: yes when detection gap is high severity
- blocks release: yes

## G5 Safe Text Cleanup

Required evidence:
- before/after text metrics
- cleanup report
- regression evidence

Pass condition:
- visible artifact reduction or explicitly justified non-improvement outcome exists
- no uncontrolled paraphrase occurred

Fail condition:
- cleanup causes regression, or improvement is unproven

Failure actions:
- create or update issue: yes
- create `FX-*`: yes when regression or high-severity gap exists
- blocks release: yes

## G6 Semantic Reconstruction

Required evidence:
- semantic recovery report
- title, author, and lead separation evidence
- front matter and special-section evidence

Pass condition:
- semantic structure materially improves and special sections remain distinct

Fail condition:
- false heading promotion, article-opening loss, or special-section masquerade remains material

Failure actions:
- create or update issue: yes
- create `FX-*`: yes
- blocks release: yes

## G7 TOC / Navigation Quality

Required evidence:
- TOC report
- valid `nav.xhtml`
- valid `toc.ncx`
- anchor and path validation

Pass condition:
- TOC is derived from final semantics, links work, noise is under threshold

Fail condition:
- dead links, invalid paths, or low-value TOC dominance remains

Failure actions:
- create or update issue: yes
- create `FX-*`: yes
- blocks release: yes

## G8 Image / Layout Handling

Required evidence:
- layout classification report
- handling decisions
- Kindle risk log
- fallback justification report

Pass condition:
- image and layout classes are explicit
- Kindle risks are documented
- screenshot or page-image fallback is classified and justified
- unjustified fallback count is zero for release-ready output

Fail condition:
- handling path is implicit
- risk logging is incomplete
- fallback remains on normal text pages without explicit justification

Failure actions:
- create or update issue: yes
- create `FX-*`: yes
- blocks release: yes

## G9 Kindle CSS / UX Quality

Required evidence:
- CSS or UX report
- page-label noise measurement
- reading-flow sanity evidence

Pass condition:
- Kindle-safe typography baseline is met and reading comfort improves visibly

Fail condition:
- page labels dominate, semantic differentiation is weak, or reading flow is degraded

Failure actions:
- create or update issue: yes
- create `FX-*`: yes when high severity
- blocks release: yes

## G10 End-To-End Validation

Required evidence:
- repeatable PDF -> baseline EPUB -> final EPUB path
- distinction proof between baseline and final output
- no foreign frontend/runtime dependency in runtime path

Pass condition:
- final output is distinct, traceable, and the pipeline is isolated

Fail condition:
- improvements do not survive to final artifact, or pipeline depends on external or foreign frontend/runtime assumptions

Failure actions:
- create or update issue: yes
- create `FX-*`: yes
- blocks release: yes

## G11 Failed-Improvement Root-Cause Audit

Required evidence:
- artifact flow audit
- overwrite audit
- root-cause report

Pass condition:
- failed-improvement cause is explicit and overwrite or destructive stages are fixed or tightly bounded

Fail condition:
- lack of improvement remains unexplained

Failure actions:
- create or update issue: yes
- create `FX-*`: yes
- blocks release: yes

## G12 Test-Suite Completeness And Enforcement

Required evidence:
- structural tests
- text threshold tests
- TOC and anchor tests
- front matter tests
- metadata tests
- typography and UX tests
- page-label noise tests
- screenshot/page-image fallback tests
- scenario tests
- regression tests
- isolation tests
- genericity guard tests
- release-blocking enforcement

Pass condition:
- all required test classes exist and can be run
- scenario gaps are either covered by real repo-local fixtures or logged as explicit blockers

Fail condition:
- any required test class is missing or non-blocking

Failure actions:
- create or update issue: yes
- create `FX-*`: yes for missing coverage or enforcement
- blocks release: yes

## G13 Final Regression, Premium Audit, And Release Readiness

Required evidence:
- all required tests pass
- full regression report exists
- premium Kindle audit exists
- final score exists
- release verdict exists
- immutable release candidate evidence exists
- corpus-wide text-first report exists

Pass condition:
- premium Kindle audit passes
- no unresolved high or critical release blocker remains
- final approved release artifact is distinct from baseline and stored as an immutable release candidate
- no unjustified screenshot or page-image fallback remains on normal text pages
- corpus-wide quality-first gate passes for the supported fixture bank

Fail condition:
- any release-blocking test fails
- premium audit fails
- score is below threshold
- unjustified fallback remains
- corpus-wide gate fails
- coupling remains unresolved

Failure actions:
- create or update issue: yes
- create `FX-*`: yes
- blocks release: yes
