# Chess marker crop corpus

This directory contains minimal side-marker crops for regression-checking the
adaptive `△` / `▼` classifier.

Policy:

- The committed files are synthetic marker-only crops.
- No full PDF pages, board pages, or book text are included.
- Rows are training/evaluation candidates only.
- `allowed_for_runtime_truth` must remain `false`; these rows must not directly
  publish full FEN or bypass side-to-move trust gates.
- Synthetic rows protect compatibility only. They cannot satisfy the real
  fixed-edition acceptance gate.
- The `yusupov-fundamentals` grammar requires an upright outline triangle for
  White and an inverted filled triangle for Black. Orientation/fill
  disagreement is review-only.
- Real calibration may use only the `calibration` split. Holdout rows are
  evaluated separately and never tune thresholds or confidence calibration.

Classes:

- `white_outline_triangle`: visible outline triangle, expected side `w`.
- `black_filled_triangle`: visible filled triangle, expected side `b`.
- `bad_crop`: crop quality examples that must stay review-only.
- `multiple`: multiple marker-like components, review-only.
- `unclear`: low-confidence or unclear marker examples, review-only.

Local fixture pack:

Real book-derived marker micro-crops may be evaluated locally, but should stay
outside git unless they are legally cleared and contain only the tiny marker
region. A local pack should mirror this structure and manifest schema:

```text
reference_inputs_local/chess_fen/marker_crops/
  manifest.json
  white_outline_triangle/*.png
  black_filled_triangle/*.png
  bad_crop/*.png
  multiple/*.png
  unclear/*.png
```

The local pack can be used for experiments, but runtime acceptance must still
depend on deterministic crop quality and side-marker trust gates. Evaluate a
secure #257 corpus with:

```bash
python kindlemaster.py ml evaluate-marker-crops \
  --corpus-root /secure/path/to/yusupov-corpus
```

The generated JSON and Markdown reports keep train, calibration, and holdout
metrics separate and include reliability data. Without a secure manifest the
real holdout status remains `corpus_unavailable`.
