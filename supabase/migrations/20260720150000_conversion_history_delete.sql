-- Allow users to permanently delete only their own conversion history and objects.

drop policy if exists conversion_jobs_delete_own on public.conversion_jobs;
create policy conversion_jobs_delete_own
    on public.conversion_jobs for delete
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists kindlemaster_storage_delete_own on storage.objects;
create policy kindlemaster_storage_delete_own
    on storage.objects for delete
    to authenticated
    using (
        bucket_id = 'kindlemaster-artifacts'
        and (select auth.uid())::text = (storage.foldername(name))[1]
    );
