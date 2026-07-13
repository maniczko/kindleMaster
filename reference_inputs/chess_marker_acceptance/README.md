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
