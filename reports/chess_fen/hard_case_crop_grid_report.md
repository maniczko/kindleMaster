# Chess FEN Hard-Case Crop/Grid Report

Diagnostics: `reports\chess_fen\fundamenty_marker_rule_recovery_review_diagnostics.json`

## Summary

- Review total: `197`
- Hard-case total: `197`
- Unresolved crop/grid blockers: `0`
- Crop recovery evidence: `68`
- Recognition hard cases: `78`
- Metadata hard cases: `197`
- Full-FEN validation hard cases: `0`

## Tag Counts

| Tag | Count |
|---|---:|
| `metadata_hard_case` | 197 |
| `recognition_hard_case` | 78 |
| `crop_recovery_evidence` | 68 |

## First 50 Hard Cases

| Diagram | Page | Tags | Primary blocker | Recommendation |
|---|---:|---|---|---|
| p14:scan_chess_p014_02.png | 14 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p14:scan_chess_p014_04.png | 14 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p14:scan_chess_p014_05.png | 14 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p15:scan_chess_p015_01.png | 15 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p15:scan_chess_p015_02.png | 15 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p15:scan_chess_p015_05.png | 15 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p23:scan_chess_p023_01.png | 23 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p25:scan_chess_p025_03.png | 25 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
| p25:scan_chess_p025_04.png | 25 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p26:scan_chess_p026_02.png | 26 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p26:scan_chess_p026_03.png | 26 | `metadata_hard_case, recognition_hard_case` | `pawn_on_back_rank` | compare placement against crop and template confidence |
| p26:scan_chess_p026_04.png | 26 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p26:scan_chess_p026_05.png | 26 | `metadata_hard_case, recognition_hard_case` | `annotation_cross_marker_suppressed` | compare placement against crop and template confidence |
| p32:scan_chess_p032_01.png | 32 | `metadata_hard_case` | `side_to_move_caption_ambiguous` | inspect side-to-move/full FEN validation evidence |
| p32:scan_chess_p032_02.png | 32 | `metadata_hard_case` | `side_to_move_caption_ambiguous` | inspect side-to-move/full FEN validation evidence |
| p36:scan_chess_p036_02.png | 36 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p38:scan_chess_p038_01.png | 38 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p39:scan_chess_p039_02.png | 39 | `metadata_hard_case, recognition_hard_case` | `annotation_cross_marker_suppressed` | compare placement against crop and template confidence |
| p39:scan_chess_p039_03.png | 39 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p47:scan_chess_p047_01.png | 47 | `metadata_hard_case` | `side_to_move_caption_ambiguous` | inspect side-to-move/full FEN validation evidence |
| p47:scan_chess_p047_03.png | 47 | `metadata_hard_case` | `side_to_move_caption_ambiguous` | inspect side-to-move/full FEN validation evidence |
| p48:scan_chess_p048_01.png | 48 | `metadata_hard_case` | `side_to_move_caption_ambiguous` | inspect side-to-move/full FEN validation evidence |
| p48:scan_chess_p048_02.png | 48 | `metadata_hard_case` | `side_to_move_caption_ambiguous` | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_01.png | 50 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_02.png | 50 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_03.png | 50 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_04.png | 50 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_05.png | 50 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_01.png | 51 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_02.png | 51 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_03.png | 51 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_04.png | 51 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_05.png | 51 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p59:scan_chess_p059_03.png | 59 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p59:scan_chess_p059_04.png | 59 | `metadata_hard_case, recognition_hard_case` | `annotation_cross_marker_suppressed` | compare placement against crop and template confidence |
| p59:scan_chess_p059_05.png | 59 | `metadata_hard_case, recognition_hard_case` | `annotation_cross_marker_suppressed` | compare placement against crop and template confidence |
| p59:scan_chess_p059_06.png | 59 | `metadata_hard_case, recognition_hard_case` | `annotation_cross_marker_suppressed` | compare placement against crop and template confidence |
| p60:scan_chess_p060_02.png | 60 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p60:scan_chess_p060_03.png | 60 | `metadata_hard_case, recognition_hard_case` | `annotation_cross_marker_suppressed` | compare placement against crop and template confidence |
| p70:scan_chess_p070_01.png | 70 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
| p70:scan_chess_p070_02.png | 70 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p70:scan_chess_p070_03.png | 70 | `crop_recovery_evidence, metadata_hard_case` | `reader_visible_crop_fen_used` | inspect side-to-move/full FEN validation evidence |
| p70:scan_chess_p070_04.png | 70 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p70:scan_chess_p070_05.png | 70 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p70:scan_chess_p070_06.png | 70 | `metadata_hard_case, recognition_hard_case` | `black_king_count_invalid` | compare placement against crop and template confidence |
| p71:scan_chess_p071_02.png | 71 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p71:scan_chess_p071_03.png | 71 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p71:scan_chess_p071_05.png | 71 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p77:scan_chess_p077_03.png | 77 | `metadata_hard_case, recognition_hard_case` | `piece_template_confidence_below_threshold` | compare placement against crop and template confidence |
| p79:scan_chess_p079_01.png | 79 | `metadata_hard_case` | `side_to_move_inferred` | inspect side-to-move/full FEN validation evidence |
