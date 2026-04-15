# Phase R0-006: Text Cleanup Effectiveness Audit

Date: 2026-04-12
Owner: Text Cleanup
Gate: `GR0`

## Scope Verification

Cleanup did run globally over the final-remediation input:

- all `84` page XHTML files were rewritten
- `title.xhtml` was also rewritten

This was not a partial one-page cleanup.

## Measured Effectiveness

Baseline -> final on the active PDF sample:

- `split_word_count`: `3 -> 3`
- `joined_word_count`: `18 -> 18`
- `suspicious_paragraph_join_count`: `933 -> 651`

## What Improved

- some paragraph joining reduced obvious fragmentation

## What Did Not Improve

- split-word count did not materially improve
- joined-word count did not materially improve
- weak metadata remained unchanged

## Remaining Artifact Evidence

Final EPUB still contains unresolved PDF-style residues such as:

- `certifica tions`
- `hu man`
- `do tyczące`
- `proj ect`

These are visible in final XHTML content, which proves cleanup did not close key reading defects.

## Pipeline Observation

The cleanup code contains high-confidence hyphen repair and paragraph merge logic, but:

- it is not materially reducing the visible artifact classes that dominate the current sample
- there is no post-cleanup rescan inside the end-to-end runner proving effect before packaging final output

## Conclusion

Text cleanup is active but not effective enough on the final-remediation input. The pipeline currently rewrites structure more aggressively than it removes visible PDF extraction defects.
