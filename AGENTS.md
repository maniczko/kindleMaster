# AGENTS

## Project Purpose

This repository exists to produce premium Kindle-quality EPUB outputs from PDF and/or EPUB input through a generic, regression-safe pipeline:

`PDF -> baseline EPUB -> EPUB remediation -> final EPUB for Kindle premium reading`

This repository must support at least:
- document-like publications
- book-like publications
- report-like publications
- magazine-like publications
- mixed-layout publications

The control plane is authoritative. Do not run ad hoc cleanup, conversion, or release work outside the repository control model.

## Source Of Truth

Authoritative execution files:
- `project_control/backlog.yaml`
- `project_control/orchestration.md`
- `project_control/agents.yaml`
- `project_control/input_registry.yaml`
- `project_control/publication_manifest.yaml`
- `project_control/issue_register.yaml`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/metrics.json`
- `project_control/status_board.md`
- `reports/`

## Absolute Content Safety Rules

- Never paraphrase publication content.
- Never silently change meaning.
- Never rewrite for style polish.
- Never assume that "looks better" means improved.
- Use deterministic logic first and AI-assisted logic second.
- Route uncertainty into `project_control/low_confidence_review_queue.yaml` instead of guessing.
- Editorial regressions count as failures even when technical cleanup improves.
- Do not add publication-specific, sample-specific, or word-specific remediation rules to production conversion logic.
- Do not special-case `publication_id`, known sample file names, or single known phrases in quality-affecting runtime code.

## Artifact Lifecycle Rules

Artifact classes:
- baseline outputs: `kindlemaster_runtime/output/baseline_epub/`
- intermediate outputs: temporary extracted working trees, analysis JSON, comparison reports, remediation evidence
- final working outputs: `kindlemaster_runtime/output/final_epub/`
- immutable release candidates: `kindlemaster_runtime/output/release_candidate/`
- proof artifacts: `kindlemaster_runtime/output/reports/` and `reports/`

Hard lifecycle rules:
- Baseline stages may write only baseline outputs.
- Remediation may mutate only temporary working trees and may pack only to final-working-output paths.
- Final working outputs must never be silently overwritten by earlier stages.
- Release candidates must be created only after the release gate passes and must never be mutated in place.
- No destructive regeneration of final artifacts from older sources is allowed.
- A quality-affecting task cannot be `DONE` unless the correct artifact class exists and the gate passed on the correct artifact class.

## Text-First Rules

- Normal article pages must default to reflowable text.
- Screenshot or page-image fallback is forbidden for normal article pages.
- Image fallback is allowed only for:
  - genuine illustrations
  - diagrams
  - advertisements
  - true non-reflowable layouts
  - pages proven unsafe to reconstruct as text
- Every page-image fallback must be explicitly classified, justified, and reported in conversion evidence.
- Any unjustified screenshot or page-image fallback on a normal text page is release-blocking.

## Task Completion Rules

No quality-affecting task may be `DONE` unless all are true:
- output exists
- issue register was updated if relevant
- metrics were updated if relevant
- the assigned gate passed
- evidence exists
- regressions were checked

Negative rules:
- "looks better" is not evidence
- "probably fixed" is not evidence
- changed file count is not evidence

## Release-Blocking Rules

- No `READY` without passing tests, premium audit, and release criteria.
- No quality-affecting task may be `DONE` without evidence, metrics where relevant, updated issues, and gate success.
- No `READY` while any high-severity or critical unresolved release blocker remains.
- No `READY` while required scenario tests are missing.
- No `READY` while unjustified screenshot or page-image fallback remains on normal text pages.
- No `READY` while unresolved foreign frontend or shared-runtime coupling remains.
- No `READY` while this repository still carries foreign product code or unrelated build logic.
- No `READY` while unresolved finalizer overwrite risk remains.
- No `READY` while final working output lacks authoritative manifest-backed release metadata.

## Project Isolation Rules

`KINDLE MASTER` must remain isolated from any foreign frontend or shared runtime.

Forbidden overlap:
- shared code paths
- shared config assumptions
- shared output folders
- shared build or conversion scripts
- shared CI/CD logic
- shared caches or temp output assumptions
- shared workspace/package linkage
- shared environment assumptions
- shared docs that imply co-execution
- shared release process assumptions

Any detected coupling must:
- be logged in `project_control/issue_register.yaml`
- create `FX-*` remediation when severity is high or critical
- remain release-blocking until resolved or explicitly scope-limited with evidence

Current directive:
- this repository root is the canonical `KINDLE MASTER` repository
- any previously co-located foreign frontend must remain outside this repository
- no external product path is part of this repository contract

## Autonomous Loop Rules

Always:
1. read `project_control/backlog.yaml`
1a. if present, treat `project_control/backlog_archive.yaml` as historical evidence only
2. pick the highest-priority `TODO` task whose dependencies are `DONE`
3. move it to `IN_PROGRESS`
4. execute it
5. update, in order:
   - `project_control/backlog.yaml`
   - `project_control/issue_register.yaml`
   - `project_control/metrics.json`
   - `project_control/low_confidence_review_queue.yaml`
   - `project_control/status_board.md`
   - current iteration report in `reports/`
6. run the assigned gate
7. move to `DONE` only on evidence-backed pass
8. on failure, move to `REVIEW` or `BLOCKED`, log issue(s), create `FX-*` when required
9. if verdict is `NOT READY`, generate continuation tasks and continue automatically

After `READY`:
- keep `project_control/backlog.yaml` short and active
- preserve older execution history in `project_control/backlog_archive.yaml`
- reopen active work only when new issues, regressions, or scope changes appear

## Quality Guardian Authority

- Only the `Quality Guardian / Release Gate Agent` may approve final quality-gate success.
- Only the `Release Readiness Agent` may emit final `READY / NOT READY` after evidence exists.
- The `Lead / Orchestrator Agent` cannot override a failed quality gate.

## Interruption Handling

If execution is interrupted:
- backlog, issue register, metrics, review queue, status board, and iteration report must still reflect the exact current state
- the next run must resume from repository control files without ambiguity
- no partial execution may be hidden behind stale status

## Protected Areas

Allowed Kindle Master work:
- `project_control/`
- `reports/`
- `docs/`
- `samples/`
- `kindlemaster_*.py`
- `kindle_semantic_cleanup.py`
- `kindlemaster_templates/`
- `kindlemaster_runtime/`
- `.codex/config.toml`
- dedicated Kindle Master tests and harnesses

Protected foreign areas:
- any external product path outside this repository
- any external frontend runtime or deployment config

Do not use Vite or any external frontend localhost output as proof that Kindle Master works.

## Role Authority Summary

Primary roles:
- Lead / Orchestrator Agent
- Input & Intake Agent
- Cross-Project Boundary Agent
- PDF Analysis Agent
- Baseline Conversion Strategy Agent
- EPUB Analysis Agent
- Text Cleanup Agent
- Structure & Semantics Agent
- TOC & Navigation Agent
- Image & Layout Agent
- CSS / Kindle UX Agent
- Quality Guardian / Release Gate Agent
- QA / Regression Agent
- Release Readiness Agent
