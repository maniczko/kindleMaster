-- KindleMaster cloud accounts and durable library.
-- Apply in a Supabase project with Auth enabled. The app never relies on
-- user-editable metadata for authorization; RLS uses stable auth.uid() checks.

create table if not exists public.user_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    conversion_defaults jsonb not null default '{}'::jsonb,
    smtp_defaults jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.conversion_jobs (
    job_id text primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    status text not null default 'queued',
    message text not null default '',
    filename text not null default '',
    source_type text not null default '',
    download_name text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    elapsed_seconds integer,
    output_size_bytes bigint not null default 0,
    metadata jsonb not null default '{}'::jsonb,
    quality_state_snapshot jsonb not null default '{}'::jsonb,
    auto_repair jsonb not null default '{}'::jsonb,
    email_delivery jsonb not null default '{}'::jsonb,
    runtime jsonb not null default '{}'::jsonb,
    error text not null default '',
    error_code text not null default '',
    imported_from_local boolean not null default false
);

create table if not exists public.conversion_artifacts (
    id uuid primary key default gen_random_uuid(),
    job_id text not null references public.conversion_jobs(job_id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    kind text not null,
    filename text not null default '',
    content_type text not null default 'application/octet-stream',
    size_bytes bigint not null default 0,
    storage_bucket text not null default 'kindlemaster-artifacts',
    storage_path text not null,
    signed_url_metadata jsonb not null default '{}'::jsonb,
    retention_days integer not null default 30,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (job_id, user_id, kind, filename)
);

create index if not exists conversion_jobs_user_updated_idx
    on public.conversion_jobs (user_id, updated_at desc);

create index if not exists conversion_artifacts_user_job_idx
    on public.conversion_artifacts (user_id, job_id, kind);

alter table public.user_profiles enable row level security;
alter table public.conversion_jobs enable row level security;
alter table public.conversion_artifacts enable row level security;

drop policy if exists user_profiles_select_own on public.user_profiles;
create policy user_profiles_select_own
    on public.user_profiles for select
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists user_profiles_insert_own on public.user_profiles;
create policy user_profiles_insert_own
    on public.user_profiles for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists user_profiles_update_own on public.user_profiles;
create policy user_profiles_update_own
    on public.user_profiles for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

drop policy if exists conversion_jobs_select_own on public.conversion_jobs;
create policy conversion_jobs_select_own
    on public.conversion_jobs for select
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists conversion_jobs_insert_own on public.conversion_jobs;
create policy conversion_jobs_insert_own
    on public.conversion_jobs for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists conversion_jobs_update_own on public.conversion_jobs;
create policy conversion_jobs_update_own
    on public.conversion_jobs for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

drop policy if exists conversion_artifacts_select_own on public.conversion_artifacts;
create policy conversion_artifacts_select_own
    on public.conversion_artifacts for select
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists conversion_artifacts_insert_own on public.conversion_artifacts;
create policy conversion_artifacts_insert_own
    on public.conversion_artifacts for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists conversion_artifacts_update_own on public.conversion_artifacts;
create policy conversion_artifacts_update_own
    on public.conversion_artifacts for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

grant usage on schema public to authenticated;
grant select, insert, update on public.user_profiles to authenticated;
grant select, insert, update on public.conversion_jobs to authenticated;
grant select, insert, update on public.conversion_artifacts to authenticated;

insert into storage.buckets (id, name, public)
values ('kindlemaster-artifacts', 'kindlemaster-artifacts', false)
on conflict (id) do update set public = false;

drop policy if exists kindlemaster_storage_select_own on storage.objects;
create policy kindlemaster_storage_select_own
    on storage.objects for select
    to authenticated
    using (
        bucket_id = 'kindlemaster-artifacts'
        and (select auth.uid())::text = (storage.foldername(name))[1]
    );

drop policy if exists kindlemaster_storage_insert_own on storage.objects;
create policy kindlemaster_storage_insert_own
    on storage.objects for insert
    to authenticated
    with check (
        bucket_id = 'kindlemaster-artifacts'
        and (select auth.uid())::text = (storage.foldername(name))[1]
    );

drop policy if exists kindlemaster_storage_update_own on storage.objects;
create policy kindlemaster_storage_update_own
    on storage.objects for update
    to authenticated
    using (
        bucket_id = 'kindlemaster-artifacts'
        and (select auth.uid())::text = (storage.foldername(name))[1]
    )
    with check (
        bucket_id = 'kindlemaster-artifacts'
        and (select auth.uid())::text = (storage.foldername(name))[1]
    );
