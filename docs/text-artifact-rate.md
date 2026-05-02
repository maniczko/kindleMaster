# Text Artifact Rate

KindleMaster reports a reader-facing text artifact rate for final EPUB output.

The metric is calculated after final EPUB cleanup and repair, so it measures the text the user is likely to see on Kindle rather than only extractor input.

## Counted Signals

- split words, for example `pro- ject`
- glued words, for example long or suspicious CamelCase tokens
- OCR/mojibake junk and replacement characters
- punctuation spacing artifacts
- suspicious broken URL or DOI fragments
- visible technical placeholders such as `Object 1`, `State 1`, or `Rank = 1*3`

## Thresholds

| Status | Rule |
| --- | --- |
| `passed` | up to 1 artifact per 1000 words and no hard visible junk |
| `passed_with_warnings` | up to 4 artifacts per 1000 words, or isolated visible junk |
| `failed` | more than 4 artifacts per 1000 words, or repeated hard visible junk |

The report is available under `quality_report.text_cleanup.artifact_rate` and includes global counts plus per-document counts.

## Release Use

`passed_with_warnings` is acceptable only as an explicit review state. `failed` should block premium release until the text cleanup, OCR, or source extraction issue is repaired.

