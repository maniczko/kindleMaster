# Calibrated FEN Square Runtime

The production web conversion path and `chess-study recognize-fen-local` use the
same portable RBF-SVM artifact:

```text
models/chess/chess_fen_square_rbf_svm_v2.npz
models/chess/chess_fen_square_rbf_svm_v2.manifest.json
```

The artifact is loaded with `numpy.load(..., allow_pickle=False)`. Its SHA-256,
feature schema, validation-only temperature, and validation-only board
acceptance threshold are verified from the adjacent manifest before inference.
A missing, malformed, or hash-mismatched model fails closed to review.

## Runtime Modes

- `off`: exact rollback. Template and marker behavior is unchanged and the
  square model is not loaded.
- `shadow`: the web default. The model records placement evidence, confidence,
  provenance, and one owning blocker but cannot alter published FEN.
- `assist`: an explicit opt-in. A calibrated placement may replace a
  review-only template placement, but inferred side-to-move remains review-only.
  Full FEN still requires trusted marker/caption evidence and the existing
  deterministic acceptance gates.

Production rollback is one setting:

```powershell
$env:KINDLEMASTER_CHESS_FEN_MODEL_MODE = "off"
```

An alternate artifact can be tested without code changes:

```powershell
$env:KINDLEMASTER_CHESS_FEN_MODEL_PATH = "C:\path\to\candidate.npz"
```

## Evidence

The fixed-edition holdout contains 30 source-bound boards:

- centroid baseline: 49.43% square accuracy and 0/30 exact boards;
- promoted RBF-SVM: 98.91% square accuracy and 27/30 exact boards;
- calibrated accepted candidates: 13/30;
- false accepted candidates: 0/13;
- portable/scikit-learn prediction parity: 16,832/16,832 squares;
- maximum OVR score difference: `1.51e-14`;
- measured warm runtime: about 0.22 seconds per board on the development host.

These figures prove the fixed-edition model only. They do not establish
cross-book generalization. Keep other chess-book profiles in `shadow` until a
separate source-bound holdout passes the same zero-false-acceptance gate.

## Trust Boundaries

Every diagram report keeps these decisions separate:

- `model_runtime.trust_boundaries.board_placement`;
- `model_runtime.trust_boundaries.side_to_move`;
- `model_runtime.trust_boundaries.full_fen`;
- `model_runtime.owning_blocker`;
- `recognition_blockers`.

An accepted board placement is not equivalent to an accepted full FEN.
