# Chess FEN Strict Regression Diff

Previous report: `reports\chess_fen\fundamenty_exact_label_lookup_fix.json`
Latest report: `reports\chess_fen\fundamenty_marker_rule_recovery.json`

## Summary

- Previous strict accepted: `223`
- Latest strict accepted: `227`
- Strict delta: `4`
- Lost strict accepted: `5`
- New strict accepted: `9`

## Lost Strict Accepted

| Diagram | Page | Previous FEN | Latest status | Category | Blockers | Recommended action |
|---|---:|---|---|---|---|---|
| p98:scan_chess_p098_06.png | 98 | `5Q2/3N4/2K1k2r/7b/4P3/8/8/8 w - - 0 1` | `requires_review` | `side_to_move_metadata` | final_rendered_crop_fen_used, side_to_move_inferred, side_to_move_marker_probes_checked | inspect side-to-move marker/caption metadata |
| p125:scan_chess_p125_03.png | 125 | `4B1k1/5p1p/5pp1/p2Pn3/3QP3/7P/5PPK/1q6 w - - 0 1` | `requires_review` | `side_to_move_metadata` | reader_expanded_crop_fen_used, side_to_move_inferred, side_to_move_marker_probes_checked | inspect side-to-move marker/caption metadata |
| p144:scan_chess_p144_01.png | 144 | `2r2rk1/p3bppp/bq2pn2/3p4/1p1P4/4P1P1/PP1N1PPP/R1BQRBK1 w - - 0 1` | `requires_review` | `side_to_move_metadata` | side_to_move_inferred, side_to_move_marker_ambiguous, side_to_move_marker_detected, side_to_move_marker_multi_region_conflict, side_to_move_marker_probes_checked | inspect side-to-move marker/caption metadata |
| p170:scan_chess_p170_06.png | 170 | `2q5/8/p3R3/P5pP/3p2P1/3k4/3q4/6K1 w - - 0 1` | `requires_review` | `side_to_move_metadata` | reader_visible_crop_fen_used, side_to_move_inferred, side_to_move_marker_probes_checked | inspect side-to-move marker/caption metadata |
| p245:scan_chess_p245_05.png | 245 | `r4rk1/pp1qb1pp/5p2/4nN2/2P5/5Q2/PB3PPP/R4RK1 b - - 0 1` | `requires_review` | `side_to_move_metadata` | reader_visible_crop_fen_used, side_to_move_inferred, side_to_move_marker_ambiguous, side_to_move_marker_detected, side_to_move_marker_multi_region_conflict, side_to_move_marker_probes_checked | inspect side-to-move marker/caption metadata |

## Lost By Category

- `side_to_move_metadata`: 5

## Lost By Blocker Code

- `side_to_move_inferred`: 5
- `side_to_move_marker_probes_checked`: 5
- `side_to_move_marker_ambiguous`: 2
- `side_to_move_marker_detected`: 2
- `side_to_move_marker_multi_region_conflict`: 2
- `reader_visible_crop_fen_used`: 2
- `final_rendered_crop_fen_used`: 1
- `reader_expanded_crop_fen_used`: 1
