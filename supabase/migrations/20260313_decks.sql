do $$
begin
  if exists (
    select 1
    from information_schema.tables
    where table_schema = 'public' and table_name = 'quiz_questions'
  ) then
    execute 'alter table public.quiz_questions add column if not exists deck text not null default ''General knowledge''';

    execute $sql$
      update public.quiz_questions
      set deck = case
        when coalesce(nullif(trim(deck), ''), '') <> '' then deck
        when lower(coalesce(category, '')) like 'pgmp%' then 'PgMP'
        when lower(coalesce(category, '')) like 'english%' then 'English'
        when lower(coalesce(category, '')) like 'russian%' then 'Russian'
        else 'General knowledge'
      end
      where deck is null or trim(deck) = ''
    $sql$;
  end if;
end $$;
