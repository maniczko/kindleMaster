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
    );;
