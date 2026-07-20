-- Let authenticated owners remove their own durable conversion history.
-- Deleting a conversion job cascades to its artifact metadata rows.

drop policy if exists conversion_jobs_delete_own on public.conversion_jobs;
create policy conversion_jobs_delete_own
    on public.conversion_jobs for delete
    to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists conversion_artifacts_delete_own on public.conversion_artifacts;
create policy conversion_artifacts_delete_own
    on public.conversion_artifacts for delete
    to authenticated
    using ((select auth.uid()) = user_id);

grant delete on public.conversion_jobs to authenticated;
grant delete on public.conversion_artifacts to authenticated;
