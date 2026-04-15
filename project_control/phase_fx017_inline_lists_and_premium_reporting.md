# Phase FX-017 / FX-018

## Scope

This pass closed two generic quality gaps:

- inline ordered lists from PDF could be flattened into one paragraph instead of becoming readable ordered items in EPUB
- each conversion run still lacked a mandatory human-readable premium assessment and clear `x.y` version visibility

## Generic defects addressed

### FX-017

- Added shared detection for inline ordered sequences such as `A. ... B. ... C. ...` and `1. ... 2. ... 3. ...`
- Applied that detection inside baseline PDF to EPUB conversion before XHTML rendering
- Rendered detected sequences as ordered lists instead of flat paragraphs
- Added smoke counting for `flattened_inline_list_count`
- Added regression checks so flattened inline ordered lists now fail quality evidence

### FX-018

- Added per-run premium quality assessment to end-to-end reports
- Wrote a human-readable premium markdown sidecar for each run
- Surfaced premium score, verdict, strengths, and weaknesses in localhost conversion output
- Surfaced accepted premium report in `/quality-state`
- Normalized visible application versioning to `x.y`

## Evidence

- `python -m pytest -q` -> `44 passed`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf --publication-id newsweek-food-living-2026-01`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026`

## Verified outcomes

- active release sample remains `10.0/10`
- magazine guard remains `10.0/10`
- book-like guard remains `8.65/10`
- smoke now reports `flattened_inline_list_count = 0` on the tracked publications
- localhost `5000` reports version `1.2` and exposes premium verdict in `quality-state`

## Remaining blockers

- `ISSUE-004`
- `ISSUE-020`
- `ISSUE-021`
- `ISSUE-022`

The repository remains `NOT_READY`.
