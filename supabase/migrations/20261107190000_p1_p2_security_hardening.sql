-- StockQu P1/P2 security and budget hardening.
-- Safe for projects that already applied 001, 002, and P2 purchaser migration.

begin;

-- Actor identity is retained in audit records. Browser identity is verified by
-- Supabase Auth; system:* actors are reserved for trusted CLI/cron services.
alter table public.agent_logs
  add column if not exists actor_id text,
  add column if not exists actor_role text;

alter table public.procurement_plans
  add column if not exists requested_by uuid references auth.users(id) on delete set null,
  add column if not exists requested_by_role text,
  add column if not exists budget_allocated numeric(16,2) not null default 0;

-- Identitas requester adalah audit evidence; jangan hilangkan referensinya saat
-- akun Auth hendak dihapus.
alter table public.procurement_plans
  drop constraint if exists procurement_plans_requested_by_fkey;
alter table public.procurement_plans
  add constraint procurement_plans_requested_by_fkey
  foreign key (requested_by) references auth.users(id) on delete restrict;

-- Plan lama dibuat oleh Agent Service sebelum actor audit tersedia.
update public.procurement_plans
set requested_by_role = 'system_agent'
where requested_by_role is null;

alter table public.procurement_plans
  alter column requested_by_role set not null;

do $$
begin
  if not exists (
    select 1 from pg_catalog.pg_constraint
    where conname = 'procurement_plans_budget_allocated_check'
      and conrelid = 'public.procurement_plans'::regclass
  ) then
    alter table public.procurement_plans
      add constraint procurement_plans_budget_allocated_check
      check (budget_allocated >= 0);
  end if;
  if not exists (
    select 1 from pg_catalog.pg_constraint
    where conname = 'procurement_plans_requested_by_role_check'
      and conrelid = 'public.procurement_plans'::regclass
  ) then
    alter table public.procurement_plans
      add constraint procurement_plans_requested_by_role_check
      check (
        requested_by_role in (
          'super_admin', 'purchasing_staff', 'procurement_manager', 'system_agent'
        )
      );
  end if;
  if not exists (
    select 1 from pg_catalog.pg_constraint
    where conname = 'procurement_plans_requester_identity_check'
      and conrelid = 'public.procurement_plans'::regclass
  ) then
    alter table public.procurement_plans
      add constraint procurement_plans_requester_identity_check
      check (
        (requested_by_role = 'system_agent' and requested_by is null)
        or (requested_by_role <> 'system_agent' and requested_by is not null)
      );
  end if;
end $$;

-- Plan allocation is a real monthly reservation until P3/P4 converts it to a
-- pending PO or explicitly releases it.
create table if not exists public.procurement_budget_reservations (
  plan_id uuid primary key references public.procurement_plans(id) on delete cascade,
  period text not null check (period ~ '^[0-9]{4}-[0-9]{2}$'),
  amount numeric(16,2) not null check (amount >= 0),
  status text not null check (status in ('active', 'converted', 'released')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_budget_reservations_period_active
  on public.procurement_budget_reservations (period, status)
  where status = 'active';

alter table public.procurement_budget_reservations enable row level security;

-- ---------------------------------------------------------------- RLS helpers
-- app_metadata is administrator-controlled; user_metadata is intentionally not
-- referenced. Super Admin sees all branches, other roles require branch_ids.

drop policy if exists "authenticated branches" on public.branches;
create policy "authenticated branches" on public.branches for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('warehouse_manager', 'purchasing_staff', 'procurement_manager')
    and id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

drop policy if exists "authenticated products" on public.products;
create policy "authenticated products" on public.products for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "branch inventory" on public.inventory;
create policy "branch inventory" on public.inventory for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('warehouse_manager', 'purchasing_staff', 'procurement_manager')
    and branch_id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

drop policy if exists "authenticated suppliers" on public.suppliers;
create policy "authenticated suppliers" on public.suppliers for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "authenticated contracts" on public.contracts;
create policy "authenticated contracts" on public.contracts for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "authenticated budgets" on public.budgets;
create policy "authenticated budgets" on public.budgets for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "authenticated purchase_orders" on public.purchase_orders;
create policy "authenticated purchase_orders" on public.purchase_orders for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "authenticated po_items" on public.po_items;
create policy "authenticated po_items" on public.po_items for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "authenticated approvals" on public.approvals;
create policy "authenticated approvals" on public.approvals for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "authenticated agent_logs" on public.agent_logs;
create policy "authenticated agent_logs" on public.agent_logs for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read sales_history" on public.sales_history;
create policy "read sales_history" on public.sales_history for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('warehouse_manager', 'purchasing_staff', 'procurement_manager')
    and branch_id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

drop policy if exists "read sync_logs" on public.sync_logs;
create policy "read sync_logs" on public.sync_logs for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read analyst_forecasts" on public.analyst_forecasts;
create policy "read analyst_forecasts" on public.analyst_forecasts for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('warehouse_manager', 'purchasing_staff', 'procurement_manager')
    and branch_id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

drop policy if exists "read procurement_plans" on public.procurement_plans;
create policy "read procurement_plans" on public.procurement_plans for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read procurement_plan_items" on public.procurement_plan_items;
create policy "read procurement_plan_items" on public.procurement_plan_items for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('purchasing_staff', 'procurement_manager')
    and branch_id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

drop policy if exists "read budget reservations" on public.procurement_budget_reservations;
create policy "read budget reservations" on public.procurement_budget_reservations
  for select to authenticated using (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('super_admin', 'purchasing_staff', 'procurement_manager')
  );

-- Explicit Data API grants for the post-30-Oct-2026 Supabase default.
revoke all on table public.branches, public.products, public.inventory,
  public.suppliers, public.contracts, public.budgets, public.purchase_orders,
  public.po_items, public.approvals, public.agent_logs, public.sales_history,
  public.sync_logs, public.analyst_forecasts, public.procurement_plans,
  public.procurement_plan_items, public.procurement_budget_reservations
  from anon, authenticated;

grant select on table public.branches, public.products, public.inventory,
  public.suppliers, public.contracts, public.budgets, public.purchase_orders,
  public.po_items, public.approvals, public.agent_logs, public.sales_history,
  public.sync_logs, public.analyst_forecasts, public.procurement_plans,
  public.procurement_plan_items, public.procurement_budget_reservations
  to authenticated;

grant all on table public.branches, public.products, public.inventory,
  public.suppliers, public.contracts, public.budgets, public.purchase_orders,
  public.po_items, public.approvals, public.agent_logs, public.sales_history,
  public.sync_logs, public.analyst_forecasts, public.procurement_plans,
  public.procurement_plan_items, public.procurement_budget_reservations
  to service_role;

grant usage, select on all sequences in schema public to service_role;

-- ---------------------------------------------------------------- budget RPC
-- The transaction takes one period-scoped advisory lock, recomputes budget from
-- trusted tables, validates item totals/MOQ/pack, persists the plan, and creates
-- the active reservation before releasing the lock.
create or replace function public.persist_procurement_plan(
  p_plan jsonb,
  p_items jsonb
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_plan_id uuid;
  v_existing_plan_id uuid;
  v_existing_period text;
  v_period text := p_plan->>'period';
  v_period_start date;
  v_total numeric(16,2);
  v_recorded_used numeric(16,2);
  v_po_spent numeric(16,2);
  v_used numeric(16,2);
  v_committed numeric(16,2);
  v_allocated numeric(16,2);
  v_available numeric(16,2);
  v_calculated_total numeric(16,2);
  v_claimed_total numeric(16,2);
  v_requested_by uuid;
  v_requested_by_role text := p_plan->>'requested_by_role';
begin
  if v_period is null or v_period !~ '^[0-9]{4}-[0-9]{2}$' then
    raise exception 'period must use YYYY-MM' using errcode = '22007';
  end if;
  v_period_start := to_date(v_period || '-01', 'YYYY-MM-DD');
  if to_char(v_period_start, 'YYYY-MM') <> v_period then
    raise exception 'period is not a valid month' using errcode = '22007';
  end if;

  if coalesce(p_plan->>'run_id', '') = '' then
    raise exception 'run_id is required' using errcode = '23502';
  end if;
  v_plan_id := (p_plan->>'id')::uuid;
  if v_plan_id is null then
    raise exception 'plan id is required' using errcode = '23502';
  end if;
  v_requested_by := nullif(p_plan->>'requested_by', '')::uuid;
  if v_requested_by_role is null or v_requested_by_role not in (
    'super_admin', 'purchasing_staff', 'procurement_manager', 'system_agent'
  ) then
    raise exception 'requester role is not allowed to persist procurement plans'
      using errcode = '42501';
  end if;
  if (v_requested_by_role = 'system_agent') <> (v_requested_by is null) then
    raise exception 'requester identity does not match requester role'
      using errcode = '42501';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('stockqu-budget'),
    pg_catalog.hashtext(v_period)
  );

  -- Read the existing plan only after the period lock so a concurrent retry of
  -- the same run excludes its own reservation from the authoritative total.
  select id, period into v_existing_plan_id, v_existing_period
  from public.procurement_plans
  where run_id = p_plan->>'run_id';
  if v_existing_plan_id is not null and v_existing_plan_id <> v_plan_id then
    raise exception 'run_id cannot be rebound to a different plan id' using errcode = '23505';
  end if;
  if v_existing_period is not null and v_existing_period <> v_period then
    raise exception 'run_id cannot move between budget periods' using errcode = '23514';
  end if;

  select greatest(least(coalesce(total_budget, 50000000), 50000000), 0),
         greatest(coalesce(used_budget, 0), 0)
    into v_total, v_recorded_used
  from public.budgets
  where month = v_period
  for update;
  if not found then
    v_total := 50000000;
    v_recorded_used := 0;
  end if;

  select coalesce(sum(greatest(coalesce(total_amount, 0), 0)) filter (
           where lower(status) in ('approved', 'sent', 'received')
         ), 0),
         coalesce(sum(greatest(coalesce(total_amount, 0), 0)) filter (
           where lower(status) in ('waiting_approval', 'pending_approval')
         ), 0)
    into v_po_spent, v_committed
  from public.purchase_orders
  where created_at >= (v_period_start::timestamp at time zone 'Asia/Jakarta')
    and created_at < ((v_period_start + interval '1 month')::timestamp at time zone 'Asia/Jakarta');

  v_used := greatest(v_recorded_used, v_po_spent);

  select coalesce(sum(amount), 0)
    into v_allocated
  from public.procurement_budget_reservations
  where period = v_period
    and status = 'active'
    and plan_id <> coalesce(v_existing_plan_id, v_plan_id);

  v_available := greatest(v_total - v_used - v_committed - v_allocated, 0);

  select coalesce(sum(coalesce(x.allocated_qty, 0) * coalesce(x.unit_price, 0)), 0)
    into v_calculated_total
  from jsonb_to_recordset(coalesce(p_items, '[]'::jsonb)) as x(
    allocated_qty integer,
    unit_price numeric
  );
  v_claimed_total := coalesce((p_plan->>'proposed_total')::numeric, 0);

  if round(v_claimed_total, 2) <> round(v_calculated_total, 2) then
    raise exception 'proposed_total does not match item calculation' using errcode = '23514';
  end if;
  if v_calculated_total > v_available then
    raise exception 'procurement plan exceeds authoritative available budget' using errcode = '23514';
  end if;
  if exists (
    select 1
    from jsonb_to_recordset(coalesce(p_items, '[]'::jsonb)) as x(
      allocated_qty integer,
      unit_price numeric,
      moq integer,
      pack_size integer,
      line_total numeric
    )
    where x.allocated_qty < 0
       or (x.allocated_qty > 0 and (x.unit_price is null or x.unit_price <= 0))
       or (x.allocated_qty > 0 and (x.moq is null or x.allocated_qty < x.moq))
       or (x.allocated_qty > 0 and (x.pack_size is null or x.pack_size <= 0 or x.allocated_qty % x.pack_size <> 0))
       or round(coalesce(x.line_total, 0), 2) <>
          round(coalesce(x.allocated_qty, 0) * coalesce(x.unit_price, 0), 2)
  ) then
    raise exception 'plan item violates quantity, price, MOQ, pack, or line total invariant'
      using errcode = '23514';
  end if;
  if (
    select count(*)
    from jsonb_to_recordset(coalesce(p_items, '[]'::jsonb)) as x(product_id uuid, branch_id uuid)
  ) <> (
    select count(distinct (x.product_id, x.branch_id))
    from jsonb_to_recordset(coalesce(p_items, '[]'::jsonb)) as x(product_id uuid, branch_id uuid)
  ) then
    raise exception 'duplicate product and branch in procurement plan' using errcode = '23505';
  end if;

  insert into public.procurement_plans (
    id, run_id, analyst_run_id, period,
    budget_total, budget_used, budget_committed, budget_allocated, budget_available,
    proposed_total, remaining_after_plan, status, optimization_method,
    impact, validation, requested_by, requested_by_role, created_at
  ) values (
    v_plan_id,
    p_plan->>'run_id',
    p_plan->>'analyst_run_id',
    v_period,
    v_total,
    v_used,
    v_committed,
    v_allocated,
    v_available,
    v_calculated_total,
    v_available - v_calculated_total,
    p_plan->>'status',
    p_plan->>'optimization_method',
    coalesce(p_plan->'impact', '{}'::jsonb),
    coalesce(p_plan->'validation', '{}'::jsonb),
    v_requested_by,
    v_requested_by_role,
    coalesce((p_plan->>'created_at')::timestamptz, now())
  )
  on conflict (run_id) do update set
    analyst_run_id = excluded.analyst_run_id,
    period = excluded.period,
    budget_total = excluded.budget_total,
    budget_used = excluded.budget_used,
    budget_committed = excluded.budget_committed,
    budget_allocated = excluded.budget_allocated,
    budget_available = excluded.budget_available,
    proposed_total = excluded.proposed_total,
    remaining_after_plan = excluded.remaining_after_plan,
    status = excluded.status,
    optimization_method = excluded.optimization_method,
    impact = excluded.impact,
    validation = excluded.validation,
    requested_by = excluded.requested_by,
    requested_by_role = excluded.requested_by_role
  returning id into v_plan_id;

  delete from public.procurement_plan_items where plan_id = v_plan_id;

  insert into public.procurement_plan_items (
    plan_id, product_id, branch_id, reference_contract_id, rank, decision,
    requested_qty, allocated_qty, unit_price, moq, pack_size, line_total,
    priority_score, risk_level, reason_trace, evidence
  )
  select
    v_plan_id, x.product_id, x.branch_id, x.reference_contract_id, x.rank,
    x.decision, x.requested_qty, x.allocated_qty, x.unit_price, x.moq,
    x.pack_size, x.line_total, x.priority_score, x.risk_level,
    x.reason_trace, x.evidence
  from jsonb_to_recordset(coalesce(p_items, '[]'::jsonb)) as x(
    product_id uuid,
    branch_id uuid,
    reference_contract_id uuid,
    rank integer,
    decision text,
    requested_qty integer,
    allocated_qty integer,
    unit_price numeric,
    moq integer,
    pack_size integer,
    line_total numeric,
    priority_score numeric,
    risk_level text,
    reason_trace jsonb,
    evidence jsonb
  );

  insert into public.procurement_budget_reservations (
    plan_id, period, amount, status, created_at, updated_at
  ) values (
    v_plan_id, v_period, v_calculated_total,
    case when v_calculated_total > 0 then 'active' else 'released' end,
    now(), now()
  )
  on conflict (plan_id) do update set
    period = excluded.period,
    amount = excluded.amount,
    status = excluded.status,
    updated_at = now();

  return v_plan_id;
end;
$$;

revoke execute on function public.persist_procurement_plan(jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.persist_procurement_plan(jsonb, jsonb)
  to service_role;

commit;
