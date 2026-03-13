do $$
begin
  if exists (
    select 1
    from information_schema.tables
    where table_schema = 'public' and table_name = 'quiz_questions'
  ) then
    execute 'alter table public.quiz_questions add column if not exists question_type text not null default ''single_choice''';
    execute 'alter table public.quiz_questions add column if not exists correct_answers text[] not null default ''{}''::text[]';
    execute 'alter table public.quiz_questions add column if not exists answer_back text';
    execute 'alter table public.quiz_questions add column if not exists source_type text';
    execute 'alter table public.quiz_questions add column if not exists is_active boolean not null default true';

    update public.quiz_questions
    set correct_answers = case
      when coalesce(correct_answer, '') <> '' then array[upper(correct_answer)]
      else '{}'::text[]
    end
    where coalesce(array_length(correct_answers, 1), 0) = 0;

    update public.quiz_questions
    set question_type = 'single_choice'
    where coalesce(question_type, '') = '';
  end if;
end $$;

do $$
begin
  if exists (
    select 1
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname = 'quiz_questions'
  ) then
    if not exists (
      select 1
      from pg_policies
      where schemaname = 'public' and tablename = 'quiz_questions' and policyname = 'quiz_questions_select_all'
    ) then
      create policy quiz_questions_select_all on public.quiz_questions for select using (true);
    end if;

    if not exists (
      select 1
      from pg_policies
      where schemaname = 'public' and tablename = 'quiz_questions' and policyname = 'quiz_questions_insert_authenticated'
    ) then
      create policy quiz_questions_insert_authenticated on public.quiz_questions for insert to authenticated with check (true);
    end if;

    if not exists (
      select 1
      from pg_policies
      where schemaname = 'public' and tablename = 'quiz_questions' and policyname = 'quiz_questions_update_authenticated'
    ) then
      create policy quiz_questions_update_authenticated on public.quiz_questions for update to authenticated using (true) with check (true);
    end if;
  end if;
end $$;
