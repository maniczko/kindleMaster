-- Source-bound marker evidence review. The Railway backend is the only Data API
-- caller; browser clients never receive the service-role credential.

create table if not exists public.chess_evidence_review_sessions (
    artifact_id text primary key,
    owner_user_id uuid references auth.users(id) on delete set null,
    source_document_sha256 text not null
        check (source_document_sha256 ~ '^[0-9a-f]{64}$'),
    source_profile text not null
        check (source_profile ~ '^[a-z0-9][a-z0-9._-]{1,79}$'),
    schema_version text not null default 'kindlemaster.chess.evidence_review.session.v1',
    status text not null default 'active'
        check (status in ('active', 'complete', 'archived')),
    summary jsonb not null default '{}'::jsonb
        check (jsonb_typeof(summary) = 'object'),
    row_count integer not null default 0
        check (row_count between 0 and 5000),
    revision integer not null default 0
        check (revision >= 0),
    saved_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.chess_evidence_review_items (
    artifact_id text not null
        references public.chess_evidence_review_sessions(artifact_id) on delete cascade,
    canonical_diagram_fingerprint text not null
        check (canonical_diagram_fingerprint ~ '^dfp_[0-9a-f]{32}$'),
    source_document_sha256 text not null
        check (source_document_sha256 ~ '^[0-9a-f]{64}$'),
    source_profile text not null
        check (source_profile ~ '^[a-z0-9][a-z0-9._-]{1,79}$'),
    canonical_diagram_id text not null default '',
    legacy_intake_diagram_id text not null default '',
    page integer not null check (page > 0),
    label_status text not null default 'open'
        check (label_status in ('open', 'verified_visible', 'verified_absence', 'unclear', 'excluded')),
    human_verified boolean not null default false,
    marker_shape text not null default ''
        check (marker_shape in ('', 'outline_triangle', 'filled_triangle', 'none_confirmed', 'unclear', 'multiple', 'unavailable')),
    side_to_move text not null default ''
        check (side_to_move in ('', 'w', 'b')),
    marker_bbox jsonb,
    marker_bbox_space text not null default '',
    crop_complete boolean not null default false,
    asset_kind text not null default 'unavailable'
        check (asset_kind in ('marker_crop', 'board_crop', 'unavailable')),
    asset_rel_path text not null default '',
    verified_by text not null default '',
    verified_at timestamptz,
    notes text not null default '',
    revision integer not null default 0 check (revision >= 0),
    row_payload jsonb not null check (jsonb_typeof(row_payload) = 'object'),
    saved_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (artifact_id, canonical_diagram_fingerprint),
    check (
        asset_rel_path = '' or (
            asset_rel_path !~ '(^[\\/]|^[a-zA-Z]:|(^|[\\/])\.\.([\\/]|$))'
            and length(asset_rel_path) <= 500
        )
    )
);

create index if not exists chess_evidence_review_items_source_status_idx
    on public.chess_evidence_review_items (source_document_sha256, source_profile, label_status);

create index if not exists chess_evidence_review_items_artifact_order_idx
    on public.chess_evidence_review_items (artifact_id, page, canonical_diagram_id);

alter table public.chess_evidence_review_sessions enable row level security;
alter table public.chess_evidence_review_items enable row level security;

revoke all on table public.chess_evidence_review_sessions from anon, authenticated;
revoke all on table public.chess_evidence_review_items from anon, authenticated;
grant select, insert, update, delete on table public.chess_evidence_review_sessions to service_role;
grant select, insert, update, delete on table public.chess_evidence_review_items to service_role;

create or replace function public.import_chess_evidence_review_queue(
    p_artifact_id text,
    p_owner_user_id uuid,
    p_source_document_sha256 text,
    p_source_profile text,
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
    if p_source_profile !~ '^[a-z0-9][a-z0-9._-]{1,79}$' then
        raise exception 'invalid_source_profile';
    end if;
    if jsonb_typeof(p_rows) <> 'array' or jsonb_typeof(p_summary) <> 'object' then
        raise exception 'invalid_evidence_queue_payload';
    end if;
    resolved_count := jsonb_array_length(p_rows);
    if resolved_count = 0 or resolved_count > 5000 then
        raise exception 'evidence_queue_row_limit';
    end if;
    if exists (
        select 1 from public.chess_evidence_review_sessions session
        where session.artifact_id = p_artifact_id
          and (
              session.source_document_sha256 <> p_source_document_sha256
              or session.source_profile <> p_source_profile
          )
    ) then
        raise exception 'evidence_queue_source_mismatch';
    end if;
    if exists (
        select 1 from jsonb_array_elements(p_rows) item
        where coalesce(item ->> 'canonical_diagram_fingerprint', '') !~ '^dfp_[0-9a-f]{32}$'
           or coalesce(item ->> 'source_document_sha256', '') <> p_source_document_sha256
           or coalesce(item ->> 'source_profile', '') <> p_source_profile
           or coalesce(item ->> 'page', '') !~ '^[1-9][0-9]*$'
    ) then
        raise exception 'invalid_source_bound_evidence_row';
    end if;
    if exists (
        select 1
        from (
            select item ->> 'canonical_diagram_fingerprint'
            from jsonb_array_elements(p_rows) item
            group by item ->> 'canonical_diagram_fingerprint'
            having count(*) > 1
        ) duplicates
    ) then
        raise exception 'duplicate_canonical_diagram_fingerprint';
    end if;

    insert into public.chess_evidence_review_sessions (
        artifact_id, owner_user_id, source_document_sha256, source_profile,
        summary, row_count, saved_at, updated_at
    ) values (
        p_artifact_id, p_owner_user_id, p_source_document_sha256, p_source_profile,
        p_summary, resolved_count, p_saved_at, now()
    )
    on conflict (artifact_id) do update
    set owner_user_id = coalesce(public.chess_evidence_review_sessions.owner_user_id, excluded.owner_user_id),
        summary = excluded.summary,
        row_count = excluded.row_count,
        saved_at = excluded.saved_at,
        updated_at = now();

    insert into public.chess_evidence_review_items (
        artifact_id, canonical_diagram_fingerprint, source_document_sha256,
        source_profile, canonical_diagram_id, legacy_intake_diagram_id, page,
        label_status, human_verified, marker_shape, side_to_move, marker_bbox,
        marker_bbox_space, crop_complete, asset_kind, asset_rel_path, row_payload,
        saved_at, updated_at
    )
    select
        p_artifact_id,
        item ->> 'canonical_diagram_fingerprint',
        p_source_document_sha256,
        p_source_profile,
        coalesce(item ->> 'canonical_diagram_id', ''),
        coalesce(item ->> 'legacy_intake_diagram_id', ''),
        (item ->> 'page')::integer,
        'open', false,
        coalesce(item ->> 'marker_shape', ''),
        coalesce(item ->> 'side_to_move', ''),
        null, '', false,
        coalesce(item ->> 'asset_kind', 'unavailable'),
        coalesce(item ->> 'asset_rel_path', ''),
        item,
        p_saved_at,
        now()
    from jsonb_array_elements(p_rows) item
    on conflict (artifact_id, canonical_diagram_fingerprint) do update
    set canonical_diagram_id = excluded.canonical_diagram_id,
        legacy_intake_diagram_id = excluded.legacy_intake_diagram_id,
        page = excluded.page,
        asset_kind = excluded.asset_kind,
        asset_rel_path = excluded.asset_rel_path,
        row_payload = public.chess_evidence_review_items.row_payload || excluded.row_payload,
        saved_at = excluded.saved_at,
        updated_at = now()
    where public.chess_evidence_review_items.revision = 0;

    return jsonb_build_object(
        'artifact_id', p_artifact_id,
        'row_count', resolved_count,
        'summary', p_summary,
        'saved_at', p_saved_at
    );
end;
$$;

create or replace function public.save_chess_evidence_review_item(
    p_artifact_id text,
    p_source_document_sha256 text,
    p_source_profile text,
    p_canonical_diagram_fingerprint text,
    p_expected_revision integer,
    p_row jsonb,
    p_saved_at timestamptz default now()
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    current_item public.chess_evidence_review_items%rowtype;
    next_revision integer;
    next_status text := coalesce(p_row ->> 'label_status', 'open');
    next_shape text := coalesce(p_row ->> 'marker_shape', '');
    next_side text := coalesce(p_row ->> 'side_to_move', '');
    next_bbox jsonb := p_row -> 'marker_bbox';
    next_crop_complete boolean := coalesce((p_row ->> 'crop_complete')::boolean, false);
    next_asset_kind text := coalesce(p_row ->> 'asset_kind', 'unavailable');
begin
    select * into current_item
    from public.chess_evidence_review_items item
    where item.artifact_id = p_artifact_id
      and item.canonical_diagram_fingerprint = p_canonical_diagram_fingerprint
    for update;
    if not found then
        raise exception 'evidence_review_item_not_found';
    end if;
    if current_item.source_document_sha256 <> p_source_document_sha256
       or current_item.source_profile <> p_source_profile then
        raise exception 'evidence_review_source_mismatch';
    end if;
    if current_item.revision <> p_expected_revision then
        raise exception 'evidence_review_revision_conflict';
    end if;
    if coalesce(p_row ->> 'canonical_diagram_fingerprint', '') <> p_canonical_diagram_fingerprint then
        raise exception 'evidence_review_identity_mismatch';
    end if;
    if next_status not in ('open', 'verified_visible', 'verified_absence', 'unclear', 'excluded') then
        raise exception 'invalid_evidence_review_status';
    end if;
    if next_status = 'verified_visible' and (
        next_shape not in ('outline_triangle', 'filled_triangle')
        or next_side not in ('w', 'b')
        or jsonb_typeof(next_bbox) <> 'array'
        or jsonb_array_length(next_bbox) <> 4
        or next_asset_kind = 'unavailable'
    ) then
        raise exception 'visible_marker_requires_bbox';
    end if;
    if next_status = 'verified_absence' and (
        next_shape <> 'none_confirmed' or not next_crop_complete
    ) then
        raise exception 'marker_absence_requires_complete_crop';
    end if;

    next_revision := current_item.revision + 1;
    update public.chess_evidence_review_items
    set label_status = next_status,
        human_verified = next_status <> 'open',
        marker_shape = next_shape,
        side_to_move = next_side,
        marker_bbox = next_bbox,
        marker_bbox_space = coalesce(p_row ->> 'marker_bbox_space', ''),
        crop_complete = next_crop_complete,
        verified_by = case when next_status <> 'open' then coalesce(p_row ->> 'verified_by', '') else '' end,
        verified_at = case when next_status <> 'open' then p_saved_at else null end,
        notes = left(coalesce(p_row ->> 'notes', ''), 4000),
        revision = next_revision,
        row_payload = current_item.row_payload || p_row || jsonb_build_object(
            'revision', next_revision,
            'saved_at', p_saved_at
        ),
        saved_at = p_saved_at,
        updated_at = now()
    where artifact_id = p_artifact_id
      and canonical_diagram_fingerprint = p_canonical_diagram_fingerprint;

    update public.chess_evidence_review_sessions session
    set revision = session.revision + 1,
        summary = (
            select jsonb_build_object(
                'total', count(*),
                'open', count(*) filter (where item.label_status = 'open'),
                'verified_visible', count(*) filter (where item.label_status = 'verified_visible'),
                'verified_absence', count(*) filter (where item.label_status = 'verified_absence'),
                'unclear', count(*) filter (where item.label_status = 'unclear'),
                'excluded', count(*) filter (where item.label_status = 'excluded')
            )
            from public.chess_evidence_review_items item
            where item.artifact_id = p_artifact_id
        ),
        status = case
            when not exists (
                select 1 from public.chess_evidence_review_items item
                where item.artifact_id = p_artifact_id and item.label_status = 'open'
            ) then 'complete'
            else 'active'
        end,
        saved_at = p_saved_at,
        updated_at = now()
    where session.artifact_id = p_artifact_id;

    return jsonb_build_object(
        'artifact_id', p_artifact_id,
        'canonical_diagram_fingerprint', p_canonical_diagram_fingerprint,
        'revision', next_revision,
        'saved_at', p_saved_at,
        'row', (
            select item.row_payload
            from public.chess_evidence_review_items item
            where item.artifact_id = p_artifact_id
              and item.canonical_diagram_fingerprint = p_canonical_diagram_fingerprint
        )
    );
end;
$$;

revoke all on function public.import_chess_evidence_review_queue(text, uuid, text, text, jsonb, jsonb, timestamptz)
    from public, anon, authenticated;
revoke all on function public.save_chess_evidence_review_item(text, text, text, text, integer, jsonb, timestamptz)
    from public, anon, authenticated;
grant execute on function public.import_chess_evidence_review_queue(text, uuid, text, text, jsonb, jsonb, timestamptz)
    to service_role;
grant execute on function public.save_chess_evidence_review_item(text, text, text, text, integer, jsonb, timestamptz)
    to service_role;
