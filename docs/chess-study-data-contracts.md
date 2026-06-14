# Chess Study Data Contracts

These contracts define evidence artifacts for the MasterKindle chess-study FEN/PGN quality loop.

## Policy

- AI, preprocessing, template matching, and local classifiers create candidates only.
- `accepted` FEN/PGN may be set only by deterministic validation gates.
- Generated artifacts under `output/` are reproducible evidence, not Git source of truth.

## FEN Label Contract

Verified FEN labels are JSONL rows consumed by `build-fen-templates` and `build-square-dataset`.

Required fields:

- `diagram_id`: stable crop identifier.
- `manual_fen` or `fen`: six-field FEN.
- `manual_label`: `correct_diagram` or `cropped_diagram`.
- `label_status`: `verified`.
- `crop_path` or `crop_rel_path`: source diagram crop.
- `side_to_move` or `manual_side_to_move`: `w`, `b`, or blank when encoded in FEN.

Rows with `draft`, `rejected`, `false_positive`, or invalid FEN are ignored.

## Board Preprocess Contract

`data/board_preprocess.jsonl` contains one row per source crop.

Key fields:

- `diagram_id`, `page`, `source_crop`, `source_crop_rel`.
- `status`: `ok` or `failed`.
- `normalized_board`, `normalized_board_rel`.
- `confidence`: image-normalization confidence, not chess accuracy.
- `failure_reason`: explicit reason when normalization fails.

This artifact never changes accepted FEN.

## Square Dataset Contract

`data/fen_square_dataset.jsonl` contains one row per board square.

Key fields:

- `diagram_id`, `square`, `square_index`.
- `class`: `empty,K,Q,R,B,N,P,k,q,r,b,n,p`.
- `split`: `train`, `val`, or `holdout`.
- `image_path`, `source_crop`, `fen`, `board_sha256`.

Holdout rows must not be used for training.

## Local Model Prediction Contract

`review/fen_model_predictions.jsonl` contains local model candidates.

Key fields:

- `diagram_id`, `fen_candidate`, `placement`.
- `global_confidence`, `mean_entropy`.
- `squares[]`: per-square class, piece, confidence, entropy.
- `deterministic_validation`: local `python-chess` validation result.

Predictions are candidates only; export remains blocked until ensemble validation.

## Ensemble Contract

`reports/fen_ensemble_eval.json` combines local model evidence with verified labels and validation.

Accepted candidates require:

- valid six-field FEN;
- two kings and valid `python-chess` board;
- confidence above threshold;
- no verified-label disagreement;
- no critical warnings.

The ensemble report does not directly rewrite strict exports.
