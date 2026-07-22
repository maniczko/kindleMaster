# Issue 321 — truncated or incomplete chess solution detection

## Purpose

The solution-integrity gate detects structurally incomplete chess solutions before EPUB publication. It does not attempt to prove chess correctness or replace source review. Its purpose is to catch evidence that the first move or main line may have been lost during PDF extraction, OCR, column reconstruction, or exercise-solution pairing.

## Blocking evidence

Strict mode blocks release when a solution:

- is empty;
- starts inside a parenthesized variation;
- starts with continuation punctuation, a closing delimiter, or continuation prose;
- contains no recognizable first move;
- begins with SAN but omits the first move number;
- starts with the opposite side from the accepted diagram side-to-move;
- disagrees with an explicit expected first move number;
- contains unbalanced parenthesized variations.

A finding can have warning severity in the user-facing model while still blocking the strict release gate. This is intentional for `MISSING_FIRST_MOVE_NUMBER`: historical output remains reviewable, but production publication must not silently accept it.

## Warning-only evidence

The following signals require review but are not proof of truncation by themselves:

- commentary before the first numbered move;
- a very short solution containing zero or one recognized SAN token.

A one-move mate can be complete, and a book may introduce a solution with prose. These cases therefore remain warnings unless another blocking condition is present.

## Output contract

Each `ChessExercise` exposes `solution_integrity` containing:

- exercise ID and printed exercise number;
- exercise source page and solution page;
- the first 200 normalized characters;
- expected and detected side-to-move;
- detected first move number;
- numbered-move and SAN-token counts;
- status, strict-blocked flag, and structured findings.

The top-level semantic model exposes `solution_integrity` with accepted, warning and blocked counts, finding counts, and separate warning/strict exit codes.

Chess Reader records expose the integrity status and finding codes without duplicating the full diagnostic payload.

## Exit behavior

- `warning` mode always returns exit code `0`; findings remain visible in reports and review queues.
- `strict` mode returns exit code `1` when at least one strict-blocking finding exists.
- unsupported modes are rejected rather than silently treated as warning mode.

## False-positive policy

The detector is conservative:

- metadata can confirm a contradiction but cannot invent a missing move;
- side-to-move comparison is skipped when the accepted side is unknown;
- source text is never rewritten by this detector;
- suspiciously short solutions are never automatically classified as truncated;
- result-only strings are not treated as malformed move lines;
- existing `MISSING_SOLUTION` model behavior remains authoritative for completely absent solutions, avoiding duplicate user-facing warnings.

## Validation scope

Committed legal fixtures cover:

- complete white-to-move and black-to-move solutions;
- missing first move numbers;
- side-to-move mismatch;
- expected move-number mismatch;
- starts inside variations and continuation fragments;
- commentary before the first move;
- suspiciously short but potentially valid mates;
- unbalanced variations;
- required source context and 200-character excerpts;
- model serialization and Chess Reader propagation;
- compatibility with the existing semantic model, study pipeline, and notation reflow parser.

The dedicated workflow runs on Python 3.12 and 3.14 and retains full logs as CI evidence.

## Source-bound acceptance limitation

The known Woodpecker 224–227 cases cannot be honestly marked verified without the protected fixed-edition source and a source-bound expected manifest. Synthetic fixtures exercise the same failure classes, but they are not presented as reproductions of copyrighted source content.

Final acceptance requires:

1. running the detector against the protected fixed edition;
2. reviewing exercises 224–227 against their exact source pages and diagrams;
3. recording expected first move, side-to-move, source page, and decision in a source-bound manifest;
4. measuring detection precision and false positives across all 1,128 solution pairs;
5. retaining only safe diagnostics and hashes in the repository.
