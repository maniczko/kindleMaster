# KindleMaster Source-of-Truth Matrix

Related Linear scope: VAT-131 and VAT-132.

This document is the onboarding mirror for the control-plane source-of-truth model. `AGENTS.md` Section 30A remains the canonical human-readable authority map. This document explains how to use that model when updating docs, Linear, reports, and generated status.

## Authority Matrix

| Question | Authoritative source | Derived or mirror surfaces | Update rule |
| --- | --- | --- | --- |
| What CLI commands, flags, defaults, and exit codes exist? | `kindlemaster.py` | `README.md`, `.codex/README.md`, `.codex/config.toml`, `AGENTS.md` | Update mirrors only after the parser changes. |
| What should agents do by default? | `AGENTS.md` | `README.md`, `.codex/README.md`, this document | If agent workflow changes, update `AGENTS.md` first. |
| Which local tools are required, optional, degraded, or unsupported? | `docs/toolchain-matrix.md` plus `premium_tools.detect_toolchain()` | `docs/local-bootstrap-toolchain.md`, `README.md` | Keep expected behavior and detected behavior aligned. |
| How does PDF/DOCX become EPUB? | Runtime modules named in `docs/conversion-pipeline.md` | README summaries and issue descriptions | Do not document a stage that is not implemented. |
| What is the current project status? | `python kindlemaster.py status` via `scripts/generate_project_status.py` | `reports/project_status.json`, `reports/project_status.md`, Linear comments | Generated reports are derived from corpus/workflow/governance evidence. |
| What proves corpus/generalization quality? | `python kindlemaster.py test --suite corpus` and `reports/corpus/corpus_gate.json` | `reports/corpus/*.md`, Linear evidence comments | Corpus blockers must remain visible until fixed. |
| What proves one EPUB artifact is acceptable? | `python kindlemaster.py audit <epub>` and release/audit outputs | `docs/independent-audit-mode.md`, final task reports | Audit output is artifact truth, not whole-project truth. |
| What proves a tracked defect was handled safely? | `python kindlemaster.py workflow baseline/verify` | `reports/workflows/<run_id>/`, `output/workflows/<run_id>/` | Do not claim before/after proof without required workflow artifacts. |
| What is the task/backlog truth? | Linear issue state and comments | README/docs references and final reports | Code/docs evidence should be copied into Linear before moving status. |
| What is release readiness? | `docs/premium-epub-release-checklist.md` plus generated quality/corpus/status reports | Linear release comments | Checklist is a decision aid; machine reports remain evidence. |

## Status Surface Rules

- `reports/` and `output/` are evidence outputs. They are not policy.
- `reports/project_status.*` is generated and must not be hand-edited as a narrative status board.
- Linear status can say a task is done only after the repo has matching evidence commands and residual blockers are stated.
- If a status is red because of environment/toolchain unavailability, report it as environment/toolchain, not EPUB-quality failure.
- If a status is red because corpus output is invalid, report it as a product/release blocker.

## Drift Prevention

When changing command, toolchain, workflow, or release behavior:

1. Change the authoritative source first.
2. Update the mirrors listed above in the same change.
3. Add or update a docs/governance test when drift would be easy to miss.
4. Add a Linear comment with evidence if the change satisfies or blocks a tracked VAT.

## VAT Mapping

| VAT | This document supports it by |
| --- | --- |
| VAT-131 | Defining generated project status as derived evidence instead of a hand-maintained status surface. |
| VAT-132 | Giving each control-plane artifact an authority boundary and update owner. |
| VAT-176 | Pointing release readiness to the checklist and machine-readable evidence instead of ad hoc narrative. |
