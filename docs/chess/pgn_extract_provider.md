# Optional `pgn-extract` Provider

KindleMaster can use [`pgn-extract`](https://github.com/kentdjb/pgn-extract) as an optional cleanup/formatting helper for dirty PGN-like text. It is not a strict PGN authority.

## Safety Contract

- Downloadable PGN is still exported only after KindleMaster local parser, `python-chess` replay, and `final_fen` checks pass.
- `pgn-extract` output never makes a review-only OCR record accepted.
- In `audit` mode, `pgn-extract` only adds report metadata.
- In `format_accepted` mode, `pgn-extract` may replace formatting only for already accepted records, and only when the formatted PGN parses cleanly and replays to the same `final_fen`.
- If the tool is missing, times out, or exits nonzero, KindleMaster falls back to its current behavior.

## Configuration

```powershell
$env:KINDLEMASTER_PGN_EXTRACT_ENABLED = "1"
$env:KINDLEMASTER_PGN_EXTRACT_PATH = "pgn-extract"
$env:KINDLEMASTER_PGN_EXTRACT_TIMEOUT_MS = "5000"
$env:KINDLEMASTER_PGN_EXTRACT_MODE = "audit"  # audit | format_accepted
```

Default runtime behavior is unchanged unless `KINDLEMASTER_PGN_EXTRACT_ENABLED=1`.

## Modes

`audit`:

- Runs the tool for record-level audit payloads.
- Does not change `status`, `movetext`, `pgn`, `final_fen`, or exportability.

`format_accepted`:

- Runs only after the existing strict PGN gate has a parser/replay accepted record.
- Rejects formatted output if parser validation fails.
- Rejects formatted output if replay final FEN differs from the local accepted record.

## Operator CLI

For large external PGN files, use the standalone helper:

```powershell
python scripts/clean_dirty_pgn_with_pgn_extract.py input.pgn --output output\cleaned.pgn --report-json reports\pgn_extract_cleaning.json
```

This workflow is intentionally outside `/convert`; it cleans external PGN files and writes a report, but it does not create accepted KindleMaster OCR PGN records.

## Limitations

- The adapter intentionally uses a minimal CLI shape: a temporary PGN input file is passed to `pgn-extract`, and stdout is treated as the formatted PGN candidate.
- Exact advanced `pgn-extract` flags should be added only after testing against the installed version.
- This provider is useful for hygiene and operator workflows; it is not expected to solve OCR SAN legality or diagram segmentation by itself.
