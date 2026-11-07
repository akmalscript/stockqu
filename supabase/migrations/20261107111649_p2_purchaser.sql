-- StockQu - P2 Purchaser
-- Jalankan SETELAH 001_init_stockqu.sql dan 002_p1_analyst.sql.
-- Menambah atribut MOQ/pack pada harga referensi kontrak, procurement plan,
-- plan items, RLS read-only untuk UI, dan RPC transaksional khusus service_role.

-- 1. Data pengadaan yang dibutuhkan optimizer. Nilai default 1 adalah kebijakan
--    sistem; ganti dengan nilai kontrak nyata saat tersedia.
alter table public.contracts
  add column if not exists moq integer not null default 1 check (moq > 0),
  add column if not exists pack_size integer not null default 1 check (pack_size > 0);

-- 2. Header rencana pembelian per run Purchaser.
create table if not exists public.procurement_plans (
  id uuid primary key default gen_random_uuid(),
  run_id text not null unique,
  analyst_run_id text not null,
  period text not null check (period ~ '^[0-9]{4}-[0-9]{2}$'),
  budget_total numeric(16,2) not null check (budget_total >= 0),
  budget_used numeric(16,2) not null check (budget_used >= 0),
  budget_committed numeric(16,2) not null check (budget_committed >= 0),
  budget_available numeric(16,2) not null check (budget_available >= 0),
  proposed_total numeric(16,2) not null check (proposed_total >= 0),
  remaining_after_plan numeric(16,2) not null check (remaining_after_plan >= 0),
  status text not null check (status in ('completed', 'no_action', 'budget_insufficient', 'failed')),
  optimization_method text not null,
  impact jsonb not null default '{}'::jsonb,
  validation jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_procurement_plans_created
  on public.procurement_plans (created_at desc);
create index if not exists idx_procurement_plans_period
  on public.procurement_plans (period, created_at desc);

-- 3. Keputusan per SKU-cabang. Cabang tetap terpisah; tidak ada agregasi stok.
create table if not exists public.procurement_plan_items (
  id bigint generated always as identity primary key,
  plan_id uuid not null references public.procurement_plans(id) on delete cascade,
  product_id uuid not null references public.products(id) on delete restrict,
  branch_id uuid not null references public.branches(id) on delete restrict,
  reference_contract_id uuid references public.contracts(id) on delete set null,
  rank integer not null check (rank > 0),
  decision text not null check (decision in ('selected', 'partial', 'deferred')),
  requested_qty integer not null check (requested_qty >= 0),
  allocated_qty integer not null check (allocated_qty >= 0),
  unit_price numeric(14,2),
  moq integer,
  pack_size integer,
  line_total numeric(16,2) not null default 0 check (line_total >= 0),
  priority_score numeric(8,6),
  risk_level text not null,
  reason_trace jsonb not null default '{}'::jsonb,
  evidence jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (plan_id, product_id, branch_id),
  check (allocated_qty = 0 or unit_price is not null),
  check (allocated_qty = 0 or (moq is not null and allocated_qty >= moq)),
  check (allocated_qty = 0 or (pack_size is not null and allocated_qty % pack_size = 0))
);

create index if not exists idx_plan_items_plan_id
  on public.procurement_plan_items (plan_id);
create index if not exists idx_plan_items_product_id
  on public.procurement_plan_items (product_id);
create index if not exists idx_plan_items_branch_id
  on public.procurement_plan_items (branch_id);
create index if not exists idx_plan_items_selected
  on public.procurement_plan_items (plan_id, rank)
  where decision in ('selected', 'partial');

-- 4. RLS: UI fase P2 hanya boleh membaca. Semua write melalui Agent Service
--    dengan service_role; kebijakan RBAC per user diperketat pada P5.
alter table public.procurement_plans enable row level security;
alter table public.procurement_plan_items enable row level security;

drop policy if exists "read procurement_plans" on public.procurement_plans;
create policy "read procurement_plans"
  on public.procurement_plans for select
  to anon, authenticated
  using (true);

drop policy if exists "read procurement_plan_items" on public.procurement_plan_items;
create policy "read procurement_plan_items"
  on public.procurement_plan_items for select
  to anon, authenticated
  using (true);

-- Supabase mulai 30 Okt 2026 tidak otomatis mengekspos tabel baru ke Data API.
-- Grant eksplisit dipisahkan dari RLS dan dibuat least-privilege untuk client.
revoke all on table public.procurement_plans from anon, authenticated;
revoke all on table public.procurement_plan_items from anon, authenticated;
grant select on table public.procurement_plans to anon, authenticated;
grant select on table public.procurement_plan_items to anon, authenticated;
grant all on table public.procurement_plans to service_role;
grant all on table public.procurement_plan_items to service_role;
grant usage, select on sequence public.procurement_plan_items_id_seq to service_role;

-- Prasyarat P0/P1 yang dibaca P2 melalui Data API.
grant select on table public.budgets, public.purchase_orders, public.products,
  public.branches, public.contracts, public.analyst_forecasts, public.agent_logs
  to anon, authenticated;
grant select on table public.budgets, public.purchase_orders, public.products,
  public.branches, public.contracts, public.analyst_forecasts, public.agent_logs
  to service_role;
grant insert on table public.agent_logs to service_role;
grant select, insert, update, delete on table public.procurement_plans,
  public.procurement_plan_items to service_role;

-- 5. Simpan header + items dalam satu transaksi. SECURITY INVOKER menjaga hak
--    akses caller; fungsi hanya dapat dipanggil service_role.
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
begin
  insert into public.procurement_plans (
    id, run_id, analyst_run_id, period,
    budget_total, budget_used, budget_committed, budget_available,
    proposed_total, remaining_after_plan, status, optimization_method,
    impact, validation, created_at
  ) values (
    (p_plan->>'id')::uuid,
    p_plan->>'run_id',
    p_plan->>'analyst_run_id',
    p_plan->>'period',
    (p_plan->>'budget_total')::numeric,
    (p_plan->>'budget_used')::numeric,
    (p_plan->>'budget_committed')::numeric,
    (p_plan->>'budget_available')::numeric,
    (p_plan->>'proposed_total')::numeric,
    (p_plan->>'remaining_after_plan')::numeric,
    p_plan->>'status',
    p_plan->>'optimization_method',
    coalesce(p_plan->'impact', '{}'::jsonb),
    coalesce(p_plan->'validation', '{}'::jsonb),
    coalesce((p_plan->>'created_at')::timestamptz, now())
  )
  on conflict (run_id) do update set
    analyst_run_id = excluded.analyst_run_id,
    period = excluded.period,
    budget_total = excluded.budget_total,
    budget_used = excluded.budget_used,
    budget_committed = excluded.budget_committed,
    budget_available = excluded.budget_available,
    proposed_total = excluded.proposed_total,
    remaining_after_plan = excluded.remaining_after_plan,
    status = excluded.status,
    optimization_method = excluded.optimization_method,
    impact = excluded.impact,
    validation = excluded.validation
  returning id into v_plan_id;

  delete from public.procurement_plan_items where plan_id = v_plan_id;

  insert into public.procurement_plan_items (
    plan_id, product_id, branch_id, reference_contract_id, rank, decision,
    requested_qty, allocated_qty, unit_price, moq, pack_size, line_total,
    priority_score, risk_level, reason_trace, evidence
  )
  select
    v_plan_id,
    x.product_id,
    x.branch_id,
    x.reference_contract_id,
    x.rank,
    x.decision,
    x.requested_qty,
    x.allocated_qty,
    x.unit_price,
    x.moq,
    x.pack_size,
    x.line_total,
    x.priority_score,
    x.risk_level,
    x.reason_trace,
    x.evidence
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

  return v_plan_id;
end;
$$;

revoke execute on function public.persist_procurement_plan(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.persist_procurement_plan(jsonb, jsonb) to service_role;
