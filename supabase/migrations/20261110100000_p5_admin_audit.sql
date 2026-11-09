-- StockQu - P5 Dashboard: CRUD master data Super Admin + audit log (F-04, F-05, F-25, DESIGN 13.2)
-- Jalankan SETELAH 20261109200000_p4_orchestration_approval.sql.
--
-- Halaman Admin menulis memakai JWT user (bukan service role). Hak tulis ditegakkan RLS:
-- hanya app_metadata.role = 'super_admin'. Setiap insert/update/delete dicatat oleh trigger
-- ke audit_log yang append-only (tidak ada UPDATE/DELETE untuk siapa pun selain owner DB).

begin;

-- ---------------------------------------------------------------- 1. audit log append-only
create table if not exists public.audit_log (
  id bigint generated always as identity primary key,
  table_name text not null,
  row_id text,
  action text not null check (action in ('INSERT', 'UPDATE', 'DELETE')),
  actor_id uuid,
  actor_role text,
  old_data jsonb,
  new_data jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_audit_log_created on public.audit_log (created_at desc);
create index if not exists idx_audit_log_table on public.audit_log (table_name, created_at desc);

alter table public.audit_log enable row level security;
drop policy if exists "read audit_log" on public.audit_log;
create policy "read audit_log" on public.audit_log for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in ('super_admin', 'warehouse_manager')
);
revoke all on table public.audit_log from anon, authenticated, service_role;
grant select on table public.audit_log to authenticated, service_role;

-- SECURITY DEFINER: trigger menulis audit meski caller tidak punya hak INSERT ke audit_log.
create or replace function public.stockqu_audit_row()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.audit_log (table_name, row_id, action, actor_id, actor_role, old_data, new_data)
  values (
    tg_table_name,
    coalesce((case when tg_op = 'DELETE' then to_jsonb(old) else to_jsonb(new) end) ->> 'id', null),
    tg_op,
    auth.uid(),
    coalesce(auth.jwt() -> 'app_metadata' ->> 'role', auth.role()),
    case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end,
    case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end
  );
  return coalesce(new, old);
end;
$$;

revoke execute on function public.stockqu_audit_row() from public, anon, authenticated;

do $$
declare t text;
begin
  foreach t in array array['products', 'suppliers', 'contracts', 'budgets', 'branches'] loop
    execute format('drop trigger if exists trg_audit_%1$s on public.%1$I', t);
    execute format(
      'create trigger trg_audit_%1$s after insert or update or delete on public.%1$I '
      'for each row execute function public.stockqu_audit_row()', t);
  end loop;
end $$;

-- ---------------------------------------------------------------- 2. RLS tulis Super Admin (F-04)
do $$
declare t text;
begin
  foreach t in array array['products', 'suppliers', 'contracts', 'budgets'] loop
    execute format('drop policy if exists "super_admin write %1$s" on public.%1$I', t);
    execute format(
      'create policy "super_admin write %1$s" on public.%1$I for all to authenticated '
      'using (coalesce((select auth.jwt() -> ''app_metadata'' ->> ''role''), '''') = ''super_admin'') '
      'with check (coalesce((select auth.jwt() -> ''app_metadata'' ->> ''role''), '''') = ''super_admin'')', t);
    execute format('grant insert, update, delete on table public.%1$I to authenticated', t);
  end loop;
end $$;

-- Budget bulanan tidak boleh di atas plafon kebijakan Rp 50.000.000.
do $$
begin
  if not exists (
    select 1 from pg_catalog.pg_constraint
    where conname = 'budgets_total_cap_check' and conrelid = 'public.budgets'::regclass
  ) then
    alter table public.budgets
      add constraint budgets_total_cap_check check (total_budget >= 0 and total_budget <= 50000000);
  end if;
end $$;

-- Validasi dasar master data (UI hanya UX).
do $$
begin
  if not exists (select 1 from pg_catalog.pg_constraint where conname = 'products_values_check') then
    alter table public.products add constraint products_values_check check (
      margin >= 0 and turnover_rate >= 0 and lead_time_days >= 0 and safety_days >= 0);
  end if;
  if not exists (select 1 from pg_catalog.pg_constraint where conname = 'suppliers_values_check') then
    alter table public.suppliers add constraint suppliers_values_check check (
      lead_time_days >= 0 and performance_score between 0 and 100);
  end if;
  if not exists (select 1 from pg_catalog.pg_constraint where conname = 'contracts_values_check') then
    alter table public.contracts add constraint contracts_values_check check (price > 0 and discount >= 0 and discount <= 100);
  end if;
end $$;

commit;
