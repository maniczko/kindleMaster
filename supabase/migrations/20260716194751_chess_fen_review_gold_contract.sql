-- Promote completed human piece-grid reviews to the canonical gold-label contract.
-- The migration does not accept model candidates or excluded rows.

update public.chess_fen_review_labels
set row_payload = row_payload || jsonb_build_object(
        'id', coalesce(
            nullif(row_payload ->> 'id', ''),
            nullif(diagram_id, ''),
            diagram_fingerprint
        ),
        'fen', coalesce(
            nullif(row_payload ->> 'manual_fen', ''),
            nullif(row_payload ->> 'fen', '')
        ),
        'fen_human_verified', true,
        'human_verified', true,
        'verification_source', 'human_visual',
        'square_diff_ack', true,
        'label_provenance', 'human_visual_source_bound_piece_grid_review'
    ),
    updated_at = now()
where label_status = 'verified'
  and piece_labels_verified is true
  and coalesce(row_payload ->> 'manual_fen', '') <> ''
  and coalesce(row_payload ->> 'verified_by', '') <> ''
  and coalesce(row_payload ->> 'verified_at', '') <> '';
