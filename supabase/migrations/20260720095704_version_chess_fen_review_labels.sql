-- Add optimistic concurrency, append-only label history, and immutable dataset snapshots
-- to the existing source-bound chess FEN review store.

alter table public.chess_fen_review_sessions
    add column if not exists revision bigint not null default 0
        check (revision >= 0),
    add column if not exists closed_at timestamptz,
    add column if not exists closed_by_user_id uuid references auth.users(id) on delete set null;

alter table public.chess_fen_review_labels
    add column if not exists revision bigint not null default 0
        check (revision >= 0);

create table if not exists public.chess_fen_review_label_history (
    id bigint generated always as identity primary key,
    artifact_id text not null,
    diagram_fingerprint text not null
        check (diagram_fingerprint ~ '^[0-9a-f]{64}$'),
    label_revision bigint not null
        check (label_revision > 0),
    owner_user_id uuid references auth.users(id) on delete set null,
    changed_by_label text not null default '',
    change_source text not null default 'autosave'
        check (change_source in ('autosave', 'manual_save', 'import', 'close', 'reopen', 'migration')),
    previous_payload jsonb,
    new_payload jsonb not null
        check (jsonb_typeof(new_payload) = 'object'),
    changed_at timestamptz not null default now(),
    unique (artifact_id, diagram_fingerprint, label_revision),
    foreign key (artifact_id)
        references public.chess_fen_review_sessions(artifact_id)
        on delete cascade
);

create table if not exists public.chess_fen_dataset_versions (
    id uuid primary key default gen_random_uuid(),
    artifact_id text not null
        references public.chess_fen_review_sessions(artifact_id) on delete restrict,
    dataset_version integer not null
        check (dataset_version > 0),
    session_revision bigint not null
        check (session_revision > 0),
    owner_user_id uuid references auth.users(id) on delete set null,
    source_document_sha256 text not null
        check (source_document_sha256 ~ '^[0-9a-f]{64}$'),
    schema_version text not null default 'kindlemaster.fen_review.dataset.v1',
    summary jsonb not null
        check (jsonb_typeof(summary) = 'object'),
    rows jsonb not null
        check (jsonb_typeof(rows) = 'array' and jsonb_array_length(rows) between 1 and 2000),
    row_count integer not null
        check (row_count between 1 and 2000),
    created_at timestamptz not null default now(),
    unique (artifact_id, dataset_version),
    unique (artifact_id, session_revision)
);

create index if not exists chess_fen_review_history_artifact_changed_idx
    on public.chess_fen_review_label_history (artifact_id, changed_at desc);
create index if not exists chess_fen_review_history_owner_changed_idx
    on public.chess_fen_review_label_history (owner_user_id, changed_at desc);
create index if not exists chess_fen_dataset_versions_source_created_idx
    on public.chess_fen_dataset_versions (source_document_sha256, created_at desc);

alter table public.chess_fen_review_label_history enable row level security;
alter table public.chess_fen_dataset_versions enable row level security;

drop policy if exists chess_fen_review_sessions_owner_select on public.chess_fen_review_sessions;
create policy chess_fen_review_sessions_owner_select
    on public.chess_fen_review_sessions for select
    to authenticated
    using ((select auth.uid()) = owner_user_id);

drop policy if exists chess_fen_review_labels_owner_select on public.chess_fen_review_labels;
create policy chess_fen_review_labels_owner_select
    on public.chess_fen_review_labels for select
    to authenticated
    using (
        exists (
            select 1
            from public.chess_fen_review_sessions session
            where session.artifact_id = chess_fen_review_labels.artifact_id
              and session.owner_user_id = (select auth.uid())
        )
    );

drop policy if exists chess_fen_review_history_owner_select on public.chess_fen_review_label_history;
create policy chess_fen_review_history_owner_select
    on public.chess_fen_review_label_history for select
    to authenticated
    using ((select auth.uid()) = owner_user_id);

drop policy if exists chess_fen_dataset_versions_owner_select on public.chess_fen_dataset_versions;
create policy chess_fen_dataset_versions_owner_select
    on public.chess_fen_dataset_versions for select
    to authenticated
    using ((select auth.uid()) = owner_user_id);

revoke all on table public.chess_fen_review_label_history from anon, authenticated;
revoke all on table public.chess_fen_dataset_versions from anon, authenticated;
grant select on table public.chess_fen_review_sessions to authenticated;
grant select on table public.chess_fen_review_labels to authenticated;
grant select on table public.chess_fen_review_label_history to authenticated;
grant select on table public.chess_fen_dataset_versions to authenticated;
grant select, insert, update, delete on table public.chess_fen_review_label_history to service_role;
grant select, insert, update, delete on table public.chess_fen_dataset_versions to service_role;
grant usage, select on sequence public.chess_fen_review_label_history_id_seq to service_role;

drop function if exists public.save_chess_fen_review(text, uuid, text, jsonb, jsonb, timestamptz);

create or replace function public.save_chess_fen_review(
    p_artifact_id text,
    p_owner_user_id uuid,
    p_source_document_sha256 text,
    p_rows jsonb,
    p_summary jsonb,
    p_saved_at timestamptz default now(),
    p_expected_revision bigint default 0,
    p_action text default 'save',
    p_change_source text default 'autosave'
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    current_session public.chess_fen_review_sessions%rowtype;
    current_revision bigint := 0;
    next_revision bigint;
    resolved_count integer;
    resolved_status text;
    created_dataset_id uuid;
    next_dataset_version integer;
begin
    if p_artifact_id is null or length(btrim(p_artifact_id)) = 0 or length(p_artifact_id) > 200 then
        raise exception 'invalid_artifact_id';
    end if;
    if p_source_document_sha256 !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid_source_document_sha256';
    end if;
    if p_action not in ('save', 'close', 'reopen') then
        raise exception 'invalid_fen_review_action';
    end if;
    if p_change_source not in ('autosave', 'manual_save', 'import', 'close', 'reopen', 'migration') then
        raise exception 'invalid_fen_review_change_source';
    end if;
    if jsonb_typeof(p_rows) <> 'array' then
        raise exception 'review_rows_must_be_array';
    end if;
    resolved_count := jsonb_array_length(p_rows);
    if resolved_count = 0 or resolved_count > 2000 then
        raise exception 'review_row_limit_exceeded';
    end if;
    if jsonb_typeof(p_summary) <> 'object' then
        raise exception 'review_summary_must_be_object';
    end if;
    if exists (
        select 1
        from jsonb_array_elements(p_rows) item
        where coalesce(item ->> 'diagram_fingerprint', '') !~ '^[0-9a-f]{64}$'
           or coalesce(item ->> 'source_document_sha256', item ->> 'source_artifact_sha256', '')
                <> p_source_document_sha256
           or case
                when jsonb_typeof(item -> 'square_labels') = 'array'
                    then jsonb_array_length(item -> 'square_labels') <> 64
                else true
              end
    ) then
        raise exception 'invalid_source_bound_review_row';
    end if;
    if exists (
        select 1
        from jsonb_array_elements(p_rows) item
        group by item ->> 'diagram_fingerprint'
        having count(*) > 1
    ) then
        raise exception 'duplicate_diagram_fingerprint';
    end if;

    -- Serialize writes even before the first session row exists.
    perform pg_advisory_xact_lock(hashtextextended(p_artifact_id, 0));

    select * into current_session
    from public.chess_fen_review_sessions session
    where session.artifact_id = p_artifact_id
    for update;

    if found then
        current_revision := current_session.revision;
        if current_session.source_document_sha256 <> p_source_document_sha256 then
            raise exception 'review_source_digest_mismatch';
        end if;
        if current_session.owner_user_id is not null
           and current_session.owner_user_id is distinct from p_owner_user_id then
            raise exception 'fen_review_owner_mismatch';
        end if;
        if current_session.status = 'complete' and p_action = 'save' then
            raise exception 'fen_review_session_closed';
        end if;
    elsif p_action = 'reopen' then
        raise exception 'fen_review_session_not_found';
    end if;

    if current_revision <> p_expected_revision then
        raise exception 'fen_review_revision_conflict';
    end if;

    if p_action = 'close' and (
        coalesce((p_summary ->> 'pending')::integer, 0) <> 0
        or coalesce((p_summary ->> 'invalid')::integer, 0) <> 0
        or exists (
            select 1
            from jsonb_array_elements(p_rows) item
            where coalesce(item ->> 'label_status', '') not in ('verified', 'rejected', 'unreadable')
               or coalesce(item ->> 'verified_by', '') = ''
               or (
                    item ->> 'label_status' = 'verified'
                    and (
                        coalesce((item ->> 'piece_labels_verified')::boolean, false) is not true
                        or coalesce(item ->> 'manual_side_to_move', '') not in ('w', 'b')
                    )
               )
        )
    ) then
        raise exception 'fen_review_close_requires_complete_valid_rows';
    end if;

    next_revision := current_revision + 1;
    resolved_status := case
        when p_action = 'close' then 'complete'
        when p_action = 'reopen' then 'active'
        else 'active'
    end;

    insert into public.chess_fen_review_sessions (
        artifact_id, owner_user_id, source_document_sha256, schema_version,
        status, summary, row_count, revision, saved_at, closed_at,
        closed_by_user_id, updated_at
    ) values (
        p_artifact_id, p_owner_user_id, p_source_document_sha256,
        'kindlemaster.fen_review_progress.v2', resolved_status, p_summary,
        resolved_count, next_revision, p_saved_at,
        case when p_action = 'close' then p_saved_at else null end,
        case when p_action = 'close' then p_owner_user_id else null end,
        now()
    )
    on conflict (artifact_id) do update
    set owner_user_id = coalesce(public.chess_fen_review_sessions.owner_user_id, excluded.owner_user_id),
        schema_version = excluded.schema_version,
        status = excluded.status,
        summary = excluded.summary,
        row_count = excluded.row_count,
        revision = excluded.revision,
        saved_at = excluded.saved_at,
        closed_at = excluded.closed_at,
        closed_by_user_id = excluded.closed_by_user_id,
        updated_at = now();

    insert into public.chess_fen_review_label_history (
        artifact_id, diagram_fingerprint, label_revision, owner_user_id,
        changed_by_label, change_source, previous_payload, new_payload, changed_at
    )
    select
        p_artifact_id,
        item ->> 'diagram_fingerprint',
        coalesce(label.revision, 0) + 1,
        p_owner_user_id,
        left(coalesce(item ->> 'verified_by', ''), 200),
        p_change_source,
        label.row_payload,
        item,
        p_saved_at
    from jsonb_array_elements(p_rows) item
    left join public.chess_fen_review_labels label
      on label.artifact_id = p_artifact_id
     and label.diagram_fingerprint = item ->> 'diagram_fingerprint'
    where label.row_payload is distinct from item;

    insert into public.chess_fen_review_labels (
        artifact_id, diagram_fingerprint, source_document_sha256, diagram_id,
        page, review_index, label_status, square_labels, piece_labels_verified,
        manual_side_to_move, board_crop_label, marker_crop_label, verified_by,
        row_payload, revision, saved_at, updated_at
    )
    select
        p_artifact_id,
        item ->> 'diagram_fingerprint',
        p_source_document_sha256,
        coalesce(item ->> 'diagram_id', ''),
        case when coalesce(item ->> 'page', '') ~ '^[0-9]+$' then (item ->> 'page')::integer end,
        case when coalesce(item ->> 'review_index', '') ~ '^[0-9]+$' then (item ->> 'review_index')::integer end,
        coalesce(item ->> 'label_status', 'needs_piece_labels'),
        item -> 'square_labels',
        coalesce((item ->> 'piece_labels_verified')::boolean, false),
        coalesce(item ->> 'manual_side_to_move', ''),
        coalesce(item ->> 'board_crop_label', ''),
        coalesce(item ->> 'marker_crop_label', ''),
        left(coalesce(item ->> 'verified_by', ''), 200),
        item,
        1,
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
        revision = case
            when public.chess_fen_review_labels.row_payload is distinct from excluded.row_payload
                then public.chess_fen_review_labels.revision + 1
            else public.chess_fen_review_labels.revision
        end,
        row_payload = excluded.row_payload,
        saved_at = excluded.saved_at,
        updated_at = now();

    delete from public.chess_fen_review_labels label
    where label.artifact_id = p_artifact_id
      and not exists (
          select 1 from jsonb_array_elements(p_rows) item
          where item ->> 'diagram_fingerprint' = label.diagram_fingerprint
      );

    if p_action = 'close' then
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
    end if;

    return jsonb_build_object(
        'artifact_id', p_artifact_id,
        'saved_at', p_saved_at,
        'row_count', resolved_count,
        'summary', p_summary,
        'storage', 'database',
        'session_status', resolved_status,
        'revision', next_revision,
        'dataset_version_id', created_dataset_id
    );
end;
$$;

revoke all on function public.save_chess_fen_review(
    text, uuid, text, jsonb, jsonb, timestamptz, bigint, text, text
) from public, anon, authenticated;
grant execute on function public.save_chess_fen_review(
    text, uuid, text, jsonb, jsonb, timestamptz, bigint, text, text
) to service_role;
