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
- `verification_source`: `human_visual` for new labels. Legacy rows with
  manual-grid-review provenance are treated as `legacy_human_visual` until
  migrated.
- `human_verified`: `true` for new labels.
- `crop_sha256`: SHA-256 of the exact crop reviewed by the human.
- `square_diff_ack` or `square_diff_reviewed`: `true` after the human reviewed
  the 64-square diff/candidate comparison.

Rows with `draft`, `rejected`, `false_positive`, or invalid FEN are ignored.
AI-only values such as `ai_suggested_fen`, `ai_approved`, `arbiter_approved`,
or high confidence are never verification proof.

## FEN Crop Recovery Contract

`recover-fen-label-crops` maps existing human FEN labels to crops from a current
conversion manifest. The manifest may use file-backed images or `records[]` with
an embedded `image_data_uri`.

An accepted mapping requires:

- a valid, human-verified source FEN with non-ambiguous provenance;
- page-local one-to-one assignment;
- square and occupied-square agreement above the configured thresholds;
- sufficient margin over the second candidate;
- no overlap with the model training FEN or normalized board hash;
- a SHA-256 bound source crop and source document identity.

Recovery writes `review/fen_recovered_labels.jsonl` and decision evidence. It
never invents a FEN and never changes final export acceptance directly.

## FEN Profile/Corpus Gate Contract

Profile readiness is a separate gate after label validation. A manifest-ready
FEN profile requires:

- only verified human-reviewed labels;
- at least 20 valid seed labels per profile for release-grade proof;
- deterministic template/evaluator status `passed`;
- exact FEN accuracy at or above the configured threshold;
- `false_positive_count == 0`;
- no review-only label artifact such as `candidate_labels_review.jsonl` or
  `manual_label_template.jsonl` as the seed source.

Release-grade corpus proof (`standard` and `full`) requires at least two real
scanned chess FEN profiles. A one-profile gate is allowed only as an explicit
bounded diagnostic override or the GitHub CI proof profile, which is evidence
only and does not claim release-grade FEN generalization.

## Square Diff Contract

Square-diff rows compare two FEN placements in board order (`a8` through `h1`).

Required fields:

- `square`: algebraic square, for example `e5`.
- `expected_piece`: `empty,K,Q,R,B,N,P,k,q,r,b,n,p`.
- `actual_piece`: `empty,K,Q,R,B,N,P,k,q,r,b,n,p`.
- `reason`: `piece_mismatch`, `missing_piece`, or `extra_piece`.

Known false-positive fixtures, such as `p010_d002`, must stay in the regression
set. For that case, a candidate that places a black pawn on `e5` is invalid
because the manually verified crop has a black rook on `e5`.

## AI FEN Evidence Contract

AI review artifacts are evidence only.

Allowed AI fields:

- candidate FEN or suggested FEN;
- confidence;
- uncertain or ambiguous squares;
- reviewer notes;
- `review_opinion`: `supports_candidate`, `flags_candidate`, or `uncertain`.

Forbidden authority fields:

- `verified`;
- `accepted`;
- `accepted_for_corpus=true`;
- direct mutation of `fen` in verified label files.

Legacy `approved` fields from review providers are compatibility signals only
and must be interpreted as `review_opinion`, never as corpus verification.

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
- `source_crop`, `fen`, `board_sha256`; `image_path` is optional unless square
  image materialization was explicitly requested.

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

A source-bound verified full FEN may replace missing graphical side-marker
evidence only when the deterministic candidate exactly equals that verified
six-field FEN and `source_crop_hash` exactly equals the verified label crop SHA.
The verified evidence source and provenance must be present. A FEN mismatch,
missing hash, or hash mismatch keeps the candidate in review.

The ensemble report does not directly rewrite strict exports.
