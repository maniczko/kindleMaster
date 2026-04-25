# KindleMaster Agent Operating Manual

This file defines how AI agents should work inside `kindleMaster`.

The goal is not just to "make something pass", but to improve PDF→EPUB quality in a way that is:
- production-safe,
- generic across publication classes,
- auditable,
- easy to extend,
- aligned with premium Kindle reading quality.

If a local instruction, developer instruction, or system instruction is stricter than this file, follow the stricter instruction.

## 1. Project Goal

KindleMaster is a local-first PDF→EPUB and DOCX→EPUB conversion system focused on premium reflowable EPUB output for Kindle-like reading.

The project exists to:
- detect publication type before conversion,
- choose the right conversion route for books, magazines, training books, scans, and diagram-heavy inputs,
- preserve reading order and structure,
- rebuild EPUB semantics after noisy PDF extraction,
- improve typography, navigation, metadata, references, and language consistency,
- generate output that feels like a deliberate EPUB publication, not a rough PDF export.

## 2. Architecture in 16 Points

Use this mental model before changing anything:

1. `app.py`
   Local Flask UI and HTTP API. Handles `/`, `/analyze`, `/convert`, async `/convert/start|status|quality|download`, and sets response headers used by the browser UI.

2. `converter.py`
   Main runtime orchestrator for PDF/DOCX→EPUB conversion. This is the highest-risk integration point.

3. `docx_conversion.py`
   DOCX structure parser. Extracts metadata, headings, lists, tables, hyperlinks, and images into the shared publication model.

3. `publication_analysis.py`
   Detects document class and drives route selection.

4. `publication_pipeline.py`
   Builds the publication-aware intermediate representation and quality report.

5. `publication_model.py`
   Holds the document/report structures used across analysis and pipeline stages.

6. `premium_reflow.py`
   Main book-oriented and premium reflow extraction path.

7. `magazine_kindle_reflow.py`
   Magazine/editorial route with layout-aware heuristics.

8. `pymupdf_chess_extractor.py`
   Handles chess/training-book extraction and image/problem-specific logic.

9. `text_normalization.py`
   Wrapper/compatibility layer for EPUB text cleanup and normalization.

10. `text_cleanup_engine.py`
    Scored text cleanup engine for split words, glued tokens, PL/EN artifacts, and risk-controlled fixes.

11. `kindle_semantic_cleanup.py`
    Final semantic EPUB cleanup: headings, lists, tables, metadata normalization, language synchronization, spine/nav alignment, and many cross-cutting EPUB repairs.

12. `epub_reference_repair.py`
    Canonical post-pass for bibliography/reference reconstruction and URL recovery. This should be the single source of truth for reference repair behavior.

13. `epub_heading_repair.py`
    Canonical post-pass for heading hierarchy, anchors, and TOC rebuild.

14. `epub_quality_recovery.py`
    Release-style EPUB recovery pipeline and artifact/report generator.

15. `premium_corpus_smoke.py`
    Multi-document regression runner used to verify generalization across publication classes.

16. `premium_tools.py`
    Shared toolchain helpers such as EPUBCheck and environment/tool availability.

## 3. Core Engineering Principles

Always optimize for:
- generic solutions over one-off fixes,
- DOM-aware EPUB repair over blind string replacement,
- explicit quality gates over implicit optimism,
- conservative behavior for ambiguous data,
- strong reporting and traceability,
- readability and maintainability over clever hacks.

Never treat "EPUB opens" as sufficient proof of quality.

## 4. What Must Not Be Broken

These are protected system invariants:

- The `/convert` endpoint must continue to return a valid EPUB response or a clear JSON error.
- The browser contract in `app.py` response headers must not break without deliberate API coordination.
- EPUB manifest, spine, nav, anchors, IDs, and XHTML validity must remain intact after repairs.
- Text cleanup must not silently damage URLs, anchors, DOM IDs, code-like fragments, or metadata.
- Heading/TOC repair must not introduce fake headings from layout artifacts.
- Reference repair must not invent URLs when confidence is low.
- Release/audit scripts must continue to produce machine-readable outputs.

## 5. What Agents Must Not Touch Blindly

Do not blindly modify or delete:

- `output/`, `reports/`, `tmp_*`, generated EPUBs, and log files as if they were source of truth,
- historical fixtures in `example/`,
- package/nav/spine logic without regression tests,
- response headers in `app.py` without validating UI behavior,
- shared helper behavior in `kindle_semantic_cleanup.py` without checking downstream tests,
- identifiers and anchors just to "clean things up",
- metadata fallbacks in a way that hardcodes specific publications.

Do not add publication-specific hacks such as:
- hardcoded title/author exceptions,
- brand-specific string special cases,
- heuristics that only match one sample corpus,
- assumptions that a known sample structure is universal.

If a fix only works because the file is "BABOK", "Allegro", or "Woodpecker", it is not done.

## 6. Allowed Sources of Truth

When designing or fixing behavior, prefer this order:

1. actual source code in the repo,
2. stable runtime contracts already used by the app,
3. existing tests,
4. representative real EPUB/PDF fixtures,
5. generated reports,
6. temporary manual artifacts.

Do not reverse that order.

## 7. How to Run the Project

### Local setup

```powershell
python kindlemaster.py bootstrap
```

### Run the local app

```powershell
python kindlemaster.py serve
```

Default URL:

```text
http://kindlemaster.localhost:5001/
```

Loopback bind safety remains on `127.0.0.1:5001`; use that fallback address if a local tool does not resolve the branded hostname.

The port may be overridden via `PORT`, but `5001` is the default and should be treated as the main local verification target.

### Useful CLI flows

Release recovery:

```powershell
python epub_quality_recovery.py path\to\input.epub
```

Reference repair:

```powershell
python epub_reference_repair.py path\to\input.epub --output-dir output --reports-dir reports --language pl
```

Heading/TOC repair:

```powershell
python epub_heading_repair.py path\to\input.epub --output-dir output --reports-dir reports --language pl
```

Corpus smoke:

```powershell
python kindlemaster.py smoke --mode quick
```

Validator sweep:

```powershell
python kindlemaster.py validate reference_inputs\epub\scan_probe.epub
```

## 8. Local Server Freshness

After changing any backend conversion code or server code, always restart the local app server before claiming the change is available at `http://kindlemaster.localhost:5001/`.

Treat at least these files as restart-sensitive:
- `app.py`
- `converter.py`
- `kindle_semantic_cleanup.py`
- `publication_pipeline.py`
- `publication_analysis.py`
- `magazine_kindle_reflow.py`
- `premium_reflow.py`
- `pymupdf_chess_extractor.py`

After restart, verify all of the following:
- the process is listening on port `5001`,
- `GET /` returns `HTTP 200`,
- the server process start time is newer than the latest modification time of the restart-sensitive files.

Do not tell the user that the newest version is live until those checks pass.

## 9. Change Strategy: Work in Small Steps

For non-trivial work, agents must split execution into small stages:

1. Diagnose the failure or gap.
2. Identify the real owning layer.
3. Define the smallest safe change set.
4. Implement one coherent slice.
5. Run the minimum targeted tests.
6. Run broader regression if shared code was touched.
7. Only then integrate into runtime flow.
8. Only then restart localhost if restart-sensitive files changed.
9. Report result, risks, and next likely improvement.

Do not jump directly from "bug report" to "large rewrite" without proving the root cause.

For tracked engineering work, use the standard workflow in Section 35.

## 10. When to Ask vs When to Act

### Act without asking when:
- the expected technical direction is clear from repo context,
- the change is local and reversible,
- there is an existing pattern in the codebase,
- the user asked for implementation rather than ideation,
- the main risk is technical and can be covered by tests.

### Ask before proceeding when:
- the user intent changes product behavior in a non-obvious way,
- metadata/business values are ambiguous and cannot be inferred safely,
- there are two materially different solution paths with visible tradeoffs,
- the change would remove or invalidate existing output formats or reports,
- a fix would require publication-specific exceptions,
- there is evidence that user-authored local changes conflict with the planned edit.

When asking, ask one concise, decision-oriented question. Do not dump open-ended uncertainty back on the user.

## 11. Test Policy

Agents must run tests after implementation unless the change is strictly documentation-only.

### Minimum rule

Run the smallest relevant targeted tests first.

### Expanded rule

If you touch shared runtime orchestration or shared cleanup helpers, also run broader regression.

## 12. Test Matrix by Change Area

### A. `app.py` or web/runtime integration

Run:

```powershell
python -m unittest test_app_docx_conversion.py test_app_heading_repair.py test_converter_text_cleanup.py test_release_quality_recovery.py test_epub_quality_recovery.py
```

Then restart localhost and verify port `5001`.

### B. `converter.py`

Run:

```powershell
python -m unittest test_docx_conversion.py test_converter_text_cleanup.py test_release_quality_recovery.py test_epub_quality_recovery.py
```

If reference or heading integration changed, include the relevant repair tests too.

### C. `epub_reference_repair.py`

Run:

```powershell
python -m unittest test_epub_reference_repair.py test_converter_text_cleanup.py
```

If shared URL/reference helpers were touched in `kindle_semantic_cleanup.py`, also run:

```powershell
python -m unittest test_semantic_epub_cleanup.py
```

### D. `epub_heading_repair.py`

Run:

```powershell
python -m unittest test_epub_heading_repair.py test_toc_segmentation.py
```

If it depends on shared cleanup helpers, also run:

```powershell
python -m unittest test_semantic_epub_cleanup.py
```

### E. `text_cleanup_engine.py` or `text_normalization.py`

Run:

```powershell
python -m unittest test_text_normalization.py test_converter_text_cleanup.py
```

If shared helpers changed, add:

```powershell
python -m unittest test_semantic_epub_cleanup.py
```

### F. `kindle_semantic_cleanup.py`

Run:

```powershell
python -m unittest test_semantic_epub_cleanup.py test_toc_segmentation.py test_text_normalization.py test_converter_text_cleanup.py
```

If headings or references are affected, also run:

```powershell
python -m unittest test_epub_reference_repair.py test_epub_heading_repair.py
```

### G. `publication_pipeline.py`, `publication_analysis.py`, `premium_reflow.py`, `magazine_kindle_reflow.py`

Run:

```powershell
python -m unittest test_release_quality_recovery.py test_epub_quality_recovery.py test_premium_corpus_smoke.py
```

### H. Corpus/regression logic

Run:

```powershell
python -m unittest test_premium_corpus_smoke.py
```

If the change affects conversion output across publication classes, also run the smoke CLI if time allows.

## 13. Smoke Expectations

When a bug was reported against a real PDF/EPUB, do not stop at unit tests.

Try to run at least one relevant smoke path:
- standalone repair module,
- runtime `finalize_epub_bytes(...)`,
- or full `/convert` flow when practical.

When a fix claims to be generic, verify it against more than one class of document when possible.

## 14. Reporting Format

Agent responses after work should use:

- `PLAN`
- `IMPLEMENTATION`
- `REVIEW`

### In `PLAN`
- say what will be changed,
- state the owning layer,
- state the first verification step.

### In `IMPLEMENTATION`
- summarize the actual code changes,
- reference key files,
- avoid low-signal file-by-file changelog noise.

### In `REVIEW`
- list tests run,
- state smoke/manual verification,
- identify the main remaining weakness honestly,
- explicitly say if something is still uncertain.

## 15. Minimum Result Reporting Standard

After implementation, always report:
- what changed,
- what was tested,
- whether tests passed,
- whether localhost was restarted if required,
- what still looks weak or incomplete.

Do not say "done" without the verification story.

## 16. Quality Gates for Agents

The change is not done if any of these is true:
- tests were skipped without saying so,
- a publication-specific hack was added,
- the output is only technically valid but still visibly broken,
- a local wrapper was fixed but the real runtime path was not,
- the browser/server freshness claim was made without restart verification,
- the report hides remaining ambiguity.

## 17. Publication-Generic Design Rules

All fixes must be designed to generalize across publication classes:
- business reports,
- magazines,
- training books,
- dense handbooks,
- scan/OCR-heavy PDFs,
- PL, EN, and mixed PL/EN content.

Before finalizing, ask yourself:
- does this rely on one sample title or brand?
- does it assume one chapter naming style?
- does it require one specific PDF artifact pattern?
- would this still make sense for another class in the corpus?

If the answer is "no", it likely needs another pass.

## 18. Safe Defaults for Uncertainty

When confidence is low:
- prefer review flags over hallucinated structure,
- prefer keeping valid existing EPUB semantics over rewriting them,
- prefer omitting an uncertain rebuilt artifact from final visible output rather than showing technical junk,
- prefer report-only unresolved cases over fake certainty.

## 19. Enterprise Documentation Rule

If a repeated class of mistake appears more than once, do not only patch code.
Also update:
- tests,
- reports,
- and this operational guidance when needed.

The system should improve not only its output, but also its execution discipline.

## 20. Definition of Done

A task is done only when all applicable conditions are true:

- the owning layer was identified correctly,
- the change is implemented in the real runtime path, not only in a side wrapper or standalone script,
- relevant tests were run and reported,
- at least one appropriate smoke verification was performed for user-visible fixes,
- no new publication-specific hacks were introduced,
- regressions against protected EPUB invariants were checked,
- residual ambiguity is explicitly documented,
- localhost was restarted and verified if restart-sensitive files changed,
- the final report includes result, evidence, and known weakness.

For high-risk tasks, "done" additionally requires:
- cross-module regression coverage,
- a clear rollback path,
- and evidence that the fix generalizes beyond one fixture when practical.

## 21. Severity and Priority Model

Use this model when triaging work, deciding test breadth, and reporting risk.

### Severity

`S0`:
- catastrophic breakage,
- app cannot convert,
- EPUB output is unusable,
- data loss or structural corruption.

`S1`:
- major release blocker,
- EPUBCheck fail in final output,
- broken nav/spine/manifest,
- visibly corrupted references, headings, or metadata in a way that invalidates deliverables.

`S2`:
- serious quality issue,
- output opens but feels non-final,
- wrong TOC, noisy cleanup, wrong metadata, degraded bibliography, or layout/reading-order problems.

`S3`:
- moderate issue,
- local quality regression,
- bad heuristics in a subset of files,
- report inconsistency,
- weak but non-blocking UX in UI or CLI.

`S4`:
- minor issue,
- documentation gaps,
- cosmetic logging/reporting issues,
- low-risk cleanup.

### Priority

`P0`:
- must be handled immediately,
- blocks release or active user validation.

`P1`:
- next critical quality improvement,
- should be handled before adjacent enhancements.

`P2`:
- important but schedulable,
- improves robustness, maintainability, or corpus coverage.

`P3`:
- backlog or opportunistic improvement.

Default mapping:
- `S0/S1 -> P0 or P1`
- `S2 -> P1 or P2`
- `S3 -> P2`
- `S4 -> P3`

## 22. Rollback Policy

Agents must always preserve a clear rollback story.

### Safe rollback principles

- Prefer scoped changes in a small number of files.
- Do not mix unrelated refactors with bug fixes.
- Do not silently remove old behavior unless the new path is fully integrated and tested.
- When replacing a runtime path, keep the prior summary contract or map it forward explicitly.

### Required rollback thinking

Before making a high-risk change, know:
- what files changed,
- what contract changed,
- what symptoms would indicate rollback is needed,
- what the smallest revert unit is.

### Rollback triggers

Treat these as rollback-class failures:
- final runtime path breaks `/convert`,
- EPUBCheck regressions appear across multiple fixtures,
- browser/UI contract breaks,
- nav/spine/manifest integrity breaks,
- reference/heading/text repairs improve one sample but regress across corpus,
- test coverage can no longer explain output behavior.

### Rollback behavior

- Stop feature expansion.
- Restore the smallest known-good behavior.
- Preserve diagnostics and reports.
- Document what failed and what evidence triggered rollback.

## 23. Release Gates by Environment

Use different expectations depending on execution context.

### Local development gate

Minimum expectation:
- targeted tests pass,
- no obvious runtime crash,
- smoke check on affected path,
- localhost freshness verified if needed.

### Local release-candidate gate

Expected:
- targeted tests pass,
- broader shared-module regression passes,
- relevant smoke on real fixture passes,
- no visible technical junk in final output,
- EPUBCheck status understood and reported.

### Corpus gate

Expected for generic quality claims:
- representative corpus class coverage,
- no evidence of overfitting to one sample,
- no new blocker in unrelated publication classes,
- output quality trend not worse than baseline.

### Final release gate

Release-ready means:
- no `S0` or `S1` open,
- real runtime path fixed,
- outputs are technically valid or any remaining validator issue is explicitly known and accepted,
- user-visible artifact quality is acceptable for the intended publication class,
- report contains blockers, warnings, and recommendation.

## 24. Ownership Map by Module

This is the default ownership map agents should use when locating the real fix.

- `app.py`
  HTTP/UI integration, upload/download behavior, response headers, localhost workflow.

- `converter.py`
  top-level runtime orchestration and post-processing order.

- `publication_analysis.py`
  document classification and route selection.

- `publication_pipeline.py`
  publication assembly and quality report shaping.

- `publication_model.py`
  shared data structures and report schema.

- `premium_reflow.py`
  book-like premium extraction and book/chess-specific reflow.

- `magazine_kindle_reflow.py`
  magazine/editorial extraction and layout-heavy content handling.

- `pymupdf_chess_extractor.py`
  chess/training diagrams and problem-solution structure.

- `text_cleanup_engine.py`
  scored textual cleanup and safe/review/blocked decisions.

- `text_normalization.py`
  compatibility wrapper and text-cleanup integration surface.

- `kindle_semantic_cleanup.py`
  shared EPUB semantic cleanup, metadata/package/nav/spine normalization, list/table/reference-adjacent helpers.

- `epub_reference_repair.py`
  canonical section-level reference reconstruction and link recovery.

- `epub_heading_repair.py`
  canonical heading hierarchy, anchors, and TOC recovery.

- `epub_quality_recovery.py`
  release recovery pipeline and publication-readiness reporting.

- `premium_corpus_smoke.py`
  corpus-level generalization verification.

If a bug spans modules, identify:
- source layer,
- integration layer,
- verification layer.

Do not fix only the verification layer when the source layer is wrong.

## 25. Incident and Regression Playbook

Use this playbook when a bug keeps coming back, a fix “works” but user-visible output stays bad, or one sample improves while another regresses.

### Step 1. Reproduce on the real path

Always verify whether the failure is in:
- standalone tool only,
- runtime `finalize_epub_bytes(...)`,
- web `/convert`,
- release pipeline,
- or corpus smoke.

If two paths disagree, fix the contract split first.

### Step 2. Identify root cause class

Classify the issue:
- routing/integration mismatch,
- parser/model defect,
- scoring/heuristic defect,
- package/EPUB integrity defect,
- report/metric defect,
- fixture-only false confidence,
- UI/reporting mismatch.

### Step 3. Contain blast radius

- isolate affected modules,
- avoid broad rewrites until the owning layer is proven,
- add or update a failing regression test before large changes when feasible.

### Step 4. Repair smallest real owning layer

Examples:
- wrong `/convert` flow -> fix `app.py` or `converter.py`,
- wrong bibliography assembly -> fix `epub_reference_repair.py`,
- wrong TOC output -> fix `epub_heading_repair.py`,
- wrong metadata/package -> fix package/semantic layer.

### Step 5. Prove the fix

At minimum:
- targeted test,
- broader regression if shared code touched,
- one smoke on a real affected fixture.

For recurring bugs:
- add corpus-level evidence.

### Step 6. Report honestly

Every incident/regression report should say:
- what failed,
- where the real owning layer was,
- what changed,
- how it was tested,
- what still remains weak.

For standard execution, record those answers through the reproduce -> isolate -> fix -> validate -> compare workflow in Section 35.

## 26. Manual Review Queue Policy

Manual review is allowed, but it must be disciplined.

Use manual review when:
- confidence is below threshold,
- bibliographic/heading identity is ambiguous,
- metadata business values are uncertain,
- OCR noise prevents safe reconstruction,
- multiple valid structural interpretations exist.

Manual review must not be used as an excuse to:
- ship visible technical junk in final output,
- avoid fixing an obvious deterministic corruption,
- claim a publication is release-ready when the artifact is still visibly broken.

## 27. Dependency and Bootstrap Standard

The standard bootstrap path for this repo is:

```powershell
python kindlemaster.py bootstrap
```

Rules:
- `requirements.txt` is for runtime and first-class operational tooling.
- `requirements-dev.txt` is for build, test, and verification extras.
- Do not ask users to install packages one by one if the bootstrap contract can be updated instead.
- If a validator, smoke runner, or enterprise workflow becomes standard, its dependencies must be captured in the requirements files.

## 28. EPUB Validator Standard

KindleMaster validation is layered and must not rely on `EPUBCheck` alone.

Standard command:

```powershell
python kindlemaster.py validate path\to\file.epub
```

Validation layers:
- ZIP/container integrity,
- OPF/container/nav presence,
- manifest and spine consistency,
- internal href and fragment integrity,
- external URL syntax quality,
- `EPUBCheck`.

Do not call an EPUB healthy if structural or link validators still fail even when `EPUBCheck` passes.

## 29. Reference Inputs and Smoke Standard

Curated smoke inputs live under `reference_inputs/` and are populated by:

```powershell
python kindlemaster.py prepare-reference-inputs
```

Rules:
- smoke inputs must represent classes of documents, not favorite samples,
- include both PDF/EPUB and DOCX classes when the runtime supports them,
- the manifest must describe document class, input type, language, and smoke suitability,
- quick smoke should stay fast enough for regular use,
- full smoke should cover broader classes before claiming generic improvement.

Standard smoke command:

```powershell
python kindlemaster.py smoke --mode quick
```

DOCX smoke is part of the standard corpus. At minimum keep one quick DOCX fixture and one richer DOCX fixture with tables/lists/images.

Standard corpus-wide proof command:

```powershell
python kindlemaster.py test --suite corpus
```

This lane must persist derived corpus reports under:
- `reports/corpus/`
- `output/corpus/`

## 30. Standard Project Entrypoint

The standard operational entrypoint for this repo is:

```powershell
python kindlemaster.py <command>
```

Supported first-class commands:
- `bootstrap`
- `doctor`
- `prepare-reference-inputs`
- `serve`
- `convert`
- `validate`
- `smoke`
- `corpus`
- `status`
- `test`
- `audit`
- `workflow` with `baseline` and `verify` subcommands

Do not create new parallel top-level entrypoints for routine project operation unless there is a strong architectural reason.

## 30A. Control-Plane Source-of-Truth Matrix

This section is the canonical authority map for KindleMaster control-plane artifacts. If two docs disagree, follow the authoritative source listed here first, then update the derived mirrors in the same change.

| Artifact or question | Authoritative source | Derived or mirror sources | Notes |
| --- | --- | --- | --- |
| Available CLI commands, subcommands, flags, defaults, and exit behavior | `kindlemaster.py` parser and helpers | `AGENTS.md`, `README.md`, `.codex/config.toml` comments, `.codex/README.md`, `test_kindlemaster_entrypoint.py` | Treat the executable parser as runtime truth. Human-facing docs must mirror it, not redefine it. |
| Standard first-class commands that collaborators should use by default | `AGENTS.md` Section 30 | `README.md`, `.codex/config.toml`, `.codex/README.md` | Keep this list synchronized with `kindlemaster.py`. |
| Supported local toolchains and lane expectations for `python kindlemaster.py test --suite ...` | `docs/toolchain-matrix.md` | `README.md`, `.codex/config.toml` comments | This owns toolchain support expectations only, not the full command contract. |
| Workflow artifact paths, required filenames, and report-completeness contract | `AGENTS.md` Sections 31 and 35, implemented by `workflow_runner.py` | `README.md`, generated workflow reports under `reports/workflows/<run_id>/` and `output/workflows/<run_id>/` | Generated JSON and Markdown outputs are evidence and runtime artifacts, not the normative contract. |
| Derived project status summary | `scripts/generate_project_status.py` invoked through `python kindlemaster.py status` | `README.md`, generated `reports/project_status.json` and `reports/project_status.md` | This is a derived view over existing evidence artifacts and must not become a hand-maintained status surface. |
| Repo-local Codex settings | `.codex/config.toml` | `.codex/README.md`, `AGENTS.md` Section 34 | Only active TOML keys in `.codex/config.toml` control Codex behavior. Comments there are convenience mirrors. |
| Generated EPUBs, reports, smoke outputs, temporary files, and manual notes | Runtime outputs under `output/`, `reports/`, and local temp paths | README references and ad hoc inspection notes | These are always derived artifacts and must never become the source of truth for command or governance policy. |

When changing control-plane behavior:
- update the authoritative source first,
- update every derived mirror named in this matrix in the same change when it is affected,
- add or update a command-surface or governance-alignment test when drift is plausible.

## 31. Artifact Retention and Naming Policy

Use these locations consistently:
- `output/` for generated EPUB artifacts,
- `reports/` for JSON and Markdown audit outputs,
- `output/smoke/` and `reports/smoke/` for smoke runs,
- `output/corpus/` and `reports/corpus/` for derived corpus-wide proof runs,
- `reports/project_status.json` and `reports/project_status.md` for the derived project status view,
- `reports/validators/` for validator output,
- `reference_inputs/` for curated source fixtures.

Rules:
- do not mix canonical reference inputs with generated output,
- keep machine-readable and human-readable reports together,
- use deterministic names where possible,
- generated artifacts in these locations are derived evidence, not normative definitions of workflow behavior,
- if a report is part of a standard workflow, document its path in this file or the owning script.

## 32. Workflow Ownership and Approval Matrix

Default ownership by workflow:
- bootstrap and tooling: `requirements*.txt`, `kindlemaster.py`, `premium_tools.py`, `scripts/`
- conversion runtime: `app.py`, `converter.py`, `publication_*`, reflow modules
- post-processing: `text_cleanup_engine.py`, `kindle_semantic_cleanup.py`, `epub_reference_repair.py`, `epub_heading_repair.py`
- release and audit: `epub_quality_recovery.py`, `premium_corpus_smoke.py`, validators, smoke reports

Approval expectations:
- agents may act without asking for local, reversible, well-tested workflow improvements,
- agents must ask before changing business metadata defaults, user-visible output contracts, or removing established report artifacts,
- agents must not replace one canonical workflow with another without updating this document and the standard entrypoint.

## 33. Anti-Patterns

Avoid these failure patterns:

- fixing only a standalone CLI while web `/convert` still uses old logic,
- using EPUBCheck pass as a proxy for editorial quality,
- reporting “records reconstructed” when title/link mapping is still wrong,
- silently renumbering IDs without proof,
- merging unrelated fixes in one patch,
- touching shared helpers without widening regression coverage,
- equating one fixture success with generalization,
- leaving technical placeholders visible in user-facing EPUB output,
- saying “latest version is live” without localhost freshness checks.

## 34. Codex Project Config Standard

KindleMaster must keep repo-local Codex behavior in:

```text
.codex/config.toml
```

This file complements, but does not replace:

```text
~/.codex/config.toml
```

Rules:
- put KindleMaster-specific defaults in `.codex/config.toml`, not only in global config,
- keep only supported Codex config keys as active TOML settings,
- keep repo-standard commands and restrictions documented in `.codex/config.toml` comments and synchronized with this file,
- if a repo default changes, update `.codex/config.toml`, `README.md`, and `AGENTS.md` together,
- do not store publication-specific logic or sample-specific exceptions in project config,
- do not rely on personal global config to make the repo usable for the next session or collaborator.

At minimum, the project config should define or document:
- preferred model,
- reasoning effort,
- approval policy,
- repo-relevant tools and integrations,
- standard bootstrap, test, smoke, validate, and audit commands,
- repo-specific constraints such as localhost freshness and anti-overfitting rules.

Current repo-local Codex defaults:
- model: `gpt-5.5`
- reasoning effort: `xhigh`
- approval policy: `on-request`
- multi-agent support: enabled
- browser/runtime verification support: Browser Use plugin and pinned Playwright MCP

Keep MCP and plugin entries deterministic. Do not use floating MCP versions such as `@latest`; pin versions and update them deliberately with a small verification pass.

## 35. Standard Engineering Workflow

KindleMaster standardizes engineering work as:

```text
reproduce -> isolate -> fix -> validate -> compare before/after
```

This workflow is mandatory for runtime, cleanup, validator, and regression work that is large enough to require a tracked technical fix.

Use the standard wrapper:

```powershell
python kindlemaster.py workflow baseline path\to\input.pdf --change-area reference
python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>
```

The same workflow also applies to DOCX inputs:

```powershell
python kindlemaster.py workflow baseline path\to\input.docx --change-area converter
python kindlemaster.py workflow verify path\to\input.docx --run-id <run_id>
```

Rules:
- do not start a large fix without a saved baseline,
- baseline must capture the same input that verify will re-run,
- `change_area` is chosen once at baseline and reused during verify,
- verify must execute the mapped regression pack, not an ad hoc subset,
- do not report success without generated before/after artifacts,
- if artifact names or locations change, update `workflow_runner.py`, this file, and the derived command-surface docs in the same change,
- if baseline, verify, regression, smoke, or compare is missing, the workflow is not complete.

Required workflow artifacts live under:
- `reports/workflows/<run_id>/`
- `output/workflows/<run_id>/`

Minimum required artifacts:
- `baseline.json`
- `baseline.md`
- `isolation.json`
- `verification.json`
- `verification.md`
- `before_after.json`
- `before_after.md`

Baseline must capture:
- input path and type,
- suspected owner layers,
- protected invariants,
- recommended targeted tests,
- recommended smoke path,
- baseline status and symptoms.

Verify must capture:
- same-input rerun result,
- regression pack result,
- smoke result,
- before/after delta for quality signals,
- remaining risks and unresolved warnings.
