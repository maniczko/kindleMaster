do $$
begin
  if exists (
    select 1
    from information_schema.tables
    where table_schema = 'public' and table_name = 'quiz_questions'
  ) then
    execute 'alter table public.quiz_questions add column if not exists image_url text';
    execute 'alter table public.quiz_questions add column if not exists audio_url text';
  end if;
end $$;
