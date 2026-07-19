# Issue #327 — Blocking semantic integrity gate

## Purpose

Prevent final Chess Reader/EPUB-facing output from being written when the semantic publication contract is unsafe, even when ZIP/XML packaging would otherwise be technically valid.

## Modes

| Mode | Reports findings | Blocks final write | Source-bound FEN/TOC/metadata required |
|---|---:|---:|---:|
| `development` | yes | no | no |
| `strict` | yes | on semantic P0 | no, unless supplied evidence fails |
| `release` | yes | on any P0 | yes |

Exit code is `0` for a passing gate and `1` for strict/release blockers. Development always preserves a zero exit code while still recording the same findings.

## Invariants

The gate checks:

1. semantic book and canonical exercises exist;
2. exercise IDs and navigation targets are unique;
3. normalized titles and diagram identities exist;
4. each exercise has one canonical solution;
5. reconciliation is `exact` or `normalized` and not production-blocked;
6. solution integrity is not strict-blocked;
7. bidirectional navigation is accepted and both link labels contain the printed number;
8. page blocks do not reference orphan exercise identities;
9. HTML/XHTML anchors and relative fragments resolve using the canonical validator from #326;
10. chess move notation is not rendered as ordered/unordered HTML list items;
11. release counts match a source-bound expectation file;
12. release metadata contains title, language and identifier;
13. release TOC evidence is approved;
14. published FEN values are accepted;
15. the existing fixed-edition FEN gate from #295 reports `passed`, zero false acceptance and full published coverage;
16. every non-error warning is explicitly allowlisted.

The fragment check directly reuses `validate_internal_links()` from #326 rather than maintaining a weaker parallel resolver. Regression coverage includes links between sibling `exercises/` and `solutions/` directories using `../` paths, duplicate anchors and orphan fragments.

## Default warning allowlist

- `SHORT_SOLUTION_REVIEW`
- `LEADING_COMMENTARY_BEFORE_FIRST_MOVE`
- `SOLUTION_IDENTITY_REASSIGNED`

Additional warnings must be supplied explicitly with `--allow-warning CODE`. Unknown warnings are not silently accepted in strict/release modes.

## Evidence fields

Every finding contains:

- stable code;
- message and severity;
- P0/blocking flag;
- exercise ID and printed number;
- source page;
- bounding-box coordinates when available;
- originating report or document.

JSON and Markdown evidence are written under `reports/chess_reader/semantic_release_gate.*` for automatic rendering, or a caller-selected reports directory for CLI validation.

## Automatic hook

`render_semantic_source_reader()` and source-HTML rebuild run the gate before writing `index.html`, `styles.css`, or `app.js`.

- failed strict/release validation returns `blocked_before_write=true`;
- diagnostic JSON/Markdown remains available;
- no new final reader files are written;
- `strict_thresholds` selects strict mode in the full chess-study pipeline;
- `KINDLEMASTER_CHESS_INTEGRITY_MODE` can select `development`, `strict`, or `release` for direct renderer calls.

## CLI

```bash
python kindlemaster.py chess-release-gate reports/chess_reader/semantic_book.json \
  --mode release \
  --expected-counts-json private/woodpecker_expected_counts.json \
  --metadata-json reports/publication_metadata.json \
  --toc-report-json reports/toc_approval.json \
  --fen-release-report-json reports/chess_fen/fixed_edition_acceptance.json \
  --documents-root output/epub/EPUB \
  --reports-dir reports/chess_reader
```

## Source-bound limitation

Synthetic and legal fixtures prove validator behavior and CI portability. A real Woodpecker release cannot pass `release` mode until the protected fixed-edition expected counts, TOC approval and #295 FEN acceptance evidence are supplied. Missing private evidence is reported as a blocker; it is never converted into a false pass.
