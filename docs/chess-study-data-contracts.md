# Chess Study Data Contracts

These contracts define evidence artifacts for the MasterKindle chess-study FEN/PGN quality loop.

## Semantic Exercise Contract

`reports/chess_reader/chess_exercises.json` is the deterministic, machine-readable
intermediate model used before exercise and solution HTML is rendered. Each
record has one explicit `exercise_id`, source page geometry, raw and normalized
text, optional diagram and solution evidence, validation warnings, and a trace
for automatic normalization. The reader derives both sides of the
exercise-solution presentation from this same record.

Records without an explicit identifier are not generated. Missing diagrams or
solutions remain visible as QA warnings. Artifact paths are relative; local
absolute paths and unrelated source text must not be exported. The model can be
serialized and deserialized without changing its canonical JSON form.

## Exercise Page Geometry Contract

`reports/chess_geometry/page_geometry.json` records how printed exercise
numbers are associated with diagram regions before positions are rendered. The
reader uses visible column order rather than the technical order returned by
the PDF parser. Each assignment retains the source page, column, visual order,
diagram bounding box, number bounding box, candidate number, confidence, and
warning codes.

An exercise number is accepted only when every diagram on the page has one
unique candidate, the printed sequence is consecutive, and each match clears
the geometry confidence threshold. Duplicate, detached, ambiguous, or
non-consecutive candidates remain `needs_review`; their candidate values and
coordinates are retained for diagnosis but are not promoted to exercise IDs.

## Chess Notation Paragraph Contract

Chess move numbers are classified before generic ordered-list reconstruction.
Paragraphs containing a valid numbered SAN move, castling, promotion, check,
mate, result, or parenthesized variation remain paragraphs with the
`chess-notation` and `notation-heavy` classes. Their move numbers and side dots
must not be stripped or replaced by generated `<ol>` numbering.

Malformed OCR tokens that still carry strong chess evidence remain paragraphs
with `chess-notation-review`; this class is a QA signal and does not authorize
PGN export. A source block explicitly marked `normal-list`, `ordered-list`, or
`force-list` remains eligible for normal list reconstruction.

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

## Manual Review Persistence Contract

When Supabase is configured, `chess_fen_review_sessions` and
`chess_fen_review_labels` are the primary source of truth for browser review
progress. One label row is stored per source-bound diagram fingerprint so the
training and evaluation pipeline can query verified, rejected, unreadable, and
pending records without parsing an uploaded JSONL file.

The Railway JSONL progress file is a recovery cache and export snapshot only.
It must not override a newer database record. JSONL export remains supported
for offline backup and deterministic dataset-building commands.

Database payloads must:

- retain the source document SHA-256, crop hashes, relative asset paths, 64
  square labels, marker evidence, reviewer, and derived placement/FEN;
- exclude local absolute paths and unrelated OCR page text;
- remain review/training evidence only and never directly authorize publication;
- be writable only through the backend service role after source-binding
  validation.

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
The split is assigned before square extraction. All diagrams from one chapter,
or from one source-bound page when chapter metadata is unavailable, stay in the
same `train`, `val`, or `holdout` partition. `val` calibrates confidence and the
board-level abstention threshold; `holdout` is evaluation-only.

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
