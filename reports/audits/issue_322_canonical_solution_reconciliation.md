# Issue 322 — canonical exercise-solution reconciliation

## Decision rule

A solution is paired automatically only when all of the following are true:

1. the printed exercise number matches;
2. the normalized game title matches;
3. the relation is unique in both directions;
4. no second canonical candidate is within the ambiguity margin;
5. the aggregate evidence score reaches the configured automatic threshold.

Title normalization is case-, whitespace-, punctuation- and diacritic-insensitive. It also transliterates non-decomposing Latin characters such as `ł`, `ø`, `æ` and `œ` before comparison. Players, location, year, difficulty, neighboring numbers and source-page proximity increase the evidence score, but they never override a number or title mismatch.

## Safety behavior

- Fuzzy title similarity is reported as an alternative and never performs an automatic swap.
- A title mismatch is a production-blocking error even when the number matches.
- A solution cannot be claimed by two exercises.
- Duplicate canonical candidates remain ambiguous.
- Same-ID legacy pairing remains readable but is explicitly production-blocked when canonical number or title evidence is incomplete.
- A unique canonical match may safely repair swapped source identifiers and records the change in `correction_trace`.

## Machine-readable evidence

The semantic model includes `solution_reconciliation` with:

- match counts and confidence distribution;
- production-blocking status;
- canonical exercise and solution identities;
- per-exercise score, decision, selected solution, alternatives and blocking mismatches.

Each `ChessExercise` also exposes `solution_match` and reconciliation warnings.

## Validation scope

Committed fixtures cover:

- exact matches;
- normalization-only matches;
- title mismatches;
- high-similarity alternatives;
- duplicate/ambiguous candidates;
- one-to-one ownership;
- swapped solution identifiers;
- legacy incomplete identity;
- solution-only identifiers that must not create phantom exercises;
- Latin diacritic and non-decomposing-character normalization.

Full acceptance against all 1,128 Woodpecker pairs still requires the protected fixed-edition source and a verified source-bound manifest. The implementation does not claim full-book correctness until that acceptance run is completed.
