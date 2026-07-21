-- Preserve human-verified 64-square placements when side-to-move evidence is absent.
-- Such rows are useful training labels but must not be exported as verified FEN.

alter table public.chess_fen_review_labels
    drop constraint if exists chess_fen_review_labels_label_status_check;

alter table public.chess_fen_review_labels
    add constraint chess_fen_review_labels_label_status_check
    check (label_status in (
        'needs_piece_labels', 'verified', 'placement_verified', 'rejected', 'unreadable'
    ));

create or replace function public.close_chess_fen_review(
    p_artifact_id text,
    p_owner_user_id uuid,
    p_source_document_sha256 text,
    p_rows jsonb,
    p_summary jsonb,
    p_saved_at timestamptz default now(),
    p_expected_revision bigint default 0,
    p_change_source text default 'close'
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    save_result jsonb;
    resolved_count integer;
    next_revision bigint;
    next_dataset_version integer;
    created_dataset_id uuid;
begin
    if jsonb_typeof(p_rows) <> 'array' or jsonb_typeof(p_summary) <> 'object' then
        raise exception 'fen_review_close_requires_complete_valid_rows';
    end if;
    resolved_count := jsonb_array_length(p_rows);
    if coalesce((p_summary ->> 'pending')::integer, 0) <> 0
       or coalesce((p_summary ->> 'invalid')::integer, 0) <> 0
       or exists (
            select 1
            from jsonb_array_elements(p_rows) item
            where coalesce(item ->> 'label_status', '') not in (
                    'verified', 'placement_verified', 'rejected', 'unreadable'
                )
               or coalesce(item ->> 'verified_by', '') = ''
               or (
                    item ->> 'label_status' in ('verified', 'placement_verified')
                    and (
                        coalesce((item ->> 'piece_labels_verified')::boolean, false) is not true
                        or coalesce(item ->> 'board_crop_label', '') not in ('correct', 'cropped')
                    )
               )
               or (
                    item ->> 'label_status' = 'verified'
                    and (
                        coalesce(item ->> 'manual_side_to_move', '') not in ('w', 'b')
                        or coalesce(item ->> 'manual_side_evidence', '') not in (
                            'marker', 'caption', 'verified_source'
                        )
                    )
               )
        ) then
        raise exception 'fen_review_close_requires_complete_valid_rows';
    end if;

    -- The existing save RPC owns source, ownership, revision, duplicate and row-shape checks.
    -- Calling it inside this transaction keeps label persistence and dataset closure atomic.
    save_result := public.save_chess_fen_review(
        p_artifact_id,
        p_owner_user_id,
        p_source_document_sha256,
        p_rows,
        p_summary,
        p_saved_at,
        p_expected_revision,
        'save',
        p_change_source
    );
    next_revision := (save_result ->> 'revision')::bigint;

    update public.chess_fen_review_sessions
    set status = 'complete',
        closed_at = p_saved_at,
        closed_by_user_id = p_owner_user_id,
        updated_at = now()
    where artifact_id = p_artifact_id;

    select coalesce(max(version.dataset_version), 0) + 1
    into next_dataset_version
    from public.chess_fen_dataset_versions version
    where version.artifact_id = p_artifact_id;

    insert into public.chess_fen_dataset_versions (
        artifact_id, dataset_version, session_revision, owner_user_id,
        source_document_sha256, summary, rows, row_count, created_at
    ) values (
        p_artifact_id, next_dataset_version, next_revision, p_owner_user_id,
        p_source_document_sha256, p_summary, p_rows, resolved_count, p_saved_at
    ) returning id into created_dataset_id;

    return save_result || jsonb_build_object(
        'session_status', 'complete',
        'closed_at', p_saved_at,
        'dataset_version_id', created_dataset_id
    );
end;
$$;

revoke all on function public.close_chess_fen_review(
    text, uuid, text, jsonb, jsonb, timestamptz, bigint, text
) from public, anon, authenticated;
grant execute on function public.close_chess_fen_review(
    text, uuid, text, jsonb, jsonb, timestamptz, bigint, text
) to service_role;
