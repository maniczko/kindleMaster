# Independent EPUB Audit Mode

Related Linear scope: VAT-134.

Independent audit mode evaluates one EPUB artifact without relying on broader project status narration. Use it when the question is "is this EPUB artifact acceptable?" rather than "is the whole repository release-ready?"

## Command

```powershell
python kindlemaster.py audit path\to\book.epub --reports-dir reports --output-dir output
```

Optional metadata expectations can be supplied when they are known:

```powershell
python kindlemaster.py audit path\to\book.epub --language pl --title "Expected Title" --author "Expected Author"
```

The CLI delegates to `scripts/run_release_audit.py`, which runs `epub_quality_recovery.run_epub_publishing_quality_recovery(...)`.

## What It Checks

The audit is focused on the EPUB artifact and its quality evidence:

- package/container/nav/spine integrity,
- EPUB validation and optional EPUBCheck signal,
- metadata quality and expected metadata overrides,
- TOC/heading inventory,
- manual-review queue size,
- recovery/release recommendation,
- final artifact and report paths.

## What It Does Not Prove

- It does not prove all corpus fixtures pass.
- It does not prove browser/runtime upload flow works.
- It does not replace `python kindlemaster.py status`.
- It does not close a Linear issue unless the issue only asks for artifact-level audit evidence.

## When To Use Which Gate

| Need | Use |
| --- | --- |
| Evaluate one generated EPUB | `python kindlemaster.py audit <epub>` |
| Verify a code change against before/after evidence | `python kindlemaster.py workflow baseline/verify` |
| Verify fixture breadth and generalization | `python kindlemaster.py test --suite corpus` |
| Verify project-level release confidence | `python kindlemaster.py status` plus `docs/premium-epub-release-checklist.md` |

## Result Interpretation

| Audit decision | Meaning | Linear/final-report handling |
| --- | --- | --- |
| `pass` | Artifact-level gates passed without manual-review blockers. | Can support closing artifact-specific work if required tests also passed. |
| `pass_with_review` | Artifact is structurally usable but has review flags. | Keep issue open unless acceptance criteria allow manual review. |
| `fail` | Artifact-level blocker remains. | Do not close; add blocker details and next step. |

## Minimum Evidence To Copy Into A Task

- command run,
- exit status,
- final EPUB path,
- report path,
- decision,
- EPUBCheck status or unavailability,
- manual-review count,
- remaining blocker or review notes.
