-- Durable, queryable chess FEN review labels.
-- The browser never talks to these tables directly. The Railway backend uses
-- its service-role credential after validating the source-bound review payload.

create table if not exists public.chess_fen_review_sessions (
    artifact_id text primary key,
    owner_user_id uuid references auth.users(id) on delete set null,
    source_document_sha256 text not null
        check (source_document_sha256 ~ '^[0-9a-f]{64}$'),
    schema_version text not null default 'kindlemaster.fen_review_progress.v1',
    status text not null default 'active'
        check (status in ('active', 'complete', 'archived')),
    summary jsonb not null default '{}'::jsonb
        check (jsonb_typeof(summary) = 'object'),
    row_count integer not null default 0
        check (row_count between 0 and 2000),
    saved_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.chess_fen_review_labels (
    artifact_id text not null
        references public.chess_fen_review_sessions(artifact_id) on delete cascade,
    diagram_fingerprint text not null
        check (diagram_fingerprint ~ '^[0-9a-f]{64}$'),
    source_document_sha256 text not null
        check (source_document_sha256 ~ '^[0-9a-f]{64}$'),
    diagram_id text not null default '',
    page integer,
    review_index integer,
    label_status text not null default 'needs_piece_labels'
        check (label_status in ('needs_piece_labels', 'verified', 'rejected', 'unreadable')),
    square_labels jsonb not null
        check (
            case
                when jsonb_typeof(square_labels) = 'array'
                    then jsonb_array_length(square_labels) = 64
                else false
            end
        ),
    piece_labels_verified boolean not null default false,
    manual_side_to_move text not null default ''
        check (manual_side_to_move in ('', 'w', 'b')),
    board_crop_label text not null default ''
        check (board_crop_label in ('', 'correct', 'cropped', 'wrong', 'unreadable')),
    marker_crop_label text not null default ''
        check (marker_crop_label in ('', 'clear', 'complete_no_marker', 'cropped', 'wrong', 'unreadable')),
    verified_by text not null default '',
    row_payload jsonb not null
        check (jsonb_typeof(row_payload) = 'object'),
    saved_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (artifact_id, diagram_fingerprint)
);

create index if not exists chess_fen_review_labels_source_status_idx
    on public.chess_fen_review_labels (source_document_sha256, label_status);

create index if not exists chess_fen_review_labels_artifact_order_idx
    on public.chess_fen_review_labels (artifact_id, review_index);

create index if not exists chess_fen_review_labels_status_updated_idx
    on public.chess_fen_review_labels (label_status, updated_at desc);

alter table public.chess_fen_review_sessions enable row level security;
alter table public.chess_fen_review_labels enable row level security;

revoke all on table public.chess_fen_review_sessions from anon, authenticated;
revoke all on table public.chess_fen_review_labels from anon, authenticated;
grant select, insert, update, delete on table public.chess_fen_review_sessions to service_role;
grant select, insert, update, delete on table public.chess_fen_review_labels to service_role;

create or replace function public.save_chess_fen_review(
    p_artifact_id text,
    p_owner_user_id uuid,
    p_source_document_sha256 text,
    p_rows jsonb,
    p_summary jsonb,
    p_saved_at timestamptz default now()
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    resolved_count integer;
begin
    if p_artifact_id is null or length(btrim(p_artifact_id)) = 0 or length(p_artifact_id) > 200 then
        raise exception 'invalid_artifact_id';
    end if;
    if p_source_document_sha256 !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid_source_document_sha256';
    end if;
    if jsonb_typeof(p_rows) <> 'array' then
        raise exception 'review_rows_must_be_array';
    end if;
    resolved_count := jsonb_array_length(p_rows);
    if resolved_count > 2000 then
        raise exception 'review_row_limit_exceeded';
    end if;
    if jsonb_typeof(p_summary) <> 'object' then
        raise exception 'review_summary_must_be_object';
    end if;
    if exists (
        select 1
        from public.chess_fen_review_sessions session
        where session.artifact_id = p_artifact_id
          and session.source_document_sha256 <> p_source_document_sha256
    ) then
        raise exception 'review_source_digest_mismatch';
    end if;
    if exists (
        select 1
        from jsonb_array_elements(p_rows) item
        where coalesce(item ->> 'diagram_fingerprint', '') !~ '^[0-9a-f]{64}$'
           or coalesce(
                item ->> 'source_document_sha256',
                item ->> 'source_artifact_sha256',
                ''
           ) <> p_source_document_sha256
           or case
                when jsonb_typeof(item -> 'square_labels') = 'array'
                    then jsonb_array_length(item -> 'square_labels') <> 64
                else true
              end
    ) then
        raise exception 'invalid_source_bound_review_row';
    end if;
    if (
        select count(*)
        from (
            select item ->> 'diagram_fingerprint'
            from jsonb_array_elements(p_rows) item
            group by item ->> 'diagram_fingerprint'
            having count(*) > 1
        ) duplicates
    ) > 0 then
        raise exception 'duplicate_diagram_fingerprint';
    end if;

    insert into public.chess_fen_review_sessions (
        artifact_id,
        owner_user_id,
        source_document_sha256,
        schema_version,
        status,
        summary,
        row_count,
        saved_at,
        updated_at
    )
    values (
        p_artifact_id,
        p_owner_user_id,
        p_source_document_sha256,
        'kindlemaster.fen_review_progress.v1',
        case
            when coalesce((p_summary ->> 'pending')::integer, 0) = 0
             and coalesce((p_summary ->> 'invalid')::integer, 0) = 0
                then 'complete'
            else 'active'
        end,
        p_summary,
        resolved_count,
        p_saved_at,
        now()
    )
    on conflict (artifact_id) do update
    set owner_user_id = coalesce(
            public.chess_fen_review_sessions.owner_user_id,
            excluded.owner_user_id
        ),
        schema_version = excluded.schema_version,
        status = excluded.status,
        summary = excluded.summary,
        row_count = excluded.row_count,
        saved_at = excluded.saved_at,
        updated_at = now();

    insert into public.chess_fen_review_labels (
        artifact_id,
        diagram_fingerprint,
        source_document_sha256,
        diagram_id,
        page,
        review_index,
        label_status,
        square_labels,
        piece_labels_verified,
        manual_side_to_move,
        board_crop_label,
        marker_crop_label,
        verified_by,
        row_payload,
        saved_at,
        updated_at
    )
    select
        p_artifact_id,
        item ->> 'diagram_fingerprint',
        p_source_document_sha256,
        coalesce(item ->> 'diagram_id', ''),
        case when coalesce(item ->> 'page', '') ~ '^[0-9]+$'
            then (item ->> 'page')::integer
            else null
        end,
        case when coalesce(item ->> 'review_index', '') ~ '^[0-9]+$'
            then (item ->> 'review_index')::integer
            else null
        end,
        coalesce(item ->> 'label_status', 'needs_piece_labels'),
        item -> 'square_labels',
        coalesce((item ->> 'piece_labels_verified')::boolean, false),
        coalesce(item ->> 'manual_side_to_move', ''),
        coalesce(item ->> 'board_crop_label', ''),
        coalesce(item ->> 'marker_crop_label', ''),
        left(coalesce(item ->> 'verified_by', ''), 200),
        item,
        p_saved_at,
        now()
    from jsonb_array_elements(p_rows) item
    on conflict (artifact_id, diagram_fingerprint) do update
    set source_document_sha256 = excluded.source_document_sha256,
        diagram_id = excluded.diagram_id,
        page = excluded.page,
        review_index = excluded.review_index,
        label_status = excluded.label_status,
        square_labels = excluded.square_labels,
        piece_labels_verified = excluded.piece_labels_verified,
        manual_side_to_move = excluded.manual_side_to_move,
        board_crop_label = excluded.board_crop_label,
        marker_crop_label = excluded.marker_crop_label,
        verified_by = excluded.verified_by,
        row_payload = excluded.row_payload,
        saved_at = excluded.saved_at,
        updated_at = now();

    delete from public.chess_fen_review_labels label
    where label.artifact_id = p_artifact_id
      and not exists (
          select 1
          from jsonb_array_elements(p_rows) item
          where item ->> 'diagram_fingerprint' = label.diagram_fingerprint
      );

    return jsonb_build_object(
        'artifact_id', p_artifact_id,
        'saved_at', p_saved_at,
        'row_count', resolved_count,
        'summary', p_summary,
        'storage', 'database'
    );
end;
$$;

revoke all on function public.save_chess_fen_review(text, uuid, text, jsonb, jsonb, timestamptz)
    from public, anon, authenticated;
grant execute on function public.save_chess_fen_review(text, uuid, text, jsonb, jsonb, timestamptz)
    to service_role;
