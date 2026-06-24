# Chess FEN Review Blocker Diagnostics

Source report: `reports\chess_fen\fundamenty_marker_rule_recovery.json`

## Summary

- Review total: `197`
- With placement: `197`
- With AI candidate: `0`
- Without any candidate: `0`

## Top 20 Categories

| Category | Count |
|---|---:|
| `full_fen_validation` | 117 |
| `recognition` | 78 |
| `metadata` | 2 |

## Top 20 Blocker Codes

| Blocker | Count |
|---|---:|
| `side_to_move_marker_probes_checked` | 186 |
| `side_to_move_inferred` | 142 |
| `side_to_move_marker_detected` | 82 |
| `piece_template_confidence_below_threshold` | 78 |
| `side_to_move_context_applied` | 55 |
| `side_to_move_context_crop_used` | 55 |
| `side_to_move_context_marker_detected` | 55 |
| `side_to_move_marker_applied` | 55 |
| `reader_visible_crop_fen_used` | 54 |
| `side_to_move_marker_multi_region_conflict` | 43 |
| `white_king_count_invalid` | 33 |
| `side_to_move_marker_ambiguous` | 27 |
| `black_king_count_invalid` | 25 |
| `annotation_cross_marker_suppressed` | 19 |
| `side_to_move_marker_dominant_conflict_resolved` | 16 |
| `side_to_move_marker_multi_region_agreement` | 15 |
| `reader_expanded_crop_fen_used` | 13 |
| `side_to_move_caption_ambiguous` | 11 |
| `side_to_move_caption_detected` | 11 |
| `side_to_move_caption_unscoped_multi_diagram` | 11 |

## First 50 Review Items

| Diagram | Page | Category | Primary blocker | Has placement | Has FEN candidate | Recommendation |
|---|---:|---|---|---:|---:|---|
| p14:scan_chess_p014_02.png | 14 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p14:scan_chess_p014_04.png | 14 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p14:scan_chess_p014_05.png | 14 | `recognition` | `piece_template_confidence_below_threshold` | yes | no | compare placement against crop and template confidence |
| p15:scan_chess_p015_01.png | 15 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p15:scan_chess_p015_02.png | 15 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p15:scan_chess_p015_05.png | 15 | `recognition` | `black_king_count_invalid` | yes | yes | compare placement against crop and template confidence |
| p23:scan_chess_p023_01.png | 23 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p25:scan_chess_p025_03.png | 25 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p25:scan_chess_p025_04.png | 25 | `recognition` | `piece_template_confidence_below_threshold` | yes | no | compare placement against crop and template confidence |
| p26:scan_chess_p026_02.png | 26 | `recognition` | `black_king_count_invalid` | yes | yes | compare placement against crop and template confidence |
| p26:scan_chess_p026_03.png | 26 | `recognition` | `pawn_on_back_rank` | yes | yes | compare placement against crop and template confidence |
| p26:scan_chess_p026_04.png | 26 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p26:scan_chess_p026_05.png | 26 | `recognition` | `annotation_cross_marker_suppressed` | yes | yes | compare placement against crop and template confidence |
| p32:scan_chess_p032_01.png | 32 | `full_fen_validation` | `side_to_move_caption_ambiguous` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p32:scan_chess_p032_02.png | 32 | `full_fen_validation` | `side_to_move_caption_ambiguous` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p36:scan_chess_p036_02.png | 36 | `recognition` | `black_king_count_invalid` | yes | yes | compare placement against crop and template confidence |
| p38:scan_chess_p038_01.png | 38 | `recognition` | `piece_template_confidence_below_threshold` | yes | no | compare placement against crop and template confidence |
| p39:scan_chess_p039_02.png | 39 | `recognition` | `annotation_cross_marker_suppressed` | yes | no | compare placement against crop and template confidence |
| p39:scan_chess_p039_03.png | 39 | `recognition` | `black_king_count_invalid` | yes | no | compare placement against crop and template confidence |
| p47:scan_chess_p047_01.png | 47 | `full_fen_validation` | `side_to_move_caption_ambiguous` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p47:scan_chess_p047_03.png | 47 | `full_fen_validation` | `side_to_move_caption_ambiguous` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p48:scan_chess_p048_01.png | 48 | `full_fen_validation` | `side_to_move_caption_ambiguous` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p48:scan_chess_p048_02.png | 48 | `full_fen_validation` | `side_to_move_caption_ambiguous` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_01.png | 50 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_02.png | 50 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_03.png | 50 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_04.png | 50 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p50:scan_chess_p050_05.png | 50 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_01.png | 51 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_02.png | 51 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_03.png | 51 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_04.png | 51 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p51:scan_chess_p051_05.png | 51 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p59:scan_chess_p059_03.png | 59 | `recognition` | `black_king_count_invalid` | yes | yes | compare placement against crop and template confidence |
| p59:scan_chess_p059_04.png | 59 | `recognition` | `annotation_cross_marker_suppressed` | yes | yes | compare placement against crop and template confidence |
| p59:scan_chess_p059_05.png | 59 | `recognition` | `annotation_cross_marker_suppressed` | yes | yes | compare placement against crop and template confidence |
| p59:scan_chess_p059_06.png | 59 | `recognition` | `annotation_cross_marker_suppressed` | yes | no | compare placement against crop and template confidence |
| p60:scan_chess_p060_02.png | 60 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p60:scan_chess_p060_03.png | 60 | `recognition` | `annotation_cross_marker_suppressed` | yes | yes | compare placement against crop and template confidence |
| p70:scan_chess_p070_01.png | 70 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p70:scan_chess_p070_02.png | 70 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p70:scan_chess_p070_03.png | 70 | `full_fen_validation` | `reader_visible_crop_fen_used` | yes | yes | inspect side-to-move/full FEN validation evidence |
| p70:scan_chess_p070_04.png | 70 | `recognition` | `black_king_count_invalid` | yes | yes | compare placement against crop and template confidence |
| p70:scan_chess_p070_05.png | 70 | `recognition` | `piece_template_confidence_below_threshold` | yes | no | compare placement against crop and template confidence |
| p70:scan_chess_p070_06.png | 70 | `recognition` | `black_king_count_invalid` | yes | yes | compare placement against crop and template confidence |
| p71:scan_chess_p071_02.png | 71 | `recognition` | `piece_template_confidence_below_threshold` | yes | no | compare placement against crop and template confidence |
| p71:scan_chess_p071_03.png | 71 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p71:scan_chess_p071_05.png | 71 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p77:scan_chess_p077_03.png | 77 | `recognition` | `piece_template_confidence_below_threshold` | yes | yes | compare placement against crop and template confidence |
| p79:scan_chess_p079_01.png | 79 | `full_fen_validation` | `side_to_move_inferred` | yes | yes | inspect side-to-move/full FEN validation evidence |
