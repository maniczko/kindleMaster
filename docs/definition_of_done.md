# Definition Of Done

## Purpose

This repository does not treat "conversion completed" as success.
A conversion is done only when the produced EPUB is evidence-backed, regression-checked, and good enough for real Kindle reading.

This file is both:
- a governance rule set
- a practical checklist for deciding whether a specific conversion is acceptable

## Global Definition Of Done

No quality-affecting task may be marked `DONE` unless all are true:
- the expected output exists
- related issues were updated when relevant
- related metrics were updated when relevant
- the assigned quality gate passed
- evidence was captured in repository artifacts
- regressions were checked
- the current iteration report was updated
- uncertainty was either resolved or explicitly routed into the low-confidence queue

Negative rules:
- changed file != done
- generated EPUB != done
- technically valid ZIP package != done
- "looks better" != done
- "probably fixed" != done
- partial cleanup != done
- unmeasured improvement != done
- technical improvement with editorial regression != done
- publication-specific hack != done
- word-specific exception != done
- fixture-specific workaround != done

## Per-Class Definition Of Done

### Analysis Tasks
- the analysis artifact exists
- findings are traceable to concrete files, pages, or runtime evidence
- discovered defects were logged in the issue register
- relevant baselines were recorded in metrics
- the assigned gate passed

### Cleanup Tasks
- the changed output exists
- before/after evidence exists
- no uncontrolled paraphrase or meaning drift occurred
- affected issues and low-confidence queue entries were updated
- cleanup regressions were checked
- the assigned gate passed

### Semantic Tasks
- corrected semantic output exists
- title, author, subtitle, and lead separation are evidenced on affected samples
- special sections remain semantically distinct
- TOC pollution risk was reassessed
- the assigned gate passed

### TOC / Navigation Tasks
- updated navigation output exists
- `nav.xhtml` and `toc.ncx` are valid in affected scope
- anchors and relative paths were checked
- before/after TOC evidence exists
- the assigned gate passed

### Image / Layout Tasks
- classification or handling artifact exists
- Kindle layout risk is recorded
- decisions are explicit and traceable
- normal text pages remain text-first
- every page-image fallback is classified and justified
- the assigned gate passed

### CSS / UX Tasks
- the changed CSS or UX evidence exists
- reading-flow evidence exists
- title/author/lead distinction was checked
- page-label noise was checked
- first-screen readability was checked
- the assigned gate passed

### QA / Regression Tasks
- the report exists
- before/after deltas are explicit
- regressions are either fixed or logged
- no unstated assumptions remain
- the assigned gate passed

### Release Readiness Tasks
- all required test classes exist
- all release-blocking tests passed
- premium audit evidence exists
- final score exists
- release verdict is explicit
- immutable release candidate exists when release is approved

### Test-Writing Tasks
- executable tests exist in the repository
- the expected failure pattern is explicit
- the test target is traceable to a known issue, gate, or task
- the control plane references the new evidence
- the assigned gate passed

### Governance Hardening Tasks
- the strengthened file exists
- the rule is enforceable, not merely descriptive
- related control-plane files are synchronized
- the repository state reflects the rule where relevant

## Conversion Acceptance Checklist

Use this checklist to decide whether a specific `PDF -> EPUB -> final EPUB` conversion is good enough.
For a release-ready conversion, every required item must be `PASS`.
If an item is `FAIL`, the conversion is not done.
If an item is uncertain, it must become a low-confidence item or issue, not a silent assumption.

### 1. Intake And Traceability

- `PASS / FAIL`: source PDF path is recorded
- `PASS / FAIL`: baseline EPUB path is recorded
- `PASS / FAIL`: final EPUB path is recorded
- `PASS / FAIL`: report JSON exists
- `PASS / FAIL`: publication id is known or explicitly absent for exploratory mode
- `PASS / FAIL`: release mode state is explicit
- `PASS / FAIL`: metadata source precedence is explicit

### 2. Technical Package Integrity

- `PASS / FAIL`: EPUB opens as a valid ZIP package
- `PASS / FAIL`: `mimetype` exists
- `PASS / FAIL`: `META-INF/container.xml` exists
- `PASS / FAIL`: OPF exists and is reachable
- `PASS / FAIL`: `nav.xhtml` exists
- `PASS / FAIL`: `toc.ncx` exists when required
- `PASS / FAIL`: referenced stylesheets exist in package
- `PASS / FAIL`: no broken nav paths
- `PASS / FAIL`: no broken NCX paths
- `PASS / FAIL`: no dead anchors
- `PASS / FAIL`: title page exists and is not empty

### 3. Metadata Quality

- `PASS / FAIL`: title is human-facing and not an opaque slug/hash
- `PASS / FAIL`: creator is not `Unknown` for release-ready output
- `PASS / FAIL`: language is explicit and correct
- `PASS / FAIL`: manifest-backed metadata survives into final EPUB in release mode
- `PASS / FAIL`: metadata is traceable to trusted evidence, not guesswork

### 4. Structural Quality

- `PASS / FAIL`: valid `h1` exists where applicable
- `PASS / FAIL`: no title + author + lead + page merge
- `PASS / FAIL`: headings are not dominated by furniture or organizational residue
- `PASS / FAIL`: special sections do not masquerade as normal articles
- `PASS / FAIL`: article openings have readable hierarchy near the top of the chapter
- `PASS / FAIL`: front matter is separated from article flow

### 5. Text Quality

- `PASS / FAIL`: split-word count is at or below threshold
- `PASS / FAIL`: joined-word count is at or below threshold
- `PASS / FAIL`: broken-boundary count is at or below threshold
- `PASS / FAIL`: remaining suspicious cases are routed to review, not silently changed
- `PASS / FAIL`: no uncontrolled paraphrase occurred
- `PASS / FAIL`: no meaning drift is visible

### 6. TOC And Navigation Quality

- `PASS / FAIL`: TOC entries come from true article or section semantics
- `PASS / FAIL`: no duplicate low-value entries above threshold
- `PASS / FAIL`: no `Page N` dominance
- `PASS / FAIL`: no author-only TOC noise unless explicitly justified
- `PASS / FAIL`: no front-matter TOC pollution
- `PASS / FAIL`: TOC count is sane for the publication profile

### 7. Front Matter And Special Sections

- `PASS / FAIL`: cover/title/front matter are readable in linear Kindle flow
- `PASS / FAIL`: editorial or masthead material stays distinct from article content
- `PASS / FAIL`: organizational lines are not promoted to article headings
- `PASS / FAIL`: special sections do not pollute navigation

### 8. Typography And Kindle UX

- `PASS / FAIL`: body line-height meets baseline
- `PASS / FAIL`: heading hierarchy is visually consistent
- `PASS / FAIL`: title, byline, and lead are visually distinct
- `PASS / FAIL`: page markers are hidden or de-emphasized
- `PASS / FAIL`: figures and captions remain related
- `PASS / FAIL`: first screen of an article looks calm and readable
- `PASS / FAIL`: reading flow is Kindle-safe under reflow

### 9. Text-First And Fallback Discipline

- `PASS / FAIL`: normal article pages are rendered as reflowable text
- `PASS / FAIL`: no screenshot/page-image fallback remains on normal text pages
- `PASS / FAIL`: every fallback page is explicitly classified
- `PASS / FAIL`: every fallback page has an allowed justification
- `PASS / FAIL`: fallback usage is reported in run artifacts
- `PASS / FAIL`: unjustified fallback count is zero for release-ready output

### 10. Dual-Baseline Quality Proof

- `PASS / FAIL`: final output is distinct from baseline EPUB
- `PASS / FAIL`: final output is not worse than the accepted best EPUB
- `PASS / FAIL`: final output is not worse than the PDF baseline EPUB on tracked hard metrics
- `PASS / FAIL`: candidate quality score improved or a high-severity blocker was closed without regression
- `PASS / FAIL`: no hard regression was introduced

### 11. Test And Gate Evidence

- `PASS / FAIL`: release smoke passed
- `PASS / FAIL`: current pytest gate suite passed
- `PASS / FAIL`: scenario tests are present for supported profiles or explicit blockers exist
- `PASS / FAIL`: isolation tests passed
- `PASS / FAIL`: finalizer proof gate passed
- `PASS / FAIL`: latest iteration report is saved

### 12. Release-Specific Acceptance

- `PASS / FAIL`: release-candidate creation is still blocked until approval, or was created explicitly after approval
- `PASS / FAIL`: release candidate is immutable and traceable
- `PASS / FAIL`: no unresolved high/critical release blockers remain
- `PASS / FAIL`: no unresolved foreign frontend/runtime coupling remains
- `PASS / FAIL`: premium audit passed
- `PASS / FAIL`: final verdict is explicit
- `PASS / FAIL`: no unjustified screenshot/page-image fallback remains on normal text pages

## Minimum Acceptance Rules

### Exploratory Conversion

An exploratory conversion may be accepted for internal review only if:
- package integrity passes
- smoke checks mostly pass
- no content-safety rule was violated
- blockers are explicit
- output is clearly marked non-release

### Working Final Conversion

A working final conversion may be accepted into `final_epub/` only if:
- technical package integrity passes
- no hard regression exists versus accepted output
- quality-loop gate passes or explicitly rejects non-improving candidates without overwrite

### Release-Ready Conversion

A release-ready conversion is allowed only if:
- all required checks above pass
- all release-blocking tests pass
- premium score meets threshold
- premium audit passes
- final release verdict is `READY`

## Recording Template

For every reviewed conversion, record:
- publication id
- source PDF
- baseline EPUB
- final EPUB
- score 1-10
- premium target
- passed checks
- failed checks
- open issues
- open FX tasks
- final verdict:
  - `EXPLORATORY ONLY`
  - `WORKING FINAL`
  - `RELEASE READY`
