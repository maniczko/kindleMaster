create table if not exists public.billing_accounts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null default '',
  stripe_customer_id text unique,
  stripe_checkout_session_id text,
  stripe_subscription_id text unique,
  checkout_mode text not null default 'subscription',
  billing_status text not null default 'inactive',
  payment_status text,
  price_id text,
  currency text,
  amount_total bigint,
  current_period_end timestamptz,
  last_event_type text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

alter table public.billing_accounts enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public' and tablename = 'billing_accounts' and policyname = 'billing_accounts_select_own'
  ) then
    create policy billing_accounts_select_own
      on public.billing_accounts
      for select
      using (auth.uid() = user_id);
  end if;

  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public' and tablename = 'billing_accounts' and policyname = 'billing_accounts_insert_own'
  ) then
    create policy billing_accounts_insert_own
      on public.billing_accounts
      for insert
      with check (auth.uid() = user_id);
  end if;

  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public' and tablename = 'billing_accounts' and policyname = 'billing_accounts_update_own'
  ) then
    create policy billing_accounts_update_own
      on public.billing_accounts
      for update
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;
