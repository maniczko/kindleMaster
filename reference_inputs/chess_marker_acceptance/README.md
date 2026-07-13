# Fixed-edition chess marker acceptance corpus

The real Yusupov source, full pages, and verified labels are intentionally not
committed. The release gate only accepts a private manifest tied to the exact
PDF SHA256. Synthetic marker crops under `reference_inputs/chess_fen` remain
useful unit-test data, but can never satisfy this gate.

## Secure local pack

Set `KINDLEMASTER_CHESS_ACCEPTANCE_CORPUS_ROOT` to a directory outside git:

```text
<secure-root>/
  yusupov-fundamentals/
    manifest.json
    micro_crops/           # optional, minimal/anonymized marker crops only
```

The manifest must use
`kindlemaster.chess.marker_acceptance_manifest.v1`, identify the fixed source
with a 64-character SHA256, and carry human-verified rows for every expected
diagram. Each row must include the stable #258 fingerprint components, page,
chapter, page/chapter-separated split, marker status, side, ownership, crop
quality, source of truth, and fallback source. Holdout rows must set
`allowed_for_tuning` to `false`.

The manifest must also contain verified hard negatives for coordinates,
letters, borders, arrows, captions, and neighboring diagrams. No full PDF or
full page image belongs in this directory or in git.

## Run

```bash
python kindlemaster.py chess validate-side-markers \
  --source-profile yusupov-fundamentals \
  --job-output /secure/path/to/current-run
```

The job output may also be the safe ZIP created by #254 for diagnostics. For
closing evidence, the evidence pack must expose the exact source SHA and runtime
commit; the runtime commit must match the validator's local `main` commit.
Reports, including `corpus_unavailable` results, are written to
`reports/chess_fen/side_marker_acceptance/`.

When the private manifest is available, `python kindlemaster.py test --suite
quick` also requires `KINDLEMASTER_CHESS_ACCEPTANCE_JOB_OUTPUT` and enforces the
same gate. Without a private manifest, the gate reports `corpus_unavailable`
and never turns synthetic tests into a real-edition pass.

## Side-to-move evidence fusion

The runtime derives side-to-move evidence from independent sources and keeps
their provenance separate:

- a trusted visual marker remains the only `trusted_marker` source;
- explicit caption/OCR phrases such as `White to move` or `Ruch czarnych`
  become `text_inferred`;
- PGN FEN tags, linked first-mover metadata, and move-number `.` / `...`
  notation become `pgn_inferred`;
- source-profile layout priors are supporting evidence only and cannot fill an
  unknown result;
- exact verified labels remain `human_verified` and require the same source
  SHA256, stable diagram fingerprint, verification source, reviewer, and
  timestamp.

High-confidence disagreement produces an explicit `conflict` with
`side_to_move=unknown`; no source wins by priority. Text, PGN, and exact-label
fallback may satisfy coverage, but they do not enable full FEN by default. The
fixed-edition gate still requires `side_to_move_coverage_rate=1.0`,
`unknown_count=0`, and `false_trusted_marker_count=0` before it can emit closing
evidence.
