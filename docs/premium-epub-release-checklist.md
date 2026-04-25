# Premium EPUB Release Checklist

Related Linear scope: VAT-176.

Use this checklist before claiming that a KindleMaster conversion change is release-ready. It is a decision checklist grounded in current reports; it is not a replacement for machine-readable evidence.

## Required Verdicts

| Verdict | Meaning | May close the task? |
| --- | --- | --- |
| `release_ready` | Required checks passed and no S0/S1 release blocker remains. | Yes, if Linear evidence is attached. |
| `review_ready` | Output is usable but has explicit manual-review or degraded-tooling notes. | Only if acceptance criteria allow review. |
| `blocked` | EPUB validity, corpus, runtime, or release evidence is failed/missing. | No. |

## Checklist

| Check | Evidence source | Pass condition | Blocks release when |
| --- | --- | --- | --- |
| EPUB validation | `python kindlemaster.py validate <epub>` or audit/release report | internal validators pass; EPUBCheck is pass or explicitly unavailable | structural validation fails, broken internal hrefs, missing package/nav/spine |
| EPUBCheck | audit/release report, `python kindlemaster.py doctor` | pass, or unavailable is disclosed as degraded validation | EPUBCheck fails on final output |
| TOC depth and anchors | heading/TOC report, audit report | nav entries are useful and anchors resolve | TOC is empty/shallow for structured book, or anchors are broken |
| Metadata | audit/release report | title, author, language, and description are correct or intentionally blank | incorrect business metadata is visible in output |
| Fallback mode | quality report, corpus report | fallback is absent or explicitly accepted | fallback hides failed premium route or produces visibly non-premium output |
| Visible OCR/text junk | manual review, text cleanup reports | no visible split/glued text or OCR artifacts in sampled output | technical junk remains in reader-visible text |
| Links/references | validator/reference report | internal links resolve; external URLs are syntactically sane | broken internal links or invented low-confidence references |
| Tables/lists/images/diagrams | targeted tests, audit, smoke output | required rich content is preserved and valid | core article/book content is missing or malformed |
| Output size/budget | size budget tests and reports | output stays within class policy or warning is accepted | size budget fails without accepted reason |
| Manual review queue | audit/release report | queue size is zero for release-ready, or accepted for review-ready | unresolved manual-review item is release blocking |
| Corpus confidence | `python kindlemaster.py test --suite corpus` and `reports/corpus/corpus_gate.json` | corpus gate passes for generic claims | corpus gate fails and the task claims generic release readiness |
| Project status | `python kindlemaster.py status` | `overall_status` is `passed` for full release claims | `overall_status` is `failed` |

## Standard Command Chain

For docs-only changes, run docs/governance tests. For conversion-quality changes, run:

```powershell
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite corpus
python kindlemaster.py test --suite release
python kindlemaster.py status
```

`release` is intentionally bounded and does not duplicate the full `quick` suite; keep `quick` in the command chain before claiming a full release-ready result. A `passed_with_warnings` release result is acceptable only for review-ready handoff, not for a clean release-ready claim.

For one generated EPUB artifact, add:

```powershell
python kindlemaster.py audit path\to\artifact.epub
```

For browser-visible quality reporting, add:

```powershell
python kindlemaster.py test --suite browser
python kindlemaster.py test --suite runtime
```

## Copy/Paste Final Report Block

```markdown
## Premium EPUB release checklist

- Verdict: `release_ready` / `review_ready` / `blocked`
- Quick suite: pass/fail/not run
- Corpus gate: pass/fail/not run
- Release suite: pass/fail/not run
- Project status: passed/passed_with_warnings/failed/not run
- Audit decision: pass/pass_with_review/fail/not run
- EPUBCheck: pass/fail/unavailable/not run
- TOC/anchors: pass/fail/review/not run
- Metadata: pass/fail/review/not run
- Fallback mode: none/accepted/blocking/not checked
- Manual-review count: 0 / N / not checked
- Residual blockers:
  - ...
```

## Current Automation Boundary

Current reports cover many checklist fields, but not every human-quality judgment is fully automated. Missing automation must be reported as `not run` or `review`, not silently treated as pass.
